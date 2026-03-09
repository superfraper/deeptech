from datetime import datetime
from typing import Any

from pydantic import BaseModel, field_serializer, field_validator


class UserContextRequest(BaseModel):
    auth0_user_id: str
    context_data: dict[str, Any]


class RegenerateRequest(BaseModel):
    field_id: str
    field_name: str
    field_text: str
    unanswered_questions: list[str]
    answers: list[str]
    token_classification: str | None = "OTH"


class GenerationStatus(BaseModel):
    generation_id: str
    user_id: str
    status: str  # 'pending', 'in_progress', 'completed', 'failed'
    progress: int  # 0-100
    total_fields: int
    completed_fields: int
    current_field: str | None = None
    results: dict[str, Any] | None = None
    error_message: str | None = None
    form: dict[str, Any] | None = None
    started_at: datetime | str
    updated_at: datetime | str

    @field_validator("started_at", "updated_at", mode="before")
    @classmethod
    def validate_datetime_fields(cls, v):
        """Accept both datetime objects and strings"""
        if isinstance(v, datetime):
            return v
        elif isinstance(v, str):
            try:
                return datetime.fromisoformat(v.replace("Z", "+00:00"))
            except ValueError:
                return datetime.fromisoformat(v)
        return v

    @field_serializer("started_at", "updated_at")
    def serialize_datetime(self, v: datetime | str) -> str:
        """Always serialize to ISO string for API responses"""
        if isinstance(v, datetime):
            return v.isoformat()
        return v


class FollowUpQuestionRequest(BaseModel):
    fieldKey: str
    fieldTitle: str
    guidelines: str | None = ""
    currentValue: str | None = ""


class QueryRequest(BaseModel):
    query: str
    links: list[str] = []


class GenerateRequest(BaseModel):
    tokenClassification: str
    dateOfNotification: str | None = None
    offerorName: str
    offerorCompaniesHouseLink: str | None = None  # HttpUrl
    offerorPhone: str
    offerorEmail: str | None = None  # Changed from EmailStr to Optional[str]
    offerorLegalForm: str | None = None
    offerorRegisteredAddress: str | None = None
    offerorHeadOffice: str | None = None
    offerorRegistrationDate: str | None = None
    offerorParentCompanyName: str | None = None
    personType: str
    isCryptoAssetNameSame: str
    cryptoAssetName: str | None = None
    isCryptoProjectNameSame: str
    cryptoProjectNameSameAs: str | None = None
    cryptoProjectName: str | None = None
    issuerType: str
    issuerName: str | None = None
    issuerCompaniesHouseLink: str | None = None  # HttpUrl
    issuerPhone: str | None = None
    issuerEmail: str | None = None  # Changed from EmailStr to Optional[str]
    issuerLegalForm: str | None = None
    issuerRegisteredAddress: str | None = None
    issuerHeadOffice: str | None = None
    issuerRegistrationDate: str | None = None
    issuerParentCompanyName: str | None = None
    operatorType: str
    operatorName: str | None = None
    operatorCompaniesHouseLink: str | None = None  # HttpUrl
    operatorPhone: str | None = None
    operatorEmail: str | None = None
    operatorLegalForm: str | None = None
    operatorRegisteredAddress: str | None = None
    operatorHeadOffice: str | None = None
    operatorRegistrationDate: str | None = None
    operatorParentCompanyName: str | None = None
    whitepaperSubmitter: str
    cryptoAssetSituation: str | None = None
    responseTime: str | None = None
    documents: list[str] | None = None
    tokenName: str | None = None
    email: str | None = None  # Changed from EmailStr to Optional[str]
    links: list[str] | None = None
    whitepaperType: str | None = None
    context: str | None = None
    leiNumber: str | None = None
    offerorLeiNumber: str | None = None
    issuerLeiNumber: str | None = None
    operatorLeiNumber: str | None = None
    publicationDate: str | None = None
    submissionType: str | None = None
    prospectiveHolders: str | None = None
    reasonForOffer: str | None = None
    futureCryptoOffers: str | None = None
    # Add new fields for future crypto offer details
    minTargetSubscription: str | None = None
    maxTargetSubscription: str | None = None
    issuePrice: str | None = None
    subscriptionFees: str | None = None
    numberOfCryptoAssets: str | None = None
    offerTargetAudience: str | None = None
    offerConditionsRestrictions: str | None = None
    isPhasedOffer: str | None = None
    caspInCharge: str | None = None
    offerDate: str | None = None
    offerJurisdictions: str | None = None
    plannedUseOfFunds: str | None = None
    selectedDTIs: list[str] | None = None
    selectedFungibleDTIs: list[str] | None = None
    hasContractTerms: str | None = None
    utilityTokenDescription: str | None = None
    keyFeaturesGoodsServices: str | None = None
    keyInformation: str | None = None
    financialCondition: str | None = None
    keyDecisionMakers: str | None = None
    formalStructures: str | None = None
    thirdPartyReserveManagement: str | None = None
    thirdPartyInvestmentAuth: str | None = None
    thirdPartyDistribution: str | None = None
    issuerFinancialCondition: str | None = None
    # Add new ART-specific fields
    artMarketValueBelow5M: str | None = None
    artOnlyQualifiedInvestors: str | None = None
    artIssuerCreditInstitution: str | None = None
