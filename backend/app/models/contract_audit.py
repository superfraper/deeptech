from datetime import datetime

from pydantic import BaseModel


class ChecklistItem(BaseModel):
    question_id: str
    question: str
    answer: str | None = None
    source_quote: str | None = None
    source_document: str | None = None
    compliant: bool | None = None


class ContractAuditCreate(BaseModel):
    contract_id: str | None = None
    checklist_type: str
    checklist_name: str
    documents: list[str] = []


class ContractAuditStart(BaseModel):
    audit_id: str
    documents: list[str] = []
    custom_checklist: list[dict] | None = None


class ContractAudit(BaseModel):
    id: str
    contract_id: str | None = None
    user_id: str
    checklist_type: str
    checklist_name: str
    checklist_items: list[ChecklistItem] = []
    status: str = "pending"
    progress: int = 0
    report_s3_key: str | None = None
    created_at: datetime | None = None
    completed_at: datetime | None = None


class ContractAuditStatus(BaseModel):
    id: str
    status: str
    progress: int
    checklist_items: list[ChecklistItem] = []
    completed_at: datetime | None = None


class ChecklistDefinition(BaseModel):
    id: str
    name: str
    description: str | None = None
    questions: list[dict]
