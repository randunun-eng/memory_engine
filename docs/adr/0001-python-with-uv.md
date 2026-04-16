# 0001 — Python 3.12 with uv

## Status

Accepted — 2026-04-16

## Context

memory_engine is a reference implementation of a blueprint for a digital twin memory layer. The implementation language choice affects:

- Developer velocity (how fast can features ship).
- LLM ecosystem integration (LiteLLM, OpenAI SDK, embedders, tokenizers).
- Async I/O for the policy plane (LLM calls dominate wall time).
- Ease of auditing (solo operator + future contributors).
- Deployment to an Oracle Cloud ARM VM or equivalent constrained host.

Python 3.12 specifically offers: exception groups, improved error messages, faster CPython (about 5% over 3.11), better typing (PEP 695 type aliases, tighter generics), `tomllib` in stdlib, and full compatibility with every major LLM SDK.

Dependency management in Python is historically painful: pip-tools, Poetry, PDM, Hatch, Rye, uv. Among these, uv (by astral.sh) has three advantages: it's ~10-100x faster than pip/Poetry on resolution and install; it uses a standard `pyproject.toml` (no proprietary lockfile format inside the lockfile); and it unifies virtual env management, dependency resolution, Python version management, and script running.

The alternative languages considered:

- **TypeScript** — strong LLM ecosystem, excellent async story. Rejected because the reference implementation aims to be readable by a broad AI/ML audience, and Python has higher pickup in that audience. Also: sqlite-vec, pgvector drivers, sentence-transformers are all Python-first.
- **Rust** — excellent for correctness and performance. Rejected for this project because the iteration speed of the first deployment matters more than runtime efficiency, and the operator is solo. A Rust rewrite post-v1.0 is plausible if performance becomes a bottleneck.
- **Go** — ergonomic standard library, good concurrency. Rejected because the LLM ecosystem lags (go-openai is maintained but not as vibrant), and raw SQL workflows in Go require more boilerplate.

## Decision

Python 3.12 with uv for dependency management, virtual envs, and Python version pinning. The `.python-version` file pins `3.12` so every contributor uses the same interpreter. Dependencies and lockfile in `pyproject.toml` and `uv.lock`, both committed.

All commands go through `uv run`:

```bash
uv sync                              # install all deps
uv run pytest                        # run tests
uv run memory-engine db migrate      # run the CLI
uv run ruff check                    # lint
uv run mypy src/                     # type-check
```

## Consequences

**Easier:**
- Fast resolution, fast installs. New contributor goes from `git clone` to green tests in under 3 minutes.
- Single tool. Developers don't juggle pyenv + Poetry + pip-tools.
- Reproducible builds across developer machines and CI.
- `pyproject.toml` is the standard; we stay on it without Poetry's extensions.

**Harder:**
- uv is relatively new. A contributor on an older Linux distro may need to install uv from a curl-bash script the first time.
- If a critical uv bug lands, we don't have an easy fallback. Mitigation: pip + requirements-export could substitute with 1-2 hours of work if needed.

**Future constraints:**
- Python version bumps require a CI update and `.python-version` update. Plan for 3.13 in late 2026.
- If uv stops being maintained (unlikely but possible), we migrate to PDM or similar; the `pyproject.toml` is portable.

## Alternatives considered

- **Poetry** — mature, widely known. Rejected for speed (Poetry's resolver is 10-100x slower) and for its proprietary `poetry.lock` and slightly non-standard `[tool.poetry]` section.
- **pip + requirements.txt + pip-tools** — minimal, stdlib-friendly. Rejected because it means gluing several tools together for what uv does in one.
- **PDM** — fast, standards-compliant. Was the frontrunner before uv matured. Rejected because uv is now faster and has more momentum in the Python ecosystem.
- **Hatch** — Python's next-gen project manager. Rejected for the same reason PDM was: uv is currently faster and simpler.

## Revisit if

- uv maintenance lapses for more than 6 months.
- A critical security bug in uv's resolution algorithm goes unpatched.
- Python 3.12 is deprecated (not before late 2028).
- A contributor base emerges that is strongly tied to a different toolchain and the cost of convergence becomes too high.
