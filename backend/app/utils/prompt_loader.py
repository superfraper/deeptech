from typing import Any

import yaml


class PromptLoader:
    _instance: "PromptLoader | None" = None

    def __new__(cls) -> "PromptLoader":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        # Idempotent init; do nothing on subsequent constructions
        if getattr(self, "_initialized", False):
            return
        self._prompts: dict[str, Any] = {}
        self._initialized = True
        self.reload()

    def reload(self) -> None:
        """Force reload prompts from the YAML file"""
        prompts = self._load_prompts()
        self._prompts = prompts

    def _load_prompts(self) -> dict[str, Any]:
        """Load prompts from the YAML file"""
        import logging
        from pathlib import Path

        logger = logging.getLogger("PromptLoader")

        prompts_file_path = Path(__file__).resolve().parents[2] / "prompts.yaml"
        if prompts_file_path.exists():
            with open(prompts_file_path, encoding="utf-8") as file:
                try:
                    data = yaml.safe_load(file) or {}
                    if not isinstance(data, dict):
                        raise ValueError(f"Root of {prompts_file_path} must be a mapping")
                    prompts = data.get("prompts")
                    if not isinstance(prompts, dict):
                        raise ValueError(f"Invalid or missing 'prompts' section in {prompts_file_path}")
                    logger.info(f"Loaded prompts from {prompts_file_path}")
                    return prompts
                except yaml.YAMLError as e:
                    logger.error(f"YAML parsing error in {prompts_file_path}: {e}")
                    raise
                except UnicodeDecodeError as e:
                    logger.error(f"Encoding error reading {prompts_file_path}: {e}")
                    raise
                except Exception as e:
                    logger.error(f"Unexpected error reading {prompts_file_path}: {e}")
                    raise
        else:
            raise FileNotFoundError(f"prompts.yaml not found, in path {prompts_file_path}")

    def get_system_message(self, message_type: str = "default") -> str:
        """Get a system message by type"""
        return self._prompts.get("system_messages", {}).get(message_type, "You are a helpful assistant.")

    def get_regenerate_prompt(self, prompt_type: str, **kwargs) -> str:
        """Get a regenerate field prompt template and format it with provided kwargs"""
        template = self._prompts.get("regenerate_field", {}).get(prompt_type, {}).get("template", "")
        return template.format(**kwargs)

    def get_generate_prompt(self, prompt_type: str, **kwargs) -> str:
        """Get a generate field prompt template and format it with provided kwargs"""
        template = self._prompts.get("generate_field", {}).get(prompt_type, {}).get("template", "")
        return template.format(**kwargs)

    def get_answer_question_prompt(self, question_type: str, **kwargs) -> str:
        """Get an answer question prompt template and format it with provided kwargs"""
        template = self._prompts.get("answer_questions", {}).get(question_type, {}).get("template", "")
        return template.format(**kwargs)

    def get_unanswered_questions_message(self) -> str:
        """Get the unanswered questions message"""
        return self._prompts.get("generate_field", {}).get(
            "unanswered_questions",
            "Please answer all of the follow-up questions in order to generate the whitepaper field content.",
        )

    def get_error_message(self, error_type: str, **kwargs) -> str:
        """Get an error message template and format it with provided kwargs"""
        template = self._prompts.get("error_messages", {}).get(error_type, f"Error: {error_type}")
        return template.format(**kwargs)

    def get_constant(self, constant_name: str) -> str:
        """Get a constant value"""
        return self._prompts.get("constants", {}).get(constant_name, "")

    def generate_rag_context(self, source_type: str, **kwargs) -> str:
        """Get a RAG context generation prompt template and format it with provided kwargs"""

        template = self._prompts.get("generate_context", {}).get(source_type, {}).get("template", "")
        return template.format(**kwargs)


# Create a singleton instance
prompt_loader = PromptLoader()
