import json
import logging
import re
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI
from opensearchpy import OpenSearch

from app.models import (
    FieldFillResponse,
    FieldQuestionsFormat,
    GenerateSpecificAnswer,
    RegenerateRequest,
    TabularFormatResponse,
)
from app.utils.async_utils import run_in_thread
from app.utils.prompt_loader import prompt_loader
from app.utils.retrieve import get_field_questions, get_field_standards
from app.utils.search import hybrid_search, search_chunks

load_dotenv()

client = OpenAI()


def extract_iso_currency(text: str) -> str | None:
    """
    Try to extract a concrete ISO 4217 3-letter currency code from text.
    Falls back to common currency symbols mapping.
    """
    try:
        if not text:
            return None
        # Quick symbol mapping
        symbol_map = {
            "$": "USD",
            "€": "EUR",
            "£": "GBP",
            "zł": "PLN",
            "₣": "CHF",
            "¥": "JPY",  # could also be CNY; prefer explicit codes in text
            "₿": None,  # bitcoin symbol, not ISO 4217
        }
        for sym, code in symbol_map.items():
            if sym in text and code:
                return code

        # Explicit ISO 4217 detection against a curated set
        iso_set = {
            "EUR",
            "USD",
            "GBP",
            "PLN",
            "CHF",
            "JPY",
            "CNY",
            "CAD",
            "AUD",
            "SEK",
            "NOK",
            "DKK",
            "CZK",
            "HUF",
            "RON",
            "TRY",
            "INR",
            "BRL",
            "ZAR",
            "HKD",
            "SGD",
            "NZD",
            "MXN",
            "ILS",
            "AED",
            "SAR",
            "KRW",
            "TWD",
            "THB",
            "PHP",
            "IDR",
            "MYR",
            "ARS",
            "CLP",
            "COP",
            "PEN",
        }
        for m in re.findall(r"\b[A-Z]{3}\b", str(text)):
            if m in iso_set:
                return m
    except Exception:
        pass
    return None


async def regenerate_field_fill(request: RegenerateRequest) -> FieldFillResponse:
    """
    Regenerates the fill-out answer for a given field based on the provided request data.
    """
    logger = logging.getLogger("regenerate_field_fill")
    logger.info("Regenerating field fill for field_id: %s", request.field_id)
    logger.debug("Starting regenerate_field_fill with request: %s", request)

    # Extract token classification from request, default to "OTH" if not provided
    token_classification = "OTH"
    if hasattr(request, "token_classification") and request.token_classification:
        token_classification = request.token_classification.split("_")[0]

    # Get proper field name from database instead of using request.field_name
    from app.core.db_handler import DatabaseHandler

    db_handler = DatabaseHandler(token_classification=token_classification)
    fields = db_handler.get_fields_info()
    field_lookup = {field[0]: field[1] for field in fields}
    proper_field_name = field_lookup.get(request.field_id, request.field_name)

    # Special handling for A.12, B.10, C.10 fields
    if request.field_id in ("A.12", "B.10", "C.10"):
        combined_answers = " ".join(request.answers)
        logger.info("Combined answers for %s: %s", request.field_id, combined_answers)

        prompt = prompt_loader.get_regenerate_prompt(
            "tabular_format",
            context=combined_answers,
            field_name=request.field_name,
            previous_answer=request.field_text,
        )

        logger.debug("Constructed prompt for field '%s': %s", request.field_name, prompt)

        try:
            response = await run_in_thread(
                client.beta.chat.completions.parse,
                model="gpt-5-mini",
                messages=[
                    {
                        "role": "system",
                        "content": prompt_loader.get_system_message("tabular_extraction"),
                    },
                    {"role": "user", "content": prompt},
                ],
                response_format=TabularFormatResponse,
            )
            answer = json.dumps(
                [
                    {
                        "Identity": member.Identity,
                        "Business Address": member.Business_Address,
                        "Functions": member.Functions,
                    }
                    for member in response.choices[0].message.parsed.members
                ],
                indent=2,
            )

            logger.info(
                "Regenerated tabular format for field '%s' len(%d): %s",
                proper_field_name,
                len(answer),
                answer[:200],
            )
            return FieldFillResponse(
                field_id=request.field_id,
                field_name=proper_field_name,
                field_text=answer,
                unanswered_questions=[],
                totally_unanswered=False,
            )
        except Exception as e:
            logger.error("Error regenerating tabular format for %s: %s", request.field_id, e)
            try:
                logger.info("Attempting manual parsing for %s", request.field_id)
                members = []
                lines = combined_answers.replace("\r\n", "\n").replace("\r", "\n").split("\n")
                lines = [line.strip() for line in lines if line.strip()]

                current_member = {
                    "Identity": "",
                    "Business Address": "",
                    "Functions": "",
                }

                for line in lines:
                    line = line.strip()
                    logger.info("Processing line: '%s'", line)
                    if "," in line and not line.startswith(("Identity:", "Business Address:", "Function:")):
                        parts = [part.strip() for part in line.split(",")]
                        if len(parts) >= 3:
                            current_member = {
                                "Identity": parts[0],
                                "Business Address": parts[1],
                                "Functions": parts[2],
                            }
                            members.append(current_member)
                            current_member = {
                                "Identity": "",
                                "Business Address": "",
                                "Functions": "",
                            }
                            continue
                        elif len(parts) == 2:
                            if any(
                                role_word in parts[1].lower()
                                for role_word in [
                                    "ceo",
                                    "cto",
                                    "cfo",
                                    "director",
                                    "manager",
                                    "officer",
                                    "president",
                                ]
                            ):
                                current_member = {
                                    "Identity": parts[0],
                                    "Business Address": "",
                                    "Functions": parts[1],
                                }
                            else:
                                current_member = {
                                    "Identity": parts[0],
                                    "Business Address": parts[1],
                                    "Functions": "",
                                }
                            members.append(current_member)
                            current_member = {
                                "Identity": "",
                                "Business Address": "",
                                "Functions": "",
                            }
                            continue

                    if line.startswith("Identity:"):
                        if current_member["Identity"]:
                            members.append(current_member.copy())
                            current_member = {
                                "Identity": "",
                                "Business Address": "",
                                "Functions": "",
                            }
                        current_member["Identity"] = line.replace("Identity:", "").strip()
                    elif line.startswith("Business Address:"):
                        current_member["Business Address"] = line.replace("Business Address:", "").strip()
                    elif line.startswith("Function:") or line.startswith("Functions:"):
                        current_member["Functions"] = line.replace("Function:", "").replace("Functions:", "").strip()
                    elif ":" in line:
                        parts = line.split(":", 1)
                        if len(parts) == 2:
                            field_name_lower = parts[0].strip().lower()
                            field_value = parts[1].strip()
                            if "name" in field_name_lower or "identity" in field_name_lower:
                                current_member["Identity"] = field_value
                            elif "address" in field_name_lower or "location" in field_name_lower:
                                current_member["Business Address"] = field_value
                            elif "function" in field_name_lower or "role" in field_name_lower or "position" in field_name_lower:
                                current_member["Functions"] = field_value
                    else:
                        if not current_member["Identity"] and line:
                            current_member["Identity"] = line

                if current_member["Identity"] or current_member["Business Address"] or current_member["Functions"]:
                    members.append(current_member)

                if not members and combined_answers.strip():
                    logger.info("No structured data found, attempting to parse as single comma-separated entry")
                    text = combined_answers.strip()
                    if "," in text:
                        parts = [part.strip() for part in text.split(",")]
                        if len(parts) >= 3:
                            members = [
                                {
                                    "Identity": parts[0],
                                    "Business Address": parts[1],
                                    "Functions": ", ".join(parts[2:]),
                                }
                            ]
                        elif len(parts) == 2:
                            members = [
                                {
                                    "Identity": parts[0],
                                    "Business Address": parts[1],
                                    "Functions": "",
                                }
                            ]
                        else:
                            members = [
                                {
                                    "Identity": parts[0],
                                    "Business Address": "",
                                    "Functions": "",
                                }
                            ]
                    else:
                        # Single piece of information - put in Identity
                        members = [
                            {
                                "Identity": text[:100],  # Limit length
                                "Business Address": "",
                                "Functions": "",
                            }
                        ]

                if not members:
                    # If still no data, create empty structure
                    members = [{"Identity": "", "Business Address": "", "Functions": ""}]

                answer = json.dumps(members, indent=2)
                logger.info(
                    "Manually parsed %s field with %d members: %s",
                    request.field_id,
                    len(members),
                    answer,
                )

                return FieldFillResponse(
                    field_id=request.field_id,
                    field_name=proper_field_name,
                    field_text=answer,
                    unanswered_questions=[],
                    totally_unanswered=False,
                )
            except Exception as parse_error:
                logger.error("Error manually parsing %s: %s", request.field_id, parse_error)
                # Final fallback - return empty structure
                fallback_answer = json.dumps(
                    [{"Identity": "", "Business Address": "", "Functions": ""}],
                    indent=2,
                )
                return FieldFillResponse(
                    field_id=request.field_id,
                    field_name=proper_field_name,
                    field_text=fallback_answer,
                    unanswered_questions=[],
                    totally_unanswered=False,
                )

    # Get the field standards
    standards = get_field_standards(request.field_id, logger, token_classification)

    # Generate the prompt
    prompt = prompt_loader.get_regenerate_prompt(
        "standard",
        context=" ".join(request.answers),
        field_name=request.field_name,
        field_content=standards["field_content"],
        form_and_standards=standards["form_and_standards"],
        previous_answer=request.field_text,
    )

    logger.info("Prompt for field '%s' len(%d): %s", request.field_name, len(prompt), prompt)

    if standards.get("form_and_standards") == "Free alphanumerical text presented in a tabular format":
        logger.info("Regenerating tabular format for field '%s'", proper_field_name)
        try:
            response = await run_in_thread(
                client.beta.chat.completions.parse,
                model="gpt-5-mini",
                messages=[
                    {
                        "role": "system",
                        "content": prompt_loader.get_system_message("tabular_format"),
                    },
                    {"role": "user", "content": prompt},
                ],
                response_format=TabularFormatResponse,
            )
            answer = json.dumps(
                [
                    {
                        "Identity": member.Identity,
                        "Business Address": member.Business_Address,
                        "Functions": member.Functions,
                    }
                    for member in response.choices[0].message.parsed.members
                ],
                indent=2,
            )

            logger.info(
                "Regenerated tabular format for field '%s' len(%d): %s",
                proper_field_name,
                len(answer),
                answer[:60],
            )
            return FieldFillResponse(
                field_id=request.field_id,
                field_name=proper_field_name,
                field_text=answer,
                unanswered_questions=[],
                totally_unanswered=False,
            )
        except Exception as e:
            logger.error("Error regenerating tabular format for %s: %s", proper_field_name, e)
            return FieldFillResponse(
                field_id=request.field_id,
                field_name=proper_field_name,
                field_text=prompt_loader.get_error_message(
                    "regenerate_tabular_format",
                    field_name=proper_field_name,
                    error=str(e),
                ),
                unanswered_questions=[],
                totally_unanswered=False,
            )

    try:
        response = await run_in_thread(
            client.chat.completions.create,
            model="gpt-5-mini",
            messages=[
                {
                    "role": "system",
                    "content": prompt_loader.get_system_message("default"),
                },
                {"role": "user", "content": prompt},
            ],
        )
        logger.info(f"Response for field '{request.field_name}': {response}")
        answer = response.choices[0].message.content.strip()
        logger.info(
            "Regenerated fill for field '%s' len(%d): %s",
            proper_field_name,
            len(answer),
            answer[:60],
        )
        logger.debug("Received raw answer from GPT model: %s", answer)
        return FieldFillResponse(
            field_id=request.field_id,
            field_name=proper_field_name,
            field_text=answer,
            unanswered_questions=[],
            totally_unanswered=False,
        )
    except Exception as e:
        logger.error("Error regenerating fill for %s: %s", proper_field_name, e)
        return FieldFillResponse(
            field_id=request.field_id,
            field_name=proper_field_name,
            field_text=prompt_loader.get_error_message("regenerate_fill", field_name=proper_field_name, error=str(e)),
            unanswered_questions=[],
            totally_unanswered=False,
        )


async def generate_field_fill(
    field_id: str,
    field_name: str,
    formData: Any,
    scrapedChunks: list[dict],
    previousFields: dict[str, str],
    os_client: OpenSearch,
    logger: logging.Logger,
    user_id,
    all_missing_fields: list[str] | None = None,
) -> FieldFillResponse:
    """
    Generates a fill-out answer for a given field based on the scraped context,
    while ensuring the answer meets the expected format and standards.
    """
    logger.debug(
        "Starting generate_field_fill for field_id '%s', field_name '%s'",
        field_id,
        field_name,
    )
    if all_missing_fields is None:
        all_missing_fields = []

    logger.info("Generating fill-out for field_id: %s (field name: %s)", field_id, field_name)
    try:
        form_dict = formData if isinstance(formData, dict) else formData.dict()
    except Exception:
        form_dict = {}

    # Business rule based on diagram logic for Parts A, B, C
    person_type = form_dict.get("personType")  # Who submits: Offeror, Person, or Operator
    issuer_type = form_dict.get("issuerType")  # Same or Different from offeror
    operator_type = form_dict.get("operatorType")  # SameAsOfferor, SameAsIssuer, Different, N/A

    # Determine token classification context
    token_classification = form_dict.get("tokenClassification", "OTH")
    token_class_upper = str(token_classification).upper() if token_classification else "OTH"

    # For OTH tokens:

    if token_class_upper.startswith("OTH"):
        logger.info(f"OTH logic for field {field_id}: issuer_type={issuer_type}, operator_type={operator_type}, token_class={token_class_upper}")

        # Special handling for B.1 - this is a TRUE/FALSE field indicating if issuer is different from offeror
        if field_id == "B.1":
            issuer_same_as_offeror = issuer_type in ["Same", "SameAsOfferor", "SAME_AS_OFFEROR"]
            b1_value = "FALSE" if issuer_same_as_offeror else "TRUE"
            logger.info(f"Setting B.1 to {b1_value} based on issuerType={issuer_type}")
            return FieldFillResponse(
                field_id=field_id,
                field_name=field_name,
                field_text=b1_value,
                unanswered_questions=[],
                totally_unanswered=False,
            )

        # Part B logic for OTH: Information about Issuer
        # Business rule based on issuerType:
        # - If "same as offeror or person seeking admission to trading" (YES) -> Part B is NOT filled (N/A)
        # - If "different entity" (No) -> Part B is filled with issuer details
        if field_id.startswith("B."):
            # Check if issuer is same as offeror/person seeking admission
            # This could be indicated by issuerType values like "Same", "SameAsOfferor"
            issuer_same_as_offeror = issuer_type in ["Same", "SameAsOfferor", "SAME_AS_OFFEROR"]

            if issuer_same_as_offeror:
                logger.info(
                    "Issuer is same as offeror; short-circuiting Part B field %s to 'N/A' per business rule.",
                    field_id,
                )
                return FieldFillResponse(
                    field_id=field_id,
                    field_name=field_name,
                    field_text="N/A",
                    unanswered_questions=[],
                    totally_unanswered=False,
                )

        # Part A and C logic for OTH based on operatorType:
        # If operator is different from offeror:
        #   - Part A is filled with offeror details
        #   - Part C is Not Applicable (N/A)
        # If operator is same as offeror:
        #   - Part C is filled with offeror details
        #   - Part A is Not Applicable (N/A)
        # If operator is same as issuer (and issuer is different from offeror):
        #   - Part C is filled with offeror details
        #   - Part B is not filled (N/A)
        #   - Part A is Not Applicable (N/A)

        if field_id.startswith("A."):
            # Part A should be N/A if operator is same as offeror OR if operator is same as issuer
            operator_same_as_offeror = operator_type in ["SameAsOfferor", "SAME_AS_OFFEROR", "Same"]
            operator_same_as_issuer = operator_type in ["SameAsIssuer", "SAME_AS_ISSUER"]

            if operator_same_as_offeror or operator_same_as_issuer:
                logger.info(
                    "Operator is same as offeror or issuer; short-circuiting Part A field %s to 'N/A' per business rule.",
                    field_id,
                )
                return FieldFillResponse(
                    field_id=field_id,
                    field_name=field_name,
                    field_text="N/A",
                    unanswered_questions=[],
                    totally_unanswered=False,
                )

        if field_id.startswith("C."):
            # Part C should be N/A if operator is different from offeror
            # Part C should be filled if operator is same as offeror or same as issuer
            operator_different = operator_type in ["Different", "DIFFERENT", "N/A", "NA"]

            if operator_different:
                logger.info(
                    "Operator is different from offeror; short-circuiting Part C field %s to 'N/A' per business rule.",
                    field_id,
                )
                return FieldFillResponse(
                    field_id=field_id,
                    field_name=field_name,
                    field_text="N/A",
                    unanswered_questions=[],
                    totally_unanswered=False,
                )

        # Additional check: If operator is same as issuer, Part B should also be N/A
        if field_id.startswith("B."):
            operator_same_as_issuer = operator_type in ["SameAsIssuer", "SAME_AS_ISSUER"]
            if operator_same_as_issuer:
                logger.info(
                    "Operator is same as issuer; short-circuiting Part B field %s to 'N/A' per business rule.",
                    field_id,
                )
                return FieldFillResponse(
                    field_id=field_id,
                    field_name=field_name,
                    field_text="N/A",
                    unanswered_questions=[],
                    totally_unanswered=False,
                )

    elif token_class_upper in ["ART", "EMT"]:
        # Part A logic for ART/EMT: Information about Issuer
        # Part A should always be filled with issuer data

        # Part AA logic for ART: Other persons (if different from issuer)
        # Check if there are other persons offering/seeking admission different from issuer
        # This would be based on personType and issuer_type relationship
        if field_id.startswith("AA.") and person_type in ["Offeror", "Person"] and issuer_type == "Same":
            # If person submitting is the issuer itself, Part AA is N/A
            logger.info(
                "No other persons offering; short-circuiting Part AA field %s to 'N/A' per business rule.",
                field_id,
            )
            return FieldFillResponse(
                field_id=field_id,
                field_name=field_name,
                field_text="N/A",
                unanswered_questions=[],
                totally_unanswered=False,
            )

    # Deterministic rule based on A.6 (offerorLeiNumber)
    lei_present = bool(str(form_dict.get("offerorLeiNumber", "") or "").strip())
    if lei_present and field_id in {"A.2", "A.3", "A.4", "A.7", "A.11"}:
        logger.info(
            "offerorLeiNumber present; short-circuiting %s to 'N/A' per business rule.",
            field_id,
        )
        return FieldFillResponse(
            field_id=field_id,
            field_name=field_name,
            field_text="N/A",
            unanswered_questions=[],
            totally_unanswered=False,
        )
    token_classification = str(form_dict.get("tokenClassification", "OTH")).split("_")[0]
    standards = get_field_standards(field_id, logger, token_classification)

    # Business rule guard for E.9: if cryptoAssetSituation is compliance/admission => N/A
    if field_id == "E.9":
        try:
            cas = (str(form_dict.get("cryptoAssetSituation") or "")).strip().lower()
        except Exception:
            cas = ""
        if cas in {"compliance", "admission"}:
            return FieldFillResponse(
                field_id=field_id,
                field_name=field_name,
                field_text="N/A",
                unanswered_questions=[],
                totally_unanswered=False,
            )

    # Business rule guard for E.4: if cryptoAssetSituation is compliance/admission => N/A
    if field_id == "E.4":
        try:
            cas = (str(form_dict.get("cryptoAssetSituation") or "")).strip().lower()
        except Exception:
            cas = ""
        if cas in {"compliance", "admission"}:
            return FieldFillResponse(
                field_id=field_id,
                field_name=field_name,
                field_text="N/A",
                unanswered_questions=[],
                totally_unanswered=False,
            )

    # Business rule guard for E.1: deterministic mapping from cryptoAssetSituation to ATTR/OTPC/BOTH
    if field_id == "E.1":
        try:
            cas = (str(form_dict.get("cryptoAssetSituation") or "")).strip().lower()
        except Exception:
            cas = ""
        if cas == "compliance":
            return FieldFillResponse(
                field_id=field_id,
                field_name=field_name,
                field_text="N/A",
                unanswered_questions=[],
                totally_unanswered=False,
            )
        # Map to ATTR for admission to trading
        admission_values = {"admission", "attr", "admission_to_trading"}
        # Map to OTPC for offer to the public
        offer_values = {"offer", "offer_to_public", "otpc", "public_offer"}
        # Map to BOTH for both scenarios
        both_values = {"both", "offer_and_admission"}

        if cas in admission_values:
            return FieldFillResponse(
                field_id=field_id,
                field_name=field_name,
                field_text="ATTR",
                unanswered_questions=[],
                totally_unanswered=False,
            )
        elif cas in offer_values:
            return FieldFillResponse(
                field_id=field_id,
                field_name=field_name,
                field_text="OTPC",
                unanswered_questions=[],
                totally_unanswered=False,
            )
        elif cas in both_values:
            return FieldFillResponse(
                field_id=field_id,
                field_name=field_name,
                field_text="BOTH",
                unanswered_questions=[],
                totally_unanswered=False,
            )
        else:
            # Default to N/A if cryptoAssetSituation is not recognized
            return FieldFillResponse(
                field_id=field_id,
                field_name=field_name,
                field_text="N/A",
                unanswered_questions=[],
                totally_unanswered=False,
            )

    # Business rule guard for G.9: deterministic tri-state mapping from cryptoAssetSituation
    if field_id == "G.9":
        try:
            cas = (str(form_dict.get("cryptoAssetSituation") or "")).strip().lower()
        except Exception:
            cas = ""
        truthy = {"admission", "attr", "admission_to_trading", "both", "offer_and_admission"}
        falsy = {"offer", "offer_to_public", "otpc", "public_offer"}
        if cas in truthy:
            return FieldFillResponse(
                field_id=field_id,
                field_name=field_name,
                field_text="TRUE",
                unanswered_questions=[],
                totally_unanswered=False,
            )
        elif cas in falsy:
            return FieldFillResponse(
                field_id=field_id,
                field_name=field_name,
                field_text="FALSE",
                unanswered_questions=[],
                totally_unanswered=False,
            )
        else:
            return FieldFillResponse(
                field_id=field_id,
                field_name=field_name,
                field_text="N/A",
                unanswered_questions=[],
                totally_unanswered=False,
            )

    # Prepare form_and_standards with guard against placeholder echo
    form_std = standards.get("form_and_standards", "")
    if field_id == "E.9" and isinstance(form_std, str) and "CURRENCYCODE_3" in form_std and "DTI" in form_std:
        form_std = (
            form_std + "\nOutput a concrete ISO 4217 3-letter currency code (e.g., EUR, USD) "
            "or a valid ISO 24165 DTI string. Never output the placeholder labels "
            "'CURRENCYCODE_3' or 'DTI'. If no concrete value is found in context, return 'N/A'."
        )

    if standards.get("form_and_standards") == "Forwarded value":
        field_content = standards.get("field_content", "NO VALUE FOUND")
        logger.info(f"Field {field_name} is a simple forward, returning {field_content} value directly.")
        try:
            form_dict = formData if isinstance(formData, dict) else formData.dict()
        except Exception:
            form_dict = {}
        field_value = form_dict.get(standards.get("field_content"), "N/A")
        return FieldFillResponse(
            field_id=field_id,
            field_name=field_name,
            field_text=field_value,
            unanswered_questions=[],
            totally_unanswered=False,
        )
    elif standards.get("form_and_standards") == "Forwarded value ifLEI":
        field_content = standards.get("field_content", "NO VALUE FOUND")
        logger.info(f"Field {field_name} is a conditional forward, returning {field_content} value directly if offerorLeiNumber is present.")
        field_content_key = standards.get("field_content", "")
        try:
            form_dict = formData if isinstance(formData, dict) else formData.dict()
        except Exception:
            form_dict = {}
        has_lei = bool(str(form_dict.get("offerorLeiNumber", "") or "").strip())
        raw_value = form_dict.get(field_content_key)
        field_value = raw_value if (has_lei and raw_value not in (None, "")) else "N/A"

        logger.info(
            "Field %s (%s) conditional forward result: %r (offerorLeiNumber present: %s)",
            field_id,
            field_name,
            field_value,
            has_lei,
        )
        return FieldFillResponse(
            field_id=field_id,
            field_name=field_name,
            field_text=field_value,
            unanswered_questions=[],
            totally_unanswered=False,
        )
    elif standards.get("form_and_standards") == "Predefined alphanumerical text":
        field_content = standards.get("field_content", "NO VALUE FOUND")
        logger.info(f"Field {field_name} is a predefined alphanumerical text, returning {field_content} value directly.")
        return FieldFillResponse(
            field_id=field_id,
            field_name=field_name,
            field_text=standards.get("field_content", ""),
            unanswered_questions=[],
            totally_unanswered=False,
        )

    field_questions_answers, unanswered_questions, ids = await answer_field_questions(
        field_id,
        formData,
        scrapedChunks,
        previousFields,
        os_client,
        logger,
        user_id,
        all_missing_fields,
    )

    if not field_questions_answers:
        logger.info("No answer found for specific question for field_id: %s", field_id)
        return FieldFillResponse(
            field_id=field_id,
            field_name=field_name,
            field_text=prompt_loader.get_unanswered_questions_message(),
            unanswered_questions=[i.question for i in unanswered_questions],
            totally_unanswered=True,
        )

    if field_questions_answers[0].type == "hardcoded" and len(field_questions_answers) == 1:
        logger.info("Field '%s' is hardcoded, returning hardcoded answer.", field_name)
        return FieldFillResponse(
            field_id=field_id,
            field_name=field_name,
            field_text=field_questions_answers[0].answer,
            unanswered_questions=[i.question for i in unanswered_questions],
            totally_unanswered=False,
        )

    # Short-circuit when we have a single confident user-mapped answer and no unanswered items.
    # This avoids unnecessary LLM calls when the value is directly provided via form data.
    # However, skip short-circuit for conditional fields that need LLM evaluation.
    if (
        len(field_questions_answers) == 1
        and field_questions_answers[0].type == "user"
        and field_questions_answers[0].confident
        and len(unanswered_questions) == 0
        and standards.get("form_and_standards")
        not in [
            "Free alphanumerical text presented in a tabular format",
            "Conditional alphanumerical text",
        ]
    ):
        direct_value = field_questions_answers[0].answer
        logger.info(
            "Direct user-mapped answer for field '%s' found: %r. Skipping LLM generation.",
            field_name,
            direct_value,
        )
        return FieldFillResponse(
            field_id=field_id,
            field_name=field_name,
            field_text=direct_value,
            unanswered_questions=[],
            totally_unanswered=False,
        )

    answers_text = " ".join([f"{obj.question}: {obj.answer}" for obj in field_questions_answers])
    prompt = prompt_loader.get_generate_prompt(
        "general",
        context=answers_text,
        field_name=field_name,
        field_content=standards.get("field_content", ""),
        form_and_standards=form_std,
    )

    logger.info("Generating fill for field '%s' with field_id '%s'.", field_name, field_id)
    logger.info("Prompt for field '%s' len(%d): %s", field_name, len(prompt), prompt)
    logger.debug("Final prompt generated for field '%s': %s", field_name, prompt)

    if standards.get("form_and_standards") == "Free alphanumerical text presented in a tabular format":
        try:
            response = await run_in_thread(
                client.beta.chat.completions.parse,
                model="gpt-5-mini",
                messages=[
                    {
                        "role": "system",
                        "content": prompt_loader.get_system_message("tabular_format"),
                    },
                    {"role": "user", "content": prompt},
                ],
                response_format=TabularFormatResponse,
            )
            answer = json.dumps(
                [
                    {
                        "Identity": member.Identity,
                        "Business Address": member.Business_Address,
                        "Functions": member.Functions,
                    }
                    for member in response.choices[0].message.parsed.members
                ],
                indent=2,
            )

            logger.info(
                "Generated tabular format for field '%s' len(%d): %s",
                field_name,
                len(answer),
                answer[:60],
            )
            return FieldFillResponse(
                field_id=field_id,
                field_name=field_name,
                field_text=answer,
                unanswered_questions=[i.question for i in unanswered_questions],
                totally_unanswered=False,
                ids=ids if ids else None,
            )
        except Exception as e:
            logger.error("Error generating tabular format for %s: %s", field_name, e)
            return FieldFillResponse(
                field_id=field_id,
                field_name=field_name,
                field_text=prompt_loader.get_error_message("generate_tabular_format", field_name=field_name, error=str(e)),
                unanswered_questions=[i.question for i in unanswered_questions],
                totally_unanswered=False,
                ids=ids if ids else None,
            )
    try:
        response = await run_in_thread(
            client.chat.completions.create,
            model="gpt-5-mini",
            messages=[
                {
                    "role": "system",
                    "content": prompt_loader.get_system_message("default"),
                },
                {"role": "user", "content": prompt},
            ],
        )
        answer = response.choices[0].message.content.strip()
        logger.info(
            "Generated fill for field '%s' len(%d): %s",
            field_name,
            len(answer),
            answer[:60],
        )
        logger.debug("Raw answer from GPT model: %s", answer)

        # Post-process E.9 to prevent placeholder leakage and enforce concrete value or N/A
        if field_id == "E.9":
            cleaned = (answer or "").strip()
            upper = cleaned.upper()
            if upper in {"CURRENCYCODE_3", "DTI"} or "CURRENCYCODE_3" in upper:
                try:
                    form_issue_price = str(form_dict.get("issuePrice", "") or "")
                except Exception:
                    form_issue_price = ""
                candidate = extract_iso_currency(form_issue_price) or extract_iso_currency(answers_text)
                answer = candidate if candidate else "N/A"

        return FieldFillResponse(
            field_id=field_id,
            field_name=field_name,
            field_text=answer,
            unanswered_questions=[i.question for i in unanswered_questions],
            totally_unanswered=False,
            ids=ids if ids else None,
        )
    except Exception as e:
        logger.error("Error generating fill for %s: %s", field_name, e)
        return FieldFillResponse(
            field_id=field_id,
            field_name=field_name,
            field_text=prompt_loader.get_error_message("generate_fill", field_name=field_name, error=str(e)),
            unanswered_questions=[i.question for i in unanswered_questions],
            totally_unanswered=False,
        )


async def answer_field_questions(
    field_id: str,
    formData: Any,
    scrapedChunks: list[dict],
    previousFields: dict[str, str],
    os_client: OpenSearch,
    logger: logging.Logger,
    user_id,
    all_missing_fields: list[str] | None = None,
) -> tuple[list[GenerateSpecificAnswer], list[GenerateSpecificAnswer], list[str]]:
    """
    Gathers all questions related to the field from fields_questions.db, checks
    their type, and answers them by one of the following strategies:
      - OpenSearch and scraped context (RAG)
      - User-provided formData (direct mapping)
      - Previously generated whitepaper fields
      - Hardcoded answers

    The fields_questions.db contains columns: field_id, question, type,
    relevant_fields. Note: relevant_fields is populated only when
    type == 'whitepaper'. Supported types: 'user', 'rag', 'whitepaper',
    and 'hardcoded'.

    Args:
        field_id (str): The ID of the field for which to answer questions.
        logger (logging): The logger object for logging messages.

    Returns:
        Tuple: The answers to questions, unanswered questions, and the list of
        IDs from RAG searches.
    """
    if all_missing_fields is None:
        all_missing_fields = []

    logger.info("Answering questions for field_id: %s", field_id)
    try:
        form_dict = formData if isinstance(formData, dict) else formData.dict()
    except Exception:
        form_dict = {}
    token_classification = str(form_dict.get("tokenClassification", "OTH")).split("_")[0]
    questions: list[FieldQuestionsFormat] = get_field_questions(field_id, logger, token_classification)
    if not questions:
        logger.info("No questions found for field_id: %s", field_id)
        return [], [], []

    answers = []
    unanswered_questions = []
    all_ids = []
    all_answers = []  # Store all answers first to check confidence levels

    # First pass: collect all answers to determine confidence levels
    for question in questions:
        question_type = question.type
        question_text = question.question

        if question_type == "user":
            # Answer based on user's formData
            answer = await answer_user_question(field_id, question, formData, logger)
        elif question_type == "rag":
            # Answer based on OpenSearch and scraped context
            rag_result = await answer_rag_question(
                field_id,
                question_text,
                scrapedChunks,
                os_client,
                logger,
                user_id,
                formData,
            )
            answer = rag_result["answer"]
            if "ids" in rag_result:
                all_ids.extend(rag_result["ids"])
        elif question_type == "whitepaper":
            # Answer based on already generated whitepaper fields
            answer = await answer_whitepaper_question(field_id, question, previousFields, logger, all_missing_fields)
        elif question_type == "hardcoded":
            # Return hardcoded answer
            answer = GenerateSpecificAnswer(
                field=field_id,
                question=question_text,
                answer=question_text,
                confident=True,
            )
        else:
            answer = GenerateSpecificAnswer(
                field=field_id,
                question=question_text,
                answer=prompt_loader.get_error_message("unknown_question_type", question_text=question_text),
                confident=False,
                type=question_type,
            )

        answer.type = question_type
        all_answers.append(answer)

    # Check if there are any confident answers
    has_confident_answers = any(answer.confident for answer in all_answers)

    # Second pass: apply workaround logic based on confidence levels
    for answer in all_answers:
        if not answer.confident:
            if has_confident_answers:
                # Enhanced workaround: Use non-confident answers as context only if there are some confident answers
                logger.info("Using non-confident answer as context for field generation (field: %s) - confident answers present", field_id)
                # Create a copy for generation context (marked as confident)
                confident_copy = GenerateSpecificAnswer(
                    field=answer.field,
                    question=answer.question,
                    answer=answer.answer,
                    confident=True,  # Mark as confident for generation context
                    type=answer.type,
                )
                answers.append(confident_copy)

                # Keep original non-confident answer for follow-up questions
                unanswered_questions.append(answer)
                logger.warning("Non-confident question for field '%s' will appear as follow-up: %s", field_id, answer.question)
            else:
                # No confident answers at all - don't use workaround, treat as unanswered
                unanswered_questions.append(answer)
                logger.warning(
                    "No confident answers found for field '%s' - treating non-confident answer as unanswered: %s", field_id, answer.question
                )
        else:
            answers.append(answer)

    return answers, unanswered_questions, all_ids


async def answer_whitepaper_question(
    field_id: str,
    questionData: FieldQuestionsFormat,
    previousFields: dict[str, str],
    logger: logging.Logger,
    all_missing_fields: list[str] | None = None,
) -> GenerateSpecificAnswer:
    """
    Answers a specific question related to the field based on the previously generated fields.

    Args:
        field_id (str): The ID of the field for which to answer the question.
        question (str): The question text.
        logger (logging): The logger object for logging messages.

    Returns:
        str: The answer to the question related to the field.
    """
    if all_missing_fields is None:
        all_missing_fields = []

    logger.info("Answering whitepaper question for field_id: %s", field_id)
    # Check which fields are needed to answer the question
    relevant_fields = questionData.relevant_field.split(",") if questionData.relevant_field else []
    # Get the values from previousFields
    context = ""
    for field in relevant_fields:
        field = field.strip()
        if field in previousFields:
            context += f"{field}: {previousFields[field]}\n"
        else:
            all_missing_fields.append(field)
            logger.warning("Field '%s' not found in previousFields.", field)

    logger.info("Context for field '%s' len(%d): %s", field_id, len(context), context)
    # Generate the answer
    prompt = prompt_loader.get_answer_question_prompt("whitepaper", context=context, question=questionData.question)
    try:
        response = await run_in_thread(
            client.beta.chat.completions.parse,
            model="gpt-5",
            messages=[
                {
                    "role": "system",
                    "content": prompt_loader.get_system_message("answer_with_confidence"),
                },
                {"role": "user", "content": prompt},
            ],
            response_format=GenerateSpecificAnswer,
        )
        answer = response.choices[0].message.parsed
        answer.question = questionData.question
        logger.info(
            "Generated answer for field '%s' len(%d) confident: %s: %s",
            field_id,
            len(answer.answer),
            answer.confident,
            answer.answer,
        )
        return answer
    except Exception as e:
        logger.error("Error generating answer for %s: %s", field_id, e)
        return GenerateSpecificAnswer(
            field=field_id,
            question=questionData.question,
            answer=prompt_loader.get_error_message("generate_answer", field_id=field_id, error=str(e)),
            confident=False,
        )


async def answer_user_question(
    field_id: str,
    questionData: FieldQuestionsFormat,
    formData: Any,
    logger: logging.Logger,
) -> GenerateSpecificAnswer:
    """
    Answers a specific question related to the field based on the user's formData provided through a questionnaire.
    This function directly maps form variables to answers without using AI, making it faster and more cost-effective.

    Args:
        field_id (str): The ID of the field for which to answer the question.
        questionData (FieldQuestionsFormat): The question data including relevant variables.
        formData (dict or Pydantic model): The form data provided by the user.
        logger (logging): The logger object for logging messages.

    Returns:
        GenerateSpecificAnswer: The answer to the question related to the field.
    """
    logger.info("Answering user question for field_id: %s (direct mapping without AI)", field_id)

    # Check which fields are needed to answer the question
    relevant_variables = questionData.relevant_variable.split(",") if questionData.relevant_variable else []

    logger.debug("Question: '%s', Variables: %s", questionData.question, relevant_variables)

    # Convert formData to a dictionary if it's not already one
    form_data_dict = {}
    try:
        form_data_dict = formData if isinstance(formData, dict) else formData.dict()
    except Exception as e:
        logger.error("Error converting formData to dictionary: %s", e)
        return GenerateSpecificAnswer(
            field=field_id,
            question=questionData.question,
            answer="Not Available",
            confident=True,
            type="user",
        )

    formdata_lower = {k.lower(): (k, v) for k, v in form_data_dict.items()}
    logger.debug("FormData has %d variables available", len(formdata_lower))

    # Get the values from formData directly (no mapping - handled on frontend)
    values = []
    found_vars = []
    missing_vars = []

    for variable in relevant_variables:
        variable = variable.strip()
        variable_lower = variable.lower()

        if variable_lower in formdata_lower:
            _, value = formdata_lower[variable_lower]
            if value and str(value).strip():
                values.append(str(value))
                found_vars.append(f"{variable}='{value}'")
            else:
                values.append("Not Available")
                found_vars.append(f"{variable}=empty")
        else:
            values.append("Not Available")
            missing_vars.append(variable)

    # Log summary instead of individual variables
    if found_vars:
        logger.info("Found variables: %s", ", ".join(found_vars))
    if missing_vars:
        logger.warning("Missing variables: %s", ", ".join(missing_vars))

    # Create direct answer without AI processing
    if len(values) == 1:
        answer_text = values[0]
    elif len(values) > 1:
        # Join multiple values with space
        answer_text = " ".join(values)
    else:
        answer_text = "Not Available"

    # Determine confidence - we are always confident in user questions since we definitively know
    # whether the data exists in formData or not ("Not Available" is a definitive answer)
    confident = True

    logger.info(
        "Direct answer for field '%s': '%s' (confident: %s)",
        field_id,
        answer_text[:100] + "..." if len(answer_text) > 100 else answer_text,
        confident,
    )
    return GenerateSpecificAnswer(
        field=field_id,
        question=questionData.question,
        answer=answer_text,
        confident=True,
        type="user",
    )


async def answer_rag_question(
    field_id: str,
    question_text: str,
    scrapedChunks: list[dict],
    os_client: OpenSearch,
    logger: logging.Logger,
    user_id,
    formData: Any,
) -> dict:
    """
    Answers a specific question related to the field based on the opensearch and scraped context.

    Args:
        field_id (str): The ID of the field for which to answer the question.
        question (str): The question text.
        logger (logging): The logger object for logging messages.

    Returns:
        Dict: Dictionary containing the answer and IDs from the search.
    """
    logger.info("Answering specific question for field_id: %s", field_id)

    try:
        form_dict = formData if isinstance(formData, dict) else formData.dict()
    except Exception:
        form_dict = {}
    original_filenames = form_dict.get("documents")

    if not original_filenames or len(original_filenames) == 0:
        opensearchContext = {"context": "", "ids": []}
    else:
        opensearchContext = await hybrid_search(
            question_text,
            os_client,
            logger,
            k=20,
            user_id=user_id,
            filenames=original_filenames,
        )

    ids = opensearchContext["ids"]

    # Gather relevant context from scraped content
    scrapedContext = await search_chunks(question_text, scrapedChunks, logger)

    # Combine the contexts with explicit string handling
    open_ctx_val = opensearchContext.get("context", "")
    open_ctx_str = open_ctx_val if isinstance(open_ctx_val, str) else str(open_ctx_val or "")

    scraped_str = ""
    if scrapedContext:
        first_chunk = scrapedContext[0]
        if isinstance(first_chunk, dict):
            chunk_val = first_chunk.get("chunk")
            scraped_str = chunk_val if isinstance(chunk_val, str) else str(chunk_val or "")

    context = open_ctx_str + (("\n" + scraped_str) if scraped_str else "")
    logger.info("Combined context for field '%s' len(%d): %s", field_id, len(context), context)

    # Get the answer
    prompt = prompt_loader.get_answer_question_prompt("rag", context=context, question=question_text)
    try:
        response = await run_in_thread(
            client.beta.chat.completions.parse,
            model="gpt-5-mini",
            messages=[
                {
                    "role": "system",
                    "content": prompt_loader.get_system_message("answer_with_confidence"),
                },
                {"role": "user", "content": prompt},
            ],
            response_format=GenerateSpecificAnswer,
        )
        answer = response.choices[0].message.parsed
        answer.question = question_text
        logger.info(
            "Generated answer for field '%s' len(%d) confident: %s: %s",
            field_id,
            len(answer.answer),
            answer.confident,
            answer.answer,
        )
        return {"answer": answer, "ids": ids}
    except Exception as e:
        logger.error("Error generating fill for %s: %s", field_id, e)
        return {
            "answer": GenerateSpecificAnswer(
                field=field_id,
                question=question_text,
                answer=prompt_loader.get_error_message("generate_answer", field_id=field_id, error=str(e)),
                confident=False,
            ),
            "ids": [],
        }
