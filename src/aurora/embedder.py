"""Embedding 封装 —— 把文字变成向量。

这是 RAG 的「翻译器」：文字进、数字（向量）出。

选型：本地模型 BAAI/bge-small-zh-v1.5（约 100MB），免费离线，中文效果好。
底层用 sentence-transformers 这个库加载模型、算向量，它内部跑的是 PyTorch。

两个关键认知（面试常问）：
1. 这个模型和 DeepSeek 是「两类完全不同的模型」：DeepSeek 负责生成文字，
   这个只负责把文字压成向量，两者输入输出都不一样（上一轮讲过）。
2. 向量必须「同一模型、同一坐标系」——文档和问题都要用它，检索才有意义。

BGE 系列还有个细节：检索时官方推荐给「问题」加一句前缀指令，让模型更清楚
「这是拿来查东西的」，效果会更好。文档（passage）则不加。
"""

from functools import lru_cache

import numpy as np

# 模型名（HuggingFace 上的标识），首次使用会自动下载
MODEL_NAME = "BAAI/bge-small-zh-v1.5"
# BGE 官方推荐的「查询前缀」，只加在用户问题上，不加在文档上
QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："

# 模型从 HuggingFace 下载。有 VPN 走官方 huggingface.co 即可（默认行为）；
# 无 VPN 时，在终端先执行 `export HF_ENDPOINT=https://hf-mirror.com` 再运行本脚本。
# 这里不主动覆盖，尊重你已有的 HF_ENDPOINT 环境变量设置。


@lru_cache(maxsize=1)
def get_model():
    """加载模型（带缓存，进程内只加载一次，避免每次提问都重新加载）。"""
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(MODEL_NAME)


def encode_passages(texts: list[str]) -> np.ndarray:
    """把一批「文档片段」向量化。返回形状 (n, 维度) 的 numpy 数组。

    normalize_embeddings=True 表示把每个向量归一化到长度 1，这样后面算
    「余弦相似度」时，直接做点积即可（因为 |a||b|=1，点积 == 余弦值）。
    """
    model = get_model()
    vectors = model.encode(
        texts,
        normalize_embeddings=True,
        show_progress_bar=False,
        convert_to_numpy=True,
    )
    return np.asarray(vectors, dtype="float32")


def encode_query(text: str) -> np.ndarray:
    """把一条「用户问题」向量化（带查询前缀），返回形状 (维度,) 的向量。"""
    model = get_model()
    vector = model.encode(
        QUERY_INSTRUCTION + text,
        normalize_embeddings=True,
        convert_to_numpy=True,
    )
    return np.asarray(vector, dtype="float32")
