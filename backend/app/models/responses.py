from typing import Any

from pydantic import BaseModel, Field


class TabularFormatMember(BaseModel):
    Identity: str = Field(description="Full name or identifier of the member")
    Business_Address: str = Field(description="Complete business address")
    Functions: str = Field(description="Role or function within the organization")


class UserContextResponse(BaseModel):
    auth0_user_id: str
    context_data: dict[str, Any] | None = None
    message: str = ""


class FieldFillResponse(BaseModel):
    field_id: str
    field_name: str
    field_text: str
    unanswered_questions: list[str]
    ids: list[str] | None = []
    totally_unanswered: bool | None = False


class GenerateSpecificAnswer(BaseModel):
    field: str
    question: str
    answer: str
    confident: bool
    type: str | None = None


class TabularFormatResponse(BaseModel):
    members: list[TabularFormatMember] = Field(description="List of extracted members")
