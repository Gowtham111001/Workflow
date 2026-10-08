"""Thin wrapper over the Claude API for structured (Pydantic-typed) calls.

Every LLM step in the pipeline is "text in, validated object out", so this is
the only place that talks to the API. Tests swap in a fake with the same
`parse` signature.
"""

from __future__ import annotations

from typing import Protocol, TypeVar

import anthropic
from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    pass


class StructuredLLM(Protocol):
    def parse(self, *, system: str, prompt: str, schema: type[T], effort: str = "medium") -> T: ...


class ClaudeLLM:
    def __init__(self, model: str = "claude-opus-5-5", client: anthropic.Anthropic | None = None):
        self.model = model
        # Credentials resolve from ANTHROPIC_API_KEY (or an `ant auth login` profile).
        self.client = client or anthropic.Anthropic()

    def parse(self, *, system: str, prompt: str, schema: type[T], effort: str = "medium") -> T:
        try:
            response = self.client.beta.messages.parse(
                model=self.model,
                max_tokens=16000,
                # Callers put stable context (instructions + your master resume)
                # in `system`, so it is cached across the many per-job calls.
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": prompt}],
                output_format=schema,
                output_config={"effort": effort},
                # If a safety classifier declines (e.g. a false positive on a
                # security-engineering job description), retry server-side on
                # Anthropic's recommended fallback model instead of failing.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except anthropic.RateLimitError as e:
            raise LLMError(f"Rate limited by the Claude API; try again shortly ({e.message})") from e
        except anthropic.APIStatusError as e:
            raise LLMError(f"Claude API error {e.status_code}: {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise LLMError(f"Could not reach the Claude API: {e}") from e
        except ValidationError as e:
            # The SDK parses text eagerly; a refused or truncated reply leaves invalid JSON.
            raise LLMError("Claude's reply was not valid structured output (refused or cut off)") from e

        if response.stop_reason == "refusal":
            category = response.stop_details.category if response.stop_details else None
            raise LLMError(f"Claude declined this request (category: {category})")
        if response.stop_reason == "max_tokens":
            raise LLMError("Response was cut off at max_tokens")
        if response.parsed_output is None:
            raise LLMError("Response did not contain the expected structured output")
        return response.parsed_output
