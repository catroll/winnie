"""34langchain-rag-hybrid：向量检索 ∪ 关键词检索。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from _shared.rag_data import build_store, keyword_search, split_docs


def main() -> None:
    q = "Qdrant"
    all_docs = split_docs()
    vec = build_store(all_docs).similarity_search(q, k=3)
    lex = keyword_search(all_docs, q, k=3)
    print("Vector:")
    for d in vec:
        print("-", d.page_content[:50])
    print("Lexical:")
    for d in lex:
        print("-", d.page_content[:50])
    merged = {d.page_content: d for d in vec + lex}
    print("Hybrid union:", len(merged))


if __name__ == "__main__":
    main()
