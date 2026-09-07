from __future__ import annotations
import argparse, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableLambda, RunnablePassthrough
from common.llm_record import LlmInteractionRecorder
from common.model import build_chat_model, load_env
from common.rag_data import build_store
EXAMPLE_ID = ROOT.name; LOGS_DIR = ROOT / "logs"

def main():
    load_env(ROOT)
    if not os.getenv("OPENAI_API_KEY"):
        print("缺少 OPENAI_API_KEY", file=sys.stderr); sys.exit(1)
    p=argparse.ArgumentParser(); p.add_argument("-q", default="知识怎么存储？"); a=p.parse_args()
    rec=LlmInteractionRecorder(LOGS_DIR, example_id=EXAMPLE_ID)
    retriever=build_store().as_retriever(search_kwargs={"k":3})
    prompt=ChatPromptTemplate.from_messages([
        ("system", "只根据上下文回答；不知则说不知。\n上下文：\n{context}"),
        ("human", "{question}"),
    ])
    fmt=RunnableLambda(lambda docs: "\n".join(d.page_content for d in docs))
    chain=({"context": retriever|fmt, "question": RunnablePassthrough()} | prompt | build_chat_model() | StrOutputParser())
    print(chain.invoke(a.q, config={"callbacks":[rec]}))
if __name__=="__main__":
    main()
