# Phase 7 honest 80% assessment

**Date:** 2026-04-21
**Live deployment:** twincore-alpha on Mac mini, 2 days of real WhatsApp traffic
**Eval corpus:** 90 neurons / 50 queries (frozen)

This document answers the question CLAUDE.md §1 implicitly asks:
*"We're targeting 80% reliable orchestration. Are we actually there?"*
Written after 3 intense days of shipping Phase 7 P0/P1 work, so we can
see clearly what the system is, what it isn't, and where the 20% lives.

---

## What "80%" means for us

Two axes:
1. **Probabilistic reliability** — for any given inbound, how often does
   the full loop (ingest → extract → ground → recall → draft → save)
   produce a useful, memory-informed result? We target ≥80%.
2. **Invariant reliability** — cross-counterparty leaks, PII egress,
   scope violations. These are 100% or we halt. Zero tolerance.

The 80% number is the ceiling suggested by the HaluMem benchmark
(no memory system exceeds 70% QA under long-context); we aim higher
through grounding + distinct-source counting + bi-temporal + hard
privacy invariants, but we don't claim zero-fault.

---

## What IS at 80%+ today (with evidence)

### 1. Retrieval quality — strong
- **MRR@10 = 0.953** on 50 real queries against a 90-neuron frozen corpus
- **Hit@5 = 1.000** — every single query surfaces a relevant neuron in top 5
- **Hit@10 = 1.000** — zero-recall rate is 0/50
- Stack: BM25Plus + vector (MiniLM-L12 multilingual) + graph, RRF fusion

**This is not an MVP claim, it's measured.** Reproducible via
`uv run pytest tests/eval/test_retrieval_baseline.py --eval -v -s`.

### 2. Grounding gate accuracy — above baseline
- **88%** similarity-only at threshold 0.60 on 50 fixtures
- **94% accuracy, 100% precision** with the full LLM-judge pipeline at
  threshold 0.60
- Up from the Phase 2 bag-of-words 72% baseline

### 3. End-to-end WhatsApp loop — PROVEN
On 2026-04-19 at 18:44 local, Babi's `"where are you"` became a
memory-informed draft `"I'm at work, babi. What's up?"` that landed
in the operator's phone via self-chat notification, got `/approve`d,
and delivered. Ingest → memory_engine → recall → Gemini draft →
control-plane save → self-chat notify → operator approve → bridge
send — every link confirmed.

### 4. Memory plane under sustained real traffic
- 514+ events ingested over 2 days of real Sinhala/English WhatsApp
- 71 active neurons (post-dedup) covering electronics, family,
  hobbies, domain knowledge
- Consolidator processing at 67% acceptance rate (threshold 0.60)

### 5. Invariant coverage — architectural
- Governance rules 1-16 have invariant tests (75 tests in
  `tests/invariants/`)
- Event immutability, scope tightening, distinct-source counting,
  citation grounding — all covered

---

## What ISN'T at 80% (the actual gap)

### A. Sustained-use verification — 2 of 7 days
Original Phase 7 acceptance requires one week of sustained use. We've
had 2 days. Can't honestly claim the system handles 7 days of real
load; we've seen 3 SQLite corruptions in those 2 days alone.

### B. Hard invariant monitoring — blind in production
We have `wiki_v3_invariant_violation_total` counter (Phase 3) and a
`wiki_v3_db_integrity_ok` gauge (this session). **No dashboard
consumes them.** No alert fires. We'd learn about violations by
reading logs. That's not 80% reliability, that's hope.

### C. First quarantine review — not done
366+ candidates in `quarantine_neurons` table. We built the CLI
(`memory-engine quarantine {list,show,promote,reject}`). We never
ran it against live data. The operator hasn't triaged a single one.

### D. Twin-agent prompt builder ignores recall scores
`recall()` returns fused scores (`bm25`, `vector`, `fused`). Twin-agent
takes the top-5 regardless of score magnitude. Adversarial evidence:
`"quantum computing algorithms"` (a query with no real coverage)
still returns 5 neurons and all 5 would be injected into Gemini's
prompt as "relevant memory". Real-world case: low-confidence recall
is being fed to the model as if it were high-confidence.

### E. Old neurons orphaned in vector plane
Switched embedder L6 → L12 on 2026-04-20. Old neurons keep
`embedder_rev = "sbert-minilm-l6-v2-1"` and recall's vector stream
filters them out (correctly — rule 4.7 per synthesis). They only
surface via BM25. Roughly half the accumulated memory is
BM25-findable but semantically invisible. No re-embedding pipeline.

### F. Consolidation log skipped events
Migration 007 backfilled `consolidation_log` from *active neurons'*
source_event_ids — 144 events. The other 193 events (broadcasts
skipped, rejected candidates whose source events weren't saved,
early Dockerfile-pin events) were never marked consolidated and
never will be unless something promotes them. They're memory holes.

### G. Identity plane still dead downstream
`/v1/identity/load` now accepts the real YAML (fixed this session),
writes to `personas.identity_doc`. **Twin-agent reads YAML off disk
directly** — doesn't use `personas.identity_doc`. Phase 4 drift
flags, outbound non-negotiable checks, contradiction judge against
self_facts — all sit in code but never fire in production.

### H. No operator dashboard, no runbook execution
Runbooks exist (`docs/runbooks/*.md`). Nobody's ever run one during
a real alert because there's no alerting. Grafana dashboards exist
as JSON in `dashboards/`. No Grafana instance. Metrics render via
`/metrics` but nothing scrapes them.

---

## Known failure modes (what breaks, when, why)

| mode | frequency | root cause known? | current mitigation |
|---|---|---|---|
| SQLite index corruption | 3× in 2 days | **No** — suspect macOS Docker bind-mount FUSE, moved to named volumes but CP still saw `disk I/O error` after | `busy_timeout=30s` + retry shim + integrity watchdog + `.recover` runbook |
| Twin-agent poll stall | 3× in 2 days | **No** — suspect sync fetch blocking async loop, but also saw stalls AFTER `asyncio.to_thread` wrap | 3s heartbeat + fetch timeout + manual restart |
| HF model re-download on each rebuild | every `--no-cache` | Docker caches the `git clone` layer; `--no-cache` doesn't invalidate downloads done inside a RUN | `ARG CACHEBUST=$(date +%s)` + host-cache copy |
| Consolidator silently drops events | unknown | events past the WM ring-buffer horizon but not in `consolidation_log` | none, needs backfill |
| Gemini rate limit | occasional | shared with twin-agent's 15 RPM free tier | `MEMORY_ENGINE_CONSOLIDATOR_MAX_RPM=6` split |
| Grounding rejects legit extractions | ~33% at 0.60 threshold | cross-lingual sim drops for paraphrase | threshold tuned; quarantine review would close more |

---

## The 20% we're NOT claiming

To be honest about the ceiling:

1. **Multi-persona** — schema supports, deployment doesn't. Per-persona
   owner keys infrastructure added; never exercised. (Gap 8 in synthesis)
2. **Multi-modal ingest** — text only. No audio/image/PDF path. (Gap)
3. **Cold-start from history** — if a new operator joins, there's no
   "ingest the last 6 months of WhatsApp and catch up" tooling.
4. **Sinhala/Singlish eval** — our 50 queries are mostly English. Real
   traffic is mixed. We haven't measured retrieval on actual Sinhala.
5. **Signature verification of identity documents** — YAML is signed at
   bootstrap, no code verifies the signature on load. (DRIFT
   `identity-load-signature-not-verified`)
6. **Cross-counterparty admin path** — `admin_cross_counterparty_recall`
   is mentioned in governance rule 12 as the one way to cross the
   boundary; doesn't exist yet.
7. **Prompt A/B + shadow** — `prompt_templates` has `shadow` column,
   Phase 6 built the harness, but we've never actually flipped a
   prompt and compared outputs on live traffic.
8. **Token budget enforcement** — `token_budget` param in `recall()`
   exists, but twin-agent never sets it, so context bloat is possible
   on long chats.

---

## Priority ordering — closing the gap to 80%

Ranked by demo-impact × effort:

| # | gap | effort | unblocks |
|---|---|---|---|
| 1 | **First quarantine review** (run the CLI, triage 20 rejects) | 30 min | Acceptance criterion closed; learn real rejection patterns |
| 2 | **Revert OPERATOR_BACKOFF_MINUTES to 10** | 2 min | Daily-use sanity; self-chat stops being noisy |
| 3 | **Twin-agent threshold on fused recall score** | 1 hr | Stops adversarial / low-conf memory leaking into prompts |
| 4 | **Backfill consolidation_log + re-embed old neurons to L12** | 1 hr (script + run) | Closes the memory-holes gap (F + E) |
| 5 | **Wire twin-agent to read identity from `personas.identity_doc`** | 30 min | Makes Phase 4 identity plane actually alive (G) |
| 6 | **Root-cause twin-agent stall + SQLite I/O error** | half-day deep-dive | Stops whack-a-mole on production-critical bugs |
| 7 | **Set up `memory-engine doctor` command** — one-shot invariant + integrity + lag + quarantine summary | 2 hr | Operator has an alternative to dashboards until those exist (B) |
| 8 | **Sustained use** | 5 more days | Only calendar-blocks; we do the other work in parallel |
| 9 | **Real Sinhala/Singlish query eval** | half-day (labeler + corpus + run) | Validate the 0.953 MRR number survives native-language queries |

---

## Honest closing

The MVP demo works. Babi got a memory-informed reply. The numbers on
the frozen corpus are strong.

But the system is two live-days old and we've already seen it corrupt
its own database three times, silently stall the draft pipeline
three times, and carry a memory-hole where 193 events disappeared
into pre-backfill purgatory. Those aren't "occasional edge cases" —
they're structural fragilities in the Docker-on-macOS runtime and
the consolidator's event-tracking.

We've been bandaging. Each bandage is correct for its proximate
symptom; none addresses the class of problem. A one-week unattended
run would hit every one of these failures multiple times.

**80% claim status:** retrieval yes, grounding yes, MVP demo yes.
End-to-end continuous operation: not yet. Not without the #1-#7 work.
