"""Per-spawn shell-command permission evaluator.

Syntactic classifiers (regex or substring matching over the raw shell text)
produce both false positives and false negatives on compositional shell:
pipes, subshells, ``&&``/``||`` sequences, command substitution, and ``cd``
changing the working directory mid-command all defeat text matching. A robust
gate needs to decompose the command into the individual processes it would
actually spawn and reason about each one.

This module does that. It uses :mod:`bashlex` to parse a shell command into an
abstract syntax tree, walks the tree into a list of :class:`Spawn` objects (one
per ``execve``-level process invocation), tracks working-directory changes
across the command with a ``cwd`` stack so path-relative checks are correct, and
evaluates each spawn against a caller-supplied per-binary safety function. Any
shell construct it cannot prove safe to decompose results in a **fail-safe
DENY** with a named reason, never a silent allow.

The engine ships no policy of its own: :data:`DEFAULT_POLICY` is empty, so an
unconfigured evaluator denies every spawn. Callers register per-binary safety
functions describing which invocations are safe to auto-approve.

Integration with the SDK is through :func:`create_bash_permission_evaluator`,
which adapts the engine into a :data:`~claude_agent_sdk.CanUseTool` callback
that gates the ``Bash`` tool. It is opt-in: unless the
``CLAUDE_AGENT_SDK_SHELL_PERMISSIONS`` environment variable is set to a truthy
value the callback defers to the fallback behavior, so wiring it in changes
nothing until it is explicitly enabled.

Supported shell constructs (decomposed and evaluated):

- Simple commands
- Pipelines (``|``)
- Sequences (``;``, ``&``, newline)
- Logical operators (``&&``, ``||``)
- Redirects (``>``, ``<``, ``>>``)
- Command substitution (``$(...)``, bounded by :data:`MAX_SUBSTITUTION_DEPTH`)
- Group commands (``{ ... }``, ``( ... )``)

Unsupported constructs (fail-safe DENY with a named reason):

- Heredocs / here-strings (``<<``, ``<<-``, ``<<<``)
- Process substitution (``<(...)``, ``>(...)``)
- Backticks (```...```) -- deprecated, rewrite as ``$(...)``
- Arithmetic expansion (``$((...))``)
- Control flow (``if``, ``for``, ``while``, ``case``, ``select``, ``until``,
  function definitions)

Rejected built-ins (dynamic execution is not decomposable, fail-safe DENY):

- ``eval``, ``source``, ``.``, ``exec``

State-tracking built-ins (do not spawn a process, mutate the cwd stack):

- ``cd``, ``export``, ``unset``, ``alias``, ``unalias``, ``set``, ``shopt``

No-op safe built-ins (allow, no state change):

- ``echo``, ``printf``, ``true``, ``false``, ``pwd``, ``test``, ``[``, ``:``

``bashlex`` is an optional dependency. Install it with the extra::

    pip install "claude-agent-sdk[shell-permissions]"
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .types import CanUseTool, PermissionResult, ToolPermissionContext

# ── Design constants ────────────────────────────────────────────────────────

MAX_SUBSTITUTION_DEPTH: int = 5
"""Recursion-depth limit for command substitution ``$(...)``.

Deeply nested substitution is pathological in real shell use. Bounding it keeps
the decomposer terminating on adversarial input.
"""


# Built-in classification.
STATE_TRACKING_BUILTINS: frozenset[str] = frozenset(
    {
        "cd",
        "export",
        "unset",
        "alias",
        "unalias",
        "set",
        "shopt",
    }
)

NO_OP_SAFE_BUILTINS: frozenset[str] = frozenset(
    {
        "echo",
        "printf",
        "true",
        "false",
        "pwd",
        "test",
        "[",
        ":",
    }
)

REJECTED_BUILTINS: frozenset[str] = frozenset(
    {
        "eval",
        "source",
        ".",
        "exec",
    }
)

ALL_BUILTINS: frozenset[str] = (
    STATE_TRACKING_BUILTINS | NO_OP_SAFE_BUILTINS | REJECTED_BUILTINS
)


# ── Public types ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Spawn:
    """A single process invocation decomposed from a shell command.

    ``argv[0]`` is the binary name as written in the shell command (not
    resolved through ``$PATH``). ``cwd`` is the effective working directory for
    this spawn, which may differ from the process working directory if the
    command included a ``cd`` before this spawn.
    """

    binary: str
    argv: tuple[str, ...]
    cwd: str


@dataclass
class DecomposeResult:
    """Return type for :func:`decompose`.

    Exactly one of ``spawns`` and ``reject_reason`` is set:

    - Success: ``spawns=[...], reject_reason=None``
    - Fail-safe deny: ``spawns=None, reject_reason="<construct>"``
    """

    spawns: list[Spawn] | None
    reject_reason: str | None


@dataclass
class EvaluateResult:
    """Return type for :func:`evaluate`.

    ``allowed`` is the final decision. ``reason`` explains ``False`` decisions
    (which policy rejected which spawn, or which construct triggered fail-safe
    deny). ``spawns`` echoes the decomposed spawns for audit / logging.
    """

    allowed: bool
    reason: str
    spawns: list[Spawn] = field(default_factory=list)


PolicyFn = Callable[[list[str], str], bool]
"""Signature of a per-binary safety function.

Called as ``fn(argv, cwd)`` where ``argv[0]`` is the binary name. Returns
``True`` if the invocation is safe to auto-approve, ``False`` to reject. A
binary with no registered function is treated as an implicit reject.
"""


# ── Empty default policy ─────────────────────────────────────────────────────

DEFAULT_POLICY: dict[str, PolicyFn] = {}
"""Default policy registry -- empty.

The engine ships with no policy. An empty policy denies every spawn (fail-safe).
Register per-binary safety functions before invoking :func:`evaluate`, or pass
them explicitly.
"""


# ── Decomposition ───────────────────────────────────────────────────────────


def _import_bashlex() -> Any:
    """Import bashlex lazily, with a helpful error if the extra is missing."""
    try:
        import bashlex

        return bashlex
    except ImportError as exc:  # pragma: no cover - exercised via monkeypatch
        raise ImportError(
            "The shell-command permission evaluator requires the 'bashlex' "
            "package. Install it with: pip install "
            '"claude-agent-sdk[shell-permissions]"'
        ) from exc


def decompose(command: str, initial_cwd: str | None = None) -> DecomposeResult:
    """Decompose a shell command into a list of :class:`Spawn` objects.

    Fail-safe DENY (``spawns=None``) on:

    - :class:`bashlex.errors.ParsingError` (malformed shell)
    - Heredoc / here-string (``<<``, ``<<-``, ``<<<``)
    - Process substitution (``<(...)``, ``>(...)``)
    - Backticks (```...```) in the raw source
    - Arithmetic expansion (``$((...))``) in the raw source
    - Control-flow constructs (``if``, ``for``, ``while``, ``case``, functions)
    - Rejected builtins (``eval``, ``source``, ``.``, ``exec``)
    - Command substitution beyond :data:`MAX_SUBSTITUTION_DEPTH`
    - Variable/parameter expansion in a ``cd`` path (``cd $HOME``)
    - Unrecognized ``bashlex`` node kinds

    Args:
        command: The shell command string to decompose.
        initial_cwd: Starting working directory. Defaults to
            :func:`os.getcwd`. State-tracking builtins push new entries onto a
            per-command cwd stack; each spawn records the top of the stack at
            the point it is emitted.

    Returns:
        A :class:`DecomposeResult` naming either the successful spawn list or a
        specific reject reason. The reason is intended to be surfaced to the
        caller so an over-strict deny is diagnosable.
    """
    bashlex = _import_bashlex()

    if initial_cwd is None:
        initial_cwd = str(Path.cwd())

    if not command.strip():
        return DecomposeResult([], None)

    # Pre-check the raw string for constructs bashlex may accept but that we
    # explicitly refuse. Backticks are deprecated (recommend ``$(...)``);
    # arithmetic expansion ``$((...))`` is not decomposable to a spawn.
    if _contains_unquoted_backtick(command):
        return DecomposeResult(
            None,
            "backtick command substitution deprecated -- rewrite as $(...)",
        )
    if _contains_arithmetic_expansion(command):
        return DecomposeResult(
            None,
            "arithmetic expansion $(( ... )) is not per-spawn decomposable",
        )
    if _contains_process_substitution(command):
        return DecomposeResult(
            None,
            "process substitution <(...) / >(...) is not per-spawn decomposable",
        )
    if _contains_heredoc(command):
        return DecomposeResult(
            None,
            "heredoc (<<, <<-, <<EOF) is not per-spawn decomposable -- "
            "use `sh -c '...'` if a single-spawn wrapper is intended",
        )

    try:
        trees = bashlex.parse(command)
    except bashlex.errors.ParsingError as e:
        return DecomposeResult(None, f"bashlex parse error: {e}")
    except Exception as e:  # bashlex sometimes throws generic Exception
        return DecomposeResult(None, f"bashlex error: {type(e).__name__}: {e}")

    spawns: list[Spawn] = []
    cwd_stack: list[str] = [initial_cwd]

    for tree in trees:
        reject = _walk(tree, cwd_stack, spawns, sub_depth=0)
        if reject is not None:
            return DecomposeResult(None, reject)

    return DecomposeResult(spawns, None)


# ── AST walk helpers ─────────────────────────────────────────────────────────


def _walk(
    node: Any,
    cwd_stack: list[str],
    spawns: list[Spawn],
    sub_depth: int,
) -> str | None:
    """Recursively walk a bashlex parse tree.

    Returns ``None`` on success, or a string naming the specific unsupported
    construct for fail-safe DENY.
    """
    kind = node.kind

    if kind == "list":
        # Top-level sequence: parts alternate command/pipeline with operators.
        # ``;`` / ``&`` / newline separate commands; ``&&`` / ``||`` are
        # conditional. Bash semantics: cwd changes from ``cd`` persist across
        # ``;`` / ``&&`` / ``||`` within the same shell context (same list).
        for part in node.parts:
            if part.kind in ("operator", "reservedword"):
                continue
            reject = _walk(part, cwd_stack, spawns, sub_depth)
            if reject is not None:
                return reject
        return None

    if kind == "pipeline":
        # Pipe segments run in subshells -- a ``cd`` in one segment does NOT
        # affect the next. Snapshot the cwd_stack for each pipe segment.
        for part in node.parts:
            if part.kind == "pipe":
                continue
            segment_stack = list(cwd_stack)
            reject = _walk(part, segment_stack, spawns, sub_depth)
            if reject is not None:
                return reject
        return None

    if kind == "command":
        return _walk_command(node, cwd_stack, spawns, sub_depth)

    if kind == "compound":
        # ``{ ... }`` or ``( ... )``. Bashlex wraps both in a CompoundNode with
        # ``.list`` (not ``.parts``). Inside the list, control-flow constructs
        # appear as ``if`` / ``for`` / ``while`` / ``case`` / function nodes --
        # those are unsupported by design. Plain groups have reservedwords plus
        # an inner ListNode we can walk.
        #
        # Cwd semantics: ``( ... )`` is a subshell (cwd changes do not escape)
        # and ``{ ... }`` shares the parent context. We snapshot conservatively
        # for both -- for ``{}`` a rare consequence is over-deny of a following
        # command that relied on an inner-group cd, a shape rare enough that we
        # accept the false-negative (over-deny) direction.
        segment_stack = list(cwd_stack)
        children = getattr(node, "list", None) or []
        for child in children:
            child_kind = child.kind
            if child_kind == "reservedword":
                continue
            if child_kind in (
                "if",
                "for",
                "while",
                "case",
                "function",
                "select",
                "until",
            ):
                return f"unsupported shell construct: {child_kind}"
            reject = _walk(child, segment_stack, spawns, sub_depth)
            if reject is not None:
                return reject
        return None

    # Everything else is control flow, a function definition, or a shape we did
    # not design for. Deny explicitly rather than silently walking through.
    return f"unsupported shell construct: {kind}"


def _walk_command(
    node: Any,
    cwd_stack: list[str],
    spawns: list[Spawn],
    sub_depth: int,
) -> str | None:
    """Walk a bashlex ``CommandNode``.

    Extracts binary + argv from ``WordNode`` children, handles state-tracking
    builtins by mutating ``cwd_stack`` instead of emitting a spawn, and recurses
    into nested command substitutions with a bounded depth budget.
    """
    words: list[str] = []
    for part in node.parts:
        if part.kind == "word":
            reject = _walk_word(part, cwd_stack, sub_depth)
            if reject is not None:
                return reject
            words.append(part.word)
        elif part.kind == "assignment":
            # ``VAR=value`` inline assignments before a command are prefix-only
            # for that command in bash. We do not model the environment at the
            # per-spawn level, but these are structurally safe to skip.
            continue
        elif part.kind == "redirect":
            # Redirect targets are handled by the raw-source pre-checks (which
            # reject heredoc / process substitution). Simple ``>`` / ``<`` /
            # ``>>`` redirects do not spawn anything and do not mutate cwd, so
            # we can safely walk past them here.
            continue
        elif part.kind == "reservedword":
            continue
        else:
            return f"unsupported command child: {part.kind}"

    if not words:
        # Assignment-only or redirect-only command (``VAR=x``, ``> /tmp/x``).
        # No spawn.
        return None

    binary = words[0]
    argv = tuple(words)

    # Rejected builtins: dynamic execution is not decomposable.
    if binary in REJECTED_BUILTINS:
        return f"rejected builtin '{binary}' -- dynamic execution not decomposable"

    # State-tracking builtins: mutate cwd_stack, do not emit a spawn.
    if binary in STATE_TRACKING_BUILTINS:
        return _apply_state_tracking(binary, argv, cwd_stack)

    # No-op safe builtins are emitted as spawns too, so a policy can still audit
    # or gate them if it chooses (an empty policy entry simply passes through).

    current_cwd = cwd_stack[-1]
    spawns.append(Spawn(binary=binary, argv=argv, cwd=current_cwd))
    return None


def _walk_word(node: Any, cwd_stack: list[str], sub_depth: int) -> str | None:
    """Walk a ``WordNode``, recursing into any embedded command substitution."""
    parts = getattr(node, "parts", None) or []
    for part in parts:
        if part.kind == "commandsubstitution":
            if sub_depth >= MAX_SUBSTITUTION_DEPTH:
                return (
                    f"command substitution nested deeper than "
                    f"MAX_SUBSTITUTION_DEPTH={MAX_SUBSTITUTION_DEPTH}"
                )
            # Recurse into the substituted command with a fresh cwd_stack
            # (subshell isolation) and an incremented depth budget. A
            # substitution that would itself spawn a process needing policy
            # evaluation is refused: keeping the outer walk pure is simpler and
            # strictly safer (fail closed) than merging inner spawns here.
            sub_stack = list(cwd_stack)
            sub_spawns: list[Spawn] = []
            reject = _walk(part.command, sub_stack, sub_spawns, sub_depth + 1)
            if reject is not None:
                return reject
            if sub_spawns:
                return (
                    "command substitution contains a process spawn -- not "
                    "supported (the substituted command would itself need "
                    "policy evaluation)"
                )
        elif part.kind == "parameter":
            # ``$VAR`` / ``${VAR}``. Structurally allowed in words other than cd
            # targets -- cd handling below rejects them explicitly.
            continue
        elif part.kind == "tilde":
            # ``~`` or ``~user`` -- treated as a literal string here.
            continue
    return None


def _apply_state_tracking(
    binary: str, argv: tuple[str, ...], cwd_stack: list[str]
) -> str | None:
    """Apply a state-tracking builtin to ``cwd_stack``.

    Only ``cd`` currently mutates cwd; the other state-tracking builtins
    (``export``, ``unset``, ``alias``, ``unalias``, ``set``, ``shopt``) affect
    the environment or shell options, which the per-spawn evaluator does not
    model. They are accepted (no spawn emitted) but do not change cwd.
    """
    if binary != "cd":
        return None  # accepted, no cwd change

    # ``cd`` with no argument goes to $HOME, which relies on runtime state we do
    # not model. Refuse it (static paths only).
    if len(argv) < 2:
        return "cd with no argument (requires static path, no $HOME)"

    target = argv[1]

    # ``cd -`` (previous dir), ``cd ~`` (home), ``cd $VAR`` all rely on runtime
    # state we do not model. Deny with a diagnostic pointing at the workaround.
    if target == "-":
        return "cd - (previous dir) requires runtime state -- use absolute path"
    if target.startswith("~"):
        return "cd ~ requires runtime state -- use absolute path"
    if "$" in target:
        return "cd with variable expansion requires runtime state -- use absolute path"

    # Resolve relative to the current top of stack. Static paths only. We use
    # os.path.normpath for *lexical* normalization (collapsing ``.`` / ``..``
    # without touching the filesystem or resolving symlinks); Path.resolve
    # would hit the filesystem, which a pure evaluator must not do.
    current = cwd_stack[-1]
    if Path(target).is_absolute():
        resolved = os.path.normpath(target)
    else:
        resolved = os.path.normpath(str(Path(current) / target))

    cwd_stack.append(resolved)
    return None


# ── Raw-source pre-checks ────────────────────────────────────────────────────


def _contains_unquoted_backtick(command: str) -> bool:
    """Detect ````` outside single-quoted regions.

    POSIX single-quote is atomic: a backslash is literal and only a closing
    ``'`` ends it. Inside double-quotes, a backtick still triggers command
    substitution, so double-quoted regions count as unquoted for this check.
    """
    in_squote = False
    i = 0
    n = len(command)
    while i < n:
        ch = command[i]
        if in_squote:
            if ch == "'":
                in_squote = False
        else:
            if ch == "'":
                in_squote = True
            elif ch == "`":
                return True
        i += 1
    return False


def _contains_arithmetic_expansion(command: str) -> bool:
    """Detect ``$(( ... ))`` outside single-quoted regions."""
    in_squote = False
    i = 0
    n = len(command)
    while i < n - 2:
        ch = command[i]
        if in_squote:
            if ch == "'":
                in_squote = False
            i += 1
            continue
        if ch == "'":
            in_squote = True
            i += 1
            continue
        if ch == "$" and command[i + 1 : i + 3] == "((":
            return True
        i += 1
    return False


def _contains_process_substitution(command: str) -> bool:
    """Detect ``<(...)`` or ``>(...)`` outside quoted regions.

    Approximate: any unquoted ``<(`` or ``>(``. Redirects like ``> file`` and
    ``< file`` have whitespace before the paren, so they do not match.
    """
    in_squote = False
    in_dquote = False
    i = 0
    n = len(command)
    while i < n - 1:
        ch = command[i]
        if in_squote:
            if ch == "'":
                in_squote = False
            i += 1
            continue
        if in_dquote:
            if ch == '"':
                in_dquote = False
            i += 1
            continue
        if ch == "'":
            in_squote = True
            i += 1
            continue
        if ch == '"':
            in_dquote = True
            i += 1
            continue
        if ch in ("<", ">") and command[i + 1] == "(":
            return True
        i += 1
    return False


def _contains_heredoc(command: str) -> bool:
    """Detect heredoc / here-string markers (``<<``, ``<<-``, ``<<<``).

    ``<<<`` is a here-string; ``<<`` / ``<<-`` are true heredocs. All three are
    conservatively denied under the same diagnostic.
    """
    in_squote = False
    in_dquote = False
    i = 0
    n = len(command)
    while i < n - 1:
        ch = command[i]
        if in_squote:
            if ch == "'":
                in_squote = False
            i += 1
            continue
        if in_dquote:
            if ch == '"':
                in_dquote = False
            i += 1
            continue
        if ch == "'":
            in_squote = True
            i += 1
            continue
        if ch == '"':
            in_dquote = True
            i += 1
            continue
        if ch == "<" and command[i + 1] == "<":
            return True
        i += 1
    return False


# ── Evaluator (public API) ───────────────────────────────────────────────────


def evaluate(
    command: str,
    initial_cwd: str | None = None,
    policy: dict[str, PolicyFn] | None = None,
) -> EvaluateResult:
    """Evaluate a shell command under a per-binary policy.

    Steps:

    1. Decompose the command with :func:`decompose`. On decomposition failure,
       return ``allowed=False`` with the reject reason.
    2. For each :class:`Spawn`, look up ``policy[binary]``. A missing entry means
       the policy has no opinion, treated as deny by default (fail-safe).
    3. Any spawn rejected by its policy function fails the whole command
       (all-must-pass semantics).

    Args:
        command: Shell command string to evaluate.
        initial_cwd: Starting working directory (defaults to ``os.getcwd()``).
        policy: Per-binary safety-function registry. If ``None``, uses
            :data:`DEFAULT_POLICY` (empty -- every spawn will reject).

    Returns:
        An :class:`EvaluateResult` naming the final decision and, on rejection,
        the specific spawn and reason.
    """
    if policy is None:
        policy = DEFAULT_POLICY

    result = decompose(command, initial_cwd)
    if result.reject_reason is not None:
        return EvaluateResult(allowed=False, reason=result.reject_reason, spawns=[])

    spawns = result.spawns or []
    if not spawns:
        # Assignment-only or empty command -- nothing to authorize.
        return EvaluateResult(allowed=True, reason="no spawns", spawns=[])

    for spawn in spawns:
        fn = policy.get(spawn.binary)
        if fn is None:
            return EvaluateResult(
                allowed=False,
                reason=(
                    f"no policy for binary '{spawn.binary}' "
                    f"(argv={list(spawn.argv)}, cwd={spawn.cwd})"
                ),
                spawns=spawns,
            )
        if not fn(list(spawn.argv), spawn.cwd):
            return EvaluateResult(
                allowed=False,
                reason=(
                    f"policy rejected '{spawn.binary}' "
                    f"(argv={list(spawn.argv)}, cwd={spawn.cwd})"
                ),
                spawns=spawns,
            )

    return EvaluateResult(allowed=True, reason="all spawns approved", spawns=spawns)


# ── Plugin loading ───────────────────────────────────────────────────────────


def load_policy_from_module(module_ref: str) -> dict[str, PolicyFn]:
    """Load a policy registry from a module reference.

    ``module_ref`` follows the standard entry-point syntax
    ``package.module:attribute``. The attribute may be either the policy dict
    itself or a zero-arg callable returning the dict. This lets an application
    keep its private allow/deny contents in its own module while reusing this
    generic engine.
    """
    if ":" not in module_ref:
        raise ValueError(
            f"module ref must be 'package.module:attribute', got '{module_ref}'"
        )
    mod_name, _, attr = module_ref.partition(":")
    import importlib

    mod = importlib.import_module(mod_name)
    obj = getattr(mod, attr)
    if callable(obj):
        obj = obj()
    if not isinstance(obj, dict):
        raise TypeError(
            f"policy loader '{module_ref}' returned {type(obj).__name__}, "
            "expected dict[str, PolicyFn]"
        )
    return obj


# ── SDK integration ──────────────────────────────────────────────────────────


def _env_flag_enabled(value: str | None) -> bool:
    """Return True if an environment-variable value is truthy."""
    return value is not None and value.strip().lower() in {"1", "true", "yes", "on"}


def create_bash_permission_evaluator(
    policy: dict[str, PolicyFn] | None = None,
    *,
    tool_name: str = "Bash",
    command_key: str = "command",
    env_var: str | None = "CLAUDE_AGENT_SDK_SHELL_PERMISSIONS",
    initial_cwd: str | None = None,
    fallback: CanUseTool | None = None,
) -> CanUseTool:
    """Build a ``can_use_tool`` callback that gates the shell tool per spawn.

    The returned callback decomposes the ``Bash`` tool's ``command`` into the
    individual processes it would spawn (see :func:`decompose`) and evaluates
    each against ``policy`` (see :func:`evaluate`). It returns
    :class:`~claude_agent_sdk.PermissionResultAllow` only when every spawn is
    approved, and :class:`~claude_agent_sdk.PermissionResultDeny` (carrying the
    reason) otherwise, including a fail-safe deny for any shell construct it
    cannot prove safe.

    **Opt-in.** When ``env_var`` is set (the default,
    ``CLAUDE_AGENT_SDK_SHELL_PERMISSIONS``) and not truthy in the environment,
    the callback does not evaluate anything and defers to ``fallback``. This
    preserves existing behavior exactly until the feature is explicitly enabled.
    Pass ``env_var=None`` to skip the environment gate and always evaluate.

    Tools other than ``tool_name`` are never evaluated here; they defer to
    ``fallback``.

    Args:
        policy: Per-binary safety-function registry. Defaults to
            :data:`DEFAULT_POLICY` (empty), which denies every spawn.
        tool_name: The tool this evaluator gates. Defaults to ``"Bash"``.
        command_key: The key in the tool input holding the shell command string.
            Defaults to ``"command"``.
        env_var: Environment variable that enables evaluation. Defaults to
            ``"CLAUDE_AGENT_SDK_SHELL_PERMISSIONS"``. Set to ``None`` to always
            evaluate (no environment gate).
        initial_cwd: Starting working directory for cwd tracking. Defaults to
            the process working directory at evaluation time.
        fallback: A ``can_use_tool`` callback to defer to when this evaluator
            does not apply (feature disabled, a different tool, or a missing
            command). If ``None``, deferral allows the tool.

    Returns:
        A ``can_use_tool`` callback (see
        :data:`~claude_agent_sdk.CanUseTool`).
    """
    from .types import PermissionResultAllow, PermissionResultDeny

    resolved_policy = DEFAULT_POLICY if policy is None else policy

    async def _defer(
        name: str,
        input_data: dict[str, Any],
        context: ToolPermissionContext,
    ) -> PermissionResult:
        if fallback is not None:
            return await fallback(name, input_data, context)
        return PermissionResultAllow()

    async def can_use_tool(
        name: str,
        input_data: dict[str, Any],
        context: ToolPermissionContext,
    ) -> PermissionResult:
        # Opt-in gate: disabled unless the environment flag is truthy.
        if env_var is not None and not _env_flag_enabled(os.environ.get(env_var)):
            return await _defer(name, input_data, context)

        # Only the configured shell tool is evaluated here.
        if name != tool_name:
            return await _defer(name, input_data, context)

        command = input_data.get(command_key)
        if not isinstance(command, str):
            return await _defer(name, input_data, context)

        result = evaluate(command, initial_cwd=initial_cwd, policy=resolved_policy)
        if result.allowed:
            return PermissionResultAllow()
        return PermissionResultDeny(message=result.reason)

    return can_use_tool
