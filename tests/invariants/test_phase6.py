"""Phase 6 invariant tests — operational guarantees.

Verifies:
  - Every alert rule has a corresponding runbook file (alerts-without-runbooks forbidden per spec).
  - Every dashboard JSON parses (operators can import them).
  - Prompt promotion is auditable (shadow → active transition is irreversible without explicit rollback).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    import aiosqlite

from memory_engine.policy.shadow import promote_shadow, rollback_to_template

REPO_ROOT = Path(__file__).resolve().parents[2]


# ---- Alert rule / runbook coverage ----


def test_every_alert_has_a_runbook() -> None:
    """Spec §1.5: alerts without runbooks are forbidden."""
    alerts_yaml = REPO_ROOT / "dashboards" / "alerts.yaml"
    assert alerts_yaml.exists(), "alerts.yaml must exist"

    with alerts_yaml.open() as f:
        data = yaml.safe_load(f)

    missing: list[tuple[str, str]] = []
    for group in data.get("groups", []):
        for rule in group.get("rules", []):
            name = rule.get("alert")
            runbook = rule.get("annotations", {}).get("runbook")
            if not runbook:
                missing.append((name, "no runbook field"))
                continue

            runbook_path = REPO_ROOT / runbook
            if not runbook_path.exists():
                missing.append((name, f"runbook file not found: {runbook_path}"))

    assert not missing, f"Alerts missing runbooks: {missing}"


def test_every_alert_has_severity() -> None:
    """Every alert must declare severity (critical or warning)."""
    alerts_yaml = REPO_ROOT / "dashboards" / "alerts.yaml"
    with alerts_yaml.open() as f:
        data = yaml.safe_load(f)

    missing: list[str] = []
    for group in data.get("groups", []):
        for rule in group.get("rules", []):
            name = rule.get("alert")
            sev = rule.get("labels", {}).get("severity")
            if sev not in ("critical", "warning"):
                missing.append(name)

    assert not missing, f"Alerts missing severity: {missing}"


# ---- Dashboard coverage ----


def test_dashboards_are_valid_json() -> None:
    """Every dashboard JSON must parse (operators can import them into Grafana)."""
    dashboard_dir = REPO_ROOT / "dashboards"
    dashboards = list(dashboard_dir.glob("*.json"))
    assert len(dashboards) >= 3, "Expected at least 3 dashboards (ops, memory, per-persona)"

    for d in dashboards:
        with d.open() as f:
            try:
                json.load(f)
            except json.JSONDecodeError as e:
                raise AssertionError(f"Dashboard {d.name} is not valid JSON: {e}") from e


def test_operations_dashboard_has_required_panels() -> None:
    """Spec §1.4 Dashboard A must have specific panels."""
    ops = REPO_ROOT / "dashboards" / "operations.json"
    with ops.open() as f:
        data = json.load(f)

    panel_titles = {p.get("title", "") for p in data.get("panels", [])}

    # Required panels from the spec
    required = [
        "Hard invariant violations",
        "Ingest rate",
        "Recall latency",
        "Grounding gate pass rate",
        "Quarantine depth",
        "Event log size",
        "LLM spend",
        "MCP auth failures",
        "Backup freshness",
    ]
    for req in required:
        matched = any(req.lower() in t.lower() for t in panel_titles)
        assert matched, f"Operations dashboard missing panel matching {req!r}"


# ---- Prompt versioning invariants ----


async def test_only_one_active_per_site(db: aiosqlite.Connection) -> None:
    """Schema invariant: partial unique index enforces exactly one active per site."""
    await db.execute(
        """
        INSERT INTO prompt_templates (site, version, template_text, parameters, active)
        VALUES ('extract', '1.0', 'A', '{}', 1)
        """
    )
    await db.commit()

    # Inserting a second active for the same site must fail the unique index
    with pytest_raises_integrity():
        await db.execute(
            """
            INSERT INTO prompt_templates (site, version, template_text, parameters, active)
            VALUES ('extract', '2.0', 'B', '{}', 1)
            """
        )
        await db.commit()


async def test_promote_shadow_clears_old_active(db: aiosqlite.Connection) -> None:
    """After promotion, the old active must no longer be active."""
    cursor = await db.execute(
        "INSERT INTO prompt_templates (site, version, template_text, parameters, active) "
        "VALUES ('extract', '1.0', 'OLD', '{}', 1)"
    )
    await db.commit()
    old_id = cursor.lastrowid

    cursor = await db.execute(
        "INSERT INTO prompt_templates (site, version, template_text, parameters, active, shadow, shadow_traffic_pct) "
        "VALUES ('extract', '2.0', 'NEW', '{}', 0, 1, 0.5)"
    )
    await db.commit()
    new_id = cursor.lastrowid

    await promote_shadow(db, site="extract", shadow_template_id=new_id)

    cursor = await db.execute(
        "SELECT COUNT(*) FROM prompt_templates WHERE site = 'extract' AND active = 1"
    )
    count = (await cursor.fetchone())[0]
    assert count == 1, "Exactly one active per site after promotion"

    cursor = await db.execute(
        "SELECT active FROM prompt_templates WHERE id = ?", (old_id,)
    )
    assert (await cursor.fetchone())["active"] == 0


async def test_rollback_restores_previous(db: aiosqlite.Connection) -> None:
    """Rollback must restore an arbitrary previous template as active."""
    cursor = await db.execute(
        "INSERT INTO prompt_templates (site, version, template_text, parameters, active) "
        "VALUES ('extract', '1.0', 'V1', '{}', 0)"
    )
    await db.commit()
    v1_id = cursor.lastrowid

    cursor = await db.execute(
        "INSERT INTO prompt_templates (site, version, template_text, parameters, active) "
        "VALUES ('extract', '2.0', 'V2', '{}', 1)"
    )
    await db.commit()
    v2_id = cursor.lastrowid

    await rollback_to_template(db, site="extract", previous_template_id=v1_id)

    cursor = await db.execute(
        "SELECT active FROM prompt_templates WHERE id = ?", (v1_id,)
    )
    assert (await cursor.fetchone())["active"] == 1
    cursor = await db.execute(
        "SELECT active FROM prompt_templates WHERE id = ?", (v2_id,)
    )
    assert (await cursor.fetchone())["active"] == 0


# ---- Helpers ----


def pytest_raises_integrity():
    """Context manager expecting a SQLite integrity error."""
    import aiosqlite
    import pytest
    return pytest.raises(aiosqlite.IntegrityError)
