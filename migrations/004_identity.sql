-- Migration 004: Identity + Counterparties (Phase 4)
--
-- Identity drift flags track when the LLM detects potential contradictions
-- between a candidate outbound message and the persona's identity document.
-- The operator reviews and decides (accept/reject/quarantine).
--
-- Tone profiles are per-counterparty cached analysis of communication style.
--
-- IMPORTANT ARCHITECTURAL NOTE:
-- Identity document changes affect OUTBOUND evaluation going forward.
-- They do NOT retroactively modify existing neurons.
-- Memory remembers what it observed; egress decides what to say.
-- Non-negotiables are an egress concern, not a memory concern.

CREATE TABLE identity_drift_flags (
  id              INTEGER PRIMARY KEY,
  persona_id      INTEGER NOT NULL REFERENCES personas(id),
  flag_type       TEXT NOT NULL CHECK (flag_type IN (
    'value_contradiction', 'role_drift', 'tone_drift',
    'nonneg_violation', 'forbidden_topic'
  )),
  candidate_text  TEXT NOT NULL,
  rule_text       TEXT,                    -- the non-negotiable or self_fact that was violated
  flagged_at      TEXT NOT NULL DEFAULT (datetime('now')),
  reviewed_at     TEXT,
  reviewer_action TEXT CHECK (reviewer_action IS NULL OR reviewer_action IN ('accept', 'reject', 'quarantine'))
);

CREATE INDEX ix_identity_drift_unreviewed
  ON identity_drift_flags(persona_id, flag_type)
  WHERE reviewed_at IS NULL;

CREATE TABLE tone_profiles (
  counterparty_id INTEGER PRIMARY KEY REFERENCES counterparties(id),
  profile_json    TEXT NOT NULL,
  analyzed_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
