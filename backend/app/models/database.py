from pydantic import BaseModel


class FieldQuestionsFormat(BaseModel):
    field_id: str | None = None
    question: str
    type: str
    relevant_field: str | None = None
    relevant_variable: str | None = None


class TabularFormatMember(BaseModel):
    Identity: str
    Business_Address: str
    Functions: str
