"""23langchain-agent-human：Human-in-the-loop 审批后再执行高风险工具。"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from langchain.tools import tool

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from _shared.model import load_env

EXAMPLE_ID = ROOT.name
_APPROVED = {"yes", "y", "是", "同意", "ok"}


@tool
def delete_database(name: str) -> str:
    """删除数据库（高风险）。"""
    return f"DELETED:{name}"


def main() -> None:
    load_env(ROOT)
    parser = argparse.ArgumentParser(description="Human approval before risky tool")
    parser.add_argument("--yes", action="store_true", help="自动批准（冒烟用）")
    parser.add_argument("--no", action="store_true", help="自动拒绝")
    parser.add_argument("--name", default="demo_db")
    args = parser.parse_args()

    print(f"Agent: 我要删除数据库 {args.name}")
    if args.yes:
        ans = "yes"
    elif args.no:
        ans = "no"
    else:
        ans = input("Human Approval? [yes/no]: ").strip().lower()

    if ans not in _APPROVED:
        print("Resume: 已拒绝（Interrupt → 不执行）")
        return

    print("Resume: Yes → Execute")
    print(delete_database.invoke({"name": args.name}))


if __name__ == "__main__":
    main()
