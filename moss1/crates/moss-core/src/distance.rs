use crate::types::DistanceMetric;

pub fn dot(a: &[f32], b: &[f32]) -> f32 {
    a.iter().zip(b.iter()).map(|(x, y)| x * y).sum()
}

pub fn norm(a: &[f32]) -> f32 {
    dot(a, a).sqrt()
}

pub fn cosine_similarity(a: &[f32], b: &[f32]) -> f32 {
    let denom = norm(a) * norm(b);
    if denom == 0.0 {
        0.0
    } else {
        dot(a, b) / denom
    }
}

pub fn similarity(metric: DistanceMetric, a: &[f32], b: &[f32]) -> f32 {
    match metric {
        DistanceMetric::Cosine => cosine_similarity(a, b),
        DistanceMetric::Dot => dot(a, b),
    }
}

pub fn distance(metric: DistanceMetric, a: &[f32], b: &[f32]) -> f32 {
    match metric {
        DistanceMetric::Cosine => 1.0 - cosine_similarity(a, b),
        DistanceMetric::Dot => -dot(a, b),
    }
}

