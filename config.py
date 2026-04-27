"""
Shared environment variable helpers.
"""

import os
from dotenv import load_dotenv

# Keep Railway runtime env vars authoritative; only backfill from .env when needed.
load_dotenv(override=False)


def get_anthropic_api_key() -> str:
    """
    Return a non-empty Anthropic API key from environment variables.

    Supports common fallback names to avoid deployment naming mismatches.
    """
    candidates = (
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_KEY",
        "CLAUDE_API_KEY",
    )

    for key_name in candidates:
        value = os.getenv(key_name)
        if isinstance(value, str) and value.strip():
            return value.strip()

    raise RuntimeError(
        "Missing Anthropic API key. Set ANTHROPIC_API_KEY in environment (Railway Variables)."
    )
