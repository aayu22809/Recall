use alloc::collections::BTreeMap;
use alloc::string::String;
use alloc::vec::Vec;

use serde::{Deserialize, Serialize};

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub enum DistanceMetric {
    Cosine,
    Dot,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub enum MetadataValue {
    String(String),
    Integer(i64),
    Float(f64),
    Bool(bool),
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Document {
    pub id: String,
    pub namespace: String,
    pub content: String,
    pub metadata: BTreeMap<String, MetadataValue>,
    pub embedding: Vec<f32>,
    pub deleted: bool,
    pub updated_at_unix_ms: u64,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct QueryTelemetry {
    pub retrieval_latency_ms: u32,
    pub embedding_latency_ms: u32,
    pub cache_hit: bool,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct QueryResult {
    pub id: String,
    pub namespace: String,
    pub score: f32,
    pub content: String,
    pub metadata: BTreeMap<String, MetadataValue>,
    pub telemetry: QueryTelemetry,
}

