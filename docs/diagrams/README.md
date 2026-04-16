# Architecture Diagrams

> ASCII / Mermaid diagrams that render inline. For export artifacts (SVG, PNG) check `docs/diagrams/exports/` (populated during Phase 6).

## System overview

```mermaid
flowchart TB
    subgraph External["External world"]
        WA_IN[WhatsApp message arrives]
        WA_OUT[WhatsApp message sent]
    end

    subgraph Adapter["adapters.whatsapp"]
        WEBHOOK[webhook.py]
        NORM[ingress.py<br/>normalize]
        OUT[outbound.py<br/>send]
    end

    subgraph Ingress["ingress.pipeline"]
        SIG[verify signature R1]
        SCOPE[classify scope R2]
        HASH[content hash]
        IDEM[idempotency check]
    end

    subgraph Core["core"]
        EVENTS[(events<br/>immutable log)]
        WM[working_memory]
        CONS[consolidator]
        GATE[grounding gate]
        NEURONS[(neurons)]
        QUAR[(quarantine)]
    end

    subgraph Policy["policy.dispatch"]
        BROKER[context broker<br/>field projection]
        CACHE[persona-scoped cache R9]
        LLM[LLM client]
    end

    subgraph Retrieval["retrieval"]
        BM25[BM25]
        VEC[vector]
        GRAPH[graph]
        FUSE[RRF fuse]
        LENS[lens filter R12]
    end

    subgraph Outbound["outbound.approval"]
        RED[redactor]
        NN[non-negotiables]
        ID[identity align R13]
    end

    subgraph Healing["healing"]
        HEAL[healer loop]
        HALT{halted?}
    end

    WA_IN --> WEBHOOK --> NORM --> Ingress
    Ingress --> SIG --> SCOPE --> HASH --> IDEM --> EVENTS
    EVENTS -.consolidator reads.-> WM --> CONS
    CONS --> BROKER --> CACHE --> LLM
    LLM --> GATE
    GATE -->|accepted| NEURONS
    GATE -->|rejected| QUAR

    WEBHOOK -.triggers reply.-> Retrieval
    Retrieval --> LENS --> BM25 & VEC & GRAPH --> FUSE --> Outbound
    Outbound --> RED --> NN --> ID --> OUT --> WA_OUT

    EVENTS -.healer scans.-> HEAL --> HALT
    HALT -.if halted.-> Ingress
```

## The policy plane

```mermaid
flowchart LR
    subgraph CallSites["Registered call sites"]
        S1[classify_scope]
        S2[extract_entities]
        S3[grounding_judge]
        S4[judge_contradiction]
        S5[summarize_episode]
        S6[nonneg_judge]
        S7[generate_reply]
    end

    subgraph Dispatch["dispatch()"]
        D1[broker: project context]
        D2[registry: active prompt]
        D3[cache lookup<br/>persona-scoped]
        D4[llm_client: HTTP call]
        D5[output parser]
    end

    CallSites --> Dispatch
    D1 --> D2 --> D3
    D3 -->|miss| D4 --> D5
    D3 -->|hit| D5
    D5 --> Result[LLMResult]
```

## Lens enforcement (rule 12)

```mermaid
flowchart LR
    Q[query + persona_id + lens string] --> PL[parse_lens]
    PL --> LF[LensFilter<br/>where_clause + params]
    LF --> BM[BM25 SQL]
    LF --> VQ[Vector SQL]
    LF --> GQ[Graph SQL]
    BM & VQ & GQ --> FUSE[RRF]
    FUSE --> Results

    style LF fill:#f9a,stroke:#333
```

The red `LensFilter` node is load-bearing: every stream must apply it. There is no code path that runs an unfiltered query on `neurons`.

## Outbound pipeline

```mermaid
flowchart TB
    DRAFT[Generated draft] --> R[Redactor<br/>strip PII, cross-counterparty]
    R --> NN[Non-negotiables<br/>pattern → LLM judge]
    NN -->|violated| BLOCK[outbound_blocked event<br/>NOT delivered]
    NN -->|pass| IA[Identity alignment<br/>boundaries, tone]
    IA --> APPROVED[Approved draft]
    APPROVED --> MCP[MCP signs message_out]
    MCP --> WA[WhatsApp send]

    style BLOCK fill:#f99,stroke:#333
    style APPROVED fill:#9f9,stroke:#333
```

## Event → neuron lifecycle

```mermaid
sequenceDiagram
    participant MCP
    participant Ingress
    participant Log as events (log)
    participant WM as working_memory
    participant Cons as consolidator
    participant Pol as policy plane
    participant Neu as neurons

    MCP->>Ingress: POST /v1/ingest (signed)
    Ingress->>Ingress: verify signature (R1)
    Ingress->>Pol: classify_scope
    Pol-->>Ingress: scope
    Ingress->>Log: append event
    Log-->>Ingress: event_id
    Ingress-->>MCP: 201 Created

    Note over WM,Cons: Async consolidation
    Log->>WM: add to ring buffer
    Cons->>Pol: extract_entities
    Pol-->>Cons: candidates
    Cons->>Cons: grounding gate
    alt candidate accepted
        Cons->>Neu: insert neuron (cites event)
    else rejected
        Cons->>Neu: insert into quarantine
    end
```

## File sources

These diagrams are Mermaid source embedded in Markdown. They render on GitHub, in most Markdown viewers, and can be exported to SVG/PNG via `mmdc` (mermaid-cli):

```bash
npx -p @mermaid-js/mermaid-cli mmdc -i docs/diagrams/README.md -o docs/diagrams/exports/
```

Phase 6 adds an optional CI step that exports SVGs on every diagram change, committing them to `docs/diagrams/exports/`.
