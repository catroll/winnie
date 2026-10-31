"""Shared schedule tree fixture.

The fixture text is a cut-down but structurally faithful week file: two
week-level items, four day sections (one of them empty), tags in different
orders, a nested list, a fenced code block, a link to a sibling note and a
subsection of ``## 周末总结`` whose items must not become events.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from winnie_schedule.parser import parse_file

WEEK_TEXT = """# 周安排 05 - 11

```txt
+----+----+----+----+----+----+----+
| 一 | 二 | 三 | 四 | 五 | 六 | 日 |
+----+----+----+----+----+----+----+
|  5 |  6 |  7 |  8 |  9 | 10 | 11 |
+----+----+----+----+----+----+----+
```

## 工作事项

-   [ ] **`项目名称`** 开发内容描述
-   [ ] 季度目标对齐全组

### 10/05 周一

-   [ ] `10/05` [每天] [09:30~10:30] 值班巡检（上午）
-   [x] `10/05` 已完成事项 [记录](20261005-notes.md)
    -   子条目属于上一行
    -   [ ] 子条目里的复选框不是事件

### 10/06 周二

-   [ ] `10/06` [12:00] 单点时间
-   [ ] `10/06` 带代码块

    ```sh
    echo hi
    ```

### 10/07 周三

### 10/11 周日

-   [ ] `10/11` [newapi] 标签在日期之后

## 周末总结

### 本周工作总结

-   [ ] 这不是事件

### 其他事项

...

<!-- ############################################################ -->

[重点]: https://img.shields.io/badge/-⏺-red "重点"
"""

MONDAY = date(2026, 10, 5)


def write_week(root: Path, text: str, monday: date = MONDAY, name: str | None = None) -> Path:
    """Write ``text`` as the week file of ``monday`` below ``root``.

    The file lands in the canonical ``work/<YYYY>/Q<n>/<MM>/`` layout.
    """
    stem = name or f"{monday:%Y%m}-{monday:%d}-{monday + timedelta(days=6):%d}"
    quarter = (monday.month + 2) // 3
    directory = root / "work" / f"{monday.year:04d}" / f"Q{quarter}" / f"{monday.month:02d}"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{stem}.md"
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def schedule_root(tmp_path: Path) -> Path:
    root = tmp_path / "WLB"
    write_week(root, WEEK_TEXT)
    return root


@pytest.fixture
def week_path(schedule_root: Path) -> Path:
    return schedule_root / "work" / "2026" / "Q4" / "10" / "202610-05-11.md"


@pytest.fixture
def week(week_path: Path):
    return parse_file(week_path)
