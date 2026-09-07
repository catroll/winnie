"""单个自定义 Tool。"""

from langchain.tools import tool


@tool
def get_weather(city: str) -> str:
    """查询城市天气（演示用假数据）。"""
    data = {
        "北京": "晴，25°C",
        "上海": "多云，27°C",
        "深圳": "阵雨，30°C",
    }
    return data.get(city, f"{city}：暂无数据（演示）")
