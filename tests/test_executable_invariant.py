"""G5 -- the safe-executable invariant is enforced, not just documented.

Every module under ``src/`` is parsed and the suite fails if

* a process-spawning call (``subprocess.*``, ``anyio.open_process`` /
  ``run_process``, ``asyncio.create_subprocess_*``, ``os.system`` /
  ``popen`` / ``exec*`` / ``spawn*`` / ``posix_spawn*``) names its program
  with a string literal -- or a module-level ``NAME = "literal"`` constant --
  that is not an absolute path, i.e. hands the OS a bare name to go and
  search for (G1); or
* an ambient-search API (``shutil.which``,
  ``distutils.spawn.find_executable``) is imported or referenced at all.

The blessed route is ``claude_agent_sdk._internal.executable``
(``find_executable`` / ``require_executable`` / ``run``); see that module's
docstring for the guarantees. ruff's ``banned-api`` table in pyproject.toml
reports the second bullet at lint time already; this test covers both at
test time, and the parametrized cases at the bottom prove the scanner is not
vacuous.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

SRC = Path(__file__).parent.parent / "src"

# Process-spawning calls -> index of the positional argument that names the
# program to run (or the argv / command line that starts with it).
_VARIANTS = ("l", "le", "lp", "lpe", "v", "ve", "vp", "vpe")
_SPAWN_APIS: dict[str, int] = {
    "subprocess.run": 0,
    "subprocess.Popen": 0,
    "subprocess.call": 0,
    "subprocess.check_call": 0,
    "subprocess.check_output": 0,
    "subprocess.getoutput": 0,
    "subprocess.getstatusoutput": 0,
    "anyio.open_process": 0,
    "anyio.run_process": 0,
    "asyncio.create_subprocess_exec": 0,
    "asyncio.create_subprocess_shell": 0,
    "os.system": 0,
    "os.popen": 0,
    "os.startfile": 0,
    "os.posix_spawn": 0,
    "os.posix_spawnp": 0,
    **{f"os.exec{variant}": 0 for variant in _VARIANTS},
    # os.spawn*(mode, file, ...)
    **{f"os.spawn{variant}": 1 for variant in _VARIANTS},
}
_PROGRAM_KEYWORDS = frozenset({"args", "program", "command", "cmd", "file", "path"})

# Never to be used for locating programs: both search the current directory
# on Windows (and neither honours the .exe/.com-only rule).
_BANNED_LOOKUPS = frozenset({"shutil.which", "distutils.spawn.find_executable"})

_ABSOLUTE = re.compile(r"/|[A-Za-z]:[\\/]|[\\/]{2}[^\\/]")

_HINT = "resolve it via claude_agent_sdk._internal.executable (find_executable / run)"


def _imports(tree: ast.AST) -> dict[str, str]:
    """Local name -> dotted target for the file's absolute imports."""
    names: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    names[alias.asname] = alias.name
                else:
                    top = alias.name.split(".")[0]
                    names[top] = top
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            for alias in node.names:
                names[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return names


def _string_constants(tree: ast.Module) -> dict[str, str]:
    """Module-level ``NAME = "literal"`` (or annotated) assignments."""
    constants: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            for target in targets:
                if isinstance(target, ast.Name):
                    constants[target.id] = value.value
    return constants


def _dotted(node: ast.AST, imports: dict[str, str]) -> str | None:
    if isinstance(node, ast.Name):
        return imports.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value, imports)
        return f"{base}.{node.attr}" if base is not None else None
    return None


def _program_literal(arg: ast.expr, constants: dict[str, str]) -> str | None:
    """The program a spawn call names, when it is statically knowable."""
    if isinstance(arg, (ast.List, ast.Tuple)):
        if not arg.elts or isinstance(arg.elts[0], ast.Starred):
            return None
        arg = arg.elts[0]
    if isinstance(arg, ast.Name):
        return constants.get(arg.id)
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return arg.value
    return None


def violations(source: str, filename: str = "<snippet>") -> list[str]:
    """Every breach of the invariant in one module's source."""
    tree = ast.parse(source, filename)
    imports = _imports(tree)
    constants = _string_constants(tree)
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                if f"{node.module}.{alias.name}" in _BANNED_LOOKUPS:
                    found.append(
                        f"{filename}:{node.lineno}: imports {node.module}.{alias.name}"
                        f" -- {_HINT}"
                    )
        elif (
            isinstance(node, ast.Attribute)
            and _dotted(node, imports) in _BANNED_LOOKUPS
        ):
            found.append(
                f"{filename}:{node.lineno}: uses {_dotted(node, imports)} -- {_HINT}"
            )
        elif isinstance(node, ast.Call):
            api = _dotted(node.func, imports)
            if api is None or api not in _SPAWN_APIS:
                continue
            index = _SPAWN_APIS[api]
            if len(node.args) > index:
                first: ast.expr | None = node.args[index]
            else:
                first = next(
                    (kw.value for kw in node.keywords if kw.arg in _PROGRAM_KEYWORDS),
                    None,
                )
            if first is None:
                continue
            program = _program_literal(first, constants)
            if program is not None and _ABSOLUTE.match(program) is None:
                found.append(
                    f"{filename}:{node.lineno}: {api}() launches {program!r} by"
                    f" bare/relative name -- {_HINT}"
                )
    return found


def test_the_sdk_source_upholds_the_invariant() -> None:
    modules = sorted(SRC.rglob("*.py"))
    assert len(modules) > 10, f"did not find the package under {SRC}"
    problems = [
        problem
        for module in modules
        for problem in violations(
            module.read_text(encoding="utf-8"), str(module.relative_to(SRC.parent))
        )
    ]
    assert not problems, "Safe executable resolution (G1/G5) violated:\n" + "\n".join(
        problems
    )


@pytest.mark.parametrize(
    "snippet",
    [
        'import subprocess\nsubprocess.run(["git", "status"])',
        'import subprocess as sp\nsp.Popen(("claude", "-v"))',
        'from subprocess import check_output\ncheck_output(["security", "find-generic-password"])',
        'import subprocess\nsubprocess.run(args=["git", "worktree", "list"])',
        'import subprocess\nGIT = "git"\nsubprocess.run([GIT, "status"], check=False)',
        'import subprocess\nsubprocess.run(["./claude"])',
        'import subprocess\nsubprocess.run(["bin\\\\tool.exe"])',
        'import subprocess\nsubprocess.check_call("make")',
        'import anyio\nasync def f():\n    await anyio.open_process(["claude", "-v"])',
        'from anyio import run_process\nasync def f():\n    await run_process(["rg", "x"])',
        'import asyncio\nasync def f():\n    await asyncio.create_subprocess_exec("rg", "--json")',
        'import os\nos.execvp("git", ["git", "status"])',
        'import os\nos.spawnlp(os.P_WAIT, "git", "git")',
        'import os\nos.system("git status")',
        'import os\nos.popen("tar tzf x.tgz")',
        'import shutil\nshutil.which("claude")',
        'import shutil as sh\ncli = sh.which("claude")',
        "from shutil import which",
        "from distutils.spawn import find_executable",
    ],
)
def test_scanner_flags_bare_program_names_and_ambient_lookups(snippet: str) -> None:
    assert violations(snippet), snippet


@pytest.mark.parametrize(
    "snippet",
    [
        'import subprocess\nsubprocess.run(["/usr/bin/security", "find-generic-password"])',
        'import subprocess\n_BIN = "/usr/bin/security"\nsubprocess.run([_BIN, "-h"])',
        'import subprocess\nsubprocess.run(["C:\\\\Windows\\\\System32\\\\where.exe", "git"])',
        'import subprocess\nsubprocess.run([r"\\\\server\\share\\tool.exe"])',
        'import subprocess, sys\nsubprocess.run([sys.executable, "-c", "pass"])',
        "import anyio\nasync def f(cmd):\n    await anyio.open_process(cmd)",
        "import subprocess\ndef f(cli_path):\n    return subprocess.run([cli_path, '-v'])",
        'from claude_agent_sdk._internal import executable\nexecutable.run(["git", "status"])',
        'from claude_agent_sdk._internal.executable import find_executable\nfind_executable("claude")',
        "import shutil\nshutil.copyfile('a', 'b')",
    ],
)
def test_scanner_accepts_absolute_programs_and_the_blessed_helpers(
    snippet: str,
) -> None:
    assert not violations(snippet), violations(snippet)
