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
        # Common optional fields that may be referenced
        "issuePrice": "$0.30",
        "cryptoAssetSituation": None,
        "documents": [],
    }
    base.update(overrides)
    return GenerateRequest(**base)


@pytest.mark.asyncio
async def test_e9_returns_na_when_compliance():
    """
    E.9 should short-circuit to 'N/A' when cryptoAssetSituation is 'compliance'
    (or 'admission'). This avoids any OpenAI or DB interactions.
    """
    req = make_min_request(cryptoAssetSituation="compliance")

    # Patch get_field_standards to avoid hitting the real DB
    with patch("app.utils.generate.get_field_standards") as mock_standards:
        mock_standards.return_value = {
            "field_content": "The official currency or DTI",
            "form_and_standards": "CURRENCYCODE_3 or DTI",
        }

        result = await generate_field_fill(
            field_id="E.9",
            field_name="Official currency or any other crypto-assets determining the issue price",
            formData=req,
            scrapedChunks=[],
            previousFields={},
            os_client=MagicMock(),
            logger=logging.getLogger("test"),
            user_id="user-1",
        )

    assert isinstance(result, FieldFillResponse)
    assert result.field_id == "E.9"
    assert result.field_text == "N/A"


@pytest.mark.asyncio
async def test_e9_extracts_currency_when_model_echoes_placeholder():
    """
    When the model erroneously returns 'CURRENCYCODE_3', the post-processor should
    extract a concrete ISO 4217 code from form data or context. Given issuePrice='$0.30',
    the expected result is 'USD'.
    """
    req = make_min_request(cryptoAssetSituation="offer_to_public", issuePrice="$0.30")

    # Patch standards to include placeholders so augmentation triggers
    with (
        patch("app.utils.generate.get_field_standards") as mock_standards,
        patch("app.utils.generate.answer_field_questions") as mock_answer_q,
        patch("app.utils.generate.client.chat.completions.create") as mock_openai,
    ):
        mock_standards.return_value = {
            "field_content": "The official currency or DTI used to determine the issue price.",
            "form_and_standards": "CURRENCYCODE_3 or DTI",
        }

        # Provide a confident RAG/user answer to avoid 'unanswered' branch
        mock_answer_q.return_value = (
            [
                GenerateSpecificAnswer(
                    field="E.9",
                    question="Currency or DTI?",
                    answer="Issue price context mentions $0.30",
                    confident=True,
                    type="rag",
                )
            ],
            [],
            [],
        )

        # Mock OpenAI to echo the placeholder, which our post-processor should fix
        class _Msg:
            content = "CURRENCYCODE_3"

        class _Choice:
            message = _Msg()

        mock_openai.return_value = MagicMock(choices=[_Choice()])

        result = await generate_field_fill(
            field_id="E.9",
            field_name="Official currency or any other crypto-assets determining the issue price",
            formData=req,
            scrapedChunks=[],
            previousFields={},
            os_client=MagicMock(),
            logger=logging.getLogger("test"),
            user_id="user-1",
        )

    assert isinstance(result, FieldFillResponse)
    assert result.field_id == "E.9"
    # Expect USD inferred from '$' in issuePrice
    assert result.field_text == "USD"
