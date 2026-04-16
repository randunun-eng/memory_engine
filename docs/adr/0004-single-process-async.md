# 0004 — Single process, async everywhere

## Status

Accepted — 2026-04-16

## Context

memory_engine's components are: HTTP server (FastAPI), consolidator loop, healer loop, policy plane (LLM dispatch), retrieval engine, outbound approval pipeline, adapter webhook receiver.

In a microservices architecture, these would be separate processes communicating over a message bus or HTTP. Each could scale independently, fail independently, deploy independently.

In a process-per-component architecture, they share memory, share the DB connection pool, share the Python import graph. Deployment is one binary. Failure of one component affects the whole; recovery is a restart.

Deployment context:
- Target: one Oracle Cloud ARM VM (4 OCPU, 24 GB RAM) for the lifetime of the blueprint plan.
- Operator: solo, part-time.
- Scale goals: 1–10 personas, 10k–100k events per persona per month.

At this scale, microservices are overkill. The operational cost of multiple deployable units is larger than any benefit from independent scaling. The only workload that might want its own process is the embedder (CPU-bound), but Python's `asyncio.run_in_executor` handles that without a second process.

Async vs threaded vs sync:
- **Sync/threaded:** threads cost memory (~8 MB per thread by default), context switching is overhead, GIL still serializes Python bytecode.
- **Threaded with `concurrent.futures`:** fine for CPU-bound batch work, awkward for I/O-heavy work where we'd need lots of threads.
- **Async (`asyncio`):** one event loop, thousands of in-flight coroutines, zero thread overhead per concurrent operation. Every library we use (aiosqlite, httpx, asyncpg, FastAPI, pynacl's async wrappers) supports it.

The dominant latency in memory_engine is LLM calls (100ms–5s each). Async lets us start a call, suspend the coroutine, handle other traffic, and resume when the LLM responds. Threaded model would work but wastes resources.

CPU-bound work (embeddings, signature verification) is short enough that `asyncio.run_in_executor` with the default ThreadPoolExecutor handles it without creating worker-process overhead.

## Decision

**One OS process per deployment.** Started by `memory-engine serve` or equivalent, it runs:

- FastAPI HTTP server.
- Consolidator background task.
- Healer background task.
- Webhook handlers (WhatsApp, future adapters).

All as coroutines in one `asyncio` event loop.

**Async everywhere.** I/O-bound code (DB, HTTP, LLM) is `async def`. No blocking `time.sleep`, `requests.get`, `psycopg2`. CPU-bound code (embeddings, signatures on large payloads) uses `asyncio.get_running_loop().run_in_executor(None, ...)`.

**Single writer per table** (rule 9 from CLAUDE.md §4). This aligns with single-process: within one process, we serialize writes to each hot table through a lock or through design (e.g., only the consolidator writes neurons). Across multi-worker deployments (which we are NOT using in Phase 7), this invariant would require coordination.

## Consequences

**Easier:**
- Deployment: one binary, one systemd unit, one log stream.
- Observability: one `/metrics` endpoint, one structured log source.
- Debugging: run locally, all components in your IDE, set a breakpoint anywhere.
- Transactions: shared DB pool means consolidator's transaction can commit without cross-process coordination.
- Memory: shared dataclass instances, shared prompt cache, no duplication.

**Harder:**
- Failure isolation: an unhandled exception in the healer could kill the HTTP server. Mitigation: each background task runs under `asyncio.shield` and a top-level try/except that logs and restarts. Unit test this.
- CPU-bound spikes can delay async tasks. Mitigation: offload to executor; keep the main loop responsive.
- Scale past one VM: Phase 7's single-VM assumption means we don't solve multi-node today. When we do (not in this doc's scope), we add a message queue and split.

**Future constraints:**
- The event log stays on one VM. If a second VM is added, we'd need either read replicas (Postgres streaming replication) or a distributed log (Kafka, NATS). Both are significant additions.
- If LLM costs become bottleneck and we want multi-worker LLM dispatch pools, we'd split the policy plane into its own process. Still within single deployment; just a second process.

## Alternatives considered

- **Microservices (consolidator as service, retrieval as service, adapter as service).** Rejected for operational overhead that far exceeds the scale need. Microservices make sense at 5+ engineers and multi-team ownership; we have 1.
- **Multi-worker Gunicorn with shared DB.** Rejected because coordination between workers for writes requires additional locking (memcached, Redis, or advisory locks). Single-worker avoids the complication.
- **Threaded architecture.** Rejected for higher memory cost per concurrent operation and less idiomatic fit with FastAPI + LLM SDKs, all of which assume async.
- **Actor model (e.g., Thespian).** Rejected as too heavy for our problem. Async coroutines are actors-lite; we don't need the full model.

## Revisit if

- A single persona grows beyond ~10 QPS sustained.
- LLM dispatch becomes the bottleneck and we want isolated worker pools for it.
- Multi-tenant deployment (10+ personas on one engine) emerges and we want fault isolation between personas.
- A specific component (e.g., embedder service) benefits from being scaled independently on separate hardware.

None of these are likely before Phase 7 closes. Revisit during post-Phase-7 planning.
