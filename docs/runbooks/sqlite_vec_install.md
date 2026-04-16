# Runbook: Installing sqlite-vec

> Phase 0's `migrations/001_initial.sql` creates `neurons_vec` as a `vec0`-backed virtual table. Without sqlite-vec loaded, the migration fails.

## When you need this

- Fresh clone. `uv run memory-engine db migrate` fails with `no such module: vec0`.
- Deploying to a new host.
- Upgrading system Python or SQLite.

## Option 1 — pip install (simplest, recommended for development)

```bash
uv pip install --system sqlite-vec
```

The `sqlite-vec` Python package bundles a prebuilt `.so`/`.dylib`/`.dll` for common platforms (x86_64 Linux, arm64 Linux, x86_64 macOS, arm64 macOS, Windows). The engine's `db/connection.py` uses:

```python
import sqlite_vec
conn.enable_load_extension(True)
sqlite_vec.load(conn)
conn.enable_load_extension(False)
```

## Option 2 — system install (recommended for production)

Download a release binary from https://github.com/asg017/sqlite-vec/releases matching your platform:

```bash
# Linux arm64 example
curl -L -o /tmp/vec0.so \
  https://github.com/asg017/sqlite-vec/releases/download/v0.1.6/sqlite-vec-0.1.6-loadable-linux-aarch64.tar.gz
tar -xzf /tmp/vec0.so -C /usr/local/lib/
```

Pin the version. Automatic updates to vector extensions have bitten users before.

Set `MEMORY_ENGINE_SQLITE_VEC_PATH=/usr/local/lib/vec0` and the connection code uses that path instead of the pip-provided binary.

## Verifying installation

```bash
uv run python -c "
import aiosqlite, asyncio, sqlite_vec

async def check():
    conn = await aiosqlite.connect(':memory:')
    await conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    cursor = await conn.execute('SELECT vec_version()')
    row = await cursor.fetchone()
    print(f'sqlite-vec version: {row[0]}')

asyncio.run(check())
"
```

Expected output: `sqlite-vec version: v0.1.6` or similar.

## Troubleshooting

**`no such module: vec0`** — extension not loaded. Check that `enable_load_extension(True)` ran before the migration.

**`library not loaded: libvec0.dylib` on macOS** — Gatekeeper quarantined the file. Run `xattr -d com.apple.quarantine /path/to/vec0.dylib`.

**`cannot load extension: extension loading disabled by configuration`** — SQLite was compiled without `SQLITE_ENABLE_LOAD_EXTENSION`. Rare on Linux; occasionally happens with Homebrew Python. Reinstall Python from python.org or use a Linux build.

**Different dimensions needed** — if you switch embedders to a model with dimensions ≠ 384, see `embedder_dimension_change.md` (separate runbook).

## CI setup

The CI workflow (`.github/workflows/test.yml`) does `uv pip install --system sqlite-vec` as a step. No operator action needed; this runbook is for local dev and production deployment.
