use alloc::string::String;
use alloc::vec::Vec;

use serde::{Deserialize, Serialize};

use crate::distance::similarity;
use crate::types::DistanceMetric;

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct RetrievedChunk {
    pub id: String,
    pub text: String,
    pub score: f32,
    pub embedding: Vec<f32>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct TokenBudgetResult {
    pub selected: Vec<RetrievedChunk>,
    pub estimated_tokens: usize,
    pub original_tokens: usize,
    pub savings_ratio: f32,
}

fn estimate_tokens(text: &str) -> usize {
    text.split_whitespace().count().max(1)
}

fn compress_text(text: &str, budget_tokens: usize) -> String {
    let words: Vec<&str> = text.split_whitespace().collect();
    let keep = budget_tokens.min(words.len());
    words[..keep].join(" ")
}

pub fn apply_token_budget(
    query_embedding: &[f32],
    chunks: &[RetrievedChunk],
    metric: DistanceMetric,
    max_tokens: usize,
    lambda: f32,
) -> TokenBudgetResult {
    let mut remaining: Vec<RetrievedChunk> = chunks.to_vec();
    let mut selected: Vec<RetrievedChunk> = Vec::new();
    let mut used_tokens = 0_usize;
    let original_tokens: usize = chunks.iter().map(|c| estimate_tokens(&c.text)).sum();

    while !remaining.is_empty() && used_tokens < max_tokens {
        let mut best_idx = 0usize;
        let mut best_score = f32::NEG_INFINITY;
        for (idx, candidate) in remaining.iter().enumerate() {
            let relevance = similarity(metric, query_embedding, &candidate.embedding);
            let mut redundancy = 0.0_f32;
            for picked in &selected {
                let sim = similarity(metric, &candidate.embedding, &picked.embedding);
                if sim > redundancy {
                    redundancy = sim;
                }
            }
            let mmr = lambda * relevance - (1.0 - lambda) * redundancy;
            if mmr > best_score {
                best_score = mmr;
                best_idx = idx;
            }
        }

        let mut next = remaining.remove(best_idx);
        let next_tokens = estimate_tokens(&next.text);
        if used_tokens + next_tokens > max_tokens {
            let allowed = max_tokens.saturating_sub(used_tokens);
            if allowed == 0 {
                break;
            }
            next.text = compress_text(&next.text, allowed);
            used_tokens += estimate_tokens(&next.text);
            selected.push(next);
            break;
        }
        used_tokens += next_tokens;
        selected.push(next);
    }

    let savings_ratio = if original_tokens == 0 {
        0.0
    } else {
        1.0 - (used_tokens as f32 / original_tokens as f32)
    };

    TokenBudgetResult {
        selected,
        estimated_tokens: used_tokens,
        original_tokens,
        savings_ratio,
    }
}

