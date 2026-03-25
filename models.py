from typing import Optional

from pydantic import BaseModel


class ProfileRequest(BaseModel):
    username: str


class ProfileScoreRequest(BaseModel):
    username: str
    profile_data: dict


class VideoAnalyzeRequest(BaseModel):
    username: str
    shortcode: str
    profile_score_data: dict


class SaveEmailRequest(BaseModel):
    session_id: str
    email: str
    name: Optional[str] = None


class SaveContactRequest(BaseModel):
    session_id: str
    email: str
    whatsapp: str
    name: Optional[str] = None


class SaveQuizRequest(BaseModel):
    session_id: str
    answers: list[dict]  # [{"question_id": "uuid", "answer": "texto"}]
