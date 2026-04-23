use std::collections::BTreeMap;
use std::fs::File;
use std::io::{BufRead, BufReader};
use std::time::Instant;

use clap::Parser;
use moss_core::{MossConfig, MossCore, SearchOptions};
use serde::Deserialize;

#[derive(Debug, Parser)]
struct Args {
    #[arg(long)]
    index_json: String,
    #[arg(long)]
    query_json: String,
    #[arg(long, default_value_t = 10)]
    top_k: usize,
}

#[derive(Debug, Deserialize)]
struct IndexRow {
    id: String,
    vector: Vec<f32>,
    #[serde(default = "default_ns")]
    namespace: String,
}

#[derive(Debug, Deserialize)]
struct QueryRow {
    vector: Vec<f32>,
    relevant_ids: Vec<String>,
}

fn default_ns() -> String {
    "default".to_string()
}

fn main() {
    let args = Args::parse();
    let mut core = MossCore::new(MossConfig::default());

    let index_file = File::open(&args.index_json).expect("index jsonl");
    for line in BufReader::new(index_file).lines().map_while(Result::ok) {
        let row: IndexRow = serde_json::from_str(&line).expect("valid index row");
        core.insert(
            row.id.clone(),
            row.id.clone(),
            row.vector,
            row.namespace,
            BTreeMap::new(),
            0,
        )
        .expect("insert row");
    }

    let query_file = File::open(&args.query_json).expect("query jsonl");
    let mut latencies: Vec<u128> = Vec::new();
    let mut total_recall = 0_f32;
    let mut n = 0_f32;
    for line in BufReader::new(query_file).lines().map_while(Result::ok) {
        let row: QueryRow = serde_json::from_str(&line).expect("valid query row");
        let start = Instant::now();
        let results = core.search(
            &row.vector,
            &SearchOptions {
                top_k: args.top_k,
                ..SearchOptions::default()
            },
        );
        latencies.push(start.elapsed().as_micros());
        let hit_count = results
            .iter()
            .filter(|r| row.relevant_ids.contains(&r.id))
            .count();
        total_recall += hit_count as f32 / row.relevant_ids.len().max(1) as f32;
        n += 1.0;
    }

    latencies.sort_unstable();
    let p50 = percentile_us(&latencies, 0.50);
    let p95 = percentile_us(&latencies, 0.95);
    let p99 = percentile_us(&latencies, 0.99);
    let recall = if n == 0.0 { 0.0 } else { total_recall / n };

    println!("Benchmark summary");
    println!("  queries: {}", latencies.len());
    println!("  p50: {:.3} ms", p50 as f64 / 1000.0);
    println!("  p95: {:.3} ms", p95 as f64 / 1000.0);
    println!("  p99: {:.3} ms", p99 as f64 / 1000.0);
    println!("  recall@k: {:.4}", recall);
}

fn percentile_us(values: &[u128], q: f64) -> u128 {
    if values.is_empty() {
        return 0;
    }
    let idx = ((values.len() - 1) as f64 * q).round() as usize;
    values[idx.min(values.len() - 1)]
}

