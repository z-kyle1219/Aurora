"""VectorStore 文件持久化集成测试。"""

import numpy as np
import pytest

from aurora.vector_store import VectorStore


def test_vst_int_001__save_load_and_search_round_trip(tmp_path, tiny_vector_data):
    """VST-INT-001：临时目录往返保留元数据、dtype、顺序与检索结果。"""
    vectors, chunks = tiny_vector_data
    original = VectorStore(chunks=chunks[:2], vectors=vectors[:2])
    index_dir = tmp_path / "index"

    original.save(index_dir)
    loaded = VectorStore.load(index_dir)

    assert (index_dir / "vectors.npy").is_file()
    assert (index_dir / "chunks.json").is_file()
    assert loaded.chunks == original.chunks
    assert loaded.vectors.dtype == original.vectors.dtype == np.float32
    np.testing.assert_array_equal(loaded.vectors, original.vectors)

    query = np.asarray([1.0, 0.0], dtype="float32")
    original_results = original.search(query, top_k=2)
    loaded_results = loaded.search(query, top_k=2)
    assert [result.chunk for result in loaded_results] == [
        result.chunk for result in original_results
    ]
    assert [result.score for result in loaded_results] == pytest.approx(
        [result.score for result in original_results]
    )
