# Examples

Runnable demonstration scripts. Each is self-contained and calls the engine's public API (CLI or Python).

## Purpose

Examples are NOT tests. They're narrative walkthroughs of what the engine does, designed to be:

- Read top-to-bottom by a new contributor.
- Run against a local development DB.
- Used as a quick "did Phase N actually work?" check after a phase closes.

## Running an example

```bash
uv run memory-engine db migrate
uv run python examples/phase0_round_trip.py
```

Each example prints its progress and reports success or a specific failure.

## Catalog

| Example | Phase | What it demonstrates |
|---|---|---|
| `phase0_round_trip.py` | 0 | Append 10 events, read them back, verify content hashes |
| `phase1_recall_walkthrough.py` | 1 | Seed neurons, run recall under each lens, show fusion scores |
| `phase2_consolidation.py` | 2 | Feed events, watch them become neurons through the grounding gate |
| `phase4_identity_load.py` | 4 | Load a signed identity document, evaluate a sample outbound draft |
| `phase5_webhook_roundtrip.py` | 5 | Simulated WhatsApp webhook → full pipeline → outbound draft |

Later phases add their own examples as implementation proceeds. Don't write an example before the phase it demonstrates is implemented; it's harder than implementation to get right.

## Conventions

- Each example is a single `.py` file, runnable directly. No project structure.
- Uses a temporary `:memory:` SQLite by default; accepts `--db <path>` to point at an existing DB.
- Prints human-readable status. Exits 0 on success, non-zero on failure.
- Does NOT ship with API keys. LLM-calling examples use a local Ollama or a mocked dispatch path.
- Safe to run multiple times. No state written to anywhere outside the script's own DB.
