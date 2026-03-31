import logging
import os
import uuid

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
import httpx

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
    return {"status": "ok"}


@app.get("/proxy/image", tags=["Proxy"])
async def proxy_image(url: str = Query(..., description="URL da imagem do CDN do Instagram")):
    """Proxy para imagens do CDN do Instagram. Contorna bloqueio de Referrer."""
    _ALLOWED_HOSTS = ("cdninstagram.com", "instagram.com", "fbcdn.net", "scontent")
    if not any(h in url for h in _ALLOWED_HOSTS):
        raise HTTPException(status_code=400, detail="URL não permitida.")

    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15",
        "Referer": "https://www.instagram.com/",
        "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
    }
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail="Falha ao buscar imagem.")
        content_type = resp.headers.get("content-type", "image/jpeg")
        return Response(content=resp.content, media_type=content_type)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Erro no proxy de imagem: %s", e)
        raise HTTPException(status_code=502, detail="Erro ao buscar imagem.")




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
        import asyncio

        # Busca títulos dos destaques com timeout de 5s — não bloqueia se Apify demorar
        try:
            highlight_titles = await asyncio.wait_for(
                asyncio.to_thread(instagram.get_highlight_titles, request.username),
                timeout=5.0,
            )
        except asyncio.TimeoutError:
            logger.warning("Timeout ao buscar titulos de destaques para @%s — continuando sem", request.username)
            highlight_titles = []

        return await asyncio.to_thread(
            scorer.score_profile, request.username, request.profile_data, highlight_titles
        )
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
                list_id=os.getenv("ACTIVECAMPAIGN_LIST_ID"),
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
    """Salva respostas do quiz no Supabase e envia para o ActiveCampaign."""
    logger.info("Salvando quiz para session_id=%s (%d respostas)", request.session_id, len(request.answers))
    try:
        supabase_client.save_quiz_answers(request.session_id, request.answers)

        # Envia respostas ao ActiveCampaign (fire-and-forget)
        import asyncio
        session_data = supabase_client.get_session(request.session_id)
        lead_email = session_data.get("email", "")
        if lead_email:
            # Mapeia respostas para os IDs dos campos no AC
            # IDs: Quiz Resposta 1=285, Quiz Resposta 2=286, Quiz Resposta 3=287
            ac_fields = {}
            for i, ans in enumerate(request.answers[:3]):
                field_id = str(285 + i)
                ac_fields[field_id] = ans.get("answer", "")

            asyncio.create_task(
                activecampaign.upsert_contact(
                    email=lead_email,
                    custom_fields=ac_fields,
                )
            )
            logger.info("Respostas do quiz enviadas ao AC para %s", lead_email)

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
