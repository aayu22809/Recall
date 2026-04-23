use alloc::collections::BTreeSet;
use alloc::string::String;
use alloc::vec;
use alloc::vec::Vec;

use serde::{Deserialize, Serialize};

use crate::config::HnswConfig;
use crate::distance::{distance, similarity};
use crate::index::{SearchHit, VectorIndex};
use crate::types::DistanceMetric;

#[derive(Clone, Debug, Serialize, Deserialize)]
struct HnswNode {
    id: String,
    namespace: String,
    vector: Vec<f32>,
    level: usize,
    neighbors: Vec<Vec<usize>>,
    deleted: bool,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct HnswGraph {
    metric: DistanceMetric,
    config: HnswConfig,
    nodes: Vec<HnswNode>,
    entrypoint: Option<usize>,
    max_level: usize,
}

impl HnswGraph {
    pub fn new(metric: DistanceMetric, config: HnswConfig) -> Self {
        Self {
            metric,
            config,
            nodes: Vec::new(),
            entrypoint: None,
            max_level: 0,
        }
    }

    fn node_distance(&self, query: &[f32], node_idx: usize) -> f32 {
        distance(self.metric, query, &self.nodes[node_idx].vector)
    }

    fn deterministic_level(&self, id: &str) -> usize {
        let mut hash: u64 = 1469598103934665603;
        for byte in id.as_bytes() {
            hash ^= *byte as u64;
            hash = hash.wrapping_mul(1099511628211);
        }
        let mut level = 0usize;
        while level < self.config.max_level && (hash & 1) == 0 {
            level += 1;
            hash >>= 1;
        }
        level
    }

    fn ensure_level(node: &mut HnswNode, level: usize) {
        while node.neighbors.len() <= level {
            node.neighbors.push(Vec::new());
        }
    }

    fn attach_neighbor(&mut self, node_idx: usize, neighbor_idx: usize, level: usize) {
        let node = &mut self.nodes[node_idx];
        Self::ensure_level(node, level);
        let list = &mut node.neighbors[level];
        if !list.contains(&neighbor_idx) {
            if list.len() >= self.config.m {
                list.remove(0);
            }
            list.push(neighbor_idx);
        }
    }

    fn greedy_descent(&self, query: &[f32], start_idx: usize, level: usize, ns: Option<&str>) -> usize {
        let mut current = start_idx;
        let mut improved = true;
        while improved {
            improved = false;
            let mut best = self.node_distance(query, current);
            for neighbor_idx in self.nodes[current]
                .neighbors
                .get(level)
                .cloned()
                .unwrap_or_default()
            {
                let neighbor = &self.nodes[neighbor_idx];
                if neighbor.deleted {
                    continue;
                }
                if ns.map(|x| x != neighbor.namespace).unwrap_or(false) {
                    continue;
                }
                let d = self.node_distance(query, neighbor_idx);
                if d < best {
                    best = d;
                    current = neighbor_idx;
                    improved = true;
                }
            }
        }
        current
    }

    fn search_layer(
        &self,
        query: &[f32],
        entry_points: &[usize],
        ef: usize,
        level: usize,
        ns: Option<&str>,
    ) -> Vec<usize> {
        let mut visited: BTreeSet<usize> = BTreeSet::new();
        let mut candidates: Vec<(f32, usize)> = Vec::new();
        let mut best: Vec<(f32, usize)> = Vec::new();

        for &ep in entry_points {
            if ep >= self.nodes.len() {
                continue;
            }
            visited.insert(ep);
            let dist = self.node_distance(query, ep);
            candidates.push((dist, ep));
            best.push((dist, ep));
        }

        while let Some((_, node_idx)) = {
            candidates.sort_by(|a, b| a.0.total_cmp(&b.0));
            if candidates.is_empty() {
                None
            } else {
                Some(candidates.remove(0))
            }
        } {
            let neighbors = self.nodes[node_idx]
                .neighbors
                .get(level)
                .cloned()
                .unwrap_or_default();
            for neighbor_idx in neighbors {
                if visited.contains(&neighbor_idx) {
                    continue;
                }
                visited.insert(neighbor_idx);
                let neighbor = &self.nodes[neighbor_idx];
                if neighbor.deleted {
                    continue;
                }
                if ns.map(|x| x != neighbor.namespace).unwrap_or(false) {
                    continue;
                }
                let nd = self.node_distance(query, neighbor_idx);
                candidates.push((nd, neighbor_idx));
                best.push((nd, neighbor_idx));
                best.sort_by(|a, b| a.0.total_cmp(&b.0));
                if best.len() > ef {
                    best.truncate(ef);
                }
            }
        }

        best.sort_by(|a, b| a.0.total_cmp(&b.0));
        best.into_iter().map(|(_, idx)| idx).collect()
    }
}

impl VectorIndex for HnswGraph {
    fn upsert(&mut self, id: String, namespace: String, vector: Vec<f32>) {
        if let Some(existing_idx) = self.nodes.iter().position(|n| n.id == id && !n.deleted) {
            self.nodes[existing_idx].deleted = true;
        }

        let level = self.deterministic_level(&id);
        let node_idx = self.nodes.len();
        self.nodes.push(HnswNode {
            id,
            namespace,
            vector: vector.clone(),
            level,
            neighbors: vec![Vec::new(); level + 1],
            deleted: false,
        });

        if self.entrypoint.is_none() {
            self.entrypoint = Some(node_idx);
            self.max_level = level;
            return;
        }

        let mut ep = self.entrypoint.unwrap_or(node_idx);
        for l in (level + 1..=self.max_level).rev() {
            ep = self.greedy_descent(&vector, ep, l, None);
        }

        let max_link_level = level.min(self.max_level);
        for l in (0..=max_link_level).rev() {
            let ef = self.config.ef_construction.max(self.config.m);
            let neighbors = self.search_layer(&vector, &[ep], ef, l, None);
            for neighbor_idx in neighbors.into_iter().take(self.config.m) {
                self.attach_neighbor(node_idx, neighbor_idx, l);
                self.attach_neighbor(neighbor_idx, node_idx, l);
            }
            if let Some(first) = self.nodes[node_idx].neighbors[l].first().copied() {
                ep = first;
            }
        }

        if level > self.max_level {
            self.max_level = level;
            self.entrypoint = Some(node_idx);
        }
    }

    fn soft_delete(&mut self, id: &str) {
        if let Some(node) = self.nodes.iter_mut().find(|n| n.id == id && !n.deleted) {
            node.deleted = true;
        }
    }

    fn search(
        &self,
        query: &[f32],
        top_k: usize,
        threshold: Option<f32>,
        namespace: Option<&str>,
    ) -> Vec<SearchHit> {
        if self.nodes.is_empty() {
            return Vec::new();
        }

        if self.len() <= self.config.flat_exact_cutoff {
            let mut exact: Vec<SearchHit> = self
                .nodes
                .iter()
                .filter(|n| !n.deleted)
                .filter(|n| namespace.map(|ns| n.namespace == ns).unwrap_or(true))
                .map(|n| SearchHit {
                    id: n.id.clone(),
                    namespace: n.namespace.clone(),
                    score: similarity(self.metric, query, &n.vector),
                })
                .collect();
            exact.sort_by(|a, b| b.score.total_cmp(&a.score));
            if let Some(min_score) = threshold {
                exact.retain(|h| h.score >= min_score);
            }
            exact.truncate(top_k);
            return exact;
        }

        let mut ep = self.entrypoint.unwrap_or(0);
        for l in (1..=self.max_level).rev() {
            ep = self.greedy_descent(query, ep, l, namespace);
        }

        let ef = self.config.ef_search.max(top_k);
        let candidates = self.search_layer(query, &[ep], ef, 0, namespace);
        let mut hits: Vec<SearchHit> = candidates
            .into_iter()
            .filter_map(|idx| {
                let n = &self.nodes[idx];
                if n.deleted {
                    return None;
                }
                if namespace.map(|ns| n.namespace != ns).unwrap_or(false) {
                    return None;
                }
                Some(SearchHit {
                    id: n.id.clone(),
                    namespace: n.namespace.clone(),
                    score: similarity(self.metric, query, &n.vector),
                })
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
        let live_nodes: Vec<(String, String, Vec<f32>)> = self
            .nodes
            .iter()
            .filter(|n| !n.deleted)
            .map(|n| (n.id.clone(), n.namespace.clone(), n.vector.clone()))
            .collect();
        let metric = self.metric;
        let config = self.config.clone();
        *self = HnswGraph::new(metric, config);
        for (id, ns, vec) in live_nodes {
            self.upsert(id, ns, vec);
        }
    }

    fn len(&self) -> usize {
        self.nodes.iter().filter(|n| !n.deleted).count()
    }
}

