# Development Setup Guide

This guide explains how to use the code quality tools set up in this project.

## Tools Overview

### Python Backend

- **Ruff**: Fast Python linter and formatter
- **Black**: Uncompromising Python code formatter

### JavaScript/TypeScript Frontend

- **ESLint**: JavaScript/TypeScript linting utility
- **Prettier**: Opinionated code formatter

## Quick Start

### 1. Install Dependencies

```bash
# Install Python dependencies
cd backend
pip install -r requirements.txt

# Install Node.js dependencies
cd frontend
npm install
```

### 2. Use the Makefile (Recommended)

The project includes a Makefile with convenient commands:

```bash
# Show all available commands
make help

# Install all dependencies
make install

# Format all code
make format

# Lint all code
make lint

# Check formatting without changes
make format-check

# Check linting without changes
make lint-check

# Run all checks
make check

# Fix all issues
make fix

# Clean up generated files
make clean
```

## Manual Usage

### Python Tools

#### Black (Code Formatting)

```bash
cd backend

# Format all Python files
black .

# Check formatting without changes
black --check .

# Format specific file
black app/main.py
```

#### Ruff (Linting)

```bash
cd backend

# Lint all Python files
ruff check .

# Lint and auto-fix issues
ruff check . --fix

# Check specific file
ruff check app/main.py
```

### Frontend Tools

#### Prettier (Code Formatting)

```bash
cd frontend

# Format all files
npm run format

# Check formatting without changes
npm run format:check

# Format specific file
npx prettier --write src/App.js
```

#### ESLint (Linting)

```bash
cd frontend

# Lint all files
npm run lint

# Lint and auto-fix issues
npm run lint:fix

# Lint specific file
npx eslint src/App.js
```

## VS Code Integration

The project includes VS Code workspace settings that automatically:

- Format code on save
- Run ESLint fixes on save
- Organize imports on save
- Use appropriate formatters for different file types

### Required VS Code Extensions

- Python
- Black Formatter
- Ruff
- Prettier - Code formatter
- ESLint

Install these extensions or run:

```bash
code --install-extension ms-python.python
code --install-extension ms-python.black-formatter
code --install-extension charliermarsh.ruff
code --install-extension esbenp.prettier-vscode
code --install-extension dbaeumer.vscode-eslint
```

## Configuration Files

### Python

- `backend/pyproject.toml`: Black and Ruff configuration
- `backend/requirements.txt`: Python dependencies

### Frontend

- `frontend/.prettierrc`: Prettier configuration
- `frontend/.prettierignore`: Files to ignore
- `frontend/package.json`: ESLint configuration and scripts

### Root Level

- `.prettierrc`: Root Prettier configuration
- `.prettierignore`: Root ignore patterns
- `.vscode/settings.json`: VS Code workspace settings
- `.vscode/extensions.json`: Recommended extensions
- `Makefile`: Convenient commands for all tools

## Pre-commit Workflow

For the best development experience:

1. **Before committing**: Run `make check` to ensure code quality
2. **Auto-fix issues**: Run `make fix` to automatically fix formatting and linting issues
3. **Format on save**: VS Code will automatically format files when you save them

## Troubleshooting

### Common Issues

#### Python Tools Not Working

```bash
# Ensure you're in the correct directory
cd backend

# Check if tools are installed
pip list | grep -E "(black|ruff)"

# Reinstall if needed
pip install -r requirements.txt
```

#### Frontend Tools Not Working

```bash
# Ensure you're in the correct directory
cd frontend

# Check if tools are installed
npm list --depth=0 | grep -E "(prettier|eslint)"

# Reinstall if needed
rm -rf node_modules package-lock.json
npm install
```

#### VS Code Not Formatting

1. Ensure you have the required extensions installed
2. Check that the file type is recognized
3. Verify the formatter is set correctly in the status bar
4. Try reloading the VS Code window

### Reset Everything

```bash
# Clean all generated files and reinstall
make clean
make install
```

## Customization

### Python

Edit `backend/pyproject.toml` to customize:

- Line length (currently 88)
- Ruff rules and ignores
- Black formatting options

### Frontend

Edit `frontend/.prettierrc` to customize:

- Quote style (currently single quotes)
- Line length (currently 80)
- Semicolon usage (currently required)

### VS Code

Edit `.vscode/settings.json` to customize:

- Format on save behavior
- Default formatters
- Python interpreter path

## Contributing

When contributing to this project:

1. Ensure your code passes all checks: `make check`
2. Fix any issues before submitting: `make fix`
3. Follow the established formatting and linting rules
4. Use the provided tools to maintain code quality

## Additional Resources

- [Black Documentation](https://black.readthedocs.io/)
- [Ruff Documentation](https://docs.astral.sh/ruff/)
- [Prettier Documentation](https://prettier.io/docs/en/)
- [ESLint Documentation](https://eslint.org/docs/latest/)
