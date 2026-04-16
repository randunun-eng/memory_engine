# Security Policy

## Supported Versions

memory_engine is pre-v1.0. During the blueprint-execution phases (0 through 7), only the `main` branch is supported. Security fixes land there and are tagged to the current phase's release.

| Version | Supported |
|---|---|
| main    | ✅ |
| < v1.0  | ❌ (pre-release; no backports) |

Post-v1.0 support policy will be defined in an ADR before v1.0 is cut.

## Reporting a vulnerability

**Do not open a public issue for a security vulnerability.** Email the maintainer directly. A PGP / age key for encrypted mail is available at `docs/security_keys.txt` (to be added post-v1.0; for now, the maintainer's public key is pinned on their personal page).

Include in your report:
- A description of the vulnerability.
- Steps to reproduce, with minimal examples.
- The git commit SHA you tested against.
- Your assessment of severity (informational, low, medium, high, critical).
- Any proof-of-concept code (attach separately; do not embed inline).
- Whether you intend to publish and on what timeline.

## What we do in response

1. **Acknowledge** within 48 hours. If no acknowledgment in that window, resend; email deliverability occasionally fails.
2. **Triage** within 7 days. Severity assessment and rough timeline shared with reporter.
3. **Fix** on a timeline appropriate to severity:
   - Critical (active exploitation, data leak, privilege escalation): patch within 7 days, coordinated disclosure.
   - High (pre-exploitation potential, serious misuse): patch within 30 days.
   - Medium/Low: next planned release cycle.
4. **Disclose** publicly once a patch is available. Credit the reporter unless they prefer anonymity.

## Scope

**In scope:**
- Bypass of governance rules (see CLAUDE.md §4). Especially rules 1, 3, 11, 12, 14, 15.
- Unauthorized cross-counterparty data access (T3).
- Prompt-injection-caused data leak, non-negotiable bypass, or identity-document modification (T11).
- Signature forgery against registered MCP public keys.
- Vault master-key extraction from logs, metrics, or error messages.
- SQL injection (should be impossible given parameterized-only discipline).
- DoS that can be triggered without authenticated access.

**Out of scope:**
- Issues requiring root on the deployment host.
- Issues in third-party dependencies. Report to the dependency upstream; we'll update once the upstream fix is available.
- Theoretical attacks on Ed25519 or SHA-256.
- Rate limiting at the FastAPI layer (deployment concern).
- TLS configuration at the reverse-proxy layer.
- Social engineering of operators.

## Safe harbor

Good-faith security research that:
- Avoids privacy violations of operators or counterparties,
- Does not publicly disclose before we've had time to patch,
- Does not intentionally degrade service for others,
- Stays within this policy's scope,

is welcomed and will not be pursued legally. We appreciate responsible disclosure.

## Known security model limitations

See `docs/SECURITY.md` "Out of scope" for the threat model we do NOT defend against. A report exploiting one of these is still interesting; we'll document the tradeoff but may not treat it as a vulnerability.
