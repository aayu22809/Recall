use alloc::string::String;
use alloc::vec::Vec;

use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct TelemetryEvent {
    pub query_hash: String,
    pub namespace: String,
    pub top_k: usize,
    pub score_distribution: Vec<f32>,
    pub retrieval_latency_ms: u32,
    pub embedding_latency_ms: u32,
    pub cache_hit: bool,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct LatencyHistogram {
    buckets_ms: Vec<u32>,
    counts: Vec<u64>,
}

impl LatencyHistogram {
    pub fn new() -> Self {
        let buckets_ms = vec![1, 2, 5, 10, 20, 50, 100, 200, 500, 1000];
        let counts = vec![0_u64; buckets_ms.len()];
        Self { buckets_ms, counts }
    }

    pub fn observe(&mut self, value_ms: u32) {
        for (idx, bucket) in self.buckets_ms.iter().enumerate() {
            if value_ms <= *bucket {
                self.counts[idx] += 1;
                return;
            }
        }
        if let Some(last) = self.counts.last_mut() {
            *last += 1;
        }
    }
}

#[derive(Clone, Debug)]
pub enum TelemetrySink {
    None,
    Callback(fn(&TelemetryEvent)),
}

#[derive(Clone, Debug)]
pub struct TelemetryCollector {
    pub events: Vec<TelemetryEvent>,
    pub retrieval_histogram: LatencyHistogram,
    pub sink: TelemetrySink,
}

impl TelemetryCollector {
    pub fn new(sink: TelemetrySink) -> Self {
        Self {
            events: Vec::new(),
            retrieval_histogram: LatencyHistogram::new(),
            sink,
        }
    }

    pub fn emit(&mut self, event: TelemetryEvent) {
        self.retrieval_histogram.observe(event.retrieval_latency_ms);
        if let TelemetrySink::Callback(handler) = self.sink {
            handler(&event);
        }
        self.events.push(event);
    }
}

