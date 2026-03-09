import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test files"""
    temp_dir = tempfile.mkdtemp()
    yield temp_dir
    shutil.rmtree(temp_dir)


@pytest.fixture
def test_db_file(temp_dir):
    """Create a temporary SQLite database file"""
    db_path = os.path.join(temp_dir, "test.db")
    # Create an empty file
    Path(db_path).touch()
    return db_path


@pytest.fixture
def mock_settings():
    """Mock the settings module with test database paths"""
    with patch("app.core.db_handler.settings", create=True) as mock_settings:
        mock_settings.OTH_WHITEPAPER_DB = "/test/oth.db"
        mock_settings.ART_WHITEPAPER_DB = "/test/art.db"
        mock_settings.EMT_WHITEPAPER_DB = "/test/emt.db"
        mock_settings.GUIDELINES_DB = "/test/guidelines.db"
        yield mock_settings


@pytest.fixture
def sample_field_data():
    """Sample field data for testing"""
    return [
        ("A.01", "Field 1", "Section A", "Content 1"),
        ("A.02", "Field 2", "Section A", "Content 2"),
        ("B.01", "Field 3", "Section B", "Content 3"),
    ]


@pytest.fixture
def sample_section_data():
    """Sample section data for testing"""
    return [
        {"field_id": "A.01", "field_name": "Test Field 1", "content": "Test content 1"},
        {"field_id": "A.02", "field_name": "Test Field 2", "content": "Test content 2"},
    ]
