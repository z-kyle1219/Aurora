"""手写向量库 —— 存向量 + 算余弦相似度取 top-k。

这是 RAG「在线检索」的核心，也是面试最爱问的「向量检索原理」。

我们故意不引入 Chroma / FAISS，就为了把「检索」这两个字拆到只剩数学：

    两个向量 a、b 的相似度 = cos(夹角) = (a·b) / (|a|·|b|)

因为我们入库时已经把向量「归一化」成单位长度（|a|=|b|=1），分母变成 1，
于是余弦相似度就退化成一次点积 `a·b`，取值范围 [-1, 1]：

    1  → 方向完全一致（最像）
    0  → 互相垂直（无关）
    -1 → 方向相反（语义相反）

「检索 top-k」= 拿问题向量，和库里的 N 个向量各点积一次，取出分数最高的 k 个。
"""

import json
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from aurora.chunker import Chunk


@dataclass
class SearchResult:
    """一次命中的检索结果：片段 + 相似度分数。"""

    chunk: Chunk
    score: float


class VectorStore:
    """极简向量库：内存里持有一个向量矩阵 + 一段元数据，能存盘、能查。"""

    def __init__(self, chunks: list[Chunk] | None = None,
                 vectors: np.ndarray | None = None):
        self.chunks: list[Chunk] = chunks or []
        # 形状 (n, dim) 的矩阵，第 i 行对应 chunks[i]
        self.vectors: np.ndarray = vectors

    def add(self, chunk: Chunk, vector: np.ndarray) -> None:
        self.chunks.append(chunk)
        if self.vectors is None:
            self.vectors = vector.reshape(1, -1)
        else:
            self.vectors = np.vstack([self.vectors, vector.reshape(1, -1)])

    def search(self, query_vector: np.ndarray, top_k: int = 3) -> list[SearchResult]:
        """检索最相关的 top_k 个片段。

        `self.vectors @ query_vector` 就是「矩阵点积」：一次算出问题向量和
        全部 N 个向量的相似度，得到一个长度 N 的数组——这就是 numpy 比手写
        for 循环快的原因（底层用 BLAS 并行算）。
        """
        if self.vectors is None or len(self.vectors) == 0:
            return []
        scores = self.vectors @ query_vector  # 形状 (N,)
        # argsort 默认升序，[::-1] 反转成降序，取前 top_k 个下标
        top_indices = np.argsort(scores)[::-1][:top_k]
        return [
            SearchResult(chunk=self.chunks[i], score=float(scores[i]))
            for i in top_indices
        ]

    def save(self, directory: str | Path) -> None:
        """把向量和元数据存到磁盘（离线建库的最后一步）。"""
        d = Path(directory)
        d.mkdir(parents=True, exist_ok=True)
        np.save(d / "vectors.npy", self.vectors)
        (d / "chunks.json").write_text(
            json.dumps([asdict(c) for c in self.chunks], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, directory: str | Path) -> "VectorStore":
        d = Path(directory)
        vectors = np.load(d / "vectors.npy")
        chunks = [Chunk(**c) for c in json.loads((d / "chunks.json").read_text(encoding="utf-8"))]
        return cls(chunks=chunks, vectors=vectors)

    def __len__(self) -> int:
        return len(self.chunks)
