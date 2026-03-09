from datetime import datetime

from pydantic import BaseModel


class VendorCreate(BaseModel):
    name: str
    status: str = "unverified"
    last_verification_date: datetime | None = None
    next_verification_date: datetime | None = None


class VendorUpdate(BaseModel):
    name: str | None = None
    status: str | None = None
    last_verification_date: datetime | None = None
    next_verification_date: datetime | None = None


class Vendor(BaseModel):
    id: str
    user_id: str
    name: str
    status: str
    last_verification_date: datetime | None = None
    next_verification_date: datetime | None = None
    created_at: datetime | None = None


class VendorContractCreate(BaseModel):
    vendor_id: str
    filename: str
    s3_key: str | None = None


class VendorContractUpdate(BaseModel):
    audit_status: str | None = None
    compliance_status: str | None = None


class VendorContract(BaseModel):
    id: str
    vendor_id: str
    user_id: str
    filename: str
    s3_key: str | None = None
    audit_status: str = "waiting"
    compliance_status: str | None = None
    created_at: datetime | None = None
