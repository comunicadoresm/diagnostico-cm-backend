import logging
import os
import time

import httpx
from dotenv import load_dotenv
from fastapi import HTTPException

load_dotenv()
logger = logging.getLogger(__name__)

APIFY_API_TOKEN = os.getenv("APIFY_API_TOKEN")
APIFY_BASE_URL = "https://api.apify.com/v2"

# Timeout para cada run do Apify (segundos)
_RUN_TIMEOUT = 120


def _apify_run_sync(actor_id: str, input_data: dict) -> list:
    """Executa um Actor do Apify de forma sincrona e retorna os itens do dataset.

    Args:
        actor_id: ID do actor Apify (ex: "apify/instagram-profile-scraper").
        input_data: Payload de entrada para o actor.

    Returns:
        Lista de itens retornados pelo actor.

    Raises:
        HTTPException 429: Rate limit do Apify ou Instagram.
        HTTPException 502: Falha na comunicacao com Apify.
        HTTPException 500: Erro inesperado.
    """
    if not APIFY_API_TOKEN:
        raise HTTPException(
            status_code=500,
            detail="APIFY_API_TOKEN nao configurado. Adicione a variavel de ambiente no Railway.",
        )

    url = f"{APIFY_BASE_URL}/acts/{actor_id}/run-sync-get-dataset-items"
    params = {"token": APIFY_API_TOKEN, "timeout": _RUN_TIMEOUT}

    try:
        with httpx.Client(timeout=_RUN_TIMEOUT + 30) as client:
            response = client.post(url, json=input_data, params=params)

        if response.status_code == 429:
            raise HTTPException(
                status_code=429,
                detail="Rate limit atingido. Tente novamente em alguns minutos.",
            )
        if response.status_code == 402:
            raise HTTPException(
                status_code=502,
                detail="Limite do plano Apify atingido. Verifique sua conta em apify.com.",
            )
        if response.status_code not in (200, 201):
            logger.error("Apify retornou status %d: %s", response.status_code, response.text[:500])
            raise HTTPException(
                status_code=502,
                detail=f"Erro ao chamar Apify (status {response.status_code}).",
            )

        return response.json()

    except HTTPException:
        raise
    except httpx.TimeoutException:
        raise HTTPException(
            status_code=504,
            detail="Timeout ao buscar dados do Instagram via Apify. Tente novamente.",
        )
    except Exception as e:
        logger.error("Erro inesperado ao chamar Apify: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Erro interno ao buscar perfil: {str(e)}",
        )


def get_profile(username: str) -> dict:
    """Coleta dados publicos do perfil do Instagram via Apify.

    Args:
        username: Nome de usuario (com ou sem '@').

    Returns:
        Dict com informacoes do perfil.

    Raises:
        HTTPException 400: Username invalido.
        HTTPException 404: Perfil nao encontrado.
        HTTPException 403: Perfil privado.
        HTTPException 429: Rate limit atingido.
        HTTPException 500: Erro inesperado.
    """
    username = username.lstrip("@").strip()
    if not username:
        raise HTTPException(status_code=400, detail="Username nao pode ser vazio.")

    logger.info("Buscando perfil @%s via Apify", username)

    items = _apify_run_sync(
        actor_id="apify~instagram-profile-scraper",
        input_data={
            "usernames": [username],
            "resultsLimit": 1,
        },
    )

    if not items:
        raise HTTPException(
            status_code=404,
            detail=f"Perfil @{username} nao encontrado no Instagram.",
        )

    profile = items[0]

    # Verificar se o perfil e privado
    if profile.get("private", False) or profile.get("is_private", False):
        raise HTTPException(
            status_code=403,
            detail=f"O perfil @{username} e privado e nao pode ser analisado.",
        )

    return {
        "username": profile.get("username") or username,
        "full_name": profile.get("fullName") or profile.get("full_name", ""),
        "biography": profile.get("biography") or profile.get("bio", ""),
        "followers": profile.get("followersCount") or profile.get("followers", 0),
        "following": profile.get("followsCount") or profile.get("following", 0),
        "posts_count": profile.get("postsCount") or profile.get("posts_count") or profile.get("mediacount", 0),
        "profile_pic_url": profile.get("profilePicUrl") or profile.get("profile_pic_url", ""),
        "external_url": profile.get("externalUrl") or profile.get("external_url", ""),
        "highlights_count": 0,
        "is_private": False,
    }


def get_posts(username: str) -> list:
    """Coleta os ultimos 9 posts publicos do perfil via Apify.

    Args:
        username: Nome de usuario (com ou sem '@').

    Returns:
        Lista de dicts com informacoes de cada post.

    Raises:
        HTTPException 400: Username invalido.
        HTTPException 404: Perfil nao encontrado.
        HTTPException 403: Perfil privado.
        HTTPException 429: Rate limit atingido.
        HTTPException 500: Erro inesperado.
    """
    username = username.lstrip("@").strip()
    if not username:
        raise HTTPException(status_code=400, detail="Username nao pode ser vazio.")

    logger.info("Buscando posts de @%s via Apify", username)

    items = _apify_run_sync(
        actor_id="apify~instagram-post-scraper",
        input_data={
            "directUrls": [f"https://www.instagram.com/{username}/"],
            "resultsLi~it": 9,
        },
    )

    posts = []
    for item in items[:9]:
        try:
            caption = item.get("caption") or item.get("alt", "") or ""
            caption_preview = caption[:100] if caption else ""

            is_video = item.get("type") == "Video" or item.get("isVideo") or item.get("is_video", False)
            shortcode = item.get("shortCode") or item.get("shortcode", "")
            timestamp = item.get("timestamp") or item.get("takenAtTimestamp") or item.get("date")

            post_data = {
                "shortcode": shortcode,
                "is_video": is_video,
                "thumbnail_url": item.get("displayUrl") or item.get("thumbnailUrl") or item.get("url", ""),
                "likes": item.get("likesCount") or item.get("likes", 0),
                "comments": item.get("commentsCount") or item.get("comments", 0),
                "date": timestamp,
                "caption_preview": caption_preview,
                "video_url": item.get("videoUrl") if is_video else None,
                "views": item.get("videoViewCount") or item.get("views") if is_video else None,
            }
            posts.append(post_data)

        except Exception as e:
            logger.warning("Erro ao processar post: %s", e)
            continue

    return posts
