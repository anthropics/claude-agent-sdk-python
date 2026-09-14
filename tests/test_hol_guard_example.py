from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest


def _load_example() -> Any:
    path = Path(__file__).parents[1] / "examples" / "hol_guard.py"
    spec = importlib.util.spec_from_file_location("hol_guard_example", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def hol_guard() -> Any:
    return _load_example()


@pytest.mark.anyio
async def test_hook_allows_only_explicitly_benign_commands(
    monkeypatch: pytest.MonkeyPatch, hol_guard: Any
) -> None:
    calls: list[str] = []

    async def classify(command: str) -> dict[str, Any]:
        calls.append(command)
        return {
            "minimum_action": "allow",
            "classification": {"explicitly_benign": True},
        }

    monkeypatch.setattr(hol_guard, "_classify_with_hol_guard", classify)
    result = await hol_guard.check_bash_command(
        {"tool_name": "Bash", "tool_input": {"command": "git status"}}, None, None
    )

    assert result["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert calls == ["git status"]


@pytest.mark.anyio
async def test_hook_denies_review_results_without_running_bash(
    monkeypatch: pytest.MonkeyPatch, hol_guard: Any
) -> None:
    executed: list[str] = []

    async def classify(command: str) -> dict[str, Any]:
        return {
            "minimum_action": "review",
            "classification": {"explicitly_benign": False},
        }

    monkeypatch.setattr(hol_guard, "_classify_with_hol_guard", classify)
    result = await hol_guard.check_bash_command(
        {"tool_name": "Bash", "tool_input": {"command": "rm -rf ./build"}}, None, None
    )
    if result["hookSpecificOutput"]["permissionDecision"] == "allow":
        executed.append("rm -rf ./build")

    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert executed == []


@pytest.mark.anyio
async def test_hook_denies_classifier_failures(
    monkeypatch: pytest.MonkeyPatch, hol_guard: Any
) -> None:
    async def classify(command: str) -> dict[str, Any]:
        raise RuntimeError("classifier unavailable")

    monkeypatch.setattr(hol_guard, "_classify_with_hol_guard", classify)
    result = await hol_guard.check_bash_command(
        {"tool_name": "Bash", "tool_input": {"command": "echo hello"}}, None, None
    )

    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
