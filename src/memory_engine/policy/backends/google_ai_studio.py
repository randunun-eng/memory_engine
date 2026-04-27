"""Google AI Studio OpenAI-compatible backend for PolicyDispatch.

Serves Gemini and Gemma model families through the same endpoint:
  https://generativelanguage.googleapis.com/v1beta/openai/chat/completions

Default model is `gemma-4-31b-it` for the consolidator path. Gemma-family
quota is a separate pool from `gemini-2.5-flash` on the free tier, so this
call site does not contend with twin-agent's drafting budget. See DRIFT
entries `consolidator-gemma-4-baseline-invalidated` and
`consolidator-ai-studio-shared-key`.

The limiter shape is ported from twincore-alpha/twin-agent/main.py; this
is an independent instance with its own window.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque

import httpx

logger = logging.getLogger(__name__)


class AIStudioRateLimiter:
    """Sliding 60s-window limiter. Cap one slot below the free-tier ceiling."""

    def __init__(self, max_rpm: int, warn_rpm: int) -> None:
        self.max_rpm = max_rpm
        self.warn_rpm = warn_rpm
        self._window: deque[float] = deque()
        self._lock = asyncio.Lock()
        self._server_cooldown_until: float = 0.0

    def _prune(self, now: float) -> None:
        cutoff = now - 60.0
        while self._window and self._window[0] < cutoff:
            self._window.popleft()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                self._prune(now)

                if now < self._server_cooldown_until:
                    wait = self._server_cooldown_until - now
                    logger.warning(
                        "ai_studio[consolidator] server cooldown active, sleeping %.2fs",
                        wait,
                    )
                    await asyncio.sleep(wait)
                    continue

                in_window = len(self._window)
                if in_window >= self.max_rpm:
                    wait = 60.0 - (now - self._window[0]) + 0.05
                    logger.warning(
                        "ai_studio[consolidator] at cap %d/%d RPM, sleeping %.2fs",
                        in_window,
                        self.max_rpm,
                        wait,
                    )
                    await asyncio.sleep(max(wait, 0.1))
                    continue

                if in_window >= self.warn_rpm:
                    logger.info(
                        "ai_studio[consolidator] approaching cap %d/%d RPM",
                        in_window,
                        self.max_rpm,
                    )

                self._window.append(now)
                return

    def notify_429(self, retry_after_sec: float | None) -> None:
        wait = 5.0 if retry_after_sec is None else retry_after_sec
        wait = max(1.0, min(wait, 60.0))
        self._server_cooldown_until = time.monotonic() + wait
        logger.warning("ai_studio[consolidator] 429 observed, backing off %.1fs", wait)


# Per-token pricing for cost estimation (USD per token, premium-tier as
# observed in actual GCP billing 2026-04). Update if your tier differs.
# Falls back to flash pricing for unknown models.
GEMINI_PRICING: dict[str, tuple[float, float]] = {
    # model: (input_per_token, output_per_token)
    "gemini-2.5-flash": (0.000_000_075, 0.000_002_500),
    "gemini-2.5-flash-lite": (0.000_000_040, 0.000_000_150),
    "gemini-2.5-pro": (0.000_001_250, 0.000_010_000),
    "gemini-3-flash": (0.000_000_075, 0.000_002_500),
    "gemini-3.1-flash-lite": (0.000_000_040, 0.000_000_150),
    "gemma-4-31b-it": (0.000_000_075, 0.000_002_500),  # treated as Flash-tier
}


def _estimate_cost_usd(model: str, usage: dict[str, int]) -> float:
    """Estimate USD cost from a Gemini API response's `usage` block."""
    inp_per, out_per = GEMINI_PRICING.get(model, GEMINI_PRICING["gemini-2.5-flash"])
    # OpenAI-compat field names: prompt_tokens, completion_tokens
    inp = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    out = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
    return inp * inp_per + out * out_per


class BudgetExceededError(RuntimeError):
    """Raised when the configured monthly LLM budget is exhausted."""


class GoogleAIStudioBackend:
    """Async callable matching PolicyDispatch's LLMBackend signature.

    Tracks estimated USD spend per request via the response's `usage`
    block. Exposes:
      - .total_cost_usd    : cumulative cost since process start
      - .calls             : total successful API calls
      - .max_output_tokens : per-call output cap (default 1024)

    The dispatch layer reads .total_cost_usd to enforce monthly_budget_usd.
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai",
        max_rpm: int = 6,
        warn_rpm: int = 4,
        timeout_s: float = 120.0,
        max_output_tokens: int = 1024,
        service_tier: str | None = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._limiter = AIStudioRateLimiter(max_rpm=max_rpm, warn_rpm=warn_rpm)
        self._client = httpx.AsyncClient(timeout=timeout_s)
        self.max_output_tokens = max_output_tokens
        # Service tier: None=Standard (full price), "flex"=50% off
        # (best-effort, sheddable; 1-15 min latency target). Flex is right
        # for background work (consolidator extraction, grounding judge).
        # Use Standard for user-facing paths (twin-agent drafts).
        self.service_tier = service_tier
        # Cost discount factor applied when reporting back. Gemini bills
        # at the same SKU price; only the post-call billing reconciles
        # the 50% Flex discount. Track at standard rates to stay
        # conservative on the budget cap.
        self.total_cost_usd: float = 0.0
        self.calls: int = 0

    async def __call__(self, model: str, prompt: str, temperature: float) -> str:
        # Strip "gemini/" or "google/" prefix if a routing alias slipped through.
        if "/" in model:
            model = model.split("/", 1)[1]

        # Single retry on 5xx — AI Studio returns transient 503s under load.
        # 429s are NOT retried here; they flow through the rate limiter.
        last_exc: Exception | None = None
        for attempt in range(2):
            await self._limiter.acquire()
            try:
                r = await self._client.post(
                    f"{self._base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json; charset=utf-8",
                    },
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": temperature,
                        "response_format": {"type": "json_object"},
                        "max_tokens": self.max_output_tokens,
                        **({"service_tier": self.service_tier} if self.service_tier else {}),
                    },
                )
            except httpx.RequestError as e:
                last_exc = e
                if attempt == 0:
                    logger.warning("ai_studio network error %r, retrying once", e)
                    await asyncio.sleep(2.0)
                    continue
                raise
            if r.status_code == 429:
                retry_after_raw = r.headers.get("retry-after")
                retry_after: float | None = None
                if retry_after_raw:
                    try:
                        retry_after = float(retry_after_raw)
                    except ValueError:
                        retry_after = None
                self._limiter.notify_429(retry_after)
                raise RuntimeError(f"ai_studio 429 rate-limited body={r.text[:200]!r}")
            if 500 <= r.status_code < 600 and attempt == 0:
                logger.warning(
                    "ai_studio %d transient, retrying once: %s",
                    r.status_code,
                    r.text[:200],
                )
                await asyncio.sleep(2.0)
                continue
            r.raise_for_status()
            break
        else:
            if last_exc is not None:
                raise last_exc
        data = r.json()
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError(f"ai_studio returned no choices: {str(data)[:200]!r}")
        content: str = choices[0].get("message", {}).get("content", "").strip()
        if not content:
            raise RuntimeError("ai_studio returned empty content")

        # Cost accounting. The OpenAI-compat endpoint returns usage with
        # prompt_tokens / completion_tokens. Fall back to 0 if absent.
        usage = data.get("usage") or {}
        cost = _estimate_cost_usd(model, usage)
        self.total_cost_usd += cost
        self.calls += 1
        if self.calls % 100 == 1 or cost > 0.01:
            logger.info(
                "ai_studio cost: this_call=$%.4f total=$%.2f calls=%d (in=%s out=%s model=%s)",
                cost,
                self.total_cost_usd,
                self.calls,
                usage.get("prompt_tokens", "?"),
                usage.get("completion_tokens", "?"),
                model,
            )
        return content

    async def aclose(self) -> None:
        await self._client.aclose()
