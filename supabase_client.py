import logging
import os
import uuid
from datetime import datetime, timezone

from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

logger = logging.getLogger(__name__)

TABLE = "diagnostic_sessions"


def get_client():
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_KEY")
    if not url or not key:
        raise RuntimeError("SUPABASE_URL e SUPABASE_SERVICE_KEY devem estar definidos no ambiente.")
    return create_client(url, key)


def create_session(username: str, shortcode: str) -> str:
    """Insere novo registro na tabela diagnostic_sessions com status 'processing'.
    Retorna o session_id (uuid como string)."""
    client = get_client()
    session_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    payload = {
        "id": session_id,
        "username": username,
        "video_shortcode": shortcode,
        "status": "processing",
        "created_at": now,
    }

    response = client.table(TABLE).insert(payload).execute()

    if not response.data:
        raise RuntimeError(f"Falha ao criar sessao no Supabase: {response}")

    return session_id


def update_session(session_id: str, data: dict):
    """Atualiza registro existente na tabela diagnostic_sessions pelo id."""
    client = get_client()
    response = client.table(TABLE).update(data).eq("id", session_id).execute()

    if response.data is None:
        raise RuntimeError(f"Falha ao atualizar sessao {session_id} no Supabase.")

    return response.data


def update_status_detail(session_id: str, detail: str) -> None:
    """Atualiza status_detail de forma segura — falhas são apenas logadas, nunca propagadas.
    Usado para progresso informativo durante o pipeline.
    """
    try:
        update_session(session_id, {"status_detail": detail})
    except Exception as e:
        logger.warning("[%s] Falha ao atualizar status_detail='%s': %s", session_id, detail, e)


def get_session(session_id: str) -> dict:
    """Busca registro na tabela diagnostic_sessions pelo id. Retorna dict ou lança erro."""
    client = get_client()
    response = client.table(TABLE).select("*").eq("id", session_id).execute()

    if not response.data:
        raise ValueError(f"Sessao {session_id} nao encontrada no Supabase.")

    return response.data[0]


def get_quiz_questions() -> list[dict]:
    """Retorna todas as perguntas ativas do quiz ordenadas por order_num."""
    client = get_client()
    response = (
        client.table("quiz_questions")
        .select("*")
        .eq("is_active", True)
        .order("order_num")
        .execute()
    )
    return response.data or []


def get_quiz_answers(session_id: str) -> list[dict]:
    """Retorna as respostas do quiz de uma sessão."""
    client = get_client()
    response = (
        client.table("quiz_answers")
        .select("*")
        .eq("session_id", session_id)
        .execute()
    )
    return response.data or []


def save_quiz_answers(session_id: str, answers: list[dict]) -> None:
    """Salva respostas do quiz na tabela quiz_answers.

    Args:
        session_id: UUID da sessão.
        answers: Lista de dicts com question_id e answer.
    """
    client = get_client()
    now = datetime.now(timezone.utc).isoformat()

    rows = [
        {
            "id": str(uuid.uuid4()),
            "session_id": session_id,
            "question_id": a.get("question_id"),
            "question_text": a.get("question_text", ""),
            "answer": a.get("answer", ""),
            "created_at": now,
        }
        for a in answers
    ]

    if rows:
        client.table("quiz_answers").insert(rows).execute()
        logger.info("Salvas %d respostas do quiz para session %s", len(rows), session_id)
