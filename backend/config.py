"""Environment-backed configuration for the LLM Council."""

import os
from dotenv import load_dotenv

load_dotenv()

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
COUNCIL_MODELS = [
    model.strip()
    for model in os.getenv(
        "COUNCIL_MODELS",
        "openai/gpt-5.1,google/gemini-3-pro-preview,anthropic/claude-sonnet-4.5,x-ai/grok-4",
    ).split(",")
    if model.strip()
]
CHAIRMAN_MODEL = os.getenv("CHAIRMAN_MODEL", "google/gemini-3-pro-preview")
OPENROUTER_API_URL = os.getenv(
    "OPENROUTER_API_URL", "https://openrouter.ai/api/v1/chat/completions"
)
DATA_DIR = os.getenv("DATA_DIR", "data/conversations")
CORS_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "CORS_ALLOWED_ORIGINS", "http://localhost:5173,http://localhost:3000"
    ).split(",")
    if origin.strip()
]
MAX_REQUEST_CHARS = int(os.getenv("MAX_REQUEST_CHARS", "20000"))
