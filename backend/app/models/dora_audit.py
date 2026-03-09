from datetime import datetime

from pydantic import BaseModel


class DoraAuditResult(BaseModel):
    question_id: str
    question: str
    answer: str | None = None
    source_document: str | None = None
    source_quote: str | None = None
    compliant: str | None = None


class DoraAuditCreate(BaseModel):
    company_name: str
    questionnaire_data: dict
    documents: list[str] = []


class DoraAudit(BaseModel):
    id: str
    user_id: str
    company_name: str | None = None
    questionnaire_data: dict | None = None
    documents: list[str] = []
    status: str = "pending"
    progress: int = 0
    results: list[DoraAuditResult] = []
    created_at: datetime | None = None
    completed_at: datetime | None = None


class DoraAuditStatus(BaseModel):
    id: str
    status: str
    progress: int
    results: list[DoraAuditResult] = []
    company_name: str | None = None
    completed_at: datetime | None = None


class DoraAuditListItem(BaseModel):
    id: str
    company_name: str | None = None
    status: str
    progress: int
    created_at: datetime | None = None
    completed_at: datetime | None = None
