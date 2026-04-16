# Runbooks

Operational procedures for running memory_engine in production. Each runbook covers one scenario, with concrete commands, expected outputs, and escalation guidance.

## Index

| Runbook | When to use |
|---|---|
| [`sqlite_vec_install.md`](./sqlite_vec_install.md) | Phase 0+: installing the sqlite-vec extension |
| [`whatsapp_setup.md`](./whatsapp_setup.md) | Phase 5: provisioning WhatsApp Business API |
| [`mcp_rotation.md`](./mcp_rotation.md) | Any phase: rotating an MCP Ed25519 keypair |
| [`halt_investigation.md`](./halt_investigation.md) | `MemoryEngineHalted` alert firing |
| [`halt_release_emergency.md`](./halt_release_emergency.md) | Normal halt release doesn't work |
| [`backup_drill.md`](./backup_drill.md) | Monthly restore drill (Phase 6+) |
| [`disaster_recovery.md`](./disaster_recovery.md) | Total-loss recovery — new host from zero |
| [`vault_rotation.md`](./vault_rotation.md) | Rotating the secret vault master key |
| [`embedder_dimension_change.md`](./embedder_dimension_change.md) | Switching embedder to different dimensions |

## Planned (not yet written)

These are referenced in phase docs but will be authored as each phase is reached:

- `phase7_deployment.md` — initial operator deployment (Phase 7)
- `phase7_observations.md` — observations during first internal user phase (Phase 7)
- `rotations.md` — append-only log of all rotations performed
- `systemd_service.md` — systemd unit file and deployment conventions
- `incidents/` — per-incident post-mortems, one file per event

## Template

When authoring a new runbook:

```markdown
# Runbook: <short title>

> One-sentence description of what this covers.

## When to use

## Prerequisites

## Procedure

### 1. <first step>
### 2. <second step>
...

## Verification

## Troubleshooting

**<symptom>** — cause and fix.

## Post-incident / Post-action

What to record. What to schedule for next time.
```

## Conventions

- Runbooks are written for an operator under stress. Short sentences. Concrete commands. Expected outputs.
- Include what NOT to do in high-stakes procedures.
- Every runbook that writes state to the system (rotation, release, restore) ends with a "verify" step that confirms the change took effect.
- Commands assume a Unix-like host. Windows operators adapt.
- Update runbooks when the corresponding code changes. A stale runbook is worse than no runbook.

## Incidents

Post-incident reports live in `docs/runbooks/incidents/YYYY-MM-DD-<slug>.md`. Not templated here; the halt investigation runbook includes a template.
