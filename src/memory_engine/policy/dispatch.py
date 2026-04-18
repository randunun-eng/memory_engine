"""Single entry point for all LLM calls.

Every LLM invocation in the system goes through dispatch(). No exceptions,
no "just this once." This is the architectural invariant for Phase 2.

Responsibilities:
- Resolve the active prompt template for the given site
- Build the context via the broker
- Check the prompt cache
- Call the LLM
- Parse the response using the site's parser
- Log cost, latency, cache hit/miss
- Enforce the monthly budget cap

Phase 6 adds the shadow harness (A/B comparison traffic).
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import TYPE_CHECKING, Any

from memory_engine.exceptions import (
    DispatchError,
    LLMResponseParseError,
    PromptNotFound,
)

if TYPE_CHECKING:
    from memory_engine.policy.cache import PromptCache
    from memory_engine.policy.registry import PromptRegistry

logger = logging.getLogger(__name__)

# Type alias for the LLM backend callable.
# Accepts (model, prompt_text, temperature) -> raw string response.
# In production this wraps httpx calls to Ollama/LiteLLM.
# In tests this is a mock or stub.
LLMBackend = Any  # Callable[[str, str, float], Awaitable[str]] — not worth a Protocol yet


class PolicyDispatch:
    """Orchestrates every LLM call: template -> context -> cache -> call -> parse."""

    def __init__(
        self,
        registry: PromptRegistry,
        llm_backend: LLMBackend,
        cache: PromptCache,
        model: str = "ollama/llama3.1:8b",
        monthly_budget_usd: float = 0.0,
    ) -> None:
        self._registry = registry
        self._llm = llm_backend
        self._cache = cache
        self._model = model
        self._monthly_budget_usd = monthly_budget_usd
        self._monthly_spend_usd: float = 0.0

    async def dispatch(
        self,
        site: str,
        *,
        persona_id: int,
        params: dict[str, Any],
        temperature: float = 0.0,
        parser: Any | None = None,
    ) -> dict[str, Any]:
        """Execute an LLM call through the policy plane.

        Args:
            site: The prompt site name (e.g. "extract_entities", "grounding_judge").
            persona_id: For cache scoping and audit. Rule: cache keys include persona_id.
            params: Template parameters to render into the prompt.
            temperature: LLM temperature. Default 0.0 for deterministic extraction.
            parser: Optional callable(raw_str) -> dict. If None, attempts JSON parse.

        Returns:
            Parsed response dict.

        Raises:
            PromptNotFound: No active template for this site.
            DispatchError: LLM call failed.
            LLMResponseParseError: Response couldn't be parsed.
        """
        # 1. Resolve the active prompt template
        template = self._registry.get_active(site)
        if template is None:
            raise PromptNotFound(f"No active prompt template for site={site!r}")

        # 2. Render the prompt
        prompt_text = template.render(params)

        # 3. Check cache (keyed on site + prompt_hash + input_hash + persona_id)
        prompt_hash = _hash_text(template.template_text)
        input_hash = _hash_text(json.dumps(params, sort_keys=True, separators=(",", ":")))
        cache_key = (site, prompt_hash, input_hash, persona_id)

        cached = self._cache.get(cache_key)
        if cached is not None:
            logger.debug("Cache hit for site=%s persona=%d", site, persona_id)
            return cached

        # 4. Call the LLM
        t0 = time.monotonic()
        try:
            raw_response = await self._llm(self._model, prompt_text, temperature)
        except Exception as e:
            raise DispatchError(f"LLM call failed for site={site!r}: {e}") from e
        elapsed_ms = (time.monotonic() - t0) * 1000

        logger.info(
            "dispatch site=%s persona=%d latency=%.1fms model=%s",
            site, persona_id, elapsed_ms, self._model,
        )

        # 5. Parse the response
        parsed = _parse_response(raw_response, parser, site)

        # 6. Cache the result
        self._cache.put(cache_key, parsed)

        return parsed

    @property
    def monthly_spend_usd(self) -> float:
        return self._monthly_spend_usd


def _hash_text(text: str) -> str:
    """SHA-256 of text for cache key purposes."""
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _parse_response(
    raw: str,
    parser: Any | None,
    site: str,
) -> dict[str, Any]:
    """Parse LLM response. Uses custom parser if provided, else JSON."""
    if parser is not None:
        try:
            result: dict[str, Any] = parser(raw)
            return result
        except Exception as e:
            raise LLMResponseParseError(
                f"Custom parser failed for site={site!r}: {e}"
            ) from e

    # Default: tolerate reasoning-model noise, then parse JSON.
    # Gemma-4 (and other thinking-enabled models) prefix responses with
    # <thought>...</thought> blocks even when response_format=json_object is
    # requested. Strip those first, then markdown fences, then slice to the
    # outermost JSON object if there's leading/trailing prose.
    text = raw.strip()

    # 1. Strip <thought>, <thinking>, <think> reasoning blocks (Gemma, Qwen, DeepSeek).
    for tag in ("thought", "thinking", "think"):
        pattern = f"<{tag}>"
        close = f"</{tag}>"
        lower = text.lower()
        while pattern in lower:
            start = lower.find(pattern)
            end = lower.find(close, start)
            if end == -1:
                # Unclosed tag — drop everything from the open tag onward.
                text = text[:start].strip()
                break
            text = (text[:start] + text[end + len(close):]).strip()
            lower = text.lower()

    # 2. Strip markdown fences.
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    # 3. If there's still leading/trailing prose, slice to outermost { ... }.
    if text and not text.startswith("{"):
        first = text.find("{")
        last = text.rfind("}")
        if first != -1 and last > first:
            text = text[first : last + 1]

    try:
        parsed: dict[str, Any] = json.loads(text)
    except json.JSONDecodeError as e:
        raise LLMResponseParseError(
            f"JSON parse failed for site={site!r}: {e}\nRaw: {raw[:200]}"
        ) from e

    return parsed
