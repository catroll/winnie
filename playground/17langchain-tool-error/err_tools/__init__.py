"""会失败并可纠正的工具。"""

from __future__ import annotations

from langchain.tools import tool

_VALID_CITIES = {"北京", "上海", "深圳"}


@tool
def lookup_city_code(city: str) -> str:
    """查询城市编码。city 必须是「北京/上海/深圳」之一。"""
    city = city.strip()
    if city not in _VALID_CITIES:
        # 返回错误字符串（而非 raise）：作为 ToolMessage 回到模型，便于改参重试。
        # 若 raise 且未配置 handle_tool_errors，整次 Agent 会直接失败。
        return (
            f"ERROR: unsupported city={city!r}; "
            f"only {sorted(_VALID_CITIES)} allowed. "
            "请换一个支持的城市名后重试。"
        )
    codes = {"北京": "BJ", "上海": "SH", "深圳": "SZ"}
    return f"{city} → {codes[city]}"


@tool
def echo_ok(text: str) -> str:
    """回显文本（稳定成功的对照工具）。"""
    return f"ok:{text}"
