"""Phase 1 baseline seed loader.

Creates the exact neurons, counterparties, and events referenced by
tests/fixtures/phase1_baseline.yaml. This is a CONTRACT — the YAML's
expected_neuron_ids must match the IDs created here.

Persona: alice_twin (id=1)
Counterparties:
  - Alex:       whatsapp:+19175550101  (id=1)
  - Priya:      whatsapp:+442079460000 (id=2)
  - Solar crew: whatsapp-group:120363solar@g.us (id=3)

Neuron ID ranges:
  1-10:   self_facts
  11-20:  counterparty_facts for Alex
  21-30:  counterparty_facts for Priya
  31-40:  counterparty_facts for Solar crew
  41-50:  self_facts (job history cluster)
  51-56:  domain_facts
"""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING

from memory_engine.core.events import append_event, compute_content_hash
from memory_engine.policy.signing import canonical_signing_message, generate_keypair, sign
from tests.fixtures.neurons import insert_neuron, insert_neuron_vec

if TYPE_CHECKING:
    import aiosqlite


async def seed_phase1_baseline(
    conn: aiosqlite.Connection,
    embed_fn: object | None = None,
) -> dict[str, int]:
    """Seed the full Phase 1 baseline fixture.

    Args:
        conn: DB connection with migrations applied.
        embed_fn: Optional callable(text) -> list[float]. If provided,
            neurons get real embeddings in neurons_vec. If None, vectors
            are skipped (BM25-only testing).

    Returns:
        Dict with persona_id, counterparty IDs, and counts.
    """
    # Create persona
    priv, pub = generate_keypair()
    pub_b64 = base64.b64encode(pub).decode("ascii")
    cursor = await conn.execute(
        "INSERT INTO personas (id, slug) VALUES (?, ?)", (1, "alice_twin")
    )
    await conn.commit()
    persona_id = 1

    # Create counterparties
    await conn.execute(
        "INSERT INTO counterparties (id, persona_id, external_ref, display_name) VALUES (?, ?, ?, ?)",
        (1, persona_id, "whatsapp:+19175550101", "Alex"),
    )
    await conn.execute(
        "INSERT INTO counterparties (id, persona_id, external_ref, display_name) VALUES (?, ?, ?, ?)",
        (2, persona_id, "whatsapp:+442079460000", "Priya"),
    )
    await conn.execute(
        "INSERT INTO counterparties (id, persona_id, external_ref, display_name) VALUES (?, ?, ?, ?)",
        (3, persona_id, "whatsapp-group:120363solar@g.us", "Solar crew"),
    )
    await conn.commit()

    # Create source events (rule 14: every neuron needs at least one)
    event_ids: list[int] = []
    for i in range(60):
        payload = {"text": f"source event {i}", "fixture": "phase1_baseline"}
        ch = compute_content_hash(payload)
        sig = sign(priv, canonical_signing_message(persona_id, ch))
        ev = await append_event(
            conn,
            persona_id=persona_id,
            counterparty_id=None,
            event_type="message_in",
            scope="private",
            payload=payload,
            signature=sig,
            public_key_b64=pub_b64,
            idempotency_key=f"seed-{i}",
        )
        event_ids.append(ev.id)

    # ---- Self facts (IDs 1-10) ----
    self_facts = [
        (1, "I am proficient in Python and use it as my primary language for backend development"),
        (2, "I have been learning Go for systems programming and concurrency patterns"),
        (3, "I use TypeScript for frontend development with React and Next.js"),
        (5, "I live in Colombo, Sri Lanka near the Galle Face Green area"),
        (6, "My MacBook Pro died last summer when I spilled coffee on it during a coding session"),
        (7, "I prefer espresso in the morning, usually a double shot from the local cafe"),
        (8, "I stopped drinking coffee after 2pm because it was affecting my sleep quality"),
    ]
    for nid, content in self_facts:
        await insert_neuron(
            conn, neuron_id=nid, persona_id=persona_id, counterparty_id=None,
            kind="self_fact", content=content, source_event_ids=[event_ids[nid]],
        )

    # ---- Alex counterparty facts (IDs 11-20) ----
    alex_facts = [
        (11, "Alex works as a senior software engineer at a fintech startup in New York"),
        (12, "Alex has a side project building a distributed task queue in Rust"),
        (13, "Alex recommended a great Sri Lankan restaurant called Sigiriya Kitchen in Brooklyn"),
        (14, "Alex's birthday is on March 22nd, he turns 34 this year"),
        (15, "Alex installed a SolarEdge inverter at his apartment rooftop"),
        # 16 is superseded by 17
        (16, "Alex drives a 2019 Honda Civic that he bought used"),
        (17, "Alex now drives a 2025 Rivian R2 electric vehicle he picked up in January"),
    ]
    for nid, content in alex_facts:
        superseded_at = "2025-01-15 00:00:00" if nid == 16 else None
        t_valid_start = "1992-03-22" if nid == 14 else None
        recorded = "2024-01-01 00:00:00" if nid == 16 else "2025-01-01 00:00:00"
        await insert_neuron(
            conn, neuron_id=nid, persona_id=persona_id, counterparty_id=1,
            kind="counterparty_fact", content=content, source_event_ids=[event_ids[nid]],
            t_valid_start=t_valid_start, superseded_at=superseded_at,
            recorded_at=recorded,
        )

    # ---- Priya counterparty facts (IDs 21-30) ----
    priya_facts = [
        (21, "Priya is working on a machine learning pipeline for medical image analysis"),
        (22, "Priya started a new role as ML team lead at DeepHealth in July 2025"),
        (23, "Priya is researching transformer architectures for radiology report generation"),
        # 24 superseded by 22 (Priya's old job)
        (24, "Priya works as a senior data scientist at NHS Digital in London"),
        (25, "Priya has two kids, ages 4 and 7, who attend the local primary school"),
    ]
    for nid, content in priya_facts:
        superseded_at = "2025-07-01 00:00:00" if nid == 24 else None
        recorded = "2024-06-01 00:00:00" if nid == 24 else "2025-01-01 00:00:00"
        await insert_neuron(
            conn, neuron_id=nid, persona_id=persona_id, counterparty_id=2,
            kind="counterparty_fact", content=content, source_event_ids=[event_ids[nid]],
            superseded_at=superseded_at,
            recorded_at=recorded,
        )

    # ---- Solar crew counterparty facts (IDs 31-40) ----
    solar_facts = [
        (31, "The solar panel efficiency target for the community project is 22% minimum"),
        (32, "We agreed on monocrystalline panels for better efficiency in tropical climates"),
        (34, "The group decided to use a Fronius Symo inverter for the community solar array"),
    ]
    for nid, content in solar_facts:
        await insert_neuron(
            conn, neuron_id=nid, persona_id=persona_id, counterparty_id=3,
            kind="counterparty_fact", content=content, source_event_ids=[event_ids[nid]],
        )

    # ---- Job history self facts (IDs 41-50) ----
    job_facts = [
        (41, "I started my career as a junior developer at a local web agency in 2018"),
        (42, "I moved to a backend engineering role at a logistics company in 2019"),
        (43, "I became a senior engineer at a cloud infrastructure startup in 2021"),
        (44, "I led a team of 5 engineers building a real-time data pipeline in 2022"),
        (45, "I transitioned to independent consulting focused on AI systems in 2024"),
    ]
    for nid, content in job_facts:
        await insert_neuron(
            conn, neuron_id=nid, persona_id=persona_id, counterparty_id=None,
            kind="self_fact", content=content, source_event_ids=[event_ids[nid]],
        )

    # ---- Domain facts (IDs 51-56) ----
    domain_facts = [
        (51, "Maximum Power Point Tracking MPPT algorithms use perturb-and-observe for solar charge controllers"),
        (52, "Recent research on MPPT shows incremental conductance outperforms P&O in rapidly changing conditions"),
        (53, "Machine learning approaches to MPPT are gaining traction with reinforcement learning based controllers"),
        (55, "pgvector supports IVFFlat and HNSW indexes for approximate nearest neighbor search in PostgreSQL"),
        (56, "sqlite-vec provides vector search for SQLite using virtual tables with cosine and L2 distance metrics"),
    ]
    for nid, content in domain_facts:
        await insert_neuron(
            conn, neuron_id=nid, persona_id=persona_id, counterparty_id=None,
            kind="domain_fact", content=content, source_event_ids=[event_ids[nid]],
        )

    # Set superseded_by references now that all neurons exist
    await conn.execute("UPDATE neurons SET superseded_by = 17 WHERE id = 16")
    await conn.execute("UPDATE neurons SET superseded_by = 22 WHERE id = 24")

    await conn.commit()

    # Embed all active neurons if embed_fn provided
    if embed_fn is not None:
        cursor = await conn.execute(
            "SELECT id, content FROM neurons WHERE superseded_at IS NULL ORDER BY id"
        )
        rows = await cursor.fetchall()
        for row in rows:
            embedding = embed_fn(row["content"])  # type: ignore[operator]
            await insert_neuron_vec(conn, row["id"], embedding)
        await conn.commit()

    return {
        "persona_id": persona_id,
        "alex_counterparty_id": 1,
        "priya_counterparty_id": 2,
        "solar_counterparty_id": 3,
        "neuron_count": 27,  # total including superseded
        "active_neuron_count": 25,  # excluding superseded (16, 24)
    }
