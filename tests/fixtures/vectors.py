"""阶段 0 建立的二维向量与合成知识块。"""

import numpy as np

from aurora.chunker import Chunk


def make_tiny_vectors() -> np.ndarray:
    return np.asarray([[1.0, 0.0], [0.0, 1.0], [0.6, 0.8]], dtype="float32")


def make_tiny_chunks() -> list[Chunk]:
    return [
        Chunk(text="运放遵循虚短与虚断。", source="synthetic-opamp.md", heading="集成运放"),
        Chunk(text="负反馈可改善放大电路性能。", source="synthetic-feedback.md", heading="负反馈"),
        Chunk(text="整流、滤波和稳压组成直流电源。", source="synthetic-power.md", heading="直流电源"),
    ]
