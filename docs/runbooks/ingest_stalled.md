# Runbook: EventLogStalled

**Severity:** critical

**What this means (one sentence):**
The process is up (`/health` returns 200) but no events are appending — ingest is broken in a way that doesn't crash the service.

**Immediate action (do first):**
1. Check halt state: `uv run memory-engine halt status`. If halted, resolve via `hard_invariant.md`.
2. Check MCP liveness: does the MCP process still run? Does it receive messages? Ping the MCP health endpoint.
3. Tail engine logs for recent errors:
   ```bash
   journalctl -u memory-engine --since "15 min ago" | grep -E '(ERROR|WARNING)'
   ```

**Diagnostic steps:**
- Confirm network path: MCP → engine `/v1/ingest` reachable?
- Try a manual ingest with a known-valid signed payload. If it succeeds, the MCP is at fault. If it fails, the engine is at fault.
- Check `mcp_sources` for active rows. If all revoked, no MCP is authorized.
- Check disk space on the engine host (`df -h`). A full disk blocks SQLite writes without clear errors.

**Common causes, most to least likely:**
1. **Halt engaged silently.** Fix: see `hard_invariant.md`.
2. **MCP crashed / disconnected / session expired.** Fix: restart MCP. For WhatsApp, may need device re-scan.
3. **Disk full.** Fix: free space or expand volume. SQLite needs ~1.5x the DB size free for WAL checkpoints.
4. **Network partition between MCP and engine.** Fix: restore network; MCP idempotency keys prevent duplicate ingest on retry.
5. **Bad token rotation.** Fix: see `mcp_compromise.md`.

**Escalation:**
If the stall persists > 1 hour and no root cause is found, escalate. Users are noticing.

**Related:**
- runbooks/mcp_compromise.md
- runbooks/whatsapp_setup.md
