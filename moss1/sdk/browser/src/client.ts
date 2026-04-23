import type {
  InsertInput,
  SearchOptions,
  SearchResult,
  StreamChunk,
  StreamUpdate,
  WasmBridge,
} from "./types";

export class MossClient {
  constructor(private readonly wasm: WasmBridge) {}

  async insert(input: InsertInput): Promise<void> {
    await this.wasm.insert(
      input.id,
      input.content,
      input.namespace ?? "default",
      input.embedding
    );
  }

  async delete(id: string): Promise<void> {
    await this.wasm.delete(id);
  }

  async search(queryEmbedding: number[], options: SearchOptions = {}): Promise<SearchResult[]> {
    return this.wasm.search(
      queryEmbedding,
      options.top_k ?? 10,
      options.namespace
    );
  }

  async *searchStream(
    chunks: AsyncIterable<StreamChunk>,
    options: SearchOptions = {}
  ): AsyncGenerator<StreamUpdate, void, unknown> {
    let seen = false;
    for await (const chunk of chunks) {
      const results = await this.search(chunk.embedding, options);
      yield {
        sequence: chunk.sequence,
        cancelled_previous: seen,
        results,
      };
      seen = true;
    }
  }

  async compact(): Promise<void> {
    await this.wasm.compact();
  }

  async export(): Promise<Uint8Array> {
    return this.wasm.export_snapshot();
  }

  async import(snapshot: Uint8Array): Promise<void> {
    await this.wasm.import_snapshot(snapshot);
  }
}

