"""多 Tool 工具箱。"""

from langchain.tools import tool

_DB = {"order-1001": "已发货", "order-1002": "待支付"}

@tool
def get_weather(city: str) -> str:
    """查询城市天气。"""
    return {"北京": "晴 25°C", "上海": "多云 27°C"}.get(city, f"{city}: unknown")

@tool
def web_search(query: str) -> str:
    """搜索公开信息（演示假结果）。"""
    return f"[search] top1: 关于「{query}」的演示条目"

@tool
def calculator(expression: str) -> str:
    """计算简单算术，仅支持数字与 + - * / ( ) 空格。"""
    allowed = set("0123456789+-*/(). ")
    if not expression or set(expression) - allowed:
        return "error: invalid expression"
    try:
        return str(eval(expression, {"__builtins__": {}}, {}))  # noqa: S307
    except Exception as exc:  # noqa: BLE001
        return f"error: {exc}"

@tool
def db_lookup(order_id: str) -> str:
    """按订单号查询状态。"""
    return _DB.get(order_id, f"{order_id}: not found")

TOOLS = [get_weather, web_search, calculator, db_lookup]
