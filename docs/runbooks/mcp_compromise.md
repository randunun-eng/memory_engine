# Runbook: MCPAuthFailureSpike

**Severity:** critical

**What this means (one sentence):**
Sustained MCP authentication failures — possible credential compromise, token rotation gone wrong, or adversary probing.

**Immediate action (do first):**
1. Open Dashboard A, look at "MCP auth failures" panel. Which MCP is failing?
2. Check the reason breakdown:
   ```sql
   SELECT json_extract(payload, '$.reason'), COUNT(*)
   FROM events WHERE type = 'operator_action'
     AND json_extract(payload, '$.event') = 'mcp_auth_failure'
     AND recorded_at > datetime('now', '-10 minutes')
   GROUP BY 1;
   ```
3. If reason is `signature_invalid` with fresh IPs → treat as compromise.
   If reason is `token_expired` after a recent rotation → rotation issue, not adversary.

**Diagnostic steps:**
- Check recent `mcp_sources` rotations: `SELECT * FROM mcp_sources WHERE persona_id = ? ORDER BY registered_at DESC`.
- Look at source IPs in the access log (if enabled).
- Compare with legitimate MCP's expected behavior (one host, steady rate).

**Common causes, most to least likely:**
1. **Operator rotated token but MCP didn't pick up new token.** Fix: redeploy MCP with new token. Old token stays valid for 24h overlap window.
2. **Token leaked (logs, git history, config pushed to wrong repo).** Fix: revoke immediately via `memory-engine mcp revoke <mcp_id>`. Register new token. Audit log history.
3. **Adversary probing.** Fix: revoke + rotate. Block source IP at network layer. Escalate.
4. **Clock skew on MCP host (if using time-bound tokens).** Fix: fix NTP on MCP host.

**Immediate containment:**
```bash
# Revoke the suspect MCP
uv run memory-engine mcp revoke <mcp_id>

# Register a fresh MCP binding (new keypair, new token)
uv run memory-engine mcp register --persona <slug> --kind whatsapp --name whatsapp-fresh
```

**Escalation:**
If compromise is confirmed, escalate immediately. Preserve logs. Do not delete the revoked mcp_sources row — it's evidence.

**Related:**
- runbooks/mcp_rotation.md — normal token rotation procedure
- runbooks/whatsapp_setup.md — initial MCP registration
