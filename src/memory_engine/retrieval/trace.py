"""Async retrieval_trace emission.

Every recall() emits a trace event. This is a write but happens after
results are returned and does not block the caller (rule 7).
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

from memory_engine.core.events import append_event, compute_content_hash
from memory_engine.policy.signing import canonical_signing_message, sign

logger = logging.getLogger(__name__)


async def emit_trace_async(
    conn_factory: Callable[[], Coroutine[Any, Any, Any]],
    persona_id: int,
    query: str,
    lens: str,
    top_neuron_ids: list[int],
    latency_ms: int,
    private_key: bytes,
    public_key_b64: str,
) -> None:
    """Enqueue a retrieval_trace event. Does not block the caller.

    Uses asyncio.create_task on a fresh connection so the caller returns
    immediately. Phase 6 replaces this with a bounded queue.
    """

    async def _write() -> None:
        try:
            conn = await conn_factory()
            try:
                payload = {
                    "query": query,
                    "lens": lens,
                    "top_neuron_ids": top_neuron_ids,
                    "latency_ms": latency_ms,
                }
                content_hash = compute_content_hash(payload)
                message = canonical_signing_message(persona_id, content_hash)
                signature = sign(private_key, message)

                await append_event(
                    conn,
                    persona_id=persona_id,
                    counterparty_id=None,
                    event_type="retrieval_trace",
                    scope="private",
                    payload=payload,
                    signature=signature,
                    public_key_b64=public_key_b64,
                )
            finally:
                await conn.close()
        except Exception:
            logger.warning("Failed to emit retrieval trace", exc_info=True)

    _background_tasks: set[asyncio.Task[None]] = set()
    task = asyncio.create_task(_write())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
