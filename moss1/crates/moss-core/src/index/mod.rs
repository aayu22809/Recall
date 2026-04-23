use alloc::string::String;
use alloc::vec::Vec;

use crate::config::HnswConfig;
use crate::types::DistanceMetric;

pub mod flat;
pub mod hnsw;

#[derive(Clone, Debug)]
pub struct SearchHit {
    pub id: String,
    pub namespace: String,
    pub score: f32,
}

pub trait VectorIndex {
    fn upsert(&mut self, id: String, namespace: String, vector: Vec<f32>);
    fn soft_delete(&mut self, id: &str);
    fn search(
        &self,
        query: &[f32],
        top_k: usize,
        threshold: Option<f32>,
        namespace: Option<&str>,
    ) -> Vec<SearchHit>;
    fn compact(&mut self);
    fn len(&self) -> usize;
}

#[derive(Clone, Debug)]
pub enum IndexEngine {
    Flat(flat::FlatIndex),
    Hnsw(hnsw::HnswGraph),
}

impl IndexEngine {
    pub fn new_hnsw(metric: DistanceMetric, config: HnswConfig) -> Self {
        Self::Hnsw(hnsw::HnswGraph::new(metric, config))
    }

    pub fn new_flat(metric: DistanceMetric) -> Self {
        Self::Flat(flat::FlatIndex::new(metric))
    }
}

impl VectorIndex for IndexEngine {
    fn upsert(&mut self, id: String, namespace: String, vector: Vec<f32>) {
        match self {
            IndexEngine::Flat(idx) => idx.upsert(id, namespace, vector),
            IndexEngine::Hnsw(idx) => idx.upsert(id, namespace, vector),
        }
    }

    fn soft_delete(&mut self, id: &str) {
        match self {
            IndexEngine::Flat(idx) => idx.soft_delete(id),
            IndexEngine::Hnsw(idx) => idx.soft_delete(id),
        }
    }

    fn search(
        &self,
        query: &[f32],
        top_k: usize,
        threshold: Option<f32>,
        namespace: Option<&str>,
    ) -> Vec<SearchHit> {
        match self {
            IndexEngine::Flat(idx) => idx.search(query, top_k, threshold, namespace),
            IndexEngine::Hnsw(idx) => idx.search(query, top_k, threshold, namespace),
        }
    }

    fn compact(&mut self) {
        match self {
            IndexEngine::Flat(idx) => idx.compact(),
            IndexEngine::Hnsw(idx) => idx.compact(),
        }
    }

    fn len(&self) -> usize {
        match self {
            IndexEngine::Flat(idx) => idx.len(),
            IndexEngine::Hnsw(idx) => idx.len(),
        }
    }
}

