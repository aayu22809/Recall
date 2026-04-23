use alloc::string::String;
use alloc::vec::Vec;

use serde::{Deserialize, Serialize};

use crate::types::MetadataValue;

#[derive(Clone, Debug, Serialize, Deserialize)]
pub enum MetadataPredicate {
    Equals { key: String, value: MetadataValue },
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct SearchOptions {
    pub top_k: usize,
    pub threshold: Option<f32>,
    pub namespace: Option<String>,
    pub cross_namespace: bool,
    pub filters: Vec<MetadataPredicate>,
    pub token_budget: Option<usize>,
}

impl Default for SearchOptions {
    fn default() -> Self {
        Self {
            top_k: 10,
            threshold: None,
            namespace: None,
            cross_namespace: false,
            filters: Vec::new(),
            token_budget: None,
        }
    }
}

