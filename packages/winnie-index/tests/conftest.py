from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from winnie_index.config import Config, load_config


@pytest.fixture()
def tree(tmp_path: Path) -> Path:
    roots = tmp_path / "roots"
    notes = roots / "notes"
    docs = roots / "docs"
    notes.mkdir(parents=True)
    docs.mkdir(parents=True)
    (notes / "project.md").write_text(
        textwrap.dedent(
            """\
            # 项目记录

            我们决定使用 Qdrant 作为向量库。

            ## 部署

            发布前先跑 lint，然后重启服务。
            """
        ),
        encoding="utf-8",
    )
    (notes / "todo.txt").write_text("明天把检索接上\n", encoding="utf-8")
    (docs / "copy.md").write_text((notes / "project.md").read_text(encoding="utf-8"), encoding="utf-8")
    (docs / "ignored.pdf").write_bytes(b"%PDF-1.4 not indexed")
    (docs / "binary.md").write_bytes(b"\x00\x01\x02binary")
    return tmp_path


@pytest.fixture()
def config_path(tmp_path: Path, tree: Path) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(
        textwrap.dedent(
            f"""\
            data_dir: {tmp_path / "data"}
            roots:
              - name: notes
                path: {tree / "roots" / "notes"}
              - name: docs
                path: {tree / "roots" / "docs"}
            serve:
              http: 127.0.0.1:0
            skill:
              target: {tmp_path / "skill"}
            """
        ),
        encoding="utf-8",
    )
    return path


@pytest.fixture()
def config(config_path: Path) -> Config:
    return load_config(config_path, environ={})
