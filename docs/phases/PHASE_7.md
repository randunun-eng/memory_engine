# Phase 7 — First Internal User

> **Status:** Blocked on Phase 6.
>
> **Duration:** 3 weeks (operator time, not build time).
>
> **Acceptance criterion:** One persona runs live for three weeks talking to one internal counterparty (the operator). At the end of three weeks: no critical invariant violations, zero T3 leaks, ≥ 70% citation-ground-truth accuracy on spot-checks, prompt comparison data for at least 2 sites, eval baseline recorded in `tests/eval/baseline_phase7.yaml`.

---

## Goal

The system runs in production conditions with a forgiving first user — the operator themselves. This phase discovers calibration bugs, performance issues, identity document shortcomings, and missing invariants that only reveal themselves under real usage. No new code, intentionally. The work is observation and refinement.

Phase 7 is not about building more. It's about learning what the previous six phases actually produced. If Phase 7 reveals problems, they get fixed before a second user touches the system.

---

## Prerequisites

- Phase 6 complete. Dashboards, backups, prompt versioning all running.
- A running deployment (Oracle Cloud ARM VM, self-hosted Linux box, or similar).
- A registered WhatsApp Business number.
- A signed identity document for the persona.
- An operator willing to use the twin for real interactions.

---

## Week 0 — Preparation

Before going live, verify:

- `memory-engine doctor` passes all checks.
- Backup job scheduled and has produced at least 3 successful artifacts.
- Monthly drill completed once (even if synthetic).
- All 12 alerts fire on synthetic triggers without issue.
- Dashboards render with sample data.
- T3 and T11 test suites pass on the deployed binary (not just in CI).
- Prompt templates seeded for every registered site.

Document the deployment state in `docs/runbooks/phase7_deployment.md` — VM specs, install versions, config overrides, persona slug, WhatsApp number.

---

## Week 1 — Low stakes, high observation

Seed the twin with a minimal identity document:

```yaml
persona_slug: sales_twin
role:
  title: "Personal assistant twin"
  domain: "testing"
  responsibilities:
    - "Respond to messages when the operator is unavailable"
non_negotiables:
  - id: nn_1
    rule: "Do not send money, commit to purchases, or share account credentials."
    evaluator: "pattern"
    trigger_patterns: ["send .*money", "buy .*for me", "password", "credential"]
    severity: "block"
  - id: nn_2
    rule: "Do not impersonate the operator to their family or close contacts."
    evaluator: "llm"
    severity: "block"
```

Register ONE counterparty: the operator's own secondary WhatsApp number (e.g., a spare SIM). Send test messages. Observe:

- Events flow in within < 2s.
- Consolidator runs every 5 minutes and produces neurons.
- Retrieval under counterparty lens returns expected results.
- Outbound approvals happen and log cleanly.
- No invariant violations logged.
- Dashboard shows reasonable rates.

If anything is off, fix it. This week's bugs are normal. Expect to ship small patches daily.

**Metric target:** by day 7, ingest → neuron latency < 30 minutes at p99.

---

## Week 2 — Real counterparty, boundaries tested

Add a second counterparty: someone the operator trusts to test boundaries without judgment. Brief them that the twin may block some messages — that's expected.

Ask them to try:
- Asking about pricing (should trigger non-negotiable if configured).
- Asking in a language other than English.
- Attaching an image.
- Referring to something said last week (tests retrieval).
- Sending a deliberately contradictory statement after an earlier one.
- Sending a prompt-injection-style message.

Log all observations in `docs/runbooks/phase7_observations.md`. Structure:

```markdown
## 2026-04-22 10:14 UTC

**Counterparty:** Alex (secondary number)
**Event:** Alex asked "what did you tell me about the move last month"
**Expected:** retrieval returns neuron about the September move
**Observed:** retrieval returned nothing; neuron was never created
**Diagnosis:** Consolidator is set to 15-minute intervals; the event was from 12 min ago, not yet consolidated
**Action:** tighten interval to 5min for working→episodic promotion; log as DRIFT entry

```

**Metric target:** retrieval quality (operator-judged) > 60% useful by end of week 2.

---

## Week 3 — Stress, backup drill, eval baseline

Week 3 is about:

1. **Volume.** Encourage normal daily use from the primary counterparty. Let the event log grow. Monitor DB size trend, healer run time trend, recall p99. If p99 spikes, investigate before it becomes a pattern.

2. **Backup drill.** Perform a full restore drill on a VM that's not the production host. Time it. Target: < 2 hours from "start" to "service restored and serving traffic." If it takes longer, reduce backup artifact size (vacuum the DB, prune old media).

3. **Prompt tuning.** At least one prompt template should be shadow-tested during Phase 7. Pick the call site with lowest observed accept rate. Design a tighter variant. Run shadow at 20% traffic for 72 hours. Compare; promote or reject.

4. **Eval baseline.** Extract 50 query/expected-neuron pairs from the accumulated data. Curate them into `tests/eval/baseline_phase7.yaml`. Run `pytest tests/eval` to capture the first real MRR@10 number. This becomes the regression baseline for future changes.

**Metric target:** at end of week 3, the dashboards show:
- p99 recall < 500ms
- Grounding reject rate < 15%
- Invariant violations: zero critical, < 5 warnings total across the week
- LLM cost < planned budget
- Identity drift flags: all reviewed

---

## What to document at the end

When week 3 closes, Phase 7 produces four documents:

1. **`docs/runbooks/phase7_deployment.md`** — the deployment state in full, so a second deployment can be made from it.
2. **`docs/runbooks/phase7_observations.md`** — the log of what happened.
3. **`tests/eval/baseline_phase7.yaml`** — the regression baseline.
4. **`docs/adr/0007-phase7-learnings.md`** — one ADR documenting the three most important things Phase 7 taught us.

Prompt for the fourth document: what surprised you, what worked better than expected, what needs to change before a non-operator user gets access.

---

## Tests

No new source tests. Phase 7 is operational. Tests added during Phase 7 are:

- Regression tests for bugs found in weeks 1 and 2. Each fix gets a test.
- Eval baseline at `tests/eval/baseline_phase7.yaml`.

---

## Out of scope for this phase

- A second persona. Adding a second persona while the first is still unstable is asking for trouble.
- Public access. Phase 7 is internal-only.
- Marketing, press, or demos. If curious people ask, point them to the repo; do not schedule formal showcases until Phase 8 (not in this plan).
- New features. Anything shiny that comes up goes to the backlog, not Phase 7.

---

## Common pitfalls

**Operator bias.** You are both the developer and the user. You will unconsciously adapt your behavior to work around bugs rather than reporting them. Explicitly write down every "friction moment" even when you've already mentally resolved it. Your second user will not be forgiving.

**Blended roles.** The twin is talking to *you* in one counterparty, while *you* are also operating it from the other side. It's easy to blur. Stay in character: when you're the counterparty, you're the counterparty. Use the dashboard to verify behavior; don't self-debug by inspecting the log mid-conversation.

**Fix-on-the-fly temptation.** You'll want to patch bugs live. Resist the worst cases. Critical bugs get an immediate fix (halt → patch → release halt). Non-critical bugs get a note; batch and ship weekly. Too many live patches = unclear release history = hard to debug the next regression.

**Skipping the drill.** Week 3's drill is the one most likely to be skipped. "Already running fine, I'll drill next month." Don't. The drill proves the backup is real.

**Over-interpreting three weeks.** Three weeks is not enough to judge quality definitively. The eval baseline captures a snapshot. Use it as a reference point, not a verdict.

**Identity doc too permissive.** Phase 7's starter identity is minimal. The twin will sometimes respond when it should refuse. Add non-negotiables as you discover the gaps. By end of week 3, expect 6–10 non-negotiables, not the 2 you started with.

---

## After Phase 7

The repo has shipped the original blueprint. The system is operational. Further work is planned separately:

- **Phase 8+ (not in this doc):** a second persona, a non-operator counterparty, a second channel (likely Slack or email).
- **v1.0 cutover:** freeze the schema, issue a release, begin semantic versioning from that point.
- **Community release:** only after v1.0. The project is Apache-2.0 from day one, but active support for external users is a Phase 9+ concern.

The 24-week plan ends here. Anything beyond requires a new planning cycle.

---

## When Phase 7 closes

Tag: `git tag phase-7-complete` and `git tag v0.7.0`. Update `CLAUDE.md` §8 to say "Phase 7 complete. System operational. Next cycle: see `docs/phases/POST_PHASE_7.md` (to be authored)."

Commit message: `milestone(phase7): three weeks live; baseline captured`.
