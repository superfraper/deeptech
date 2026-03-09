from datetime import datetime

from pydantic import BaseModel


class ChatMessage(BaseModel):
    role: str
    content: str
    timestamp: datetime | None = None
    sources: list[dict] | None = None


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    context_documents: list[str] | None = None


class ChatResponse(BaseModel):
    message: str
    sources: list[dict] | None = None
    session_id: str


class ChatSession(BaseModel):
    id: str
    user_id: str
    messages: list[ChatMessage] = []
    context_documents: list[str] = []
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ChatSessionListItem(BaseModel):
    id: str
    created_at: datetime | None = None
    updated_at: datetime | None = None
    message_count: int = 0
    last_message: str | None = None
