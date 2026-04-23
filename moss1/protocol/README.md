# Shared Integration Protocol

The protocol defines cross-platform behavioral checks for Moss clients.

- Schema: `shared-protocol.schema.json`
- Cases: `cases/*.json`

Each runtime adapter (Rust core, WASM, Node, Python, mobile bridge) should execute the same operation sequence and satisfy the same assertions.

