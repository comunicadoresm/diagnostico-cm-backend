import asyncio
import logging
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import whisper
from dotenv import load_dotenv

import activecampaign
import scorer
import supabase_client
import whatsapp

load_dotenv()

logger = logging.getLogger(__name__)

# Cache do modelo Whisper — carregado uma única vez na primeira chamada
# para evitar recarregamento de ~1.4GB a cada pipeline
_whisper_model: Optional[whisper.Whisper] = None


def _get_whisper_model() -> whisper.Whisper:
    """Retorna o modelo Whisper, carregando-o apenas na primeira chamada."""
    global _whisper_model
    if _whisper_model is None:
        logger.info("Carregando modelo Whisper 'base' (pode levar alguns minutos na primeira vez)...")
        _whisper_model = whisper.load_model("base")
        logger.info("Modelo Whisper carregado e em cache.")
    return _whisper_model

_SCORE_LABELS = {
    (0, 40): "Perfil precisa de restruturação completa",
    (41, 65): "Perfil com potencial, perdendo vendas por gaps no conteúdo",
    (66, 85): "Perfil bom — ajustes pontuais fariam grande diferença",
    (86, 100): "Perfil bem otimizado — hora de escalar",
}


def _calcular_score_label(total_score: float) -> str:
    for (low, high), label in _SCORE_LABELS.items():
        if low <= total_score <= high:
            return label
    return "Perfil bem otimizado — hora de escalar"


def download_video(url: str, output_path: str) -> None:
    """Baixa o video do Instagram usando yt-dlp.

    Args:
        url: URL do reel do Instagram.
        output_path: Caminho completo onde salvar o arquivo .mp4.

    Raises:
        RuntimeError: Se o download falhar.
    """
    command = [
        "yt-dlp",
        "--no-playlist",
        "--format", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "--merge-output-format", "mp4",
        "--output", output_path,
        "--no-check-certificate",
        "--no-warnings",  # Menos verboso que padrão, mas não esconde erros críticos
        url,
    ]

    logger.info("Iniciando download do video: %s -> %s", url, output_path)
    result = subprocess.run(command, capture_output=True, text=True, timeout=600)  # 10 min

    if result.returncode != 0:
        stderr_msg = result.stderr[:1000] if result.stderr else "(sem saída de erro)"
        stdout_msg = result.stdout[:500] if result.stdout else ""
        logger.error("yt-dlp falhou (codigo %d):\nSTDERR: %s\nSTDOUT: %s", result.returncode, stderr_msg, stdout_msg)
        raise RuntimeError(
            f"Falha no download do video. Codigo: {result.returncode}. Erro: {stderr_msg}"
        )

    if not Path(output_path).exists():
        raise RuntimeError(f"Download concluido mas arquivo nao encontrado em: {output_path}")

    size_mb = Path(output_path).stat().st_size / (1024 * 1024)
    logger.info("Video baixado com sucesso: %s (%.1f MB)", output_path, size_mb)


def extract_audio(video_path: str, audio_path: str) -> None:
    """Extrai o audio do video em formato WAV 16kHz mono usando ffmpeg.

    Args:
        video_path: Caminho do arquivo de video.
        audio_path: Caminho de saida do arquivo .wav.

    Raises:
        RuntimeError: Se a extracao de audio falhar.
    """
    command = [
        "ffmpeg",
        "-i", video_path,
        "-ac", "1",
        "-ar", "16000",
        "-vn",
        "-acodec", "pcm_s16le",
        "-y",
        audio_path,
    ]

    logger.info("Extraindo audio: %s -> %s", video_path, audio_path)
    result = subprocess.run(command, capture_output=True, text=True, timeout=120)

    if result.returncode != 0:
        logger.error("ffmpeg falhou (codigo %d): %s", result.returncode, result.stderr)
        raise RuntimeError(
            f"Falha na extracao de audio. Codigo: {result.returncode}. Erro: {result.stderr[:500]}"
        )

    if not Path(audio_path).exists():
        raise RuntimeError(f"Audio extraido mas arquivo nao encontrado em: {audio_path}")

    logger.info("Audio extraido com sucesso: %s", audio_path)


def transcribe_audio(audio_path: str) -> str:
    """Transcreve o audio usando o modelo Whisper base.

    Args:
        audio_path: Caminho do arquivo de audio .wav.

    Returns:
        Texto transcrito.

    Raises:
        RuntimeError: Se a transcricao falhar.
    """
    logger.info("Obtendo modelo Whisper do cache...")
    try:
        model = _get_whisper_model()
        result = model.transcribe(audio_path, language="pt", fp16=False)
        text = result.get("text", "").strip()

        if not text:
            logger.warning("Whisper retornou transcricao vazia para: %s", audio_path)
            raise RuntimeError("Transcricao resultou em texto vazio. O video pode nao ter audio falado.")

        logger.info("Transcricao concluida: %d caracteres", len(text))
        return text

    except Exception as e:
        if isinstance(e, RuntimeError):
            raise
        logger.error("Erro ao transcrever audio: %s", e, exc_info=True)
        raise RuntimeError(f"Falha na transcricao com Whisper: {str(e)}")


def run_video_pipeline(
    session_id: str,
    username: str,
    shortcode: str,
    profile_score_data: dict,
) -> None:
    """Pipeline completo: download → audio → transcrição → scoring → Supabase → AC → WhatsApp.

    Executado em background pelo FastAPI BackgroundTasks.

    Args:
        session_id: UUID da sessão criada no Supabase.
        username: Nome de usuario do Instagram.
        shortcode: Shortcode do reel (ex: "ABC123xyz").
        profile_score_data: Resultado do scorer.score_profile(), usado como contexto.
    """
    tmp_dir = Path(f"/tmp/{session_id}")
    video_path = str(tmp_dir / "video.mp4")
    audio_path = str(tmp_dir / "audio.wav")

    try:
        tmp_dir.mkdir(parents=True, exist_ok=True)
        logger.info("[%s] Pipeline iniciado para @%s shortcode=%s", session_id, username, shortcode)

        # 1. Construir URL do reel
        reel_url = f"https://www.instagram.com/reel/{shortcode}/"
        logger.info("[%s] URL do reel: %s", session_id, reel_url)

        # 2. Baixar video
        supabase_client.update_status_detail(session_id, "baixando_video")
        download_video(reel_url, video_path)

        # 3. Extrair audio
        supabase_client.update_status_detail(session_id, "extraindo_audio")
        extract_audio(video_path, audio_path)

        # 4. Transcrever
        supabase_client.update_status_detail(session_id, "transcrevendo")
        transcricao = transcribe_audio(audio_path)
        logger.info("[%s] Transcricao: %s...", session_id, transcricao[:100])

        # 5. Score do video (metodologia IDF GB)
        supabase_client.update_status_detail(session_id, "calculando_score")
        video_score_data = scorer.score_video(transcricao, profile_score_data)

        # 6. Score total ponderado: perfil 40% + video 60%
        profile_score = profile_score_data.get("total_profile_score", 0) if profile_score_data else 0
        video_dimensions = [
            video_score_data.get("gancho_score", 0),
            video_score_data.get("d1_score", 0),
            video_score_data.get("d2_score", 0),
            video_score_data.get("d3_score", 0),
            video_score_data.get("cta_score", 0),
        ]
        video_avg = sum(video_dimensions) / len(video_dimensions) if video_dimensions else 0
        total_score = round((profile_score / 8 * 40) + (video_avg / 10 * 60), 1)
        score_label = _calcular_score_label(total_score)

        nivel_alerta = video_score_data.get("nivel_alerta", "importante")
        headline_diagnostico = video_score_data.get("headline_diagnostico", "Seu diagnóstico está pronto")

        logger.info(
            "[%s] Scores — perfil: %s/8, video_avg: %.1f/10, total: %.1f/100, nivel: %s",
            session_id, profile_score, video_avg, total_score, nivel_alerta,
        )

        # 7. Montar payload para Supabase
        now = datetime.now(timezone.utc).isoformat()
        update_payload = {
            "status": "completed",
            "status_detail": "concluido",
            "completed_at": now,
            "profile_score": profile_score_data,
            "video_scores": video_score_data,
            "total_score": int(total_score),
            "score_label": score_label,
            "nivel_alerta": nivel_alerta,
            "headline_diagnostico": headline_diagnostico,
        }

        supabase_client.update_session(session_id, update_payload)
        logger.info("[%s] Supabase atualizado com sucesso.", session_id)

        # 8. Buscar dados do lead para AC e WhatsApp
        session_data = supabase_client.get_session(session_id)
        lead_email = session_data.get("email", "")
        lead_whatsapp = session_data.get("whatsapp", "")
        lead_name = session_data.get("lead_name", "")

        # 9. Atualizar ActiveCampaign com resultado do diagnóstico
        if lead_email:
            report_data = {
                "username": username,
                "total_score": int(total_score),
                "nivel_alerta": nivel_alerta,
                "headline_diagnostico": headline_diagnostico,
                "video_scores": video_score_data,
            }
            asyncio.run(
                activecampaign.upsert_contact(
                    email=lead_email,
                    name=lead_name,
                    whatsapp=lead_whatsapp,
                    tags=["diagnostico_concluido"],
                    custom_fields={
                        "score_total": int(total_score),
                        "principal_gap": video_score_data.get("principal_gap", ""),
                        "nivel_alerta": nivel_alerta,
                        "username_ig": username,
                    },
                )
            )

        # 10. Enviar WhatsApp
        if lead_whatsapp:
            report_for_wa = {
                "username": username,
                "total_score": int(total_score),
                "nivel_alerta": nivel_alerta,
                "headline_diagnostico": headline_diagnostico,
                "video_scores": video_score_data,
            }
            asyncio.run(
                whatsapp.send_diagnosis_whatsapp(
                    phone=lead_whatsapp,
                    name=lead_name,
                    report=report_for_wa,
                )
            )

        logger.info("[%s] Pipeline concluido com sucesso.", session_id)

    except Exception as e:
        logger.error("[%s] Erro no pipeline: %s", session_id, e, exc_info=True)
        try:
            # Atualiza apenas colunas que existem na schema base (status + completed_at)
            supabase_client.update_session(
                session_id,
                {
                    "status": "error",
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            # Campos opcionais atualizados separadamente (tolerante a falha)
            supabase_client.update_status_detail(session_id, "erro_no_pipeline")
        except Exception as supabase_err:
            logger.error(
                "[%s] Falha adicional ao registrar erro no Supabase: %s",
                session_id, supabase_err,
            )

    finally:
        try:
            if tmp_dir.exists():
                shutil.rmtree(tmp_dir)
                logger.info("[%s] Diretorio temporario removido: %s", session_id, tmp_dir)
        except Exception as cleanup_err:
            logger.warning("[%s] Falha ao limpar temporarios: %s", session_id, cleanup_err)
