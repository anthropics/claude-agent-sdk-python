"""The declared dependency floors have to provide the APIs the SDK calls.

Nothing else catches a floor that is too low: resolvers install the newest
version satisfying every requirement, so CI always runs on the latest release.
A bogus lower bound only bites installs that honor it -- ``--no-deps``,
constraints files, lockfiles pinned to the declared minimum, and redistributors
that package the declared range.
"""

import ast
import inspect
import re
from pathlib import Path

import anyio
import pytest

REPO_ROOT = Path(__file__).parent.parent
PACKAGE = REPO_ROOT / "src" / "claude_agent_sdk"
SUBPROCESS_CLI = PACKAGE / "_internal" / "transport" / "subprocess_cli.py"
PACKAGE_INIT = PACKAGE / "__init__.py"

# Distribution -> (lowest release providing the API, what needs it). Keep an
# entry only while the guard test below still finds the call site it names.
REQUIRED_FLOORS = {
    "anyio": (
        (4, 5, 0),
        "anyio.open_process(user=...): the user/group/umask keywords landed in "
        "anyio 4.5.0, and subprocess_cli.py passes user= unconditionally",
    ),
    "typing-extensions": (
        (4, 1, 0),
        "typing_extensions.is_typeddict: added in typing_extensions 4.1.0, and "
        "__init__.py imports it on Python 3.10",
    ),
}

_REQUIREMENT = re.compile(
    r"^(?P<name>[A-Za-z0-9._-]+)\s*(?:\[[^\]]*\])?\s*(?:>=\s*(?P<floor>[0-9.]+))?"
)


def _declared_lower_bounds(package: str) -> list[tuple[str, tuple[int, ...]]]:
    """Every ``>=`` bound pyproject.toml declares for `package`."""
    tomllib = pytest.importorskip("tomllib")  # stdlib from 3.11

    config = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    project = config["project"]
    requirements = list(project["dependencies"])
    for extra in project.get("optional-dependencies", {}).values():
        requirements.extend(extra)

    bounds = []
    for requirement in requirements:
        match = _REQUIREMENT.match(requirement.split(";")[0].strip())
        assert match is not None, f"unparsable requirement: {requirement!r}"
        if match["name"].replace("_", "-").lower() != package:
            continue
        assert match["floor"] is not None, (
            f"{requirement!r} declares no lower bound, so it allows a {package} "
            f"too old for the API the SDK uses"
        )
        bounds.append((requirement, tuple(int(p) for p in match["floor"].split("."))))
    return bounds


@pytest.mark.parametrize("package", sorted(REQUIRED_FLOORS))
def test_declared_floor_provides_the_api_the_sdk_uses(package: str) -> None:
    floor, reason = REQUIRED_FLOORS[package]
    bounds = _declared_lower_bounds(package)
    assert bounds, f"{package} is no longer declared in pyproject.toml"

    for requirement, declared in bounds:
        assert declared >= floor, (
            f"{requirement!r} admits {package} {'.'.join(map(str, declared))}, "
            f"which lacks {reason}"
        )


def test_transport_still_passes_user_to_open_process() -> None:
    """Justifies the anyio floor, and checks the installed anyio provides it."""
    tree = ast.parse(SUBPROCESS_CLI.read_text())
    keywords = {
        keyword.arg
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "open_process"
        for keyword in node.keywords
        if keyword.arg is not None
    }

    assert "user" in keywords
    accepted = set(inspect.signature(anyio.open_process).parameters)
    assert keywords <= accepted, f"anyio.open_process() rejects {keywords - accepted}"


def test_package_init_still_imports_is_typeddict_from_typing_extensions() -> None:
    """Justifies the typing-extensions floor."""
    assert "from typing_extensions import is_typeddict" in PACKAGE_INIT.read_text()
