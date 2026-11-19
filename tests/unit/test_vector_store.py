"""内存向量检索纯逻辑测试。"""

import numpy as np
import pytest

from aurora.vector_store import SearchResult, VectorStore


def test_vst_unit_001__returns_top_k_in_descending_dot_product_order(tiny_vector_data):
    """VST-UNIT-001：top-k 顺序、分数和结果类型与手算点积一致。"""
    vectors, chunks = tiny_vector_data
    store = VectorStore(chunks=chunks, vectors=vectors)

    results = store.search(np.asarray([1.0, 0.0], dtype="float32"), top_k=3)

    assert all(isinstance(result, SearchResult) for result in results)
    assert [result.chunk for result in results] == [chunks[0], chunks[2], chunks[1]]
    assert [result.score for result in results] == pytest.approx([1.0, 0.6, 0.0])


@pytest.mark.parametrize(
    ("top_k", "expected_indices"),
    [(1, [0]), (3, [0, 2, 1]), (5, [0, 2, 1]), (0, []), (-1, [0, 2])],
    ids=["one", "equal-size", "larger-than-size", "zero", "negative-current-semantics"],
)
def test_vst_unit_002__handles_k_boundaries(tiny_vector_data, top_k, expected_indices):
    """VST-UNIT-002：锁定 k 的边界及负数切片现状。"""
    vectors, chunks = tiny_vector_data
    store = VectorStore(chunks=chunks, vectors=vectors)

    results = store.search(np.asarray([1.0, 0.0], dtype="float32"), top_k=top_k)

    assert [result.chunk for result in results] == [chunks[index] for index in expected_indices]


@pytest.mark.parametrize(
    "store",
    [VectorStore(), VectorStore(chunks=[], vectors=None)],
    ids=["default-empty-store", "explicit-none-vectors"],
)
def test_vst_unit_003__empty_store_returns_no_results(store):
    """VST-UNIT-003：空库和 None 向量均稳定返回空列表。"""
    query = np.asarray([1.0, 0.0], dtype="float32")

    assert store.search(query, top_k=3) == []
