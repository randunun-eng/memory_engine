"""FastAPI lifespan for the consolidator background loop.

Phase 7 P0 #4 — without this, `/v1/ingest` collects events and `/v1/recall`
returns empty because events never become neurons. See DRIFT entry
`consolidator-not-scheduled-in-http-serve`.

One task per persona, cadence = `MEMORY_ENGINE_CONSOLIDATOR_INTERVAL_S`
(default 60). On each tick we run `consolidation_pass()` and update the
`wiki_v3_consolidator_lag_seconds` gauge with the elapsed time since the
oldest unconsolidated event was recorded.

Embedder is `sentence-transformers/all-MiniLM-L6-v2`, loaded once at
startup and shared across personas. LLM is Google AI Studio (default
`gemma-4-31b-it`), sharing `GEMINI_API_KEY` with twin-agent but hitting
a different quota pool.

Signing keys for rule-8 neuron-mutation events come from env
(`MEMORY_ENGINE_CONSOLIDATOR_PRIVATE_KEY_B64` and `_PUBLIC_KEY_B64`). Same
pair is used across personas — see DRIFT
`consolidator-ai-studio-shared-key`. If either env var is missing, the
loop is disabled; ingest/recall still serve, but nothing consolidates.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import os
import time
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI

from memory_engine.core.consolidator import consolidation_pass
from memory_engine.db.connection import connect
from memory_engine.observability.metrics import gauge
from memory_engine.policy.backends.google_ai_studio import GoogleAIStudioBackend
from memory_engine.policy.cache import PromptCache
from memory_engine.policy.dispatch import PolicyDispatch
from memory_engine.policy.registry import PromptRegistry

logger = logging.getLogger(__name__)

_EMBEDDER_SINGLETON: Any = None


def _load_embedder_sync() -> Any:
    """Load MiniLM once. Blocking — call via asyncio.to_thread."""
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")


async def _get_embedder() -> Any:
    global _EMBEDDER_SINGLETON
    if _EMBEDDER_SINGLETON is None:
        logger.info("consolidator: loading MiniLM embedder (first-run may fetch weights)")
        _EMBEDDER_SINGLETON = await asyncio.to_thread(_load_embedder_sync)
        logger.info("consolidator: MiniLM embedder ready")
    return _EMBEDDER_SINGLETON


def _build_embed_fn(model: Any) -> Callable[[str], list[float]]:
    def embed(text: str) -> list[float]:
        vec = model.encode(text, normalize_embeddings=True)
        return vec.tolist() if hasattr(vec, "tolist") else list(vec)

    return embed


def _decode_env_key(name: str) -> bytes | None:
    raw = os.environ.get(name)
    if not raw:
        return None
    try:
        return base64.b64decode(raw)
    except Exception:
        logger.error("consolidator: env %s is not valid base64; disabling loop", name)
        return None


async def _list_personas(conn: Any) -> list[int]:
    cursor = await conn.execute("SELECT id FROM personas ORDER BY id")
    rows = await cursor.fetchall()
    return [int(r["id"]) for r in rows]


async def _update_lag_gauge(conn: Any, persona_id: int) -> None:
    cursor = await conn.execute(
        """
        SELECT MIN(e.recorded_at) AS oldest
        FROM events e
        LEFT JOIN working_memory wm
          ON wm.event_id = e.id AND wm.persona_id = e.persona_id
        WHERE e.persona_id = ?
          AND e.type IN ('message_in', 'message_out')
          AND wm.id IS NULL
        """,
        (persona_id,),
    )
    row = await cursor.fetchone()
    oldest = row["oldest"] if row else None
    lag = 0.0
    if oldest:
        try:
            oldest_dt = datetime.fromisoformat(oldest).replace(tzinfo=UTC)
            lag = max(0.0, (datetime.now(tz=UTC) - oldest_dt).total_seconds())
        except ValueError:
            lag = 0.0
    gauge(
        "wiki_v3_consolidator_lag_seconds",
        {"persona": str(persona_id)},
        help_text="Seconds since the oldest unconsolidated event was recorded",
    ).set(lag)


async def _consolidation_loop(
    dispatch: PolicyDispatch,
    embed_fn: Callable[[str], list[float]],
    private_key: bytes,
    public_key_b64: str,
    interval_s: float,
    similarity_threshold: float,
) -> None:
    logger.info("consolidator loop started: interval=%.1fs", interval_s)
    while True:
        try:
            conn = await connect()
            try:
                personas = await _list_personas(conn)
                for persona_id in personas:
                    t0 = time.monotonic()
                    stats = await consolidation_pass(
                        conn,
                        dispatch,
                        persona_id,
                        private_key,
                        public_key_b64,
                        embed_fn=embed_fn,
                        similarity_threshold=similarity_threshold,
                    )
                    await _update_lag_gauge(conn, persona_id)
                    logger.info(
                        "consolidator persona=%d elapsed=%.2fs stats=%s",
                        persona_id,
                        time.monotonic() - t0,
                        json.dumps(stats.__dict__),
                    )
            finally:
                await conn.close()
        except asyncio.CancelledError:
            logger.info("consolidator loop cancelled")
            raise
        except Exception:
            logger.exception("consolidator loop tick failed; continuing")

        await asyncio.sleep(interval_s)


@contextlib.asynccontextmanager
async def consolidator_lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Start a single background consolidation loop on app startup.

    The loop iterates personas inside each tick, so adding a new persona
    while serving just picks it up on the next cycle — no restart needed.
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    private_key = _decode_env_key("MEMORY_ENGINE_CONSOLIDATOR_PRIVATE_KEY_B64")
    public_key_b64 = os.environ.get("MEMORY_ENGINE_CONSOLIDATOR_PUBLIC_KEY_B64")
    interval_s = float(os.environ.get("MEMORY_ENGINE_CONSOLIDATOR_INTERVAL_S", "60"))
    # Default is gemini-2.5-flash, not a Gemma reasoning model. Gemma-4-31b-it
    # was tried first (separate quota pool from twin-agent) but live testing
    # showed extraction calls exceeding AI Studio's upstream timeout (HTTP 499)
    # because reasoning models emit multi-second <thought> blocks before the
    # JSON payload, and the 16-event batched prompt pushes the total response
    # over the edge. gemini-2.5-flash returns clean JSON in under a second at
    # the cost of sharing twin-agent's 15 RPM free-tier pool — the combined
    # cap is 9+6=15, fitting exactly. Raise cap if you upgrade tier.
    model = os.environ.get("MEMORY_ENGINE_CONSOLIDATOR_MODEL", "gemini-2.5-flash")
    max_rpm = int(os.environ.get("MEMORY_ENGINE_CONSOLIDATOR_MAX_RPM", "6"))
    warn_rpm = int(os.environ.get("MEMORY_ENGINE_CONSOLIDATOR_WARN_RPM", "4"))
    # Grounding threshold — the gate now uses per-event max similarity
    # (see grounding.py step 2), so Phase 2's 0.40 baseline is the right
    # default: valid single-fact paraphrases should beat their best source
    # event above 0.40 with MiniLM embeddings. DRIFT entry
    # `grounding-concat-over-citation` captures the per-event switch.
    similarity_threshold = float(
        os.environ.get("MEMORY_ENGINE_CONSOLIDATOR_SIMILARITY_THRESHOLD", "0.40")
    )

    task: asyncio.Task[None] | None = None
    backend: GoogleAIStudioBackend | None = None

    if not api_key:
        logger.warning(
            "consolidator: GEMINI_API_KEY not set; consolidation loop disabled"
        )
    elif private_key is None or not public_key_b64:
        logger.warning(
            "consolidator: MEMORY_ENGINE_CONSOLIDATOR_PRIVATE_KEY_B64 / "
            "_PUBLIC_KEY_B64 missing; consolidation loop disabled "
            "(ingest/recall still serve)"
        )
    else:
        registry = PromptRegistry()
        registry.load_from_directory()
        cache = PromptCache()
        backend = GoogleAIStudioBackend(
            api_key=api_key, max_rpm=max_rpm, warn_rpm=warn_rpm,
        )
        dispatch = PolicyDispatch(
            registry=registry, llm_backend=backend, cache=cache, model=model,
        )
        embedder = await _get_embedder()
        embed_fn = _build_embed_fn(embedder)

        task = asyncio.create_task(
            _consolidation_loop(
                dispatch, embed_fn, private_key, public_key_b64, interval_s,
                similarity_threshold,
            ),
            name="consolidator-loop",
        )

    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if backend is not None:
            await backend.aclose()
