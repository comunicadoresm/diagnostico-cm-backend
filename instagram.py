import time
import logging

import instaloader
from dotenv import load_dotenv
from fastapi import HTTPException

load_dotenv()

logger = logging.getLogger(__name__)


def _build_loader() -> instaloader.Instaloader:
    """Cria e retorna uma instancia do Instaloader."""
    return instaloader.Instaloader(
        quiet=True,
        download_pictures=False,
        download_videos=False,
        download_video_thumbnails=False,
        download_geotags=False,
        download_comments=False,
        save_metadata=False,
        compress_json=False,
    )


def get_profile(username: str) -> dict:
    """Coleta dados publicos do perfil do Instagram.

    Args:
        username: Nome de usuario (com ou sem '@').

    Returns:
        Dict com informacoes do perfil.

    Raises:
        HTTPException 404: Perfil nao encontrado.
        HTTPException 403: Perfil privado.
        HTTPException 429: Rate limit atingido.
        HTTPException 500: Erro inesperado.
    """
    username = username.lstrip("@").strip()
    if not username:
        raise HTTPException(status_code=400, detail="Username nao pode ser vazio.")

    L = _build_loader()

    try:
        time.sleep(2)
        profile = instaloader.Profile.from_username(L.context, username)

        if profile.is_private:
            raise HTTPException(
                status_code=403,
                detail=f"O perfil @{username} e privado e nao pode ser analisado.",
            )

        # Tenta obter highlights (requer login em alguns casos)
        highlights_count = 0
        try:
            highlights_count = len(list(L.get_highlights(profile)))
        except Exception as e:
            logger.warning("Nao foi possivel obter highlights de @%s: %s", username, e)

        return {
            "username": profile.username,
            "full_name": profile.full_name,
            "biography": profile.biography,
            "followers": profile.followers,
            "following": profile.followees,
            "posts_count": profile.mediacount,
            "profile_pic_url": profile.profile_pic_url,
            "external_url": profile.external_url,
            "highlights_count": highlights_count,
            "is_private": profile.is_private,
        }

    except HTTPException:
        raise

    except instaloader.exceptions.ProfileNotExistsException:
        raise HTTPException(
            status_code=404,
            detail=f"Perfil @{username} nao encontrado no Instagram.",
        )

    except instaloader.exceptions.PrivateProfileNotFollowedException:
        raise HTTPException(
            status_code=403,
            detail=f"O perfil @{username} e privado.",
        )

    except instaloader.exceptions.TooManyRequestsException:
        raise HTTPException(
            status_code=429,
            detail="Muitas requisicoes ao Instagram. Tente novamente em alguns minutos.",
        )

    except instaloader.exceptions.ConnectionException as e:
        error_msg = str(e).lower()
        if "429" in error_msg or "too many" in error_msg or "rate" in error_msg:
            raise HTTPException(
                status_code=429,
                detail="Rate limit do Instagram atingido. Tente novamente em alguns minutos.",
            )
        logger.error("Erro de conexao ao buscar perfil @%s: %s", username, e)
        raise HTTPException(
            status_code=502,
            detail=f"Erro de conexao ao acessar o Instagram: {str(e)}",
        )

    except Exception as e:
        logger.error("Erro inesperado ao buscar perfil @%s: %s", username, e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Erro interno ao buscar perfil: {str(e)}",
        )


def get_posts(username: str) -> list:
    """Coleta os ultimos 9 posts publicos do perfil.

    Args:
        username: Nome de usuario (com ou sem '@').

    Returns:
        Lista de dicts com informacoes de cada post.

    Raises:
        HTTPException 404: Perfil nao encontrado.
        HTTPException 403: Perfil privado.
        HTTPException 429: Rate limit atingido.
        HTTPException 500: Erro inesperado.
    """
    username = username.lstrip("@").strip()
    if not username:
        raise HTTPException(status_code=400, detail="Username nao pode ser vazio.")

    L = _build_loader()

    try:
        time.sleep(2)
        profile = instaloader.Profile.from_username(L.context, username)

        if profile.is_private:
            raise HTTPException(
                status_code=403,
                detail=f"O perfil @{username} e privado e nao pode ser analisado.",
            )

        posts = []
        for post in profile.get_posts():
            if len(posts) >= 9:
                break

            try:
                caption = post.caption or ""
                caption_preview = caption[:100] if caption else ""

                post_data = {
                    "shortcode": post.shortcode,
                    "is_video": post.is_video,
                    "thumbnail_url": post.url,
                    "likes": post.likes,
                    "comments": post.comments,
                    "date": post.date_utc.isoformat() if post.date_utc else None,
                    "caption_preview": caption_preview,
                }

                if post.is_video:
                    post_data["video_url"] = post.video_url
                    post_data["views"] = post.video_view_count
                else:
                    post_data["video_url"] = None
                    post_data["views"] = None

                posts.append(post_data)

            except Exception as e:
                logger.warning("Erro ao processar post %s: %s", post.shortcode, e)
                continue

            time.sleep(1)

        return posts

    except HTTPException:
        raise

    except instaloader.exceptions.ProfileNotExistsException:
        raise HTTPException(
            status_code=404,
            detail=f"Perfil @{username} nao encontrado no Instagram.",
        )

    except instaloader.exceptions.PrivateProfileNotFollowedException:
        raise HTTPException(
            status_code=403,
            detail=f"O perfil @{username} e privado.",
        )

    except instaloader.exceptions.TooManyRequestsException:
        raise HTTPException(
            status_code=429,
            detail="Muitas requisicoes ao Instagram. Tente novamente em alguns minutos.",
        )

    except instaloader.exceptions.ConnectionException as e:
        error_msg = str(e).lower()
        if "429" in error_msg or "too many" in error_msg or "rate" in error_msg:
            raise HTTPException(
                status_code=429,
                detail="Rate limit do Instagram atingido. Tente novamente em alguns minutos.",
            )
        logger.error("Erro de conexao ao buscar posts de @%s: %s", username, e)
        raise HTTPException(
            status_code=502,
            detail=f"Erro de conexao ao acessar o Instagram: {str(e)}",
        )

    except Exception as e:
        logger.error("Erro inesperado ao buscar posts de @%s: %s", username, e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Erro interno ao buscar posts: {str(e)}",
        )
