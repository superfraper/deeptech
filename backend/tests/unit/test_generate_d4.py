import logging
from unittest.mock import MagicMock, patch

import pytest

from app.models.requests import GenerateRequest
from app.models.responses import FieldFillResponse, GenerateSpecificAnswer
from app.utils.generate import generate_field_fill
from app.utils.prompt_loader import prompt_loader


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
        "cryptoProjectName": "AcmeChain",
    }
    base.update(overrides)
    return GenerateRequest(**base)


@pytest.mark.asyncio
async def test_d4_generates_free_text_from_answers():
    """
    D.4 is free alphanumerical text. When we have user/rag answers, we should
    build a description via LLM and return it without special post-processing.
    """
    req = make_min_request(cryptoProjectName="AcmeChain")

    with (
        patch("app.utils.generate.get_field_standards") as mock_standards,
        patch("app.utils.generate.answer_field_questions") as mock_answer_q,
        patch("app.utils.generate.client.chat.completions.create") as mock_openai,
    ):
        mock_standards.return_value = {
            "field_content": "A brief description of the crypto-asset project.",
            "form_and_standards": "Free alphanumerical text",
        }

        answers = [
            GenerateSpecificAnswer(
                field="D.4",
                question="What is the crypto asset project name?",
                answer="Project name is AcmeChain.",
                confident=True,
                type="user",
            ),
            GenerateSpecificAnswer(
                field="D.4",
                question="What is the purpose and core functionality of the crypto-asset project or network?",
                answer="It is a layer-1 network optimized for payments.",
                confident=True,
                type="rag",
            ),
        ]
        mock_answer_q.return_value = (answers, [], [])

        expected_llm = "AcmeChain is a layer-1 network optimized for payments, providing a concise project description."

        class _Msg:
            content = expected_llm

        class _Choice:
            message = _Msg()

        mock_openai.return_value = MagicMock(choices=[_Choice()])

        result = await generate_field_fill(
            field_id="D.4",
            field_name="Brief description of the crypto-asset project",
            formData=req,
            scrapedChunks=[],
            previousFields={},
            os_client=MagicMock(),
            logger=logging.getLogger("test"),
            user_id="user-1",
        )

    assert isinstance(result, FieldFillResponse)
    assert result.field_id == "D.4"
    assert result.field_text == expected_llm
    assert result.totally_unanswered is False
    assert result.unanswered_questions == []


@pytest.mark.asyncio
async def test_d4_returns_unanswered_when_no_answers():
    """
    If we cannot produce any specific answers for D.4 questions, the function should
    return the generic unanswered message and mark totally_unanswered=True.
    """
    req = make_min_request()

    with (
        patch("app.utils.generate.get_field_standards") as mock_standards,
        patch("app.utils.generate.answer_field_questions") as mock_answer_q,
    ):
        mock_standards.return_value = {
            "field_content": "A brief description of the crypto-asset project.",
            "form_and_standards": "Free alphanumerical text",
        }

        # Simulate no confident answers and one unanswered to propagate its question
        unanswered = [
            GenerateSpecificAnswer(
                field="D.4",
                question="What is the crypto asset project name?",
                answer="",
                confident=False,
                type="user",
            )
        ]
        mock_answer_q.return_value = ([], unanswered, [])

        result = await generate_field_fill(
            field_id="D.4",
            field_name="Brief description of the crypto-asset project",
            formData=req,
            scrapedChunks=[],
            previousFields={},
            os_client=MagicMock(),
            logger=logging.getLogger("test"),
            user_id="user-1",
        )

    assert isinstance(result, FieldFillResponse)
    assert result.field_id == "D.4"
    assert result.field_text == prompt_loader.get_unanswered_questions_message()
    assert result.totally_unanswered is True
    assert "What is the crypto asset project name?" in result.unanswered_questions
