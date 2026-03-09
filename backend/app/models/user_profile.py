from datetime import datetime

from pydantic import BaseModel


class UserProfileCreate(BaseModel):
    role: str
    use_cases: list[str]
    goals: list[str]


class UserProfile(BaseModel):
    id: str
    auth0_user_id: str
    role: str
    use_cases: list[str]
    goals: list[str]
    onboarding_completed: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None


class UserProfileResponse(BaseModel):
    id: str
    auth0_user_id: str
    role: str
    use_cases: list[str]
    goals: list[str]
    onboarding_completed: bool
    created_at: datetime | None = None
    updated_at: datetime | None = None
