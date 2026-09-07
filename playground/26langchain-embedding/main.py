"""26langchain-embedding：HashEmbeddings 确定性假向量（无 numpy）。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _shared.rag_data import HashEmbeddings


def main() -> None:
    emb = HashEmbeddings(size=8)
    texts = ["苹果手机", "香蕉水果", "iPhone"]
    vectors = emb.embed_documents(texts)
    for t, v in zip(texts, vectors):
        print(t, "→", [round(x, 3) for x in v])
    q = emb.embed_query("苹果")
    print("query 苹果 →", [round(x, 3) for x in q])


if __name__ == "__main__":
    main()
