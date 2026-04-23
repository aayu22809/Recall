use std::collections::BTreeMap;
use std::fs;
use std::path::PathBuf;

use moss_core::{MossConfig, MossCore, SearchOptions};
use serde::Deserialize;

#[derive(Debug, Deserialize)]
struct ProtocolCase {
    name: String,
    operations: Vec<Operation>,
}

#[derive(Debug, Deserialize)]
#[serde(tag = "op")]
enum Operation {
    #[serde(rename = "insert")]
    Insert {
        id: String,
        namespace: String,
        content: String,
        embedding: Vec<f32>,
    },
    #[serde(rename = "search")]
    Search {
        embedding: Vec<f32>,
        top_k: usize,
        expect_min_results: usize,
    },
    #[serde(rename = "delete")]
    Delete { id: String },
}

#[test]
fn runs_shared_protocol_case() {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../protocol/cases/mvp-smoke.json");
    let data = fs::read_to_string(path).expect("protocol file");
    let case: ProtocolCase = serde_json::from_str(&data).expect("valid json protocol");

    let mut core = MossCore::new(MossConfig::default());
    for op in case.operations {
        match op {
            Operation::Insert {
                id,
                namespace,
                content,
                embedding,
            } => {
                core.insert(id, content, embedding, namespace, BTreeMap::new(), 0)
                    .expect("insert");
            }
            Operation::Search {
                embedding,
                top_k,
                expect_min_results,
            } => {
                let options = SearchOptions {
                    top_k,
                    ..SearchOptions::default()
                };
                let results = core.search(&embedding, &options);
                assert!(
                    results.len() >= expect_min_results,
                    "case={} expected at least {} results, got {}",
                    case.name,
                    expect_min_results,
                    results.len()
                );
            }
            Operation::Delete { id } => {
                core.delete(&id).expect("delete");
            }
        }
    }
}

