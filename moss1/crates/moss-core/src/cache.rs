use alloc::collections::BTreeMap;
use alloc::string::String;
use alloc::vec::Vec;

use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq, PartialOrd, Ord)]
pub struct EmbeddingCacheKey {
    pub text_hash: String,
    pub model_id: String,
    pub quantization: String,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct EmbeddingCache {
    capacity: usize,
    values: BTreeMap<EmbeddingCacheKey, Vec<f32>>,
    lru: Vec<EmbeddingCacheKey>,
}

impl EmbeddingCache {
    pub fn new(capacity: usize) -> Self {
        Self {
            capacity,
            values: BTreeMap::new(),
            lru: Vec::new(),
        }
    }

    pub fn get(&mut self, key: &EmbeddingCacheKey) -> Option<Vec<f32>> {
        let value = self.values.get(key).cloned();
        if value.is_some() {
            self.touch(key.clone());
        }
        value
    }

    pub fn put(&mut self, key: EmbeddingCacheKey, embedding: Vec<f32>) {
        self.values.insert(key.clone(), embedding);
        self.touch(key);
        self.evict_if_needed();
    }

    pub fn len(&self) -> usize {
        self.values.len()
    }

    fn touch(&mut self, key: EmbeddingCacheKey) {
        self.lru.retain(|k| k != &key);
        self.lru.push(key);
    }

    fn evict_if_needed(&mut self) {
        while self.values.len() > self.capacity {
            if let Some(oldest) = self.lru.first().cloned() {
                self.lru.remove(0);
                self.values.remove(&oldest);
            } else {
                break;
            }
        }
    }
}

