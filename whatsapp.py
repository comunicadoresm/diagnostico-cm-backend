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
        phone: Número no formato 5511999999999.
        message: Texto da mensagem.

    Returns:
        True se enviado com sucesso, False caso contrário.
    """
    if not all([ZAPI_INSTANCE, ZAPI_TOKEN, ZAPI_CLIENT_TOKEN]):
        logger.warning("Z-API não configurado (ZAPI_INSTANCE_ID, ZAPI_TOKEN ou ZAPI_CLIENT_TOKEN ausente). Pulando.")
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
    """Dispara as 2 mensagens do fluxo de diagnóstico via WhatsApp.

    Mensagem 1: Resumo imediato do diagnóstico.
    Mensagem 2: Convite para imersão (~2min depois no MVP, substituir por scheduler em produção).

    Args:
        phone: Número no formato 5511999999999.
        name: Nome do lead.
        report: Dict completo do relatório (retorno do /report/{session_id}).
    """
    if not phone:
        logger.info("Sem WhatsApp cadastrado — pulando envio.")
        return

    username = report.get("username", "seu perfil")
    score = report.get("total_score", 0)
    nivel = report.get("nivel_alerta", "importante")
    headline = report.get("headline_diagnostico", "Seu diagnóstico está pronto")
    video_scores = report.get("video_scores", {})
    gap = video_scores.get("principal_gap", "")
    proximo = video_scores.get("proximo_passo", "")
    produto_url = PRODUTO_URL

    first_name = name.split()[0] if name else "Oi"

    alerta_emoji = {"critico": "🚨", "importante": "⚠️", "atencao": "📌"}.get(nivel, "📊")

    # MENSAGEM 1 — Resumo do diagnóstico
    msg1 = (
        f"{alerta_emoji} *{headline}*\n\n"
        f"Olá, {first_name}! Seu diagnóstico do @{username} foi concluído.\n\n"
        f"📊 *Score geral: {score}/100*\n\n"
        f"🔍 *Principal gap identificado:*\n{gap}\n\n"
        f"✅ *Próximo passo recomendado:*\n{proximo}\n\n"
        f"Acesse seu relatório completo no app para ver todos os detalhes da análise."
    )

    await send_whatsapp_text(phone, msg1)
    logger.info("Mensagem 1 enviada para %s", phone)

    # Aguarda antes de enviar o convite
    # TODO: em produção, substituir por scheduler (Celery, APScheduler, etc.)
    await asyncio.sleep(120)  # 2 minutos no MVP

    # MENSAGEM 2 — Convite para imersão
    if nivel == "critico":
        urgencia = "Os gaps identificados são críticos e estão custando vendas agora."
    elif nivel == "importante":
        urgencia = "Esses ajustes podem mudar seus resultados rapidamente."
    else:
        urgencia = "Com os ajustes certos, seus resultados vão escalar."

    msg2 = (
        f"💡 *Uma última coisa, {first_name}...*\n\n"
        f"{urgencia}\n\n"
        f"Quer corrigir esses gaps em um único dia, com a metodologia completa da Giullya Becker?\n\n"
        f"👉 {produto_url}\n\n"
        f"A imersão foi feita exatamente para quem está no momento que você está agora. 🎯"
    )

    await send_whatsapp_text(phone, msg2)
    logger.info("Mensagem 2 enviada para %s", phone)
