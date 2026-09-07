from __future__ import annotations
import os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT.parent))
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from _shared.llm_record import LlmInteractionRecorder
from _shared.model import build_chat_model, load_env
from _shared.rag_data import build_store
EXAMPLE_ID = ROOT.name; LOGS_DIR = ROOT / "logs"

def main():
    load_env(ROOT)
    if not os.getenv("OPENAI_API_KEY"):
        print("缺少 OPENAI_API_KEY", file=sys.stderr); sys.exit(1)
    rec=LlmInteractionRecorder(LOGS_DIR, example_id=EXAMPLE_ID)
    retriever=build_store().as_retriever(search_kwargs={"k":2})
    history=[]
    prompt=ChatPromptTemplate.from_messages([
        ("system", "结合历史与检索上下文回答。\n上下文:\n{context}"),
        MessagesPlaceholder("chat_history"),
        ("human", "{question}"),
    ])
    chain=prompt|build_chat_model()|StrOutputParser()
    for q in ["介绍产品 Winnie", "它的知识怎么存？"]:
        ctx="\n".join(d.page_content for d in retriever.invoke(q))
        print("User:", q)
        ans=chain.invoke({"context":ctx, "chat_history":history, "question":q}, config={"callbacks":[rec]})
        print("AI:", ans)
        history += [HumanMessage(content=q), AIMessage(content=ans)]
if __name__=="__main__":
    main()
