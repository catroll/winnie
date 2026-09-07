"""按环境可配置的 ChatModel + RunnableConfig 演示链。"""

from __future__ import annotations

import os
from typing import Any, Literal

from langchain.chat_models import init_chat_model
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import ConfigurableField, RunnableConfig, RunnableLambda
from langchain_core.runnables import RunnablePassthrough

EnvName = Literal["dev", "test", "prod"]

ENV_MODEL_VARS: dict[EnvName, str] = {
    "dev": "MODEL_DEV",
    "test": "MODEL_TEST",
    "prod": "MODEL_PROD",
}


def resolve_model_name(env: EnvName) -> str:
    var = ENV_MODEL_VARS[env]
    return os.getenv(var) or os.getenv("OPENAI_MODEL") or "gpt-4o-mini"


def build_base_model():
    """带 configurable_fields 的模型：运行时改 model_name。"""
    kwargs: dict[str, Any] = {"temperature": 0}
    if base_url := os.getenv("OPENAI_BASE_URL"):
        kwargs["base_url"] = base_url
    if api_key := os.getenv("OPENAI_API_KEY"):
        kwargs["api_key"] = api_key

    # 默认挂 dev 模型；invoke 时可用 config 切到 test/prod
    default_model = resolve_model_name("dev")
    model = init_chat_model(f"openai:{default_model}", **kwargs)
    return model.configurable_fields(
        model_name=ConfigurableField(
            id="model_name",
            name="Model Name",
            description="OpenAI-compatible model id (dev/test/prod 切换)",
        )
    )


PROMPT = ChatPromptTemplate.from_messages(
    [
        ("system", "用一句中文回答。若能感知，可提到你是哪类助手。"),
        ("human", "{question}"),
    ]
)


def build_chain():
    """
    链本身不写死环境；通过 RunnableConfig.configurable 注入 model_name。
    额外用 Lambda 把 config 中的 tags/metadata 回显到结果旁路（教学用）。
    """
    model = build_base_model()
    llm_chain = PROMPT | model | StrOutputParser()

    def attach_runtime_view(payload: dict) -> dict:
        # payload: {"answer": str, "question": str} from parallel-style assign
        return payload

    # question → {question, answer} ，便于打印
    return (
        RunnablePassthrough.assign(answer=llm_chain)
        | RunnableLambda(attach_runtime_view)
    )


def config_for_env(env: EnvName, *, question: str) -> RunnableConfig:
    """组装 RunnableConfig：tags + metadata + configurable fields。"""
    model_name = resolve_model_name(env)
    return {
        "tags": [f"env:{env}", "playground", "11langchain-config"],
        "metadata": {
            "app_env": env,
            "feature": "model_switch",
            "question_preview": question[:40],
        },
        "configurable": {
            "model_name": model_name,
        },
    }


def describe_envs() -> list[dict[str, str]]:
    rows = []
    for env in ("dev", "test", "prod"):
        rows.append({"env": env, "model": resolve_model_name(env)})  # type: ignore[arg-type]
    return rows
