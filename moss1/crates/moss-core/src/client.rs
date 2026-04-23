use alloc::collections::BTreeMap;
use alloc::string::{String, ToString};
use alloc::vec::Vec;

use serde::{Deserialize, Serialize};

use crate::cache::EmbeddingCache;
use crate::config::MossConfig;
use crate::error::MossError;
use crate::index::{IndexEngine, VectorIndex};
use crate::query::{MetadataPredicate, SearchOptions};
use crate::telemetry::{TelemetryCollector, TelemetryEvent, TelemetrySink};
use crate::token_budget::{apply_token_budget, RetrievedChunk};
use crate::types::{Document, MetadataValue, QueryResult, QueryTelemetry};

#[derive(Clone, Debug, Serialize, Deserialize)]
struct Snapshot {
    config: MossConfig,
    docs: BTreeMap<String, Document>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct StreamFrame {
    pub sequence: u64,
    pub transcript: String,
    pub query_embedding: Vec<f32>,
    pub stable: bool,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct StreamResult {
    pub sequence: u64,
    pub cancelled_previous: bool,
    pub results: Vec<QueryResult>,
}

pub struct MossCore {
    pub config: MossConfig,
    pub index: IndexEngine,
    pub docs: BTreeMap<String, Document>,
    pub cache: EmbeddingCache,
    pub telemetry: TelemetryCollector,
}

impl MossCore {
    pub fn new(config: MossConfig) -> Self {
        let index = IndexEngine::new_hnsw(config.metric, config.hnsw.clone());
        Self {
            config,
            index,
            docs: BTreeMap::new(),
            cache: EmbeddingCache::new(10_000),
            telemetry: TelemetryCollector::new(TelemetrySink::None),
        }
    }

    pub fn insert(
        &mut self,
        id: String,
        content: String,
        embedding: Vec<f32>,
        namespace: String,
        metadata: BTreeMap<String, MetadataValue>,
        updated_at_unix_ms: u64,
    ) -> Result<(), MossError> {
        if embedding.is_empty() {
            return Err(MossError::InvalidVectorDimension);
        }
        if let Some(existing) = self.docs.get(&id) {
            if existing.embedding.len() != embedding.len() {
                return Err(MossError::InvalidVectorDimension);
            }
        }

        self.index
            .upsert(id.clone(), namespace.clone(), embedding.clone());
        self.docs.insert(
            id.clone(),
            Document {
                id,
                namespace,
                content,
                metadata,
                embedding,
                deleted: false,
                updated_at_unix_ms,
            },
        );
        Ok(())
    }

    pub fn delete(&mut self, id: &str) -> Result<(), MossError> {
        let doc = self.docs.get_mut(id).ok_or(MossError::MissingDocument)?;
        doc.deleted = true;
        self.index.soft_delete(id);
        Ok(())
    }

    pub fn compact(&mut self) {
        self.docs.retain(|_, doc| !doc.deleted);
        self.index.compact();
    }

    pub fn search(&mut self, query_embedding: &[f32], options: &SearchOptions) -> Vec<QueryResult> {
        let start_ms = now_millis();
        let namespace = if options.cross_namespace {
            None
        } else {
            options.namespace.as_deref()
        };
        let hits = self.index.search(
            query_embedding,
            options.top_k,
            options.threshold,
            namespace,
        );

        let mut results: Vec<QueryResult> = hits
            .into_iter()
            .filter_map(|hit| {
                let doc = self.docs.get(&hit.id)?;
                if doc.deleted {
                    return None;
                }
                if !matches_filters(doc, &options.filters) {
                    return None;
                }
                Some(QueryResult {
                    id: doc.id.clone(),
                    namespace: doc.namespace.clone(),
                    score: hit.score,
                    content: doc.content.clone(),
                    metadata: doc.metadata.clone(),
                    telemetry: QueryTelemetry {
                        retrieval_latency_ms: 0,
                        embedding_latency_ms: 0,
                        cache_hit: false,
                    },
                })
            })
            .collect();

        if let Some(max_tokens) = options.token_budget {
            let chunks: Vec<RetrievedChunk> = results
                .iter()
                .filter_map(|r| self.docs.get(&r.id).map(|doc| (r, doc)))
                .map(|(r, doc)| RetrievedChunk {
                    id: r.id.clone(),
                    text: r.content.clone(),
                    score: r.score,
                    embedding: doc.embedding.clone(),
                })
                .collect();
            let budget = apply_token_budget(
                query_embedding,
                &chunks,
                self.config.metric,
                max_tokens,
                0.7,
            );
            let selected_ids: Vec<String> = budget.selected.into_iter().map(|c| c.id).collect();
            results.retain(|r| selected_ids.contains(&r.id));
        }

        let latency = now_millis().saturating_sub(start_ms);
        let scores = results.iter().map(|r| r.score).collect::<Vec<_>>();
        for result in &mut results {
            result.telemetry.retrieval_latency_ms = latency as u32;
        }
        self.telemetry.emit(TelemetryEvent {
            query_hash: simple_query_hash(query_embedding),
            namespace: options
                .namespace
                .clone()
                .unwrap_or_else(|| "default".to_string()),
            top_k: options.top_k,
            score_distribution: scores,
            retrieval_latency_ms: latency as u32,
            embedding_latency_ms: 0,
            cache_hit: false,
        });

        results
    }

    pub fn search_stream<I>(&mut self, frames: I, options: &SearchOptions) -> Vec<StreamResult>
    where
        I: IntoIterator<Item = StreamFrame>,
    {
        let mut out = Vec::new();
        let mut seen_any = false;
        for frame in frames {
            let results = self.search(&frame.query_embedding, options);
            out.push(StreamResult {
                sequence: frame.sequence,
                cancelled_previous: seen_any,
                results,
            });
            seen_any = true;
        }
        out
    }

    #[cfg(feature = "std")]
    pub fn export_snapshot(&self) -> Result<Vec<u8>, MossError> {
        let snapshot = Snapshot {
            config: self.config.clone(),
            docs: self.docs.clone(),
        };
        serde_json::to_vec(&snapshot).map_err(|_| MossError::SerializationFailed)
    }

    #[cfg(not(feature = "std"))]
    pub fn export_snapshot(&self) -> Result<Vec<u8>, MossError> {
        Err(MossError::SerializationUnavailable)
    }

    #[cfg(feature = "std")]
    pub fn import_snapshot(&mut self, bytes: &[u8]) -> Result<(), MossError> {
        let snapshot: Snapshot =
            serde_json::from_slice(bytes).map_err(|_| MossError::DeserializationFailed)?;
        self.config = snapshot.config.clone();
        self.docs = snapshot.docs;
        self.index = IndexEngine::new_hnsw(self.config.metric, self.config.hnsw.clone());
        for doc in self.docs.values() {
            if !doc.deleted {
                self.index
                    .upsert(doc.id.clone(), doc.namespace.clone(), doc.embedding.clone());
            }
        }
        Ok(())
    }

    #[cfg(not(feature = "std"))]
    pub fn import_snapshot(&mut self, _bytes: &[u8]) -> Result<(), MossError> {
        Err(MossError::SerializationUnavailable)
    }
}

fn matches_filters(doc: &Document, filters: &[MetadataPredicate]) -> bool {
    for filter in filters {
        match filter {
            MetadataPredicate::Equals { key, value } => {
                if doc.metadata.get(key) != Some(value) {
                    return false;
                }
            }
        }
    }
    true
}

fn simple_query_hash(query_embedding: &[f32]) -> String {
    let mut acc = 0_u64;
    for value in query_embedding.iter().take(16) {
        acc ^= value.to_bits() as u64;
        acc = acc.rotate_left(5);
    }
    format!("{acc:x}")
}

#[cfg(feature = "std")]
fn now_millis() -> u64 {
    use std::time::{SystemTime, UNIX_EPOCH};
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}

#[cfg(not(feature = "std"))]
fn now_millis() -> u64 {
    0
}

