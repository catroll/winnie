"""示例工具：Agent 通过 tool calling 调用这些函数。"""

from datetime import datetime, timezone

from langchain.tools import tool


@tool
def get_current_time(timezone_name: str = "UTC") -> str:
    """返回当前时间。timezone_name 仅作标注（示例固定输出 UTC）。"""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    return f"{now} (requested tz label: {timezone_name})"


@tool
def calculator(expression: str) -> str:
    """计算简单算术表达式，仅支持数字与 + - * / ( ) 和空格。"""
    allowed = set("0123456789+-*/(). ")
    if not expression or set(expression) - allowed:
        return "error: only simple arithmetic is allowed"
    try:
        value = eval(expression, {"__builtins__": {}}, {})  # noqa: S307 — 已限制字符集
    except Exception as exc:  # noqa: BLE001 — 演示用，向模型返回可读错误
        return f"error: {exc}"
    return str(value)


@tool
def note_preference(key: str, value: str) -> str:
    """假装写入一条个人偏好（演示 Tool / 副作用；进程内字典，不落盘）。"""
    _PREFS[key] = value
    return f"saved preference: {key}={value}"


@tool
def read_preference(key: str) -> str:
    """读取先前用 note_preference 写入的偏好。"""
    if key not in _PREFS:
        return f"no preference for key={key}"
    return f"{key}={_PREFS[key]}"


_PREFS: dict[str, str] = {}

TOOLS = [get_current_time, calculator, note_preference, read_preference]
