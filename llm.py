from __future__ import annotations
"""Thin wrapper around an OpenAI-compatible server (LM Studio by default).

chat_json() asks for JSON that follows a schema. If the server rejects the
schema or the model returns messy text, it retries once and cleans the output.
"""

import json
import re

import config

_client = None


def _get_client():
    global _client
    if _client is None:
        from openai import OpenAI  # imported here so tests can run without it

        _client = OpenAI(
            base_url=config.BASE_URL,
            api_key=config.API_KEY,
            timeout=config.TIMEOUT_SECONDS,
        )
    return _client


def list_models() -> list:
    """Model ids the server offers (LM Studio: the downloaded models). [] if unreachable."""
    try:
        ids = [m.id for m in _get_client().models.list().data]
    except Exception:  # noqa: BLE001
        return []
    return sorted(i for i in ids if "embed" not in i.lower())


def extract_json(text: str) -> dict:
    """Parse JSON even if the model wrapped it in code fences or added text."""
    text = (text or "").strip()
    # reasoning models (Qwen3, DeepSeek-R1...) may put their thinking before the answer
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE).strip()
    if not text:
        raise ValueError(
            "The model returned an empty answer. A thinking model probably used all its tokens "
            "on reasoning: turn thinking off in LM Studio or raise LLM_MAX_TOKENS."
        )
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            return json.loads(text[start : end + 1])
        raise


def chat_json(prompt: str, schema: dict | None = None, name: str = "answer") -> dict:
    client = _get_client()
    base = dict(
        model=config.MODEL,
        temperature=config.TEMPERATURE,
        max_tokens=config.MAX_TOKENS,
    )
    last_error = None
    for attempt in range(2):
        content = prompt
        if "qwen3" in config.MODEL.lower():
            content += "\n\n/no_think"  # Qwen3 switch: answer directly, no long hidden reasoning
        kwargs = dict(base)
        if schema is not None and attempt == 0:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": name, "strict": True, "schema": schema},
            }
        if attempt == 1:
            content += "\n\nReturn ONLY one valid JSON object. No other text."
        try:
            resp = client.chat.completions.create(
                messages=[{"role": "user", "content": content}], **kwargs
            )
            return extract_json(resp.choices[0].message.content)
        except Exception as exc:  # noqa: BLE001 - we want to retry on anything
            last_error = exc
    raise RuntimeError(
        f"The model call failed twice. Is the LM Studio server running and is the "
        f"model name '{config.MODEL}' correct? Last error: {last_error}"
    )
