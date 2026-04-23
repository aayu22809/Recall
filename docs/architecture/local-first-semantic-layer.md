# Local-First Semantic Intelligence Layer (Moss/Trayce) — Architecture v0.1

This document defines a production-grade architecture and implementation scaffold for a local-first semantic runtime that keeps data on-device by default and supports browser, desktop, mobile, and server targets from one Rust core.

## 1) System architecture diagram (ASCII)

```text
                                        ┌────────────────────────────┐
                                        │     UI Surfaces            │
                                        │  - Spotlight Overlay       │
                                        │  - Browser Extension       │
                                        │  - Raycast / CLI           │
                                        └──────────────┬─────────────┘
                                                       │
                                           Local IPC/HTTP (127.0.0.1)
                                                       │
                          ┌────────────────────────────┴────────────────────────────┐
                          │                  Runtime Orchestrator                    │
                          │  - Query planner (semantic + BM25 + recency)            │
                          │  - Token budget + MMR compression                        │
                          │  - Session approvals / injection policy                  │
                          └──────────────┬──────────────────────────┬────────────────┘
                                         │                          │
                           Query path     │                          │     Ingest/sync path
                                         │                          │
                    ┌────────────────────▼────────────────┐   ┌────▼────────────────────────┐
                    │      Embedding Pipeline (local)      │   │      Connector Workers      │
                    │  - Text: nomic-e5/minilm (quantized) │   │ Gmail/Drive/GCal/Notion/...│
                    │  - Vision: moondream/LLaVA (local)   │   │ OAuth token via OS keychain│
                    │  - Audio: Whisper -> text embedding   │   │ schedule + incremental sync│
                    │  - Cache(hash+model+quant)            │   └────┬────────────────────────┘
                    └────────────────────┬──────────────────┘        │
                                         │                           │ normalized content
                                         ▼                           ▼
                           ┌─────────────────────────────────────────────────┐
                           │        Rust Core Retrieval Engine (shared)      │
                           │  - HNSW ANN + Flat exact fallback               │
                           │  - Namespaces, filters, soft deletes            │
                           │  - search()/searchStream()/compact()/snapshot   │
                           │  - Telemetry + HDR histogram                    │
                           └───────────────┬─────────────────────┬───────────┘
                                           │                     │
                               vector index│                     │metadata/graph/audit
                                           ▼                     ▼
                                  ┌────────────────┐    ┌────────────────────────────┐
                                  │ Encrypted Store │    │ SQLite + FTS5 + Entity KG │
                                  │ AES-256-GCM     │    │ docs, entities, edges, log │
                                  │ mmap/ArrayBuffer│    │ injection audit            │
                                  └────────────────┘    └────────────────────────────┘

Targets from same Rust core:
  - Browser/Edge: wasm32-unknown-unknown (ESM + Workers)
  - Desktop: Tauri backend
  - Mobile: UniFFI (Swift/Kotlin)
  - Server Node: napi-rs adapter (+ WASM fallback)
  - Server Python: PyO3 asyncio adapter
```

## 2) Rust crate layout and responsibilities

```text
moss/
  Cargo.toml                     # workspace root
  README.md                      # quickstarts + target matrix
  crates/
    moss-core/                   # no_std-friendly retrieval and token runtime
      src/
        lib.rs
        config.rs                # index/search tuning knobs
        error.rs                 # explicit error surface
        types.rs                 # Vector, Document, QueryResult, metadata
        query.rs                 # SearchOptions, predicates
        distance.rs              # cosine/dot similarity kernels
        cache.rs                 # embedding cache (hash/model/quant key)
        token_budget.rs          # MMR + budget planner
        telemetry.rs             # structured events + histogram
        client.rs                # MossCore unified API implementation
        index/
          mod.rs                 # IndexEngine enum + trait
          flat.rs                # exact search path (<10k vectors)
          hnsw.rs                # ANN graph path (incremental upsert)
      tests/shared_protocol.rs   # protocol-driven integration tests
      benches/latency_recall.rs  # reproducible benchmark harness
    moss-wasm/                   # wasm-bindgen bridge for browser/edge
      src/lib.rs
    moss-bench/                  # CLI benchmark runner for CI + local perf checks
      src/main.rs
  sdk/
    browser/                     # TypeScript MossClient wrapper over wasm
      src/client.ts
      src/types.ts
      src/index.ts
  protocol/
    shared-protocol.schema.json  # cross-platform test contract
    cases/mvp-smoke.json         # reference scenario
```

## 3) Non-functional guarantees and guardrails

1. **Privacy-first default**: no network egress during file indexing or querying unless user explicitly enables connector sync or cloud sync.
2. **Security boundary**: index snapshots and metadata stores encrypted with AES-256-GCM; connector tokens in OS keychain only.
3. **Isolation**: namespace-scoped queries by default; cross-namespace queries require explicit opt-in.
4. **Hot-path constraints**: bounded candidate sets (`ef_search`, `top_k`) and no unbounded heap growth in retrieval loops.
5. **Observability**: every search emits latency + scoring telemetry; histogram tracked in-process.

## 4) Delivery split

- **v0.1 (this scaffold):** local indexing primitives, HNSW/flat engine scaffold, browser SDK wrapper, shared protocol tests, benchmark CLI.
- **v0.2:** full connector set, graph canvas, auto context detection in extension, cross-platform packaging (Windows/Linux/mobile), encrypted cloud sync.
