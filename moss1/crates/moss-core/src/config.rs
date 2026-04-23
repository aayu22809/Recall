use serde::{Deserialize, Serialize};

use crate::types::DistanceMetric;

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct HnswConfig {
    pub m: usize,
    pub ef_construction: usize,
    pub ef_search: usize,
    pub max_level: usize,
    pub flat_exact_cutoff: usize,
}

impl Default for HnswConfig {
    fn default() -> Self {
        Self {
            m: 16,
            ef_construction: 64,
            ef_search: 32,
            max_level: 8,
            flat_exact_cutoff: 10_000,
        }
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct MossConfig {
    pub metric: DistanceMetric,
    pub hnsw: HnswConfig,
}

impl Default for MossConfig {
    fn default() -> Self {
        Self {
            metric: DistanceMetric::Cosine,
            hnsw: HnswConfig::default(),
        }
    }
}

