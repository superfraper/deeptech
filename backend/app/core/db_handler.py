import logging
from typing import Any

from app.utils.json_loader import get_whitepaper_fields_by_section, load_guidelines

# Set up logging
logger = logging.getLogger("db_handler")


def get_section_table_map(token_classification: str = "OTH") -> dict[int, str]:
    """Get the appropriate section-to-table mapping based on token classification"""
    token_classification = token_classification.upper() if token_classification else "OTH"

    match token_classification:
        case "ART":
            return {
                1: "Section1",
                2: "Section2",
                3: "Section3",
                4: "Section4",
                5: "Section5",
                6: "Section6",
                7: "Section7",
                8: "Section8",
                9: "Section9",
                10: "Section10",
                13: "Section13",
            }
        case "EMT":
            return {
                1: "Section1",
                2: "Section2",
                3: "Section3",
                4: "Section4",
                5: "Section5",
                6: "Section6",
                7: "Section7",
                8: "Section8",
                13: "Section13",
            }
        case _:
            return {
                1: "Section1",
                2: "Section2",
                3: "Section3",
                4: "Section4",
                5: "Section5",
                6: "Section6",
                7: "Section7",
                8: "Section9",
                9: "Section8",
                10: "Section10",
                11: "Section11",
                13: "Section13",
            }


class DatabaseHandler:
    def __init__(self, token_classification: str = "OTH"):
        # Ensure token_classification is uppercase for consistency
        self.token_classification = token_classification.upper() if token_classification else "OTH"
        logger.info(f"Initializing DatabaseHandler with token_classification: {self.token_classification}")

    def get_fields_info(self) -> list[tuple[str, str, str, str]]:
        """Get fields info from guidelines JSON for the current token_classification."""
        try:
            logger.info(f"Getting fields info from JSON for {self.token_classification}")
            items = load_guidelines(self.token_classification)
            # Return list of tuples to preserve downstream expectations:
            # (no, field, section_name, CONTENT TO BE REPORTED)
            fields = [
                (
                    str(g.no),
                    g.field,
                    g.section_name,
                    g.content_to_be_reported,
                )
                for g in items
            ]
            logger.info(f"Retrieved {len(fields)} fields from JSON.")
            return fields
        except Exception as e:
            logger.error(f"Error getting fields info from JSON for {self.token_classification}: {e}")
            return []

    def get_all_section_fields(self) -> dict[str, list[dict[str, Any]]]:
        """Get all fields from all sections"""
        result = {}
        # Use section numbers from the appropriate mapping
        section_table_map = get_section_table_map(self.token_classification)
        for section_number in section_table_map:
            fields = get_whitepaper_fields_by_section(self.token_classification, section_number)
            if fields:
                result[f"section{section_number}"] = fields
        return result

    def get_section_field_by_id(self, section_number: int, field_id: str) -> dict[str, Any] | None:
        """Get a specific field by its ID within a section"""
        # Get the appropriate section-to-table mapping for the current token type
        section_table_map = get_section_table_map(self.token_classification)

        table_name = section_table_map.get(section_number)
        if not table_name:
            return None
        fields = get_whitepaper_fields_by_section(self.token_classification, section_number)
        if not fields:
            return None

        for row in fields:
            if str(row.get("field_id")) == str(field_id):
                return row

        logger.info(f"field_id={field_id} not found in section {section_number}; searched {len(fields)} fields")
        return None
