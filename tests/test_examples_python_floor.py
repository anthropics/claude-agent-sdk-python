"""The examples must run on the oldest Python the package supports.

``pyproject.toml`` declares ``requires-python = ">=3.10"`` and the README
points users at these examples, but CI runs them on 3.13 only, so an asyncio
API added after 3.10 can land in an example unnoticed. ``asyncio.timeout``
did: ``examples/streaming_mode.py`` raised ``AttributeError`` on Python 3.10.
"""

import ast
from pathlib import Path

import pytest

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"

# asyncio module attributes that do not exist on Python 3.10.
ASYNCIO_NEWER_THAN_310 = frozenset(
    {
        # 3.11
        "Barrier",
        "BrokenBarrierError",
        "Runner",
        "TaskGroup",
        "Timeout",
        "timeout",
        "timeout_at",
        # 3.12
        "create_eager_task_factory",
        "eager_task_factory",
        # 3.13
        "QueueShutDown",
    }
)


def _example_files() -> list[Path]:
    return sorted(EXAMPLES_DIR.rglob("*.py"))


@pytest.mark.parametrize(
    "path", _example_files(), ids=lambda p: p.relative_to(EXAMPLES_DIR).as_posix()
)
def test_example_uses_no_asyncio_api_newer_than_python_310(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    uses = [
        f"line {node.lineno}: asyncio.{node.attr}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "asyncio"
        and node.attr in ASYNCIO_NEWER_THAN_310
    ]
    assert not uses, f"{path.name} uses asyncio APIs missing on Python 3.10: {uses}"
