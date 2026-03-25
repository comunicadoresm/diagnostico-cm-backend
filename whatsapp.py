import asyncio
import logging
import os

import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

ZAPI_INSTANCE = os.getenv("ZAPI_INSTANCE_ID", "")
ZAPI_TOKEN = os.getenv("ZAPI_TOKEN", "")
ZAPI_CLIENT_TOKEN = os.getenv("ZAPI_CLIENT_TOKEN", "")
PRODUTO_URL = os.getenv("PRODUTO_URL", "")


async def send_whatsapp_text(phone: str, message: str) -> bool:
    """Envia mensagem de texto via Z-API.

    Args:
        phone: NÃºmero no formato 5511999999999.
        message: Texto da mensagem.

    Returns:
        True se enviado com sucesso, False caso contrÃ¡rio.
    """
    if not all([ZAPI_INSTANCE, ZAPI_TOKEN, ZAPI_CLIENT_TOKEN]):
        logger.warning("Z-API nÃ£o configurado (ZAPI_INSTANCE_ID, ZAPI_TOKEN ou ZAPI_CLIENT_TOKEN ausente). Pulando.")
        return False

    url = f"https://api.z-api.io/instances/{ZAPI_INSTANCE}/token/{ZAPI_TOKEN}/send-text"
    headers = {"Client-Token": ZAPI_CLIENT_TOKEN, "Content-Type": "application/json"}
    payload = {"phone": phone, "message": message}

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(url, json=payload, headers=headers)
            success = r.status_code == 200
            if success:
                logger.info("WhatsApp enviado para %s (status=%d)", phone, r.status_code)
            else:
                logger.warning("Falha ao enviar WhatsApp para %s: status=%d, body=%s", phone, r.status_code, r.text[:200])
            return success
    except Exception as e:
        logger.error("Erro ao enviar WhatsApp para %s: %s", phone, e)
        return False


async def send_diagnosis_whatsapp(phone: str, name: str, report: dict) -> None:
    """Dispara as 2 mensagens do fluxo de diagnÃ³stico via WhatsApp.

    Mensagem 1: Resumo imediato do diagnÃ³stico.
    Mensagem 2: Convite para imersÃ£o (~2min depois no MVP, substituir por scheduler em produÃ§Ã£o).

    Args:
        phone: NÃºmero no formato 5511999999999.
        name: Nome do lead.
        report: Dict completo do relatÃ³rio (retorno do /report/{session_id}).
    """
    if not phone:
        logger.info("Sem WhatsApp cadastrado â pulando envio.")
        return

    username = report.get("username", "seu perfil")
    score = report.get("total_score", 0)
    nivel = report.get("nivel_alerta", "importante")
    headline = report.get("headline_diagnostico", "Seu diagnÃ³stico estÃ¡ pronto")
    video_scores = report.get("video_scores", {})
    gap = video_scores.get("principal_gap", "")
    proximo = video_scores.get("proximo_passo", "")
    produto_url = PRODUTO_URL

    first_name = name.split()[0] if name else "Oi"

    alerta_emoji = {"critico": "ð¨", "importante": "â ï¸", "atencao": "ð"}.get(nivel, "ð")

    # MENSAGEM 1 â Resumo do diagnÃ³stico
    msg1 = (
        f"{alerta_emoji} *{headline}*\n\n"
        f"OlÃ¡, {first_name}! Seu diagnÃ³stico do @{username} foi concluÃ­do.\n\n"
        f"ð *Score geral: {score}/100*\n\n"
        f"ð *Principal gap identificado:*\n{gap}\n\n"
        f"â *PrÃ³ximo passo recomendado:*\n{proximo}\n\n"
        f"Acesse seu relatÃ³rio completo no app para ver todos os detalhes da anÃ¡lise."
    )

    await send_whatsapp_text(phone, msg1)
    logger.info("Mensagem 1 enviada para %s", phone)

    # Aguarda antes de enviar o convite
    # TODO: em produÃ§Ã£o, substituir por scheduler (Celery, APScheduler, etc.)
    await asyncio.sleep(120)  # 2 minutos no MVP

    # MENSAGEM 2 â Convite para imersÃ£o
    if nivel == "critico":
        urgencia = "Os gaps identificados sÃ£o crÃ­ticos e estÃ£o custando vendas agora."
    elif nivel == "importante":
        urgencia = "Esses ajustes podem mudar seus resultados rapidamente."
    else:
        urgencia = "Com os ajustes certos, seus resultados vÃ£o escalar."

    msg2 = (
        f"ð¡ *Uma Ãºltima coisa, {first_name}...*\n\n"
        f"{urgencia}\n\n"
        f"Quer corrigir esses gaps em um Ãºnico dia, com a metodologia completa da Giullya Becker?\n\n"
        f"ð {produto_url}\n\n"
        f"A imersÃ£o foi feita exatamente para quem estÃ¡ no momento que vocÃª estÃ¡ agora. ð¯"
    )

    await send_whatsapp_text(phone, msg2)
    logger.info("Mensagem 2 enviada para %s", phone)
