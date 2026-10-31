from __future__ import annotations

import ast
from pathlib import Path

from winnie_api.main import create_app
from winnie_api.settings import Settings

PACKAGES = Path(__file__).resolve().parents[3] / "packages"

ALLOWED = {
    "winnie-core": {"winnie_core"},
    "winnie-gateway": {"winnie_core", "winnie_gateway"},
    "winnie-knowledge": {"winnie_knowledge"},
    "winnie-meetings": {"winnie_core", "winnie_knowledge", "winnie_meetings"},
    # winnie-local is a shared execution layer (like winnie-core), not a
    # capability. It may be used by the assistant; it imports no capability.
    "winnie-local": {"winnie_local"},
    "winnie-assistant": {"winnie_core", "winnie_assistant", "winnie_local"},
}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_capability_packages_do_not_import_the_host_or_each_other() -> None:
    violations: list[str] = []
    for package, allowed in ALLOWED.items():
        root = PACKAGES / package / "src"
        for path in root.rglob("*.py"):
            for module in _imports(path):
                if module == "winnie_api" or module.startswith("winnie_api."):
                    violations.append(f"{path}: host import {module}")
                    continue
                if module.startswith("winnie_"):
                    top = module.split(".", 1)[0]
                    if top not in allowed:
                        violations.append(f"{path}: forbidden {module}")
    assert violations == []


def test_host_registers_expected_capabilities(meeting_settings: Settings) -> None:
    app = create_app(settings=meeting_settings)
    assert app.state.capability_names == ("gateway", "meetings")


def test_host_registers_the_assistant_when_enabled(assistant_settings: Settings) -> None:
    app = create_app(settings=assistant_settings)
    assert app.state.capability_names == ("assistant", "gateway")
