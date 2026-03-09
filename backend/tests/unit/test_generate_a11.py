import logging
from unittest.mock import MagicMock, patch

import pytest

from app.models.requests import GenerateRequest
from app.models.responses import FieldFillResponse
from app.utils.generate import generate_field_fill


def make_min_request(**overrides):
    """Helper to build a minimal GenerateRequest with sensible defaults."""
    base = {
        "tokenClassification": "OTH",
        "offerorName": "Acme Corp",
        "offerorPhone": "123456789",
        "personType": "LEGAL",
        "isCryptoAssetNameSame": "TRUE",
        "isCryptoProjectNameSame": "TRUE",
        "issuerType": "SAME_AS_OFFEROR",
        "operatorType": "SAME_AS_OFFEROR",
        "whitepaperSubmitter": "Acme Corp",
        # Common optional fields that may be referenced
        "documents": [],
        "offerorLeiNumber": "529900T8BM49AURSDO55",
    }
    base.update(overrides)
    return GenerateRequest(**base)


@pytest.mark.asyncio
async def test_a11_forwards_parent_company_name_when_present():
    """
    A.11 should be 'N/A' when offerorLeiNumber is present per business rule.
    """
    req = make_min_request(offerorParentCompanyName="Acme Holdings Ltd")
    logger = logging.getLogger("test")

    with patch("app.utils.generate.get_field_standards") as mock_standards:
        mock_standards.return_value = {
            "field_content": "offerorParentCompanyName",
            "form_and_standards": "Forwarded value ifLEI",
        }

        result = await generate_field_fill(
            field_id="A.11",
            field_name="Parent Company Name",
            formData=req,
            scrapedChunks=[],
            previousFields={},
            os_client=MagicMock(),
            logger=logger,
            user_id="user-1",
        )

    assert isinstance(result, FieldFillResponse)
    logger.info(result)
    assert result.field_id == "A.11"
    assert result.field_text == "N/A"
    assert result.totally_unanswered is False
    assert result.unanswered_questions == []


@pytest.mark.asyncio
async def test_a11_returns_na_when_parent_company_missing():
    """
    If offerorParentCompanyName is not provided, the generator should return 'N/A'
    for A.11 forwarded value branch.
    """
    req = make_min_request()  # no offerorParentCompanyName provided
    logger = logging.getLogger("test")

    with patch("app.utils.generate.get_field_standards") as mock_standards:
        mock_standards.return_value = {
            "field_content": "offerorParentCompanyName",
            "form_and_standards": "Forwarded value ifLEI",
        }

        result = await generate_field_fill(
            field_id="A.11",
            field_name="Parent Company Name",
            formData=req,
            scrapedChunks=[],
            previousFields={},
            os_client=MagicMock(),
            logger=logger,
            user_id="user-1",
        )

    assert isinstance(result, FieldFillResponse)
    logger.info(result)
    assert result.field_id == "A.11"
    # Expect 'N/A' when LEI is present regardless of missing parent company
    assert result.field_text == "N/A"
    assert result.totally_unanswered is False
    assert result.unanswered_questions == []
