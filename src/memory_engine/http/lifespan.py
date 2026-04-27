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
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from memory_engine.core.consolidator import consolidation_pass
from memory_engine.db.connection import connect
from memory_engine.observability.metrics import gauge
from memory_engine.policy.backends.google_ai_studio import GoogleAIStudioBackend
from memory_engine.policy.cache import PromptCache
from memory_engine.policy.dispatch import PolicyDispatch
from memory_engine.policy.registry import PromptRegistry

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from fastapi import FastAPI

logger = logging.getLogger(__name__)

_EMBEDDER_SINGLETON: Any = None


# Default to a MULTILINGUAL MiniLM so Sinhala/Singlish events (the live
# alpha workload) produce meaningful similarities against English
# extractions. MiniLM-L6-v2 is English-only and was rejecting ~80% of
# real Sinhala→English paraphrases as low_similarity. The L12 multilingual
# model is 384-dim (same schema), handles 50+ languages including Sinhala,
# and preserves the per-event-max-similarity gate behaviour. embedder_rev
# changes accordingly — old L6-rev neurons stay separate in neurons_vec
# and will not surface via vector recall until re-embedded.
_EMBEDDER_MODEL = os.environ.get(
    "MEMORY_ENGINE_EMBEDDER_MODEL",
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
)
_EMBEDDER_REV = os.environ.get(
    "MEMORY_ENGINE_EMBEDDER_REV",
    "paraphrase-multilingual-minilm-l12-v2-1",
)


def _load_embedder_sync() -> Any:
    """Load the embedder once. Blocking — call via asyncio.to_thread."""
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(_EMBEDDER_MODEL)


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


async def _resolve_persona_public_key(
    conn: Any,
    persona_id: int,
    fallback: str,
) -> str:
    """Return the per-persona owner_public_key from the DB, falling back
    to the provided default (historically the shared env key).

    Added by migration 008. NULL column means "no per-persona key set" —
    use the fallback. Once operators start populating the column via
    POST /v1/personas or a future /v1/identity/load signature flow,
    the fallback stops being exercised. See DRIFT
    `consolidator-ai-studio-shared-key`.
    """
    try:
        cursor = await conn.execute(
            "SELECT owner_public_key FROM personas WHERE id = ?",
            (persona_id,),
        )
        row = await cursor.fetchone()
    except Exception:
        return fallback
    if row is None:
        return fallback
    key = row["owner_public_key"]
    return str(key) if key else fallback


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


async def _run_integrity_check(conn: Any) -> tuple[bool, str]:
    """Run PRAGMA integrity_check. Returns (ok, detail).

    'ok' means result is literally 'ok'. Anything else (malformed page,
    orphan row, wrong index entry count) is treated as corruption — the
    loop halts and surfaces the error via the integrity metric so an
    operator can run `.recover` before the next write compounds the damage.
    """
    try:
        cursor = await conn.execute("PRAGMA integrity_check")
        rows = await cursor.fetchall()
    except Exception as e:
        return False, f"integrity_check raised: {type(e).__name__}: {e}"
    if not rows:
        return False, "integrity_check returned no rows"
    first = rows[0][0] if rows[0] else None
    if first == "ok":
        return True, "ok"
    return False, "; ".join(str(r[0]) for r in rows[:5])


async def _consolidation_loop(
    dispatch: PolicyDispatch,
    embed_fn: Callable[[str], list[float]],
    private_key: bytes,
    public_key_b64: str,
    interval_s: float,
    similarity_threshold: float,
    integrity_check_every: int = 10,
) -> None:
    """Per-persona consolidation loop.

    Every `integrity_check_every` ticks, run `PRAGMA integrity_check` against
    the DB before extraction runs. On failure, halt the loop — continuing
    would cause cascading corruption across neurons/consolidation_log/vec tables.
    Gauge `wiki_v3_db_integrity_ok` tracks the most-recent result (1/0) so an
    alert rule can wake an operator.
    """
    logger.info(
        "consolidator loop started: interval=%.1fs integrity_check_every=%d",
        interval_s,
        integrity_check_every,
    )
    tick = 0
    while True:
        tick += 1
        try:
            conn = await connect()
            try:
                # Integrity watchdog — every Nth tick plus always on tick 1.
                if tick == 1 or tick % integrity_check_every == 0:
                    ok, detail = await _run_integrity_check(conn)
                    gauge(
                        "wiki_v3_db_integrity_ok",
                        help_text="1 if the most recent PRAGMA integrity_check returned 'ok', 0 otherwise",
                    ).set(1.0 if ok else 0.0)
                    if not ok:
                        logger.error(
                            "INTEGRITY CHECK FAILED tick=%d detail=%s — halting consolidator loop",
                            tick,
                            detail,
                        )
                        # Stay in the loop so the gauge keeps at 0; do NOT
                        # run consolidation_pass (could corrupt further).
                        await asyncio.sleep(interval_s)
                        continue
                    logger.info("integrity check OK tick=%d", tick)

                personas = await _list_personas(conn)
                for persona_id in personas:
                    t0 = time.monotonic()
                    # Per-persona owner public key (migration 008) — falls
                    # back to the env-provided key when column is NULL.
                    persona_pub = await _resolve_persona_public_key(
                        conn,
                        persona_id,
                        public_key_b64,
                    )
                    stats = await consolidation_pass(
                        conn,
                        dispatch,
                        persona_id,
                        private_key,
                        persona_pub,
                        embedder_rev=_EMBEDDER_REV,
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
    logger.info("consolidator_lifespan entered")
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
    # Grounding threshold — empirically re-measured 2026-04-20 on the
    # 50-fixture test set with the current stack (multilingual MiniLM-L12
    # + per-event-max similarity gate). Sweep results:
    #   0.25 → 60% acc (accepts everything, below noise floor)
    #   0.50 → 72% acc (matches Phase 2 BoW baseline)
    #   0.60 → 88% acc ← optimum (+16pp over Phase 2 BoW)
    #   0.70 → 82% acc (over-filters; recall 83%)
    # Grounded/ungrounded mean sim: 0.82 / 0.57 (clean 0.25 separation).
    # Override via MEMORY_ENGINE_CONSOLIDATOR_SIMILARITY_THRESHOLD.
    similarity_threshold = float(
        os.environ.get("MEMORY_ENGINE_CONSOLIDATOR_SIMILARITY_THRESHOLD", "0.60")
    )

    task: asyncio.Task[None] | None = None
    backend: GoogleAIStudioBackend | None = None

    logger.info(
        "env check: GEMINI_API_KEY=%s PRIV=%s PUB=%s",
        bool(api_key),
        private_key is not None,
        bool(public_key_b64),
    )
    if not api_key:
        logger.warning("consolidator: GEMINI_API_KEY not set; consolidation loop disabled")
    elif private_key is None or not public_key_b64:
        logger.warning(
            "consolidator: MEMORY_ENGINE_CONSOLIDATOR_PRIVATE_KEY_B64 / "
            "_PUBLIC_KEY_B64 missing; consolidation loop disabled "
            "(ingest/recall still serve)"
        )
    else:
        logger.info("env OK — starting consolidator loop")
        registry = PromptRegistry()
        registry.load_from_directory()
        # Increased cache size — was 256 default, but the consolidator
        # produces ~16 events per tick * 1440 ticks/day = 23K unique
        # input hashes/day. 256 churns instantly. 4096 covers ~3-4 hours
        # of typical traffic, so reruns of the same batch (after a
        # restart, or when grounding gate rejects then re-asks) hit cache.
        cache = PromptCache(max_size=4096)
        # Cap per-call output to bound cost (default 1024 tokens). Set
        # via env if you need richer extractions.
        max_output = int(os.environ.get("MEMORY_ENGINE_LLM_MAX_OUTPUT_TOKENS", "1024"))
        # Consolidator runs in the background (60s tick, sheddable mins
        # of latency are fine). Flex tier = 50% discount, occasional
        # 429-shed on capacity pressure (we already retry). Set tier=
        # standard for user-facing paths.
        service_tier = os.environ.get("MEMORY_ENGINE_LLM_SERVICE_TIER", "flex") or None
        backend = GoogleAIStudioBackend(
            api_key=api_key,
            max_rpm=max_rpm,
            warn_rpm=warn_rpm,
            max_output_tokens=max_output,
            service_tier=service_tier,
        )
        # Hard monthly budget — when exceeded, dispatch raises
        # BudgetExceeded and consolidator skips the tick (next tick
        # tries again, which still fails until cumulative cost is
        # reset, e.g. by restart). Default $10/month for memory_engine
        # alone. After the GCP $251 spike, this is a guardrail.
        budget = float(os.environ.get("MEMORY_ENGINE_LLM_MONTHLY_BUDGET_USD", "10"))
        dispatch = PolicyDispatch(
            registry=registry,
            llm_backend=backend,
            cache=cache,
            model=model,
            monthly_budget_usd=budget,
        )
        embedder = await _get_embedder()
        embed_fn = _build_embed_fn(embedder)
        # Share with HTTP routes (recall embeds queries with the same model).
        app.state.embed_fn = embed_fn
        app.state.embedder_rev = _EMBEDDER_REV

        task = asyncio.create_task(
            _consolidation_loop(
                dispatch,
                embed_fn,
                private_key,
                public_key_b64,
                interval_s,
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
