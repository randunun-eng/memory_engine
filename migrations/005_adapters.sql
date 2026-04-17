-- ========================================================================
-- Migration 005: MCP Adapters (Phase 5)
-- Created: 2026-04-16
--
-- Creates mcp_sources (per-persona MCP binding with Ed25519 public key
-- and hashed bearer token) and tombstones (soft deletion / reingestion
-- prevention). Adds sender_hint to events for group messages.
--
-- The mcp_source_id column already exists on events (from 001_initial.sql)
-- without a FK constraint. SQLite doesn't support ALTER TABLE ADD CONSTRAINT,
-- so we enforce the relationship in code. The column is populated at ingest
-- when the MCP source is known.
-- ========================================================================

-- MCP sources: one per persona per adapter. The public key verifies
-- event signatures; the token_hash authenticates API requests.
CREATE TABLE mcp_sources (
  id                    INTEGER PRIMARY KEY,
  persona_id            INTEGER NOT NULL REFERENCES personas(id),
  kind                  TEXT NOT NULL CHECK (kind IN ('whatsapp')),
  name                  TEXT NOT NULL,
  public_key_ed25519    TEXT NOT NULL,       -- base64-encoded Ed25519 public key
  token_hash            TEXT NOT NULL,       -- SHA-256 of bearer token; token shown once
  registered_at         TEXT NOT NULL DEFAULT (datetime('now')),
  revoked_at            TEXT,
  UNIQUE (persona_id, name)
);

CREATE INDEX ix_mcp_sources_persona_active
  ON mcp_sources(persona_id)
  WHERE revoked_at IS NULL;

-- Tombstones: soft deletion markers that prevent reingestion.
-- Scope is a pattern: 'counterparty:<external_ref>', 'event:<id>',
-- 'idempotency:<key>', 'content_hash:<hash>'.
CREATE TABLE tombstones (
  id            INTEGER PRIMARY KEY,
  persona_id    INTEGER NOT NULL REFERENCES personas(id),
  scope         TEXT NOT NULL,
  reason        TEXT,
  created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX ix_tombstones_persona_scope
  ON tombstones(persona_id, scope);

-- Add sender_hint to events for group messages.
-- Stores the individual sender within a group (e.g. "whatsapp:+94771234567")
-- as metadata only. Never used in retrieval queries — only for audit.
ALTER TABLE events ADD COLUMN sender_hint TEXT;
