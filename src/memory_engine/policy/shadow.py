"""Prompt shadow harness + comparison batch.

A **shadow prompt** runs alongside the active prompt on a configurable
fraction of events. Both outputs are captured; the active one is used;
the comparison is logged for offline analysis.

Flow:
    active = get_active(site)
    shadow = get_shadow(site)
    if shadow and random() < shadow.shadow_traffic_pct:
        active_out = llm(active.template, inputs)
        shadow_out = llm(shadow.template, inputs)
        log_shadow_comparison(...)
        return active_out
    else:
        return llm(active.template, inputs)

Guardrails (see blueprint §3.9):
  - Only shadow prompts can have active=False and shadow_traffic_pct > 0.
  - Promotion requires CLI command; never automatic.
  - Every promotion writes a 'prompt_promoted' event.

This module provides the harness (dispatch-time logic) and the comparison
batch (daily aggregation of shadow logs into prompt_comparison_daily).
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    import aiosqlite

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ShadowResult:
    """Result of a shadow-aware prompt execution."""

    active_output: Any
    shadow_output: Any | None       # None if no shadow ran
    active_template_id: int
    shadow_template_id: int | None
    logged: bool                     # True if a shadow comparison was persisted


async def get_active_template(
    conn: aiosqlite.Connection,
    site: str,
) -> tuple[int, str] | None:
    """Return (template_id, template_text) for the active prompt at site."""
    cursor = await conn.execute(
        """
        SELECT id, template_text FROM prompt_templates
        WHERE site = ? AND active = 1
        """,
        (site,),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    return int(row["id"]), row["template_text"]


async def get_shadow_template(
    conn: aiosqlite.Connection,
    site: str,
) -> tuple[int, str, float] | None:
    """Return (template_id, template_text, traffic_pct) for shadow at site.

    Returns None if no shadow is configured or traffic_pct is 0.
    """
    cursor = await conn.execute(
        """
        SELECT id, template_text, shadow_traffic_pct FROM prompt_templates
        WHERE site = ? AND shadow = 1 AND active = 0 AND shadow_traffic_pct > 0
        LIMIT 1
        """,
        (site,),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    return int(row["id"]), row["template_text"], float(row["shadow_traffic_pct"])


def _input_hash(inputs: dict[str, Any]) -> str:
    """Stable hash of prompt inputs for dedup analysis."""
    canonical = json.dumps(inputs, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


async def dispatch_with_shadow(
    conn: aiosqlite.Connection,
    *,
    persona_id: int,
    site: str,
    inputs: dict[str, Any],
    llm_fn: Callable[[str, dict[str, Any]], Any],
    rng: Callable[[], float] | None = None,
) -> ShadowResult:
    """Execute a prompt with shadow comparison when configured.

    Args:
        conn: DB connection for template lookup and shadow logging.
        persona_id: Persona context (for per-persona shadow analysis).
        site: Prompt site name.
        inputs: Prompt input variables.
        llm_fn: Synchronous function (template_text, inputs) -> output.
            Must be deterministic for cache keying; caller handles async if
            needed by wrapping.
        rng: Optional random source [0.0, 1.0). Defaults to secrets.randbelow.

    Returns:
        ShadowResult with active output (used by caller) and optional shadow.
    """
    active = await get_active_template(conn, site)
    if active is None:
        msg = f"No active prompt template for site {site!r}"
        raise ValueError(msg)

    active_id, active_text = active
    shadow = await get_shadow_template(conn, site)

    # No shadow → just run active
    if shadow is None:
        t0 = time.perf_counter()
        out = llm_fn(active_text, inputs)
        _ = (time.perf_counter() - t0) * 1000  # latency (for future metric)
        return ShadowResult(
            active_output=out,
            shadow_output=None,
            active_template_id=active_id,
            shadow_template_id=None,
            logged=False,
        )

    shadow_id, shadow_text, traffic_pct = shadow

    # Decide whether to run shadow this call
    draw = rng() if rng is not None else (secrets.randbelow(10_000) / 10_000.0)
    if draw >= traffic_pct:
        # Just run active
        out = llm_fn(active_text, inputs)
        return ShadowResult(
            active_output=out,
            shadow_output=None,
            active_template_id=active_id,
            shadow_template_id=shadow_id,
            logged=False,
        )

    # Run both
    t0 = time.perf_counter()
    active_out = llm_fn(active_text, inputs)
    active_latency_ms = int((time.perf_counter() - t0) * 1000)

    t1 = time.perf_counter()
    shadow_out = llm_fn(shadow_text, inputs)
    shadow_latency_ms = int((time.perf_counter() - t1) * 1000)

    # Log comparison (active output is what the caller uses)
    await _log_shadow_comparison(
        conn,
        persona_id=persona_id,
        site=site,
        active_id=active_id,
        shadow_id=shadow_id,
        inputs=inputs,
        active_out=active_out,
        shadow_out=shadow_out,
        active_latency_ms=active_latency_ms,
        shadow_latency_ms=shadow_latency_ms,
    )

    return ShadowResult(
        active_output=active_out,
        shadow_output=shadow_out,
        active_template_id=active_id,
        shadow_template_id=shadow_id,
        logged=True,
    )


async def _log_shadow_comparison(
    conn: aiosqlite.Connection,
    *,
    persona_id: int,
    site: str,
    active_id: int,
    shadow_id: int,
    inputs: dict[str, Any],
    active_out: Any,
    shadow_out: Any,
    active_latency_ms: int,
    shadow_latency_ms: int,
    active_cost_usd: float = 0.0,
    shadow_cost_usd: float = 0.0,
) -> None:
    """Persist a shadow comparison row."""
    await conn.execute(
        """
        INSERT INTO prompt_shadow_logs
            (persona_id, site, active_template_id, shadow_template_id,
             input_hash, active_output, shadow_output,
             active_latency_ms, shadow_latency_ms,
             active_cost_usd, shadow_cost_usd)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            persona_id, site, active_id, shadow_id,
            _input_hash(inputs),
            json.dumps(active_out, default=str),
            json.dumps(shadow_out, default=str),
            active_latency_ms, shadow_latency_ms,
            active_cost_usd, shadow_cost_usd,
        ),
    )
    await conn.commit()


# ---- Comparison batch ----


async def compute_daily_comparison(
    conn: aiosqlite.Connection,
    *,
    day: str,
) -> list[dict[str, Any]]:
    """Compute and persist daily comparison metrics from shadow logs.

    For each (site, active_template_id, shadow_template_id) group in the
    given day, computes:
      - sample_count
      - active_mean_latency_ms, shadow_mean_latency_ms
      - active_mean_cost_usd, shadow_mean_cost_usd
      - output_agreement_rate (fraction of calls where outputs matched exactly)

    Args:
        conn: DB connection.
        day: 'YYYY-MM-DD' UTC date to aggregate.

    Returns:
        List of dicts with per-group metrics (also persisted to
        prompt_comparison_daily).
    """
    cursor = await conn.execute(
        """
        SELECT site, active_template_id, shadow_template_id,
               active_output, shadow_output,
               active_latency_ms, shadow_latency_ms,
               active_cost_usd, shadow_cost_usd
        FROM prompt_shadow_logs
        WHERE date(recorded_at) = ?
        """,
        (day,),
    )
    rows = await cursor.fetchall()

    # Group by (site, active_id, shadow_id)
    groups: dict[tuple[str, int, int], list[dict[str, Any]]] = {}
    for row in rows:
        key = (row["site"], row["active_template_id"], row["shadow_template_id"])
        groups.setdefault(key, []).append({
            "active_output": row["active_output"],
            "shadow_output": row["shadow_output"],
            "active_latency_ms": row["active_latency_ms"],
            "shadow_latency_ms": row["shadow_latency_ms"],
            "active_cost_usd": row["active_cost_usd"],
            "shadow_cost_usd": row["shadow_cost_usd"],
        })

    results: list[dict[str, Any]] = []
    for (site, active_id, shadow_id), samples in groups.items():
        n = len(samples)
        active_lat = sum(s["active_latency_ms"] for s in samples) / n
        shadow_lat = sum(s["shadow_latency_ms"] for s in samples) / n
        active_cost = sum(s["active_cost_usd"] for s in samples) / n
        shadow_cost = sum(s["shadow_cost_usd"] for s in samples) / n
        agreement = sum(
            1 for s in samples if s["active_output"] == s["shadow_output"]
        ) / n

        metrics = {
            "sample_count": n,
            "active_mean_latency_ms": active_lat,
            "shadow_mean_latency_ms": shadow_lat,
            "active_mean_cost_usd": active_cost,
            "shadow_mean_cost_usd": shadow_cost,
            "output_agreement_rate": agreement,
        }

        await conn.execute(
            """
            INSERT INTO prompt_comparison_daily
                (day, site, active_template_id, shadow_template_id,
                 sample_count, metrics_json)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (day, site, active_template_id, shadow_template_id)
            DO UPDATE SET
                sample_count = excluded.sample_count,
                metrics_json = excluded.metrics_json,
                computed_at = datetime('now')
            """,
            (day, site, active_id, shadow_id, n, json.dumps(metrics)),
        )

        results.append({
            "day": day,
            "site": site,
            "active_template_id": active_id,
            "shadow_template_id": shadow_id,
            **metrics,
        })

    await conn.commit()
    return results


# ---- Promotion and rollback (CLI surface) ----


async def promote_shadow(
    conn: aiosqlite.Connection,
    *,
    site: str,
    shadow_template_id: int,
) -> None:
    """Promote a shadow prompt to active.

    Deactivates the current active prompt, activates the shadow, clears
    shadow flag. The caller must emit a 'prompt_promoted' event to the
    event log — this function does not write events (needs persona + keypair).
    """
    await conn.execute(
        "UPDATE prompt_templates SET active = 0 WHERE site = ? AND active = 1",
        (site,),
    )
    await conn.execute(
        """
        UPDATE prompt_templates
        SET active = 1, shadow = 0, shadow_traffic_pct = 0
        WHERE id = ?
        """,
        (shadow_template_id,),
    )
    await conn.commit()
    logger.info("Promoted prompt template %d for site %s", shadow_template_id, site)


async def rollback_to_template(
    conn: aiosqlite.Connection,
    *,
    site: str,
    previous_template_id: int,
) -> None:
    """Roll back to a previously-active template.

    Deactivates the current active, reactivates the given previous template.
    Must complete in under 60s per spec. Changes no data, only which
    template is active.
    """
    await conn.execute(
        "UPDATE prompt_templates SET active = 0 WHERE site = ? AND active = 1",
        (site,),
    )
    await conn.execute(
        "UPDATE prompt_templates SET active = 1 WHERE id = ?",
        (previous_template_id,),
    )
    await conn.commit()
    logger.warning("Rolled back prompt for site %s to template %d", site, previous_template_id)
