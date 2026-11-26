"""Model settings. Change them here or with environment variables.

LM Studio: start the local server (Developer tab) and copy the model name
exactly as LM Studio shows it.
To use another model later (Qwen, Claude via a compatible endpoint, ...)
only these values change.
"""
import os

BASE_URL = os.getenv("LLM_BASE_URL", "http://localhost:1234/v1")
API_KEY = os.getenv("LLM_API_KEY", "lm-studio")  # LM Studio ignores the value
MODEL = os.getenv("LLM_MODEL", "google/gemma-4-e4b")
TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0"))
MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "1500"))
TIMEOUT_SECONDS = int(os.getenv("LLM_TIMEOUT", "180"))
