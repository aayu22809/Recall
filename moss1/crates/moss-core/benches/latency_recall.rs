use std::collections::BTreeMap;
use std::time::Instant;

use moss_core::{MossConfig, MossCore, SearchOptions};

fn synthetic_vector(seed: usize, dim: usize) -> Vec<f32> {
    (0..dim)
        .map(|i| (((seed as u64 * 6364136223846793005 + i as u64) % 10_000) as f32) / 10_000.0)
        .collect()
}

#[test]
fn latency_smoke_under_small_dataset() {
    let mut core = MossCore::new(MossConfig::default());
    let dim = 128;
    for i in 0..10_000 {
        core.insert(
            format!("doc-{i}"),
            format!("document {i}"),
            synthetic_vector(i, dim),
            "default".to_string(),
            BTreeMap::new(),
            0,
        )
        .expect("insert");
    }

    let query = synthetic_vector(42, dim);
    let options = SearchOptions {
        top_k: 10,
        ..SearchOptions::default()
    };

    let start = Instant::now();
    let results = core.search(&query, &options);
    let elapsed_ms = start.elapsed().as_millis();

    assert!(!results.is_empty());
    assert!(elapsed_ms <= 50, "smoke latency exceeded: {}ms", elapsed_ms);
}

