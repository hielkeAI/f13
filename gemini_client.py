"""Small async client for the Google Gemini REST API."""

from __future__ import annotations

import asyncio
import os
from typing import Any

import aiohttp

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash").strip()
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"


def is_configured() -> bool:
    return bool(GEMINI_API_KEY) and not GEMINI_API_KEY.startswith("replace_with_")


async def generate_text(
    prompt: str,
    *,
    system_instruction: str | None = None,
    temperature: float = 0.7,
    max_output_tokens: int = 1024,
) -> str:
    if not is_configured():
        raise RuntimeError("GEMINI_API_KEY is not configured in .env")

    payload: dict[str, Any] = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_output_tokens,
        },
    }
    if system_instruction:
        payload["systemInstruction"] = {"parts": [{"text": system_instruction}]}

    fallback_models = ("gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite")
    model_candidates = [GEMINI_MODEL, *[m for m in fallback_models if m != GEMINI_MODEL]]
    last_error: str | None = None
    timeout = aiohttp.ClientTimeout(total=90, connect=30, sock_read=80)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for model_name in model_candidates:
            url = f"{GEMINI_BASE_URL}/models/{model_name}:generateContent"
            try:
                async with session.post(
                    url,
                    headers={"x-goog-api-key": GEMINI_API_KEY, "Content-Type": "application/json"},
                    json=payload,
                ) as response:
                    data = await response.json(content_type=None)
                    if response.status == 200:
                        break
                    error = data.get("error", {}) if isinstance(data, dict) else {}
                    message = error.get("message") or f"HTTP {response.status}"
                    last_error = f"Gemini request failed ({model_name}): {message}"
                    # Retry another model on overload/not-found.
                    if response.status in (404, 429, 500, 503):
                        continue
                    raise RuntimeError(last_error)
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                last_error = f"Gemini request failed ({model_name}): {exc}"
                continue
        else:
            raise RuntimeError(last_error or "Gemini request failed")

    candidates = data.get("candidates", []) if isinstance(data, dict) else []
    if not candidates:
        raise RuntimeError("Gemini returned no response")

    parts = candidates[0].get("content", {}).get("parts", [])
    text = "".join(part.get("text", "") for part in parts if isinstance(part, dict))
    if not text.strip():
        raise RuntimeError("Gemini returned an empty response")
    return text.strip()
