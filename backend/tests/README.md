# Testing

This directory contains the test suite for the RAG backend application.

## Structure

```
tests/
├── __init__.py
├── conftest.py              # Shared fixtures
├── unit/                    # Unit tests
│   ├── test_db_handler.py   # Database handler tests
│   └── ...
├── integration/             # Integration tests (planned)
└── fixtures/                # Test data and fixtures
```

## Running Tests

### All Tests
> Please use `uv` if you are not yet using it.
```bash
# Run all tests
uv run pytest

# Run with coverage
uv run pytest --cov=app --cov-report=html
```

### Test Structure
```python
class TestYourComponent:
    """Test cases for YourComponent"""

    def test_happy_path(self):
        """Test the main success scenario"""
        # Arrange
        # Act  
        # Assert
        
    def test_edge_case(self):
        """Test edge cases and error conditions"""
        # Test implementation
        
    @patch("module.dependency")
    def test_with_mocking(self, mock_dependency):
        """Test with external dependencies mocked"""
        # Test implementation
```

### Fixtures
Common fixtures are available in `conftest.py`:
- `temp_dir` - Temporary directory for test files
- `test_db_file` - Temporary SQLite database file
- `mock_settings` - Mocked settings configuration
- `sample_field_data` - Sample test data
