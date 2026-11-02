from __future__ import annotations

from pathlib import Path

import pytest

from winnie_llm import ApiKeys, MissingApiKey, env_name_for
from winnie_llm.keys import write_env_value


def test_known_suppliers_map_to_the_documented_variables() -> None:
    assert env_name_for("deepseek") == "DEEPSEEK_API_KEY"
    assert env_name_for("openai") == "OPENAI_API_KEY"
    assert env_name_for("anthropic") == "ANTHROPIC_API_KEY"
    assert env_name_for("qwen") == "DASHSCOPE_API_KEY"
    assert env_name_for("zhipu") == "ZHIPU_API_KEY"
    assert env_name_for("volcengine") == "VOLCENGINE_ASR_API_KEY"
    # A second account for the same vendor still has an obvious name.
    assert env_name_for("deepseek-work") == "DEEPSEEK_WORK_API_KEY"


def test_env_file_parsing_keeps_quotes_and_drops_trailing_comments(
    tmp_path: Path,
) -> None:
    path = tmp_path / ".env"
    path.write_text(
        "# vendor keys\n"
        "DEEPSEEK_API_KEY=abc123\n"
        "export VOLCENGINE_ASR_API_KEY='quoted value'\n"
        'OPENAI_API_KEY="double quoted"\n'
        "CUSTOM_ONE_API_KEY=value # trailing comment\n"
        "\n"
        "NOT_A_KEY=ignored\n",
        encoding="utf-8",
    )
    keys = ApiKeys.load(path, environ={})
    assert keys.get("deepseek") == "abc123"
    assert keys.get("volcengine") == "quoted value"
    assert keys.get("openai") == "double quoted"
    assert keys.get("custom-one") == "value"
    assert keys.get("missing") == ""
    assert keys.present("deepseek") is True
    assert keys.present("missing") is False


def test_process_environment_overrides_the_file(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text("DEEPSEEK_API_KEY=from-file\n", encoding="utf-8")
    keys = ApiKeys.load(path, environ={"DEEPSEEK_API_KEY": "from-shell"})
    assert keys.get("deepseek") == "from-shell"


def test_missing_key_names_the_file_to_edit(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text("OPENAI_API_KEY=\n", encoding="utf-8")
    keys = ApiKeys.load(path, environ={})
    with pytest.raises(MissingApiKey) as raised:
        keys.require("deepseek")
    message = str(raised.value)
    assert "DEEPSEEK_API_KEY" in message
    assert str(path) in message


def test_env_file_variable_selects_another_file(tmp_path: Path) -> None:
    path = tmp_path / "custom.env"
    path.write_text("OPENAI_API_KEY=custom\n", encoding="utf-8")
    keys = ApiKeys.load(environ={"WINNIE_LLM_ENV_FILE": str(path)})
    assert keys.path == path
    assert keys.get("openai") == "custom"


def test_writing_a_key_keeps_the_rest_of_the_env_file(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text(
        "# vendor keys\n"
        "OPENAI_API_KEY=old\n"
        "UNRELATED=keep # a note\n",
        encoding="utf-8",
    )
    write_env_value(path, "OPENAI_API_KEY", "new-value")
    assert path.read_text(encoding="utf-8") == (
        "# vendor keys\n"
        "OPENAI_API_KEY=new-value\n"
        "UNRELATED=keep # a note\n"
    )
    # A missing variable is appended, and the round trip reads back the value.
    write_env_value(path, "ZHIPU_API_KEY", "zhipu-value")
    assert ApiKeys.load(path, environ={}).get("zhipu") == "zhipu-value"
    # The file holds credentials, so it is not world-readable.
    assert (path.stat().st_mode & 0o777) == 0o600


def test_writing_a_key_preserves_an_export_prefix_and_quotes_spaces(
    tmp_path: Path,
) -> None:
    path = tmp_path / ".env"
    path.write_text("export OPENAI_API_KEY=old\n", encoding="utf-8")
    write_env_value(path, "OPENAI_API_KEY", "new value")
    text = path.read_text(encoding="utf-8")
    assert text == 'export OPENAI_API_KEY="new value"\n'
    assert ApiKeys.load(path, environ={}).get("openai") == "new value"


def test_api_keys_set_updates_the_file_and_the_snapshot(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    keys = ApiKeys.load(path, environ={})
    keys.set("deepseek-work", "abc")
    assert path.read_text(encoding="utf-8") == "DEEPSEEK_WORK_API_KEY=abc\n"
    assert keys.get("deepseek-work") == "abc"
