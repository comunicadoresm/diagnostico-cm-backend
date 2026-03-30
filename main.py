import logging
import os
import uuid

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

import activecampaign
import instagram
import pipeline
import scorer
import supabase_client
from models import (
    ProfileRequest,
    ProfileScoreRequest,
    SaveContactRequest,
    SaveEmailRequest,
    SaveQuizRequest,
    VideoAnalyzeRequest,
)

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

# ── FastAPI app ───────────────────────────────────────────────────────────────
app = FastAPI(
    title="Diagnostico CM API",
    description="Backend para analise magnetica de perfis e videos do Instagram usando IA.",
    version="2.0.0",
)


# ── CORS ──────────────────────────────────────────────────────────────────────
_raw_origins = os.getenv("ALLOWED_ORIGINS", "*")
_origins_list = [o.strip() for o in _raw_origins.split(",") if o.strip()]

# allow_credentials=True é incompatível com allow_origins=["*"] no FastAPI.
# Quando ALLOWED_ORIGINS=* (padrão), libera tudo sem credentials.
if _origins_list == ["*"]:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
else:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

logger.info("CORS configurado para origens: %s", _origins_list)


# ── Rotas ─────────────────────────────────────────────────────────────────────

@app.get("/health", tags=["Status"])
async def health_check():
    return {"status": "ok", "version": "groq-v2"}


@app.post("/debug/score-video", tags=["Status"])
def debug_score_video(request: dict):
    """Testa score_video diretamente — remover após diagnóstico."""
    transcricao = request.get("transcricao", "Teste de transcrição mínima.")
    try:
        result = scorer.score_video(transcricao, {})
        return {"ok": True, "result": result}
    except Exception as e:
        return {"ok": False, "error_type": type(e).__name__, "error": str(e)[:1000]}


@app.get("/debug/groq", tags=["Status"])
def debug_groq():
    """Testa conectividade com Groq API — remover após diagnóstico."""
    import struct
    from groq import Groq

    key = os.getenv("GROQ_API_KEY", "")
    if not key:
        return {"key_set": False, "error": "GROQ_API_KEY não configurado"}

    # WAV mínimo: 1 segundo de silêncio a 16kHz mono 16-bit
    sample_rate, n_samples = 16000, 16000
    wav_header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", 36 + n_samples * 2, b"WAVE",
        b"fmt ", 16, 1, 1, sample_rate, sample_rate * 2, 2, 16,
        b"data", n_samples * 2,
    )
    silence_wav = wav_header + bytes(n_samples * 2)

    try:
        client = Groq(api_key=key, timeout=30.0)
        result = client.audio.transcriptions.create(
            model="whisper-large-v3",
            file=("silence.wav", silence_wav, "audio/wav"),
            language="pt",
            response_format="text",
        )
        text = result if isinstance(result, str) else getattr(result, "text", "")
        return {"key_set": True, "groq_accessible": True, "transcription": text}
    except Exception as e:
        return {"key_set": True, "groq_accessible": False, "error": str(e)}


@app.post("/analyze/profile", tags=["Analise"])
async def analyze_profile(request: ProfileRequest):
    logger.info("Requisicao de analise de perfil: @%s", request.username)
    try:
        return instagram.get_profile(request.username)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Erro em /analyze/profile para @%s: %s", request.username, e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Erro interno: {str(e)}")


@app.post("/analyze/profile-score", tags=["Analise"])
async def analyze_profile_score(request: ProfileScoreRequest):
    logger.info("Requisicao de score de perfil: @%s", request.username)
    try:
        return scorer.score_profile(request.username, request.profile_data)
    except HTTPException:
        raise
    except RuntimeError as e:
        logger.error("Erro ao calcular score de perfil para @%s: %s", request.username, e)
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:
        logger.error("Erro em /analyze/profile-score para @%s: %s", request.username, e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Erro interno: {str(e)}")


@app.get("/posts/{username}", tags=["Analise"])
async def get_posts(username: str):
    logger.info("Requisicao de posts: @%s", username)
    try:
        posts = instagram.get_posts(username)
        return {"username": username, "posts": posts, "count": len(posts)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Erro em /posts/%s: %s", username, e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Erro interno: {str(e)}")


@app.post("/analyze/video", status_code=202, tags=["Analise"])
async def analyze_video(request: VideoAnalyzeRequest, background_tasks: BackgroundTasks):
    """Inicia análise completa de um reel em background. Retorna session_id para polling."""
    logger.info(
        "Requisicao de analise de video: @%s shortcode=%s",
        request.username, request.shortcode,
    )
    try:
        session_id = supabase_client.create_session(request.username, request.shortcode)
        logger.info("Sessao criada: %s", session_id)

        background_tasks.add_task(
            pipeline.run_video_pipeline,
            session_id=session_id,
            username=request.username,
            shortcode=request.shortcode,
            profile_score_data=request.profile_score_data,
            video_url=request.video_url,
        )

        return {"session_id": session_id, "status": "processing"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Erro ao iniciar analise de video para @%s/%s: %s",
            request.username, request.shortcode, e, exc_info=True,
        )
        raise HTTPException(status_code=500, detail=f"Erro ao iniciar analise: {str(e)}")


@app.get("/report/{session_id}", tags=["Relatorio"])
async def get_report(session_id: str):
    logger.info("Requisicao de relatorio: session_id=%s", session_id)
    try:
        return supabase_client.get_session(session_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error("Erro ao buscar relatorio %s: %s", session_id, e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Erro ao buscar relatorio: {str(e)}")


@app.get("/questions", tags=["Quiz"])
async def get_questions():
    """Retorna as perguntas ativas do quiz direto do Supabase."""
    try:
        questions = supabase_client.get_quiz_questions()
        return {"questions": questions}
    except Exception as e:
        logger.error("Erro ao buscar perguntas do quiz: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Erro ao buscar perguntas: {str(e)}")


@app.post("/save/contact", tags=["Lead"])
async def save_contact(request: SaveContactRequest):
    """Salva dados de contato do lead e dispara tag no ActiveCampaign."""
    logger.info("Salvando contato para session_id=%s email=%s", request.session_id, request.email)
    try:
        update_data = {
            "email": request.email,
            "whatsapp": request.whatsapp,
        }
        if request.name:
            update_data["lead_name"] = request.name

        supabase_client.update_session(request.session_id, update_data)

        # Dispara contato no ActiveCampaign em background (fire-and-forget)
        import asyncio
        asyncio.create_task(
            activecampaign.upsert_contact(
                email=request.email,
                name=request.name or "",
                whatsapp=request.whatsapp,
                tags=["diagnostico_iniciado"],
            )
        )

        return {"ok": True}

    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error("Erro ao salvar contato para session %s: %s", request.session_id, e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Erro ao salvar contato: {str(e)}")


@app.post("/save/quiz", tags=["Quiz"])
async def save_quiz(request: SaveQuizRequest):
    """Salva respostas do quiz associadas à sessão."""
    logger.info("Salvando quiz para session_id=%s (%d respostas)", request.session_id, len(request.answers))
    try:
        supabase_client.save_quiz_answers(request.session_id, request.answers)
        return {"ok": True, "saved": len(request.answers)}
    except Exception as e:
        logger.error("Erro ao salvar quiz para session %s: %s", request.session_id, e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Erro ao salvar respostas: {str(e)}")


@app.post("/save/email", tags=["Lead"])
async def save_email(request: SaveEmailRequest):
    """Salva email do usuario na sessao (mantido para retrocompatibilidade)."""
    logger.info("Salvando email para session_id=%s", request.session_id)
    try:
        update_data = {"email": request.email}
        if request.name:
            update_data["lead_name"] = request.name
        supabase_client.update_session(request.session_id, update_data)
        return {"session_id": request.session_id, "status": "email_saved"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error("Erro ao salvar email para session %s: %s", request.session_id, e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Erro ao salvar email: {str(e)}")


# ── Entrypoint ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
