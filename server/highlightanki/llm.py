"""Thin Claude client: every call returns schema-validated JSON via structured outputs.

The API key is never handled here: the Anthropic SDK reads
`ANTHROPIC_API_KEY` from the environment (populated from `.env` by
config.py), so it exists only in that file and in this process.
"""
from __future__ import annotations

import json

import anthropic


class LLMError(Exception):
    """Raised when Claude cannot produce the requested structured output."""


class LLM:
    def __init__(self, model: str, client=None):
        self.client = client or anthropic.Anthropic(max_retries=4)
        self.model = model

    def json(self, prompt: str, schema: dict, max_tokens: int = 8192) -> dict:
        try:
            # Streamed: the SDK rejects non-streaming requests whose max_tokens
            # implies a >10 min wall clock.
            with self.client.messages.stream(
                model=self.model,
                max_tokens=max_tokens,
                thinking={"type": "adaptive"},
                output_config={"format": {"type": "json_schema", "schema": schema}},
                messages=[{"role": "user", "content": [{"type": "text", "text": prompt}]}],
            ) as stream:
                response = stream.get_final_message()
        except anthropic.APIError as exc:
            raise LLMError(str(exc)) from exc

        if response.stop_reason == "max_tokens":
            raise LLMError("response truncated (max_tokens) — raise max_tokens in prompts.yaml")
        if response.stop_reason == "refusal":
            raise LLMError("model refused the request")
        text = next((b.text for b in response.content if b.type == "text"), None)
        if text is None:
            raise LLMError("no text block in response")
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise LLMError(f"invalid JSON from model: {exc}") from exc
