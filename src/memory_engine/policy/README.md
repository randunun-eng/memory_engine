# `memory_engine.policy`

The policy plane. **Every LLM call in memory_engine flows through here.** This is not a suggestion; it is enforced by CI (`.github/workflows/test.yml` → `integrity` job).

## What belongs here

- `dispatch.py` — the single entry point for LLM calls. Call sites are enumerated in `sites.py`.
- `registry.py` — prompt template storage and loading. Hot-reloads from the `prompt_templates` table.
- `broker.py` — context broker. Projects the incoming context dict to only the fields the site's schema declares. Implements "field projection" from the cost-optimization design.
- `cache.py` — persona-scoped prompt cache. Key format is `(persona_id, site, prompt_hash, input_hash)`. Missing `persona_id` raises `CacheKeyInvalid` — there is no global cache (R9).
- `llm_client.py` — OpenAI-compatible HTTP client. Can talk to OpenAI, Anthropic, a local LiteLLM proxy, Ollama with an OpenAI shim, etc.
- `signing.py` — Ed25519 sign and verify for MCP signatures (see ADR 0005).
- `sites.py` — registered call sites with their `SiteSchema` (required fields, optional fields, output parser).
- `prompts/*.md` — prompt template source files. In Phase 2 they live on disk; from Phase 6 they move to the DB-backed registry.

## What does NOT belong here

- Business logic (→ `memory_engine.core`, `memory_engine.outbound`).
- DB migration or connection code (→ `memory_engine.db`).
- Retrieval (→ `memory_engine.retrieval`). Retrieval *uses* policy via dispatch for the auto-lens classifier, but the retrieval logic itself is separate.

## Why this module is a choke point

Every LLM call ends up here so that:

- **Cost tracking** happens in one place. One counter increment per call.
- **Prompt versioning** is uniform. Phase 6's shadow harness intercepts every site equally.
- **Caching** is enforced to be persona-scoped (R9). Skipping the cache means a bypass; not possible without modifying this module.
- **Rate limiting** can be added without hunting through the codebase.
- **Injection-defensive framing** is applied uniformly to every prompt that includes counterparty-provided text.
- **Audit** is trivial — one log line per dispatch.
- **Testing** works. Tests mock `dispatch`; real LLM endpoints are only touched by `tests/eval/`.

## CI enforcement

The workflow `integrity` job greps for `from openai`, `from anthropic`, `from litellm` across `src/memory_engine/` and fails if any import is outside this module. Do not rationalize an exception; if you need a raw LLM client somewhere, the policy plane is the place.

## Conventions

- Sites are added by adding a `SiteSchema` to `sites.py` and a prompt template file. Never by adding a new dispatch function.
- Prompt templates declare their parameters in a JSON schema; the broker validates before rendering.
- Output parsers are permitted to fail; `dispatch` catches and surfaces a structured error to the caller.
- Every prompt that incorporates untrusted counterparty text has a defensive preamble (see `docs/SECURITY.md` R10).
