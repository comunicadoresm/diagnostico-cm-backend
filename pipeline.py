import asyncio
import logging
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx
from groq import Groq
from dotenv import load_dotenv

import activecampaign
import scorer
import supabase_client
import whatsapp

load_dotenv()

logger = logging.getLogger(__name__)


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


def download_video_direct(url: str, output_path: str) -> None:
    """Baixa vídeo diretamente de uma URL CDN via httpx (sem yt-dlp).
    Usado quando o frontend já passou a video_url do Apify.

    Args:
        url: URL direta do CDN (ex: scontent-*.cdninstagram.com/...).
        output_path: Caminho onde salvar o arquivo.

    Raises:
        RuntimeError: Se o download falhar.
    """
    logger.info("Download direto via httpx: %s -> %s", url[:80], output_path)
    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15",
        "Referer": "https://www.instagram.com/",
    }
    with httpx.Client(timeout=120, follow_redirects=True) as client:
        with client.stream("GET", url, headers=headers) as response:
            if response.status_code != 200:
                raise RuntimeError(f"Download CDN falhou com status {response.status_code}")
            with open(output_path, "wb") as f:
                for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                    f.write(chunk)

    if not Path(output_path).exists():
        raise RuntimeError(f"Download concluido mas arquivo nao encontrado em: {output_path}")

    size_mb = Path(output_path).stat().st_size / (1024 * 1024)
    logger.info("Video baixado com sucesso: %s (%.1f MB)", output_path, size_mb)


def download_video(url: str, output_path: str) -> None:
    """Baixa o video do Instagram usando yt-dlp (fallback quando não há video_url direto).

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
        "--no-warnings",
        url,
    ]

    logger.info("Iniciando download via yt-dlp: %s -> %s", url, output_path)
    result = subprocess.run(command, capture_output=True, text=True, timeout=600)

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


def transcribe_audio(media_path: str) -> str:
    """Transcreve mídia via Groq Whisper large-v3 (API remota, sem RAM local).

    Args:
        media_path: Caminho do arquivo de vídeo (.mp4) ou áudio (.wav).

    Returns:
        Texto transcrito.

    Raises:
        RuntimeError: Se a transcrição falhar.
    """
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY não configurado nas variáveis de ambiente.")

    # Envia o mp4 diretamente ao Groq — sem ffmpeg, sem risco de OOM.
    # Groq aceita mp4 até 25MB. Reels do Instagram tipicamente ficam abaixo disso.
    file_size_mb = Path(media_path).stat().st_size / (1024 * 1024)
    logger.info("Enviando %.1f MB ao Groq Whisper large-v3 (mp4 direto, sem ffmpeg)...", file_size_mb)

    if file_size_mb > 24:
        raise RuntimeError(
            f"Arquivo muito grande para Groq ({file_size_mb:.1f}MB > 25MB). "
            "Considere usar um reel mais curto."
        )

    try:
        client = Groq(api_key=api_key, timeout=120.0)
        with open(media_path, "rb") as f:
            result = client.audio.transcriptions.create(
                model="whisper-large-v3",
                file=(Path(media_path).name, f, "video/mp4"),
                language="pt",
                response_format="text",
            )

        text = result.strip() if isinstance(result, str) else result.text.strip()

        if not text:
            raise RuntimeError("Transcricao resultou em texto vazio. O video pode nao ter audio falado.")

        logger.info("Transcricao concluida via Groq: %d caracteres", len(text))
        return text

    except RuntimeError:
        raise
    except Exception as e:
        logger.error("Erro ao transcrever via Groq: %s", e, exc_info=True)
        raise RuntimeError(f"Falha na transcricao com Groq Whisper: {str(e)}")


def run_video_pipeline(
    session_id: str,
    username: str,
    shortcode: str,
    profile_score_data: dict,
    video_url: Optional[str] = None,
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

    try:
        tmp_dir.mkdir(parents=True, exist_ok=True)
        logger.info("[%s] Pipeline iniciado para @%s shortcode=%s", session_id, username, shortcode)

        # 1. Construir URL do reel
        reel_url = f"https://www.instagram.com/reel/{shortcode}/"
        logger.info("[%s] URL do reel: %s", session_id, reel_url)

        # 2. Baixar video (preferência: URL direta do CDN, fallback: yt-dlp)
        supabase_client.update_status_detail(session_id, "baixando_video")
        if video_url:
            logger.info("[%s] Usando URL direta do CDN para download.", session_id)
            download_video_direct(video_url, video_path)
        else:
            logger.info("[%s] Usando yt-dlp para download (sem video_url).", session_id)
            download_video(reel_url, video_path)

        # 3. Transcrever diretamente do vídeo (Whisper lida com ffmpeg internamente)
        # Skip da extração de áudio explícita via ffmpeg para evitar travamento no Railway
        supabase_client.update_status_detail(session_id, "transcrevendo")
        transcricao = transcribe_audio(video_path)
        logger.info("[%s] Transcricao: %s...", session_id, transcricao[:100])

        # 5. Score do video (metodologia IDF GB)
        supabase_client.update_status_detail(session_id, "calculando_score")
        try:
            video_score_data = scorer.score_video(transcricao, profile_score_data)
        except Exception as e:
            raise RuntimeError(f"STEP_score_video: {type(e).__name__}: {e}")

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

        nivel_alerta = video_score_data.get("nivel_alerta", "importante")
        headline_diagnostico = video_score_data.get("headline_diagnostico", "Seu diagnóstico está pronto")

        # Gerar score_label personalizado com base no quiz + scores
        quiz_answers = supabase_client.get_quiz_answers(session_id)
        score_label = scorer.generate_score_label(
            quiz_answers=quiz_answers,
            total_score=total_score,
            profile_score_data=profile_score_data,
            video_score_data=video_score_data,
        )

        logger.info(
            "[%s] Scores — perfil: %s/8, video_avg: %.1f/10, total: %.1f/100, nivel: %s",
            session_id, profile_score, video_avg, total_score, nivel_alerta,
        )

        # 7. Salvar resultado no Supabase
        supabase_client.update_status_detail(session_id, "salvando_resultado")
        now = datetime.now(timezone.utc).isoformat()
        update_payload = {
            "status": "completed",
            "status_detail": "concluido",
            "completed_at": now,
            "profile_score": profile_score_data,
            "video_analysis": video_score_data,
            "total_score": int(total_score),
            "score_label": score_label,
            "nivel_alerta": nivel_alerta,
            "headline_diagnostico": headline_diagnostico,
        }

        try:
            supabase_client.update_session(session_id, update_payload)
        except Exception as e:
            raise RuntimeError(f"STEP_supabase_update: {type(e).__name__}: {e}")

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
                        "284": int(total_score),       # Score Total
                        "283": video_score_data.get("principal_gap", ""),  # Gap Principal
                        "282": username,               # Username IG
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
            supabase_client.update_session(
                session_id,
                {
                    "status": "error",
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                    "error_message": (str(e) or f"{type(e).__name__}")[:500],
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
