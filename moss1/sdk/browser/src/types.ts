export type MetadataValue = string | number | boolean;

export interface SearchOptions {
  top_k?: number;
  threshold?: number;
  namespace?: string;
  token_budget?: number;
}

export interface QueryTelemetry {
  retrieval_latency_ms: number;
  embedding_latency_ms: number;
  cache_hit: boolean;
}

export interface SearchResult {
  id: string;
  namespace: string;
  score: number;
  content: string;
  metadata: Record<string, MetadataValue>;
  telemetry: QueryTelemetry;
}

export interface InsertInput {
  id: string;
  content: string;
  embedding: number[];
  namespace?: string;
  metadata?: Record<string, MetadataValue>;
}

export interface StreamChunk {
  sequence: number;
  transcript: string;
  embedding: number[];
  stable?: boolean;
}

export interface StreamUpdate {
  sequence: number;
  cancelled_previous: boolean;
  results: SearchResult[];
}

export interface WasmBridge {
  insert(
    id: string,
    content: string,
    namespace: string,
    embedding: number[]
  ): void | Promise<void>;
  delete(id: string): void | Promise<void>;
  search(
    embedding: number[],
    topK: number,
    namespace?: string
  ): SearchResult[] | Promise<SearchResult[]>;
  compact(): void | Promise<void>;
  export_snapshot(): Uint8Array | Promise<Uint8Array>;
  import_snapshot(snapshot: Uint8Array): void | Promise<void>;
}

