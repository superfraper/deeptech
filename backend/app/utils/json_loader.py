import json
import logging
from collections.abc import Sequence
from enum import Enum
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, ValidationError, field_validator

from app.config import settings

logger = logging.getLogger("json_loader")


class TokenClass(str, Enum):
    OTH = "OTH"
    ART = "ART"
    EMT = "EMT"


def _normalize_tc(tc: str | TokenClass | None) -> TokenClass:
    if not tc:
        raise ValueError("token_classification is required")
    if isinstance(tc, TokenClass):
        return tc
    return TokenClass(tc.upper())


def _json_path(dataset: str, tc: TokenClass) -> Path:
    return Path(settings.JSON_DATA_DIR) / f"{dataset}_{tc.value.lower()}.json"


class Guideline(BaseModel):
    no: str | int
    field: str
    section_name: str
    content_to_be_reported: str
    form_and_standards: str

    @field_validator("no")
    @classmethod
    def _no_to_str_if_needed(cls, v: str | int) -> str:
        # Normalize to string for consistent matching
        return str(v)


class SubQuestion(BaseModel):
    field_id: str
    question: str
    type: str  # e.g., "rag", "hardcoded", "whitepaper", ...
    relevant_field: str | None = None
    relevant_variable: str | None = None


class WhitepaperField(BaseModel):
    section_number: int
    field_id: str
    field_name: str
    id: int | None = None


@lru_cache(maxsize=3)
def load_whitepaper_fields(tc: str | TokenClass) -> list[WhitepaperField]:
    """Load and validate whitepaper fields (per-section field list) for a token class."""
    tc_u = _normalize_tc(tc)
    path = _json_path("whitepaper_fields", tc_u)
    raw = _read_json_file(path)
    try:
        return [WhitepaperField(**item) for item in raw]
    except ValidationError as e:
        raise ValueError(f"Whitepaper fields JSON validation failed for {path}: {e}") from e


def get_whitepaper_fields_by_section(tc: str | TokenClass, section_number: int) -> list[dict[str, str | int | None]]:
    """Return a list of dicts for a section: [{'id': int|None, 'field_id': str, 'field_name': str}, ...]."""
    items = load_whitepaper_fields(tc)
    sec = int(section_number)
    result = [{"id": it.id, "field_id": it.field_id, "field_name": it.field_name} for it in items if int(it.section_number) == sec]
    return result


def _read_json_file(path: Path) -> Sequence[dict]:
    if not path.exists():
        raise FileNotFoundError(f"Required JSON file not found: {path}")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON content in {path}: {e}") from e

    if not isinstance(data, list):
        raise ValueError(f"Expected top-level array in {path}, got {type(data).__name__}")
    return data


@lru_cache(maxsize=3)
def load_guidelines(tc: str | TokenClass) -> list[Guideline]:
    """Load and validate guidelines for a token class. Fail-fast on any error."""
    tc_u = _normalize_tc(tc)
    path = _json_path("guidelines", tc_u)
    raw = _read_json_file(path)
    try:
        return [Guideline(**item) for item in raw]
    except ValidationError as e:
        raise ValueError(f"Guidelines JSON validation failed for {path}: {e}") from e


@lru_cache(maxsize=3)
def load_subquestions(tc: str | TokenClass) -> list[SubQuestion]:
    """Load and validate subquestions for a token class. Fail-fast on any error."""
    tc_u = _normalize_tc(tc)
    path = _json_path("subquestions", tc_u)
    raw = _read_json_file(path)
    try:
        return [SubQuestion(**item) for item in raw]
    except ValidationError as e:
        raise ValueError(f"Subquestions JSON validation failed for {path}: {e}") from e


@lru_cache(maxsize=3)
def get_guidelines_map_by_no(tc: str | TokenClass) -> dict[str, Guideline]:
    """Build an in-memory map by field `no` for fast lookups."""
    items = load_guidelines(tc)
    result: dict[str, Guideline] = {}
    for g in items:
        # normalize key to string to handle numeric vs string "No"
        key = str(g.no).strip()
        if key in result:
            raise ValueError(f"Duplicate guideline `no` value `{key}` found in {tc} dataset. Each `no` must be unique.")
        result[key] = g
    return result


def get_guideline_by_no(tc: str | TokenClass, no: str | int) -> Guideline | None:
    return get_guidelines_map_by_no(tc).get(str(no).strip())


def get_subquestions_by_field_id(tc: str | TokenClass, field_id: str) -> list[SubQuestion]:
    fid = field_id.strip()
    return [sq for sq in load_subquestions(tc) if sq.field_id.strip() == fid]


def get_relevant_variable(tc: str | TokenClass, field_id: str) -> str | None:
    """Return the relevant_variable for a field if present (simple forward)."""
    questions = get_subquestions_by_field_id(tc, field_id)
    # Prefer entries that explicitly define relevant_variable
    for q in questions:
        if q.relevant_variable and q.relevant_variable.strip():
            return q.relevant_variable.strip()
    return None


def preflight_json_validation() -> None:
    """
    Validate that all required JSON datasets are present and valid.
    This should be called at application startup. Any failure raises and should stop the app.
    """
    required_tcs: list[TokenClass] = [TokenClass.OTH, TokenClass.ART, TokenClass.EMT]

    # Validate presence and structure for all token classes and both datasets

    for tc in required_tcs:
        g_path = _json_path("guidelines", tc)
        s_path = _json_path("subquestions", tc)
        logger.info(f"Preflight: validating JSON files: {g_path} and {s_path}")

        # Loaders are fail-fast and cached
        _ = load_guidelines(tc)
        _ = load_subquestions(tc)
        _ = load_whitepaper_fields(tc)

    logger.info("Preflight JSON validation succeeded for all required datasets.")
