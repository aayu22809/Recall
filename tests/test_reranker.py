from __future__ import annotations

from vector_embedded_finder.reranker import reciprocal_rank_fusion


def test_rrf_single_list() -> None:
    rows = reciprocal_rank_fusion([["a", "b", "c"]], k=60)
    assert rows == ["a", "b", "c"]


def test_rrf_merge_two_lists() -> None:
    rows = reciprocal_rank_fusion([["a", "b", "c"], ["x", "b", "y"]], k=60)
    assert rows[0] == "b"


def test_rrf_k_parameter() -> None:
    low_k = reciprocal_rank_fusion([["a", "b", "c"], ["x", "b", "y"]], k=1)
    high_k = reciprocal_rank_fusion([["a", "b", "c"], ["x", "b", "y"]], k=1000)
    assert low_k[0] == "b"
    assert "b" in high_k
