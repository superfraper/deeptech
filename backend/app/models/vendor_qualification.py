from datetime import datetime

from pydantic import BaseModel


class ServiceMapping(BaseModel):
    name: str
    service_type_id: str | None = None
    is_critical: bool | None = None
    source_document: str | None = None
    source_quote: str | None = None


class StepData(BaseModel):
    step_number: int
    question_responses: dict | None = None
    approved: bool = False
    approved_at: datetime | None = None


class VendorQualificationCreate(BaseModel):
    vendor_id: str
    vendor_name: str | None = None


class VendorQualificationUpdate(BaseModel):
    status: str | None = None
    current_step: int | None = None
    is_ict_provider: bool | None = None


class VendorQualificationStepUpdate(BaseModel):
    question_responses: dict | None = None
    approved: bool = False


class VendorQualification(BaseModel):
    id: str
    vendor_id: str
    vendor_name: str | None = None
    user_id: str
    status: str = "draft"
    current_step: int = 1
    step_data: dict | None = None
    is_ict_provider: bool | None = None
    services_mapping: list[ServiceMapping] | None = None
    created_at: datetime | None = None
    completed_at: datetime | None = None


class VendorQualificationResponse(BaseModel):
    qualification: VendorQualification
    message: str | None = None


class VendorQualificationListItem(BaseModel):
    id: str
    vendor_id: str
    vendor_name: str | None = None
    status: str
    current_step: int
    is_ict_provider: bool | None = None
    created_at: datetime | None = None


class GenerateAnswerRequest(BaseModel):
    question_id: str
    question_text: str
    documents: list[str] | None = None
    additional_context: str | None = None


class GenerateAnswerResponse(BaseModel):
    answer: str
    source_document: str | None = None
    source_quote: str | None = None
    confidence: float | None = None
    is_ict_provider_suggestion: bool | None = None


class DoraIctService(BaseModel):
    id: str
    name: str
    description: str


class DoraIctServicesResponse(BaseModel):
    services: list[DoraIctService]
    ict_services_definition: str | None = None
