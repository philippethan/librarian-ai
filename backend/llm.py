import asyncio
import os

import httpx
from dotenv import load_dotenv

load_dotenv()

from backend.extractors import repair_json, validate_fields

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "mistral:7b-instruct")
LLM_CONCURRENCY = int(os.getenv("LLM_CONCURRENCY", "2"))
LLM_MAX_CHARS = int(os.getenv("LLM_MAX_CHARS", "3000"))

_semaphore: asyncio.Semaphore | None = None


def _get_semaphore() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(LLM_CONCURRENCY)
    return _semaphore


def _truncate(text: str) -> str:
    if len(text) <= LLM_MAX_CHARS:
        return text
    return text[:2000] + text[-1000:]


def _build_prompt(text: str, filepath: str, pass_name: str) -> str:
    return (
        f"You are a librarian metadata extractor. "
        f"Extract bibliographic metadata from the following book text.\n\n"
        f"File: {filepath}\nPass: {pass_name}\n\n"
        f"Text:\n{text}\n\n"
        f"Return ONLY a JSON object with these fields (omit unknown fields as null):\n"
        f'{{"title": str, "author": str, "year": int, "language": "xx (ISO 639-1)", '
        f'"category": str, "subcategory": str, '
        f'"difficulty": "Beginner|Intermediate|Advanced", '
        f'"description": str, "tags": [str]}}'
    )


async def call_ollama(text: str, filepath: str, pass_name: str) -> dict:
    truncated = _truncate(text)
    prompt = _build_prompt(truncated, filepath, pass_name)
    payload = {"model": OLLAMA_MODEL, "prompt": prompt, "stream": False}
    timeout = httpx.Timeout(45.0)

    async with _get_semaphore():
        for attempt in range(2):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.post(
                        f"{OLLAMA_URL}/api/generate", json=payload
                    )
                if resp.status_code == 500:
                    if attempt == 0:
                        continue
                    return {}
                resp.raise_for_status()
                raw = resp.json().get("response", "")
                parsed = repair_json(raw)
                return validate_fields(parsed)
            except (httpx.TimeoutException, httpx.RequestError):
                if attempt == 0:
                    continue
                return {}
        return {}


def call_ollama_sync(text: str, filepath: str, pass_name: str) -> dict:
    return asyncio.run(call_ollama(text, filepath, pass_name))


async def call_ollama_chat(system: str, context: str, message: str) -> str:
    """Call Ollama chat API; returns the reply string."""
    payload = {
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {message}"},
        ],
        "stream": False,
    }
    timeout = httpx.Timeout(45.0)
    async with _get_semaphore():
        for attempt in range(2):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.post(f"{OLLAMA_URL}/api/chat", json=payload)
                if resp.status_code == 500:
                    if attempt == 0:
                        continue
                    return "Sorry, the AI service is unavailable."
                resp.raise_for_status()
                return resp.json().get("message", {}).get("content", "")
            except (httpx.TimeoutException, httpx.RequestError):
                if attempt == 0:
                    continue
                return "Sorry, the AI service timed out."
    return ""
