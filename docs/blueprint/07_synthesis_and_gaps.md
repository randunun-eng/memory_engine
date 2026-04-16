# Wiki v3 — Synthesis and Gaps

> Consolidated view of what the blueprint now contains, what is still missing, and what 80% reliable orchestration concretely means given current SOTA evidence.
> Reads as the final document before implementation. Not a revision.

---

## 1. The Honest Frame

The target is 80% reliable orchestration, not zero-fault. This is the right framing and the rest of this document is built on it.

Evidence: HaluMem (2025) evaluated every major memory system and found **none exceeds 62% accuracy on memory extraction or 70% on QA** under long-context conditions. That is the state of the art. Anyone claiming a zero-fault memory system is either wrong, lying, or measuring something other than what users actually do with the system.

What 80% means concretely for this blueprint, decomposed by metric category:

| Metric category | Target | What "80%" means here | What 100% would mean |
|---|---|---|---|
| **Hard privacy invariants** | **100% (non-negotiable)** | Zero cross-counterparty leaks, zero PII egress, zero scope violations. The system halts on violation. | Same. |
| **Identity consistency** | 95%+ | Twin stays in character; self-contradiction caught and flagged; manipulation attempts don't rewrite identity. Checkable against identity document. | Impossible — some judgement calls are genuinely ambiguous. |
| **Retrieval quality** | 80% (Recall@10, MRR) | Most relevant memories surface for most queries. | Impossible — "relevance" is subjective. |
| **Grounding quality** | 75%+ extraction accuracy | Above current SOTA (62%) because grounding gate rejects ungrounded candidates at ingest. | Impossible — source text is ambiguous. |
| **Reinforcement discipline** | 99%+ distinct-source fidelity | One hallucination does not accumulate into 808 echoes. | Achievable — this is a schema + counter discipline, not an LLM problem. |
| **Temporal reasoning** | 90%+ on point-in-time queries | As-of queries return state matching the validity intervals we recorded. | Impossible — unobserved time windows can't be reconstructed. |
| **Subjective quality** | unmeasurable | Users find the twin useful most of the time. | Impossible — depends on user, task, mood. |

The blueprint is engineered to deliver the left column. It cannot deliver the right column; no memory system can.

The rest of this document enumerates what the blueprint contains today, where the gaps are, and what 80% requires that we haven't yet specified.

---

## 2. What the Blueprint Now Contains

Five design documents plus one adapter spec. The architecture has 42 named concepts across the stack. Here is the consolidated view.

### 2.1 Version history with provenance

| Version | Introduced | Motivation |
|---|---|---|
| v0.0 | Base architecture: event-sourced log, five memory tiers, privacy scopes, self-healing invariants, pluggable hybrid retrieval | Starting blueprint from scratch for an open-source memory orchestrator |
| v0.1 | Six contradiction fixes: event immutability, dedup vs reinforcement split, retrieval-is-read-only, event-sourced mutations, `retrievals` table, scope wording | Post-hoc review of v0.0 found six cases where the design contradicted itself |
| v0.2 | Evaluation framework, cost model, log compaction with snapshots, honest defaults, biology reframe, realistic roadmap | v0.1 closed contradictions but left evaluation, cost, and operational gaps unaddressed |
| v0.3 | Identity layer: personas, counterparties, three neuron kinds, retrieval lenses, four-pillar invariants, identity protocol | Use case shift from generic agents to digital twins/employees required an identity layer |
| v0.4 | Anti-hallucination hardening: bi-temporal modeling, grounding verification gate, distinct-source counting, targeted contradiction detection, identity-aware extraction, honest SOTA targets | Mem0 audit evidence of echo-inflation and Graphiti's temporal modeling exposed hallucination gaps |
| WhatsApp adapter | One MCP per persona, group-as-counterparty, deletion policy in identity doc, outbound approval pipeline | Concrete channel integration locked in on user decisions |

### 2.2 Stack at a glance

```
┌─────────────────────────────────────────────────────────────────────┐
│                    EXTERNAL WORLD                                   │
│          (WhatsApp MCP, file watchers, HTTP, etc.)                  │
└────────────────────────────┬────────────────────────────────────────┘
                             │
              ┌──────────────▼───────────────┐
              │   SENSORY (ingress, §4.1)    │
              │  adapter → normalize →       │
              │  classify → scrub →          │
              │  idempotency+tombstone check │
              └──────────────┬───────────────┘
                             │
                  ┌──────────▼──────────┐
                  │  EVENT LOG (WAL)    │  ← immutable, content-hashed, signed
                  │      §4.2           │
                  └──────────┬──────────┘
                             │
       ┌─────────────────────┼─────────────────────┐
       │                     │                     │
┌──────▼──────┐    ┌─────────▼─────────┐   ┌──────▼──────┐
│   WORKING   │    │  PRIVACY LAYER    │   │  OBSERVER   │
│   MEMORY    │    │ (vault, scopes,   │   │ (metrics,   │
│  (§4.3)     │    │  redactor, §4.4)  │   │  logs)      │
└──────┬──────┘    └───────────────────┘   └─────────────┘
       │
┌──────▼──────────────────────────────────────────────────────────────┐
│                    CONSOLIDATOR (§4.6)                              │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │ GROUNDING GATE (v0.4) — cite resolves → similarity → accept  │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  • Promoter: working → episodic → semantic → procedural             │
│  • Reinforcer (LTP, distinct-source only — v0.4)                    │
│  • Decayer (LTD per-tier half-life)                                 │
│  • Pruner                                                           │
│  • Contradiction detector (targeted, same-entity-pair — v0.4)       │
│  • Skill crystallization (procedural tier, v0.2)                    │
│  • Retrieval-trace applier (batches LTP from read events, v0.1)     │
└────────────────────────────┬────────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────────┐
│            STORAGE (Cortex, §4.5) — bi-temporal (v0.4)              │
│                                                                     │
│  personas · counterparties · neurons · synapses · episodes          │
│  · patterns/skills · vault · tombstones · retrievals                │
│  · healing_log · quarantine_neurons · mcp_sources · snapshots       │
└────────────────────────────┬────────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────────┐
│           RETRIEVAL (§4.7) — pure-read                              │
│                                                                     │
│  BM25 ⊕ vector ⊕ graph-walk → RRF                                   │
│  Lens (self | counterparty:X | domain | auto)                       │
│  As-of parameter for point-in-time (v0.4)                           │
│  Cross-counterparty isolation at query level (v0.3, hard)           │
│  → emit retrieval_trace event → privacy filter → redactor → out     │
└────────────────────────────┬────────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────────┐
│         OUTBOUND APPROVAL (§5, adapter spec)                        │
│                                                                     │
│  identity check → self-contradiction → privacy → egress → deliver   │
└────────────────────────────┬────────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────────┐
│                    HEALING (§4.8)                                   │
│  Hard invariants (halt): privacy, PII, cross-counterparty           │
│  Soft invariants (flag): identity drift, low-confidence assertions  │
│  Repair library · Quarantine · Review queue                         │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.3 Governance contracts (16 rules total)

| # | Rule | Source |
|---|---|---|
| 1 | Events are immutable | v0.0 → v0.1 (strengthened) |
| 2 | Derived state is disposable, rebuildable from events | v0.0 |
| 3 | Scope tightening automatic; loosening explicit only | v0.0 → v0.3 (clarified) |
| 4 | Secrets never appear in embeddings | v0.0 |
| 5 | Invariants declarative | v0.0 |
| 6 | Provenance on everything | v0.0 |
| 7 | Retrieval never writes to neuron state synchronously | v0.1 |
| 8 | Every neuron mutation emits an event | v0.1 |
| 9 | Single writer per table | v0.0 |
| 10 | Events never truncated by default | v0.2 |
| 11 | Identity documents are authoritative, not derived | v0.3 |
| 12 | Cross-counterparty retrieval structurally forbidden in normal API | v0.3 |
| 13 | Pillar conflict hierarchy (privacy > counterparty > persona > factual) | v0.3 |
| 14 | Every neuron cites at least one specific source event | v0.4 |
| 15 | Retrieval ranking uses `distinct_source_count`, not `source_count` | v0.4 |
| 16 | Validity-time fields never fabricated | v0.4 |

These are the test suite. Each rule has a failure mode known to have hurt other teams or to have been identified in review. None is theoretical.

### 2.4 What the blueprint explicitly rejects

For the sake of being explicit about what we are NOT building:

- Multi-tenant per-instance (v0.3: schema-ready, deployment-scoped single)
- Multi-instance mesh sync (v0.0 non-goal)
- Built-in UI (v0.0 non-goal)
- Agent framework replacement (v0.0 non-goal — we are memory, not the agent)
- Own LLM training (v0.0 non-goal — we consume, don't train)
- Automatic identity modification by LLM verdict (v0.3 rule 11)
- Cross-counterparty recall via normal API (v0.3 rule 12)
- Zero-hallucination claim (v0.4 §18 acknowledges SOTA ceiling)

---

## 3. Remaining Gaps (Ranked)

Fifteen identified. Ranked by whether they block the 80% target or not. "Block" means 80% is unachievable without addressing it; "degrade" means 80% is achievable but less robust; "deferred" means it's a real gap but safe to handle after shipping.

### 3.1 Blocks 80% — fix before first production deployment

**Gap 1. Observability runbook.**
v0.2 specified `/metrics` and structured logs. Nothing specifies dashboards, alerting thresholds, or on-call procedures. Without these, an operator discovers problems from users, not from monitoring.
**What's needed:** Grafana dashboard JSON exported with the repo, alert rules for each hard invariant, a one-page runbook per alert.
**Size:** 1 week.

**Gap 2. Backup and disaster recovery.**
Snapshots (v0.2 §20) are an in-process optimization, not a backup strategy. There is no spec for offsite backups, recovery drills, or RPO/RTO targets. A disk failure or ransomware event would lose everything.
**What's needed:** Documented backup procedure (pg_dump or SQLite .backup + encrypted offsite copy), scheduled drill requirement, recovery time measured.
**Size:** 1 week.

**Gap 3. Prompt template versioning.**
Extraction prompts will evolve. Right now a prompt change silently affects every future extraction with no rollback path. If a prompt regression causes the grounding gate to misbehave, there's no way to compare before/after.
**What's needed:** `prompt_templates` table with version, commit hash, hot-reload, A/B harness that routes a small fraction of events through a candidate prompt and compares grounding-gate pass rates.
**Size:** 1–2 weeks.

### 3.2 Degrades 80% — fix before scaling past one persona

**Gap 4. Multi-modal ingest.**
Your Digital Brain handles voice notes via Whisper and Sherpa. v0.4's grounding gate assumes extraction works on text. Image, voice, and document ingestion each need a pipeline step that produces text (or fails loudly) before entering the grounding gate. No spec exists.
**What's needed:** Adapter pattern per modality (voice → transcript with confidence; image → OCR + caption with confidence; document → chunked text with source spans). Modal confidence is a new metadata field on events. Below-threshold confidence events are quarantined before the grounding gate even runs.
**Size:** 2–3 weeks.

**Gap 5. Cold-start problem.**
A fresh twin has no memory. For the first week of deployment it has nothing useful to recall. Users may abandon before the twin becomes useful.
**What's needed:** Seed mechanism. Operator can ingest a "persona background" document at init that becomes low-confidence `self_fact` neurons and gets reinforced or superseded by real interactions. Identity document already serves partly as this; extending it to a seedable memory document closes the gap.
**Size:** 1 week.

**Gap 6. Embedder rotation and re-embedding.**
v0.4 has `embedder_rev` on neurons. No spec for how to migrate when the embedder upgrades. A local-to-remote embedder switch requires re-embedding every neuron; running ad-hoc on a busy system will degrade retrieval during the migration window.
**What's needed:** Batch re-embedding tool with progress tracking, shadow-period comparison between old and new embeddings on a held-out query set before cutover, ability to roll back.
**Size:** 1 week.

**Gap 7. Quarantine review workflow.**
The quarantine table exists (v0.3 healing, v0.4 grounding gate). There is no UI, no triage workflow, no SLA for review, no escalation rule. Quarantine will fill up and operators will ignore it, which means legitimate memories stay rejected and extraction problems go undiagnosed.
**What's needed:** Minimal CLI review tool, SLA target in config (e.g. quarantine older than X days triggers alert), auto-categorization of quarantine reasons so operator can review by type not one-by-one.
**Size:** 1 week.

### 3.3 Degrades 80% — fix before multi-persona deployment

**Gap 8. Cross-persona domain knowledge sharing.**
An organization with multiple digital employees (sales twin, support twin, HR twin) will share domain knowledge (company policies, product info) but isolate counterparty memory. v0.3 noted this as deferred. Without a sharing mechanism, every persona duplicates domain knowledge; with a naïve sharing mechanism, counterparty isolation breaks.
**What's needed:** `shared_domain_library` concept at deployment level. Only `domain_fact` neurons with scope=`shared` or `public` can enter it. Personas query their own memory first, then fall back to the shared library for domain queries only (never counterparty or self queries). Explicit opt-in per persona.
**Size:** 2 weeks. Only when second persona is deployed.

**Gap 9. Rate limiting and abuse detection.**
A counterparty sending 10,000 messages in an hour fills working memory, strains the consolidator, and inflates cost. Nothing in the blueprint prevents this.
**What's needed:** Per-counterparty rate limits on ingest, configurable in identity document. Burst allowances for legitimate heavy traffic. Graceful degradation: reject-with-retry-after header at WhatsApp MCP boundary, not at storage.
**Size:** 1 week.

### 3.4 Deferrable — real gaps, safe to handle later

**Gap 10. Adversarial robustness threat model.**
v0.4 §23 handles prompt injection pattern detection. A full threat model — memory poisoning, retrieval-time manipulation, vault extraction attempts, timing side channels, embedder adversarial inputs — does not exist.
**Size:** 2 weeks of dedicated work. Defer until deployment context is more concrete. Production is where adversaries surface.

**Gap 11. Legal/regulatory compliance beyond GDPR erasure.**
Protective forgetting covers the right to erasure. DSAR responses (what data do we hold on Alice), consent tracking (did Alice consent to this processing), audit trail export for regulators — all absent.
**Size:** 2–3 weeks. Defer until a jurisdiction actually requires it.

**Gap 12. Canary deployments and safe prompt rollout.**
Gap 3 (prompt versioning) opens the door. A full canary system — route 1% of events to new prompt, monitor grounding gate pass rate and hallucination metric for 24 hours, auto-promote or auto-rollback — is a maturity feature.
**Size:** 2 weeks. Defer.

**Gap 13. Schema migration for breaking changes.**
Additive-only works for v1.x. At some point a breaking change will be needed (e.g. splitting `neurons` by kind into separate tables for performance). No story yet.
**Size:** 1–2 weeks when needed. Defer.

**Gap 14. Performance under load.**
No benchmarks establish how the system behaves when events arrive faster than the consolidator can process. SQLite single-writer will bottleneck at some rate; Postgres+pgvector at a higher rate. No specified backpressure mechanism.
**Size:** 1 week of load testing + backpressure spec. Defer until real load is observable.

**Gap 15. Multi-device and multi-process coordination.**
One MCP per persona from the WhatsApp adapter. What about two MCPs for redundancy? Two consolidator processes for parallelism? v0.0 made single-process a principle; scaling beyond one process is explicit non-goal but will be asked for.
**Size:** substantial. Defer indefinitely.

### 3.5 Summary of the gap table

| Severity | Count | Effort to close all | Block 80% target? |
|---|---|---|---|
| Blocks first deployment | 3 | ~3–4 weeks | Yes |
| Degrades single-persona deployment | 4 | ~5–7 weeks | Partially |
| Degrades multi-persona deployment | 2 | ~3 weeks | Only if multi-persona |
| Deferrable | 6 | Substantial | No |

**Recommended pre-deployment work beyond v0.4:** ~3–4 weeks to close the three blocking gaps. Total pre-deployment: v0.4 roadmap (20–26 weeks half-time) + 3–4 weeks for blocking gaps = **23–30 weeks half-time solo** to a defensibly-operable first production deployment.

---

## 4. What 80% Reliable Orchestration Means, Given This Blueprint

The phrase resolves differently for different stakeholders. Here is the honest breakdown.

### 4.1 For the operator building the system

**What is reliable:** Schema integrity, privacy invariants, event log durability, identity protection, signed-MCP boundaries, audit trails, point-in-time replay. These are mechanical and testable. Target them at 100% (not 80%) and break deployment on failure.

**What is 80%:** Retrieval returns the most relevant memories for most queries. Extraction correctly identifies and grounds most salient facts. Contradictions get caught before they propagate most of the time. Identity drift is flagged for review before it entrenches.

**What is below 80%:** First-month deployment before memory accumulates. Adversarial scenarios not yet in the threat model. Multi-modal content that slipped past the confidence gate. Queries about time windows the twin never observed.

### 4.2 For the person the twin is talking to

They experience the system as "the twin mostly remembers correctly, sometimes says it's not sure, occasionally forgets." The correct experience, if the blueprint is implemented well:

- Common scenario: "What did we discuss about the solar quote last month?" → correct recall with a citation and a timestamp. Works 80%+ of the time.
- Edge case: "What do you know about me overall?" → summary that's directionally correct but missing nuance. Works most of the time; failures are noticeable but not harmful.
- Confusion case: Complex multi-session question requiring temporal reasoning → sometimes gets it, sometimes hedges appropriately, sometimes wrong. Because of the bi-temporal model, when it's wrong it's wrong with dated citations, which makes the error diagnosable.
- Failure case: Something happened 3 months ago with contradictory information since → twin says "I have conflicting information, let me show you what I know" rather than confidently stating one version. Hedging behavior is a feature.

### 4.3 For the person whose privacy the system protects

**This stays 100%, not 80%.** The hard invariants (cross-counterparty leak, PII egress, scope leak) are not probabilistic. They halt the system on violation. If the blueprint is implemented correctly, the leak rate is not "rare" — it is zero in the design. If it's not zero in production, that's a critical bug requiring immediate investigation, not a statistical tolerance.

This distinction is why §4.8 puts privacy as the top pillar in the conflict hierarchy. Everything else degrades gracefully; privacy does not degrade.

### 4.4 What breaks the 80% target

Most likely failure modes, in order of probability:

1. **Extractor prompts not tuned for actual production traffic.** SOTA systems fail here. v0.4 grounding gate mitigates by quarantining ungrounded outputs, but an over-aggressive gate rejects real memories. Requires production data to calibrate.

2. **Embedder choice doesn't match query patterns.** Default embedder works for general English; digital-twin use cases involving technical terminology, proper nouns, or multilingual content may need fine-tuning or alternative embedders.

3. **Operator ignores quarantine queue.** Gap 7 directly. Quarantine fills with rejected extractions, operator doesn't review, real memories stay missing, twin seems forgetful.

4. **Cold-start problem unresolved.** Gap 5 directly. First week of deployment looks broken to users; they churn.

5. **Dashboards missing or thresholds wrong.** Gap 1 directly. Invariant violations go unnoticed until a user complains. By then, damage is larger than necessary.

Each of these is addressable, and each is in the gap list above. None of them indicate a design flaw; they indicate that the blueprint, while complete, is not self-operating.

---

## 5. Recommendations

In priority order.

### 5.1 Before writing the first line of production code

- Close gaps 1, 2, 3. Cost: 3–4 weeks. Pays back on the first production incident.
- Do not attempt to close more than those three. Deferring everything else is correct.

### 5.2 Sequence the v0.4 roadmap right

Start Phase 0 with the schema including `personas`, `counterparties`, `t_valid_start`, `t_valid_end`, `distinct_source_count`, `embedder_rev`, `version` from day one. Adding these later requires migrations. Adding them now is a one-time schema decision.

### 5.3 Stop revising the blueprint

This document is v0.5-candidate. Resist promoting it to v0.5. The blueprint has matured to the point where further design produces diminishing returns and implementation produces compounding returns. Every week spent building is worth two weeks spent designing at this stage.

The exception is if implementation discovers a contradiction or unreachable constraint — that deserves a targeted revision, in the v0.1 diff style, not a full new version.

### 5.4 Set success criteria before starting

Before writing code, commit to what "working" means at 30, 60, 90 days:

- **Day 30:** Phase 0 complete. Can ingest, recall, and log events end-to-end. No consolidator yet. Privacy invariants wired and tested.
- **Day 60:** Consolidator producing neurons under grounding gate discipline. Retrieval with all three streams + RRF. First eval run against synthetic fixtures.
- **Day 90:** Identity protocol, quarantine review, WhatsApp adapter MVP. First internal user (you) can have a week-long conversation and trust the recall results.

These are calibration points. If 90-day progress deviates significantly, revise plan, not targets.

### 5.5 Establish the eval baseline first thing in production

The moment real traffic starts, capture 100 recall queries with expected results. Weekly re-run. This is your regression detector. Without it, quality drifts invisibly.

### 5.6 Publish the blueprint as an artifact of the build

When the build is underway and the first internal deployment is stable, release v0.0–v0.4 + adapter spec publicly. Not as a product announcement. As a contribution. The field has Mem0, Zep/Graphiti, MemoryOS, and a dozen commercial systems. None of them have the combination of identity layer + hard privacy invariants + bi-temporal modeling + grounding gate + event-sourced integrity that this blueprint specifies. Others will want to build on it or critique it; both are useful.

---

## 6. The One-Paragraph Summary

You set out to build a memory system that works like a human brain, holds privacy boundaries, self-heals, and reduces hallucination. The blueprint delivers the substrate for all of that, in five revisions that collectively cover base architecture, contradiction fixes, evaluation honesty, identity layer for digital twins, and anti-hallucination hardening informed by other teams' production failures. The system is specified for single-persona deployment with schema-ready multi-persona scale-out, first channel adapter is WhatsApp with cross-counterparty isolation structurally enforced, and 16 governance rules encode the failure modes we want to prevent. Three operational gaps (observability, backup, prompt versioning) block first production deployment and need ~3–4 weeks of additional work. Six more gaps degrade experience but are not blockers. Six are safely deferrable. The 80% reliability target decomposes into hard 100% targets for privacy invariants and soft 75–95% targets for probabilistic capabilities like extraction and retrieval, in line with current SOTA honestly assessed. The remaining work is implementation, not design. Stop revising the blueprint.

---

*End of synthesis. Six documents now constitute the complete blueprint: v0.0, v0.1, v0.2, v0.3, v0.4, WhatsApp adapter spec, and this synthesis. The next document in the stack should be a commit message in the repository that implements Phase 0, not another design document.*
