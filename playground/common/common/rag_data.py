"""RAG 教学共用：示例语料 + 简易切分 + 本地假向量 + 简易向量库（无 numpy）。"""

from __future__ import annotations

import hashlib
import math
import re
from pathlib import Path

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.retrievers import BaseRetriever
from pydantic import ConfigDict, Field

CORPUS_SECTIONS = [
    (
        "产品说明",
        "Winnie 是具备长期记忆的个人智能基础设施。"
        "知识以 Markdown 存储，并用 Git 做版本管理。"
        "向量检索默认使用 Qdrant；模型通过 OpenAI 兼容接口接入，避免厂商锁定。",
    ),
    (
        "开发约定",
        "服务层使用 FastAPI。"
        "对话接口兼容 OpenAI Chat Completions。"
        "记忆写入前会做语义提炼与分类打标。",
    ),
    (
        "演示里程碑",
        "两周内完成对话与记忆闭环。"
        "管理界面可编辑文档并重新纳入知识库。",
    ),
]


class HashEmbeddings(Embeddings):
    """无 numpy 依赖的确定性假向量（教学用，非语义质量保证）。"""

    def __init__(self, size: int = 64) -> None:
        self.size = size

    def _vec(self, text: str) -> list[float]:
        # 一半维度：token 哈希桶（近似「词重叠」）；一半：全文 SHA 扰动
        half = self.size // 2
        buckets = [0.0] * half
        for tok in re.findall(r"[\w\u4e00-\u9fff]+", text.lower()):
            h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
            buckets[h % half] += 1.0
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        noise: list[float] = []
        while len(noise) < self.size - half:
            for b in digest:
                noise.append((b / 255.0) * 2 - 1)
                if len(noise) >= self.size - half:
                    break
            digest = hashlib.sha256(digest).digest()
        vals = buckets + noise
        norm = math.sqrt(sum(v * v for v in vals)) or 1.0
        return [v / norm for v in vals]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


class SimpleVectorStore:
    """教学用内存向量库：add / similarity_search / as_retriever。"""

    def __init__(self, embedding: Embeddings) -> None:
        self.embedding = embedding
        self._docs: list[Document] = []
        self._vectors: list[list[float]] = []

    def add_documents(self, documents: list[Document]) -> list[str]:
        vectors = self.embedding.embed_documents([d.page_content for d in documents])
        self._docs.extend(documents)
        self._vectors.extend(vectors)
        return [str(i) for i in range(len(documents))]

    def similarity_search(self, query: str, k: int = 4) -> list[Document]:
        return [d for d, _ in self.similarity_search_with_score(query, k=k)]

    def similarity_search_with_score(
        self, query: str, k: int = 4
    ) -> list[tuple[Document, float]]:
        if not self._docs:
            return []
        q = self.embedding.embed_query(query)
        scored = [
            (doc, _cosine(q, vec)) for doc, vec in zip(self._docs, self._vectors)
        ]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:k]

    def as_retriever(self, *, search_kwargs: dict | None = None) -> BaseRetriever:
        k = int((search_kwargs or {}).get("k", 4))
        store = self

        class _Ret(BaseRetriever):
            model_config = ConfigDict(arbitrary_types_allowed=True)
            k: int = Field(default=4)

            def _get_relevant_documents(self, query: str) -> list[Document]:
                return store.similarity_search(query, k=self.k)

        return _Ret(k=k)


def sample_documents() -> list[Document]:
    return [
        Document(page_content=body, metadata={"source": f"{title}.md", "title": title})
        for title, body in CORPUS_SECTIONS
    ]


def split_text(text: str, chunk_size: int = 80, overlap: int = 16) -> list[str]:
    if chunk_size <= overlap:
        raise ValueError("chunk_size must be > overlap")
    chunks: list[str] = []
    i = 0
    while i < len(text):
        chunks.append(text[i : i + chunk_size])
        i += chunk_size - overlap
    return chunks


def split_docs(
    docs: list[Document] | None = None,
    chunk_size: int = 80,
    overlap: int = 16,
) -> list[Document]:
    out: list[Document] = []
    for doc in docs or sample_documents():
        for j, piece in enumerate(split_text(doc.page_content, chunk_size, overlap)):
            out.append(
                Document(page_content=piece, metadata={**doc.metadata, "chunk": j})
            )
    return out


def build_store(docs: list[Document] | None = None) -> SimpleVectorStore:
    store = SimpleVectorStore(embedding=HashEmbeddings(size=64))
    store.add_documents(docs or split_docs())
    return store


def keyword_search(docs: list[Document], query: str, k: int = 3) -> list[Document]:
    """简易「BM25 替身」：按查询词命中数排序。"""
    terms = [t for t in re.split(r"\W+", query.lower()) if t]
    scored: list[tuple[int, Document]] = []
    for doc in docs:
        text = doc.page_content.lower()
        score = sum(1 for t in terms if t in text)
        if score:
            scored.append((score, doc))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [d for _, d in scored[:k]]


def write_sample_files(data_dir: Path) -> list[Path]:
    data_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for title, body in CORPUS_SECTIONS:
        p = data_dir / f"{title}.md"
        p.write_text(f"# {title}\n\n{body}\n", encoding="utf-8")
        paths.append(p)
    csv_path = data_dir / "orders.csv"
    csv_path.write_text(
        "order_id,status\n1001,shipped\n1002,pending\n", encoding="utf-8"
    )
    paths.append(csv_path)
    return paths
