import logging
from unittest.mock import MagicMock, patch

import pytest

from app.models.requests import GenerateRequest
from app.models.responses import FieldFillResponse, GenerateSpecificAnswer
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
        # Common optional fields referenced by E. section
        "cryptoAssetSituation": None,
        "minTargetSubscription": None,
        "documents": [],
    }
    base.update(overrides)
    return GenerateRequest(**base)


@pytest.mark.asyncio
@pytest.mark.parametrize("situation", ["compliance", "admission"])
async def test_e4_returns_na_when_compliance_or_admission(situation):
    """
    E.4 should short-circuit to 'N/A' when cryptoAssetSituation is 'compliance' or 'admission'.
    This avoids any OpenAI or DB interactions for generation.
    """
    req = make_min_request(cryptoAssetSituation=situation)

    with (
        patch("app.utils.generate.get_field_standards") as mock_standards,
        patch("app.utils.generate.client.chat.completions.create") as mock_openai,
    ):
        mock_standards.return_value = {
            "field_content": (
                "if cryptoAssetSituation = compliance/admission, this is N/A, else: Where applicable, minimum subscription goals set for the offer..."
            ),
            "form_and_standards": "Amount in monetary value (DECIMAL-18/3) or Numerical (INTEGER-n)",
        }

        result = await generate_field_fill(
            field_id="E.4",
            field_name="Minimum subscription goals",
            formData=req,
            scrapedChunks=[],
            previousFields={},
            os_client=MagicMock(),
            logger=logging.getLogger("test"),
            user_id="user-1",
        )

    assert isinstance(result, FieldFillResponse)
    assert result.field_id == "E.4"
    assert result.field_text == "N/A"
    # Ensure we didn't hit OpenAI for these short-circuit cases
    assert not mock_openai.called
    assert result.totally_unanswered is False
    assert result.unanswered_questions == []


@pytest.mark.asyncio
async def test_e4_uses_user_min_target_to_generate_when_offer():
    """
    When cryptoAssetSituation is an offer (not compliance/admission), and minTargetSubscription is provided by user,
    the generator should build a prompt and return the numeric/decimal value (mock LLM echo here).
    """
    req = make_min_request(cryptoAssetSituation="offer_to_public", minTargetSubscription="100000")

    with (
        patch("app.utils.generate.get_field_standards") as mock_standards,
        patch("app.utils.generate.answer_field_questions") as mock_answer_q,
        patch("app.utils.generate.client.chat.completions.create") as mock_openai,
    ):
        mock_standards.return_value = {
            "field_content": (
                "Where applicable, minimum subscription goals set for the offer to the public "
                "of the crypto-assets in an official currency or any other crypto-assets."
            ),
            "form_and_standards": "Amount in monetary value (DECIMAL-18/3) or Numerical (INTEGER-n)",
        }

        answers = [
            GenerateSpecificAnswer(
                field="E.4",
                question="what is the minTargetSubscription?",
                answer="100000",
                confident=True,
                type="user",
            ),
            GenerateSpecificAnswer(
                field="E.4",
                question="What is the crypto asset situation?",
                answer="offer_to_public",
                confident=True,
                type="user",
            ),
        ]
        mock_answer_q.return_value = (answers, [], [])

        class _Msg:
            content = "100000"

        class _Choice:
            message = _Msg()

        mock_openai.return_value = MagicMock(choices=[_Choice()])

        result = await generate_field_fill(
            field_id="E.4",
            field_name="Minimum subscription goals",
            formData=req,
            scrapedChunks=[],
            previousFields={},
            os_client=MagicMock(),
            logger=logging.getLogger("test"),
            user_id="user-1",
        )

    assert isinstance(result, FieldFillResponse)
    assert result.field_id == "E.4"
    assert result.field_text == "100000"
    assert result.totally_unanswered is False
    assert result.unanswered_questions == []
