# Moss Runtime Scaffold

Local-first semantic runtime scaffold built around a shared Rust core.

## Quickstart

### Rust core

```bash
cd moss
cargo test -p moss-core
```

### Browser/WASM

```bash
cd moss
cargo build -p moss-wasm --target wasm32-unknown-unknown
# or use wasm-pack once packaging config is finalized
```

### Benchmark harness

```bash
cd moss
cargo run -p moss-bench -- \
  --index-json ./datasets/index.jsonl \
  --query-json ./datasets/queries.jsonl \
  --top-k 10
```

### Browser SDK

```bash
cd moss/sdk/browser
npm install
npm run build
```

## Runtime target status

- Browser/Edge (WASM): **scaffolded**
- Desktop (Tauri backend): **planned integration**
- Node.js (napi-rs): **planned adapter**
- Python (PyO3/maturin): **planned adapter**
- Mobile (UniFFI): **planned adapter**

## Shared integration protocol

Protocol cases and schema live in `moss/protocol/`. Any runtime adapter should pass the same protocol cases to be considered compliant.

