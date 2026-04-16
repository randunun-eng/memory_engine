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


if __name__ == "__main__":
    main()
