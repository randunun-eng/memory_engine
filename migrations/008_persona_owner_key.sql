-- Migration 008: per-persona owner public key
--
-- Additive migration. Adds owner_public_key column to personas so the
-- consolidator can sign rule-8 neuron-mutation events with a per-persona
-- key instead of a shared env-var key. See DRIFT
-- `consolidator-ai-studio-shared-key`.
--
-- Phase 7 alpha is single-persona so the shared env key works today. This
-- migration is the prerequisite for multi-persona deployments where each
-- persona has its own owner. NULL is allowed for backward compatibility;
-- the consolidator falls back to the env key when the column is NULL.
--
-- Private keys are NOT stored here. Operator keys remain in env (or,
-- later, in secret_vault — governance rule §11). Only the PUBLIC key
-- goes on the persona row, enabling third-party signature verification
-- against a known-to-the-deployment public key.

ALTER TABLE personas ADD COLUMN owner_public_key TEXT;

CREATE INDEX IF NOT EXISTS ix_personas_owner_public_key
  ON personas(owner_public_key)
  WHERE owner_public_key IS NOT NULL;
