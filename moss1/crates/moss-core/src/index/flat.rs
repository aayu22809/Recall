use alloc::string::String;
use alloc::vec::Vec;

use serde::{Deserialize, Serialize};

use crate::distance::similarity;
use crate::index::{SearchHit, VectorIndex};
use crate::types::DistanceMetric;

#[derive(Clone, Debug, Serialize, Deserialize)]
struct FlatRecord {
    id: String,
    namespace: String,
    vector: Vec<f32>,
    deleted: bool,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct FlatIndex {
    metric: DistanceMetric,
    records: Vec<FlatRecord>,
}

impl FlatIndex {
    pub fn new(metric: DistanceMetric) -> Self {
        Self {
            metric,
            records: Vec::new(),
        }
    }
}

impl VectorIndex for FlatIndex {
    fn upsert(&mut self, id: String, namespace: String, vector: Vec<f32>) {
        if let Some(record) = self.records.iter_mut().find(|r| r.id == id) {
            record.namespace = namespace;
            record.vector = vector;
            record.deleted = false;
            return;
        }
        self.records.push(FlatRecord {
            id,
            namespace,
            vector,
            deleted: false,
        });
    }

    fn soft_delete(&mut self, id: &str) {
        if let Some(record) = self.records.iter_mut().find(|r| r.id == id) {
            record.deleted = true;
        }
    }

    fn search(
        &self,
        query: &[f32],
        top_k: usize,
        threshold: Option<f32>,
        namespace: Option<&str>,
    ) -> Vec<SearchHit> {
        let mut hits: Vec<SearchHit> = self
            .records
            .iter()
            .filter(|r| !r.deleted)
            .filter(|r| namespace.map(|ns| r.namespace == ns).unwrap_or(true))
            .map(|r| SearchHit {
                id: r.id.clone(),
                namespace: r.namespace.clone(),
                score: similarity(self.metric, query, &r.vector),
            })
            .collect();
        hits.sort_by(|a, b| b.score.total_cmp(&a.score));
        if let Some(min_score) = threshold {
            hits.retain(|h| h.score >= min_score);
        }
        hits.truncate(top_k);
        hits
    }

    fn compact(&mut self) {
        self.records.retain(|r| !r.deleted);
    }

    fn len(&self) -> usize {
        self.records.iter().filter(|r| !r.deleted).count()
    }
}

