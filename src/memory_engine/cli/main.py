"""memory-engine CLI entry point.

Phase 0: db migrate, db status. Later phases add doctor, prompt, heal, etc.
"""

from __future__ import annotations

import asyncio

import click


@click.group()
def main() -> None:
    """memory_engine CLI."""


@main.group()
def db() -> None:
    """Database operations."""


@db.command("migrate")
def db_migrate() -> None:
    """Apply pending migrations."""

    async def _run() -> None:
        from memory_engine.db.connection import connect
        from memory_engine.db.migrations import apply_all

        conn = await connect()
        try:
            applied = await apply_all(conn)
            if applied:
                click.echo(f"Applied: {', '.join(applied)}")
            else:
                click.echo("No pending migrations.")
        finally:
            await conn.close()

    asyncio.run(_run())


@db.command("status")
def db_status() -> None:
    """Show applied migrations."""

    async def _run() -> None:
        from memory_engine.db.connection import connect
        from memory_engine.db.migrations import migration_status

        conn = await connect()
        try:
            rows = await migration_status(conn)
            if not rows:
                click.echo("No migrations applied.")
                return
            for row in rows:
                click.echo(f"  {row['version']:>3}  {row['name']:<40}  {row['applied_at']}")
        finally:
            await conn.close()

    asyncio.run(_run())


@main.group()
def quarantine() -> None:
    """Inspect and act on quarantined neuron candidates."""


@quarantine.command("list")
@click.option("--persona", "persona_id", type=int, help="Filter by persona ID.")
@click.option("--limit", type=int, default=20, help="Max rows to show (default 20).")
@click.option("--reason", type=str, help="Filter by reason (e.g. low_similarity).")
def quarantine_list(persona_id: int | None, limit: int, reason: str | None) -> None:
    """List quarantined candidates newest-first.

    Examples:
        memory-engine quarantine list
        memory-engine quarantine list --persona 1 --limit 50
        memory-engine quarantine list --reason low_similarity
    """
    import json as _json

    async def _run() -> None:
        from memory_engine.db.connection import connect

        conn = await connect()
        try:
            where: list[str] = ["reviewed_at IS NULL"]
            params: list[object] = []
            if persona_id is not None:
                where.append("persona_id = ?")
                params.append(persona_id)
            if reason:
                where.append("reason = ?")
                params.append(reason)
            # `where` is built from hardcoded string literals in this
            # module — no user input reaches the WHERE fragment. All
            # parameters bind via `params` below. Safe per-review.
            where_clause = " AND ".join(where)
            sql = (
                f"SELECT id, persona_id, reason, candidate_json, created_at "  # noqa: S608
                f"FROM quarantine_neurons WHERE {where_clause} ORDER BY id DESC LIMIT ?"
            )
            params.append(limit)
            cursor = await conn.execute(sql, tuple(params))
            rows = await cursor.fetchall()
            if not rows:
                click.echo("(no quarantine entries)")
                return
            click.echo(f"{'id':>6} {'persona':>3} {'reason':<20} {'created_at':<20}  content")
            click.echo("-" * 100)
            for row in rows:
                try:
                    content = _json.loads(row["candidate_json"]).get("content", "")
                except Exception:
                    content = row["candidate_json"][:80]
                click.echo(
                    f"{row['id']:>6} {row['persona_id']:>3} "
                    f"{row['reason']:<20} {row['created_at']:<20}  {content[:80]}"
                )
        finally:
            await conn.close()

    asyncio.run(_run())


@quarantine.command("show")
@click.argument("quarantine_id", type=int)
def quarantine_show(quarantine_id: int) -> None:
    """Show the full candidate JSON + source-event payloads for one entry."""
    import json as _json

    async def _run() -> None:
        from memory_engine.db.connection import connect

        conn = await connect()
        try:
            cursor = await conn.execute(
                """
                SELECT id, persona_id, reason, candidate_json,
                       source_event_ids, created_at, reviewed_at, review_verdict
                FROM quarantine_neurons WHERE id = ?
                """,
                (quarantine_id,),
            )
            row = await cursor.fetchone()
            if not row:
                click.echo(f"No quarantine entry {quarantine_id}")
                return
            click.echo(f"Quarantine #{row['id']} persona={row['persona_id']}")
            click.echo(f"  reason:      {row['reason']}")
            click.echo(f"  created_at:  {row['created_at']}")
            if row["reviewed_at"]:
                click.echo(f"  reviewed_at: {row['reviewed_at']}")
                click.echo(f"  verdict:     {row['review_verdict']}")
            click.echo("  candidate:")
            try:
                cand = _json.loads(row["candidate_json"])
                click.echo(_json.dumps(cand, indent=4, ensure_ascii=False))
            except Exception:
                click.echo(f"    {row['candidate_json']}")
            # Source events
            event_ids = _json.loads(row["source_event_ids"])
            click.echo(f"  source_event_ids: {event_ids}")
            for eid in event_ids:
                ec = await conn.execute("SELECT id, payload FROM events WHERE id = ?", (eid,))
                er = await ec.fetchone()
                if er:
                    try:
                        payload = _json.loads(er["payload"])
                        text = payload.get("text", payload)
                    except Exception:
                        text = er["payload"]
                    click.echo(f"    [event {eid}] {text}")
        finally:
            await conn.close()

    asyncio.run(_run())


@quarantine.command("promote")
@click.argument("quarantine_id", type=int)
@click.option("--tier", type=str, default="working", help="Target tier (default working).")
def quarantine_promote(quarantine_id: int, tier: str) -> None:
    """Accept a quarantined candidate as a real neuron.

    Operator override — use when the grounding gate rejected something
    that's actually a valid claim. Marks the quarantine row as reviewed
    with verdict='promoted'. Writes a real neuron row with the same
    content + source_event_ids, signed by the env owner key.
    """
    import hashlib
    import json as _json
    import os

    async def _run() -> None:
        from memory_engine.db.connection import connect

        conn = await connect()
        try:
            cursor = await conn.execute(
                "SELECT * FROM quarantine_neurons WHERE id = ?", (quarantine_id,)
            )
            row = await cursor.fetchone()
            if not row:
                click.echo(f"No quarantine entry {quarantine_id}")
                return
            if row["reviewed_at"]:
                click.echo(f"Already reviewed ({row['review_verdict']}) at {row['reviewed_at']}")
                return

            cand = _json.loads(row["candidate_json"])
            content = cand["content"]
            kind = cand.get("kind", "counterparty_fact")
            source_event_ids = _json.loads(row["source_event_ids"])

            # Resolve counterparty_id if this is a counterparty_fact.
            counterparty_id = None
            if kind == "counterparty_fact":
                for eid in source_event_ids:
                    ec = await conn.execute(
                        "SELECT counterparty_id FROM events WHERE id = ?", (eid,)
                    )
                    er = await ec.fetchone()
                    if er and er["counterparty_id"] is not None:
                        counterparty_id = int(er["counterparty_id"])
                        break

            content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
            embedder_rev = os.environ.get(
                "MEMORY_ENGINE_EMBEDDER_REV",
                "paraphrase-multilingual-minilm-l12-v2-1",
            )

            ncursor = await conn.execute(
                """
                INSERT INTO neurons
                  (persona_id, counterparty_id, kind, content, content_hash,
                   source_event_ids, source_count, distinct_source_count,
                   tier, embedder_rev)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["persona_id"],
                    counterparty_id,
                    kind,
                    content,
                    content_hash,
                    row["source_event_ids"],
                    len(source_event_ids),
                    len(set(source_event_ids)),
                    tier,
                    embedder_rev,
                ),
            )
            await conn.commit()
            new_neuron_id = ncursor.lastrowid

            await conn.execute(
                "UPDATE quarantine_neurons "
                "SET reviewed_at = datetime('now'), review_verdict = 'promoted' "
                "WHERE id = ?",
                (quarantine_id,),
            )
            await conn.commit()

            click.echo(
                f"Promoted quarantine #{quarantine_id} → neuron #{new_neuron_id} "
                f"(kind={kind}, counterparty_id={counterparty_id}, tier={tier})"
            )
            click.echo(
                "Note: neurons_vec was NOT populated — re-embed via consolidator "
                "run, or add an admin reembed command later."
            )
        finally:
            await conn.close()

    asyncio.run(_run())


@quarantine.command("reject")
@click.argument("quarantine_id", type=int)
@click.option("--note", type=str, default="", help="Free-text reason for the rejection.")
def quarantine_reject(quarantine_id: int, note: str) -> None:
    """Mark a quarantined candidate as reviewed + permanently rejected.

    Doesn't delete the row — the quarantine trail is evidence. Just
    stops it from appearing in future `quarantine list` default views.
    """

    async def _run() -> None:
        from memory_engine.db.connection import connect

        conn = await connect()
        try:
            cursor = await conn.execute(
                "UPDATE quarantine_neurons "
                "SET reviewed_at = datetime('now'), "
                "    review_verdict = ? "
                "WHERE id = ? AND reviewed_at IS NULL",
                (f"rejected:{note}" if note else "rejected", quarantine_id),
            )
            await conn.commit()
            if cursor.rowcount == 0:
                click.echo(f"No pending quarantine entry {quarantine_id}")
                return
            click.echo(f"Quarantine #{quarantine_id} marked rejected.")
        finally:
            await conn.close()

    asyncio.run(_run())


if __name__ == "__main__":
    main()
