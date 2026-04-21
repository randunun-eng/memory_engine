"""Phase 6 integration tests — observability, prompt versioning, backup/DR.

Tests verify:
  - Metric registry (counter, gauge, histogram) and Prometheus text rendering
  - Structured JSON logger with required fields
  - Prompt shadow harness (A/B execution, comparison logging, daily batch)
  - Promotion and rollback CLI surface
  - Backup/restore scripts produce round-trippable artifacts

Out of scope for these tests (would require process-level setup):
  - Actually running the FastAPI /metrics endpoint
  - Cron/systemd scheduling
  - Grafana dashboard import
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    import aiosqlite

from memory_engine.observability import logging as obs_logging
from memory_engine.observability import metrics as obs_metrics
from memory_engine.policy.shadow import (
    compute_daily_comparison,
    dispatch_with_shadow,
    get_active_template,
    get_shadow_template,
    promote_shadow,
    rollback_to_template,
)

# ---- Metrics ----


def test_counter_increments() -> None:
    obs_metrics.clear()
    c = obs_metrics.counter("test_counter_total", {"kind": "a"}, help_text="Test counter")
    c.inc()
    c.inc(2)
    output = obs_metrics.render()
    assert 'test_counter_total{kind="a"} 3' in output
    assert "# TYPE test_counter_total counter" in output
    assert "# HELP test_counter_total Test counter" in output


def test_counter_rejects_negative() -> None:
    obs_metrics.clear()
    c = obs_metrics.counter("test_neg_total")
    with pytest.raises(ValueError, match="cannot be decremented"):
        c.inc(-1)


def test_gauge_set_inc_dec() -> None:
    obs_metrics.clear()
    g = obs_metrics.gauge("test_gauge")
    g.set(42.0)
    assert g.value == 42.0
    g.inc(3)
    assert g.value == 45.0
    g.dec(10)
    assert g.value == 35.0


def test_histogram_observe_and_render() -> None:
    obs_metrics.clear()
    h = obs_metrics.histogram("test_latency_seconds", {"path": "ingest"})
    h.observe(0.005)
    h.observe(0.05)
    h.observe(0.5)
    h.observe(5.0)
    output = obs_metrics.render()
    assert "test_latency_seconds_bucket" in output
    assert "test_latency_seconds_count" in output
    assert "test_latency_seconds_sum" in output
    # +Inf bucket should count all observations
    assert 'le="+Inf"' in output


def test_metric_kind_mismatch_rejected() -> None:
    obs_metrics.clear()
    obs_metrics.counter("dup_metric")
    with pytest.raises(ValueError, match="already registered"):
        obs_metrics.gauge("dup_metric")


def test_metric_multiple_label_sets() -> None:
    obs_metrics.clear()
    obs_metrics.counter("labeled_total", {"persona": "a"}).inc()
    obs_metrics.counter("labeled_total", {"persona": "b"}).inc(5)
    output = obs_metrics.render()
    assert 'labeled_total{persona="a"} 1' in output
    assert 'labeled_total{persona="b"} 5' in output


def test_metric_label_value_escaping() -> None:
    obs_metrics.clear()
    obs_metrics.counter("escape_test_total", {"msg": 'hello "world"'}).inc()
    output = obs_metrics.render()
    # Quote must be escaped
    assert 'msg="hello \\"world\\""' in output


# ---- Structured logging ----


def test_logger_emits_json_with_required_fields(caplog: pytest.LogCaptureFixture) -> None:
    """Structured logger produces JSON with ts, level, module, event."""
    log = obs_logging.get_logger("test.module")
    with caplog.at_level(logging.INFO):
        log.info("grounding_verdict", verdict="accepted", similarity=0.73)

    assert len(caplog.records) >= 1
    record = caplog.records[-1]
    extras = getattr(record, "_structured", None)
    assert extras is not None
    assert extras["event"] == "grounding_verdict"
    assert extras["verdict"] == "accepted"
    assert extras["similarity"] == 0.73


def test_json_formatter_produces_valid_json() -> None:
    """The JSONFormatter output must parse as JSON."""
    formatter = obs_logging.JSONFormatter()
    record = logging.LogRecord(
        name="test.module",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="test_event",
        args=(),
        exc_info=None,
    )
    record._structured = {"event": "test_event", "foo": "bar", "n": 42}  # type: ignore[attr-defined]

    output = formatter.format(record)
    parsed = json.loads(output)
    assert parsed["level"] == "info"
    assert parsed["module"] == "test.module"
    assert parsed["event"] == "test_event"
    assert parsed["foo"] == "bar"
    assert parsed["n"] == 42
    assert "ts" in parsed


# ---- Shadow harness ----


async def _insert_prompt(
    db: aiosqlite.Connection,
    *,
    site: str,
    version: str,
    text: str,
    active: bool = False,
    shadow: bool = False,
    traffic_pct: float = 0.0,
) -> int:
    cursor = await db.execute(
        """
        INSERT INTO prompt_templates
            (site, version, template_text, parameters, active, shadow, shadow_traffic_pct)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (site, version, text, "{}", 1 if active else 0, 1 if shadow else 0, traffic_pct),
    )
    await db.commit()
    assert cursor.lastrowid is not None
    return cursor.lastrowid


async def test_get_active_returns_active_template(db: aiosqlite.Connection) -> None:
    await _insert_prompt(db, site="extract", version="1.0", text="ACTIVE", active=True)
    await _insert_prompt(db, site="extract", version="2.0", text="OTHER", active=False)

    result = await get_active_template(db, "extract")
    assert result is not None
    _, text = result
    assert text == "ACTIVE"


async def test_get_shadow_returns_configured_shadow(db: aiosqlite.Connection) -> None:
    await _insert_prompt(db, site="extract", version="1.0", text="ACTIVE", active=True)
    await _insert_prompt(
        db,
        site="extract",
        version="2.0",
        text="SHADOW",
        active=False,
        shadow=True,
        traffic_pct=0.1,
    )

    result = await get_shadow_template(db, "extract")
    assert result is not None
    _, text, pct = result
    assert text == "SHADOW"
    assert pct == 0.1


async def test_shadow_not_returned_if_traffic_zero(db: aiosqlite.Connection) -> None:
    """Zero-traffic shadow is treated as no shadow (saves the LLM call)."""
    await _insert_prompt(db, site="extract", version="1.0", text="ACTIVE", active=True)
    await _insert_prompt(
        db,
        site="extract",
        version="2.0",
        text="SHADOW",
        active=False,
        shadow=True,
        traffic_pct=0.0,
    )

    result = await get_shadow_template(db, "extract")
    assert result is None


async def test_dispatch_runs_only_active_when_no_shadow(db: aiosqlite.Connection) -> None:
    # Create persona
    cursor = await db.execute("INSERT INTO personas (slug) VALUES ('p1')")
    await db.commit()
    pid = cursor.lastrowid
    assert pid is not None

    await _insert_prompt(db, site="extract", version="1.0", text="ACTIVE", active=True)

    calls: list[str] = []

    def fake_llm(template: str, inputs: dict) -> dict:
        calls.append(template)
        return {"result": template}

    result = await dispatch_with_shadow(
        db,
        persona_id=pid,
        site="extract",
        inputs={"x": 1},
        llm_fn=fake_llm,
    )
    assert calls == ["ACTIVE"]
    assert result.active_output == {"result": "ACTIVE"}
    assert result.shadow_output is None
    assert result.logged is False


async def test_dispatch_runs_both_when_shadow_drawn(db: aiosqlite.Connection) -> None:
    """With rng() < traffic_pct, both active and shadow should run, comparison logged."""
    cursor = await db.execute("INSERT INTO personas (slug) VALUES ('p2')")
    await db.commit()
    pid = cursor.lastrowid
    assert pid is not None

    await _insert_prompt(db, site="extract", version="1.0", text="ACTIVE", active=True)
    await _insert_prompt(
        db,
        site="extract",
        version="2.0",
        text="SHADOW",
        active=False,
        shadow=True,
        traffic_pct=1.0,
    )

    calls: list[str] = []

    def fake_llm(template: str, inputs: dict) -> dict:
        calls.append(template)
        return {"template": template, "input": inputs}

    result = await dispatch_with_shadow(
        db,
        persona_id=pid,
        site="extract",
        inputs={"x": 1},
        llm_fn=fake_llm,
        rng=lambda: 0.0,  # always draw
    )
    assert calls == ["ACTIVE", "SHADOW"]
    assert result.logged is True

    # Verify a comparison row was written
    cursor = await db.execute("SELECT COUNT(*) FROM prompt_shadow_logs")
    row = await cursor.fetchone()
    assert row[0] == 1


async def test_dispatch_skips_shadow_when_rng_above_threshold(db: aiosqlite.Connection) -> None:
    cursor = await db.execute("INSERT INTO personas (slug) VALUES ('p3')")
    await db.commit()
    pid = cursor.lastrowid
    assert pid is not None

    await _insert_prompt(db, site="extract", version="1.0", text="ACTIVE", active=True)
    await _insert_prompt(
        db,
        site="extract",
        version="2.0",
        text="SHADOW",
        active=False,
        shadow=True,
        traffic_pct=0.1,
    )

    calls: list[str] = []

    def fake_llm(template: str, inputs: dict) -> dict:
        calls.append(template)
        return {}

    # rng returns 0.5, which is > 0.1 threshold
    result = await dispatch_with_shadow(
        db,
        persona_id=pid,
        site="extract",
        inputs={"x": 1},
        llm_fn=fake_llm,
        rng=lambda: 0.5,
    )
    assert calls == ["ACTIVE"]
    assert result.shadow_output is None
    assert result.logged is False


async def test_compute_daily_comparison_aggregates(db: aiosqlite.Connection) -> None:
    """Daily batch aggregates shadow logs into per-site metrics."""
    cursor = await db.execute("INSERT INTO personas (slug) VALUES ('p4')")
    await db.commit()
    pid = cursor.lastrowid
    assert pid is not None

    active_id = await _insert_prompt(db, site="extract", version="1.0", text="A", active=True)
    shadow_id = await _insert_prompt(
        db,
        site="extract",
        version="2.0",
        text="S",
        active=False,
        shadow=True,
        traffic_pct=1.0,
    )

    # Insert 3 shadow log rows for today
    today = "2026-04-16"
    for i in range(3):
        await db.execute(
            """
            INSERT INTO prompt_shadow_logs
                (persona_id, site, active_template_id, shadow_template_id,
                 input_hash, active_output, shadow_output,
                 active_latency_ms, shadow_latency_ms,
                 active_cost_usd, shadow_cost_usd, recorded_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pid,
                "extract",
                active_id,
                shadow_id,
                f"input_{i}",
                json.dumps({"out": "same"}),
                json.dumps({"out": "same"}),
                100,
                120,
                0.01,
                0.02,
                f"{today}T10:00:00",
            ),
        )
    await db.commit()

    results = await compute_daily_comparison(db, day=today)
    assert len(results) == 1
    r = results[0]
    assert r["sample_count"] == 3
    assert r["active_mean_latency_ms"] == 100
    assert r["shadow_mean_latency_ms"] == 120
    assert r["output_agreement_rate"] == 1.0  # all outputs matched


async def test_promote_shadow_swaps_active(db: aiosqlite.Connection) -> None:
    active_id = await _insert_prompt(db, site="extract", version="1.0", text="A", active=True)
    shadow_id = await _insert_prompt(
        db,
        site="extract",
        version="2.0",
        text="S",
        active=False,
        shadow=True,
        traffic_pct=0.5,
    )

    await promote_shadow(db, site="extract", shadow_template_id=shadow_id)

    # Old active should now be inactive
    cursor = await db.execute(
        "SELECT active, shadow FROM prompt_templates WHERE id = ?", (active_id,)
    )
    row = await cursor.fetchone()
    assert row["active"] == 0

    # New active should be the former shadow, with shadow flag cleared
    cursor = await db.execute(
        "SELECT active, shadow, shadow_traffic_pct FROM prompt_templates WHERE id = ?", (shadow_id,)
    )
    row = await cursor.fetchone()
    assert row["active"] == 1
    assert row["shadow"] == 0
    assert row["shadow_traffic_pct"] == 0.0


async def test_rollback_to_previous(db: aiosqlite.Connection) -> None:
    v1 = await _insert_prompt(db, site="extract", version="1.0", text="V1", active=False)
    v2 = await _insert_prompt(db, site="extract", version="2.0", text="V2", active=True)

    await rollback_to_template(db, site="extract", previous_template_id=v1)

    cursor = await db.execute("SELECT active FROM prompt_templates WHERE id = ?", (v1,))
    assert (await cursor.fetchone())["active"] == 1
    cursor = await db.execute("SELECT active FROM prompt_templates WHERE id = ?", (v2,))
    assert (await cursor.fetchone())["active"] == 0


# ---- Migration 006 schema ----


async def test_retrieval_traces_table_exists(db: aiosqlite.Connection) -> None:
    cursor = await db.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'retrieval_traces'"
    )
    assert await cursor.fetchone() is not None


async def test_prompt_shadow_logs_table_exists(db: aiosqlite.Connection) -> None:
    cursor = await db.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'prompt_shadow_logs'"
    )
    assert await cursor.fetchone() is not None


async def test_backup_status_table_exists(db: aiosqlite.Connection) -> None:
    cursor = await db.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'backup_status'"
    )
    assert await cursor.fetchone() is not None


# ---- Backup / restore round-trip ----


def test_backup_and_restore_round_trip(tmp_path: Path) -> None:
    """backup.sh + restore.sh round-trip preserves SQLite contents.

    Requires: sqlite3, age, tar (and shred or equivalent) installed.
    Skipped if age is not available.
    """
    import shutil

    if shutil.which("age") is None:
        pytest.skip("age binary not available")
    if shutil.which("age-keygen") is None:
        pytest.skip("age-keygen binary not available")

    repo_root = Path(__file__).resolve().parents[2]
    backup_sh = repo_root / "bin" / "backup.sh"
    restore_sh = repo_root / "bin" / "restore.sh"
    assert backup_sh.exists()
    assert restore_sh.exists()

    # Set up a fake data dir with a SQLite DB
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db_path = data_dir / "engine.db"
    subprocess.run(
        ["sqlite3", str(db_path), "CREATE TABLE t (x INTEGER); INSERT INTO t VALUES (42);"],
        check=True,
    )

    # Generate age keypair
    key_path = tmp_path / "age.key"
    result = subprocess.run(
        ["age-keygen", "-o", str(key_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    # Recipient public key is in stderr for age-keygen
    recipient = next(
        line.split(": ")[1] for line in result.stderr.splitlines() if line.startswith("Public key:")
    )

    dest_dir = tmp_path / "backups"
    dest_dir.mkdir()

    env = os.environ.copy()
    env["MEMORY_ENGINE_BACKUP_DEST"] = str(dest_dir)
    env["MEMORY_ENGINE_BACKUP_RECIPIENT"] = recipient
    env["MEMORY_ENGINE_DATA_DIR"] = str(data_dir)

    # Run backup
    result = subprocess.run(
        ["bash", str(backup_sh), "test_persona"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"backup failed: {result.stderr}"

    # Find the produced artifact
    artifacts = list(dest_dir.glob("test_persona_*.tar.age"))
    assert len(artifacts) == 1

    # Now restore into a different data dir
    restore_dir = tmp_path / "restored_data"
    restore_dir.mkdir()

    restore_env = env.copy()
    restore_env["MEMORY_ENGINE_BACKUP_IDENTITY"] = str(key_path)
    restore_env["MEMORY_ENGINE_DATA_DIR"] = str(restore_dir)

    result = subprocess.run(
        ["bash", str(restore_sh), str(artifacts[0]), "--force"],
        env=restore_env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"restore failed: {result.stderr}\n{result.stdout}"

    # Verify restored DB has our data
    result = subprocess.run(
        ["sqlite3", str(restore_dir / "engine.db"), "SELECT x FROM t;"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "42"


def test_backup_script_rejects_missing_env() -> None:
    """backup.sh fails cleanly without required environment."""
    repo_root = Path(__file__).resolve().parents[2]
    backup_sh = repo_root / "bin" / "backup.sh"

    # Run with empty env (minus essentials for bash)
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
    }

    result = subprocess.run(
        ["bash", str(backup_sh), "some_persona"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "MEMORY_ENGINE_BACKUP" in result.stderr


def test_drill_script_exists_and_executable() -> None:
    """Smoke test: drill.sh is present, executable, and refuses bad args."""
    repo_root = Path(__file__).resolve().parents[2]
    drill_sh = repo_root / "bin" / "drill.sh"
    assert drill_sh.exists()
    assert os.access(drill_sh, os.X_OK)

    # Running without persona_slug should exit with usage error
    result = subprocess.run(
        ["bash", str(drill_sh)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "Usage" in result.stderr
