import logging
import os

import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

AC_URL = os.getenv("ACTIVECAMPAIGN_URL", "").rstrip("/")
AC_KEY = os.getenv("ACTIVECAMPAIGN_KEY", "")


async def upsert_contact(
    email: str,
    name: str = "",
    whatsapp: str = "",
    tags: list[str] = None,
    custom_fields: dict = None,
    list_id: str = None,
) -> str | None:
    """Cria ou atualiza contato no ActiveCampaign, adiciona tags e custom fields.

    Args:
        email: Email do contato.
        name: Nome completo (opcional).
        whatsapp: Número WhatsApp (opcional).
        tags: Lista de nomes de tags a adicionar.
        custom_fields: Dict {field_id: value} para atualizar.

    Returns:
        ID do contato criado/atualizado, ou None em caso de falha.
    """
    if not AC_URL or not AC_KEY:
        logger.warning("ActiveCampaign não configurado (AC_URL ou AC_KEY ausente). Pulando.")
        return None

    headers = {"Api-Token": AC_KEY, "Content-Type": "application/json"}
    tags = tags or []
    custom_fields = custom_fields or {}

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            # 1. Criar/atualizar contato via sync
            first_name = name.split()[0] if name else ""
            last_name = " ".join(name.split()[1:]) if name and len(name.split()) > 1 else ""

            payload = {
                "contact": {
                    "email": email,
                    "firstName": first_name,
                    "lastName": last_name,
                    "phone": whatsapp,
                }
            }

            r = await client.post(f"{AC_URL}/api/3/contact/sync", json=payload, headers=headers)
            r.raise_for_status()
            contact_id = r.json()["contact"]["id"]
            logger.info("Contato AC upserted: %s (id=%s)", email, contact_id)

            # 2. Adicionar tags
            for tag_name in tags:
                try:
                    tag_r = await client.post(
                        f"{AC_URL}/api/3/tags",
                        json={"tag": {"tag": tag_name, "tagType": "contact"}},
                        headers=headers,
                    )
                    tag_id = tag_r.json().get("tag", {}).get("id")
                    if tag_id:
                        await client.post(
                            f"{AC_URL}/api/3/contactTags",
                            json={"contactTag": {"contact": contact_id, "tag": tag_id}},
                            headers=headers,
                        )
                        logger.info("Tag '%s' adicionada ao contato %s", tag_name, contact_id)
                except Exception as tag_err:
                    logger.warning("Falha ao adicionar tag '%s': %s", tag_name, tag_err)

            # 3. Atualizar custom fields
            for field_id, value in custom_fields.items():
                try:
                    await client.post(
                        f"{AC_URL}/api/3/fieldValues",
                        json={
                            "fieldValue": {
                                "contact": contact_id,
                                "field": str(field_id),
                                "value": str(value),
                            }
                        },
                        headers=headers,
                    )
                except Exception as field_err:
                    logger.warning("Falha ao atualizar field %s: %s", field_id, field_err)

            # 4. Adicionar à lista
            if list_id:
                try:
                    await client.post(
                        f"{AC_URL}/api/3/contactLists",
                        json={"contactList": {"list": list_id, "contact": contact_id, "status": 1}},
                        headers=headers,
                    )
                    logger.info("Contato %s adicionado à lista %s", contact_id, list_id)
                except Exception as list_err:
                    logger.warning("Falha ao adicionar contato à lista %s: %s", list_id, list_err)

            return str(contact_id)

    except Exception as e:
        logger.error("Erro ao upsert contato AC (%s): %s", email, e, exc_info=True)
        return None
