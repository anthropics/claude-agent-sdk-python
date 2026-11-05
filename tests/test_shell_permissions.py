"""Unit tests for the per-spawn shell-command permission evaluator.

Two layers are covered:

- The pure engine (``decompose`` / ``evaluate``): decomposition of
  compositional shell, fail-safe deny on unsupported constructs, cwd tracking
  across state-tracking builtins, and per-binary safety functions.
- The SDK integration (``create_bash_permission_evaluator``): the opt-in env
  gate, allow/deny mapping onto the ``can_use_tool`` permission results, and
  deferral for non-shell tools.

No real subprocess is executed -- everything is string-in, decision-out.

The example policies here are synthetic and illustrative; the engine ships no
policy of its own.
"""

from __future__ import annotations

import pytest

from claude_agent_sdk import (
    PermissionResultAllow,
    PermissionResultDeny,
    ToolPermissionContext,
    shell_permissions,
)
from claude_agent_sdk.shell_permissions import (
    MAX_SUBSTITUTION_DEPTH,
    DecomposeResult,
    Spawn,
    create_bash_permission_evaluator,
    decompose,
    evaluate,
)

# ── Synthetic policy fixtures ────────────────────────────────────────────────
#
# The engine ships an empty DEFAULT_POLICY. These fixtures are synthetic and
# exist only to exercise the engine.


def _always_ok(argv: list[str], cwd: str) -> bool:
    return True


def _always_no(argv: list[str], cwd: str) -> bool:
    return False


def _grep_only_readonly(argv: list[str], cwd: str) -> bool:
    # Synthetic: allow grep only when no --exec-like flag is present.
    return not any(a.startswith("--exec") for a in argv[1:])


def _ctx() -> ToolPermissionContext:
    return ToolPermissionContext()


# ── Engine: decomposition of supported constructs ────────────────────────────


class TestDecompositionSuccess:
    """Supported shell constructs decompose into a Spawn list."""

    def test_empty_command(self):
        r = decompose("", initial_cwd="/tmp")
        assert r == DecomposeResult(spawns=[], reject_reason=None)

    def test_whitespace_only(self):
        r = decompose("   ", initial_cwd="/tmp")
        assert r == DecomposeResult(spawns=[], reject_reason=None)

    def test_simple_command(self):
        r = decompose("grep -r foo .", initial_cwd="/tmp")
        assert r.reject_reason is None
        assert r.spawns == [
            Spawn(binary="grep", argv=("grep", "-r", "foo", "."), cwd="/tmp")
        ]

    def test_pipeline(self):
        r = decompose("ls | grep foo", initial_cwd="/tmp")
        assert r.reject_reason is None
        binaries = [s.binary for s in r.spawns or []]
        assert binaries == ["ls", "grep"]

    def test_sequence_semicolon(self):
        r = decompose("ls; pwd", initial_cwd="/tmp")
        assert r.reject_reason is None
        assert [s.binary for s in r.spawns or []] == ["ls", "pwd"]

    def test_logical_and(self):
        r = decompose("cd /etc && ls", initial_cwd="/tmp")
        assert r.reject_reason is None
        # cd does not spawn (state-tracking), only ls does.
        assert len(r.spawns or []) == 1
        assert r.spawns[0].binary == "ls"
        # cd propagated across the && boundary.
        assert r.spawns[0].cwd == "/etc"

    def test_logical_or(self):
        r = decompose("false || echo fallback", initial_cwd="/tmp")
        assert r.reject_reason is None
        assert [s.binary for s in r.spawns or []] == ["false", "echo"]

    def test_grep_with_awk_pipe(self):
        """A pipe-to-conditional-awk that a syntactic classifier over-denies.

        ``grep ... | awk '$1 > N'`` decomposes to two safe read-only spawns.
        """
        cmd = "grep -n foo file.txt | awk '$1 > 700 && $1 < 2540'"
        r = decompose(cmd, initial_cwd="/tmp")
        assert r.reject_reason is None
        binaries = [s.binary for s in r.spawns or []]
        assert binaries == ["grep", "awk"]

    def test_cd_chain_then_grep(self):
        """``cd /path; ...; grep ...`` -- cwd change propagates across ``;``."""
        cmd = 'cd /data/x; echo "hi"; grep -n foo bar.rs'
        r = decompose(cmd, initial_cwd="/tmp")
        assert r.reject_reason is None
        binaries = [s.binary for s in r.spawns or []]
        assert binaries == ["echo", "grep"]
        # grep sees /data/x cwd after cd propagated across ;
        assert r.spawns[-1].cwd == "/data/x"

    def test_redirect_out(self):
        r = decompose("ls -la > /tmp/list.txt", initial_cwd="/tmp")
        assert r.reject_reason is None
        assert len(r.spawns or []) == 1
        assert r.spawns[0].binary == "ls"


class TestDecompositionFailSafeDeny:
    """Unsupported constructs must return ``spawns=None, reject_reason=<str>``.

    The specific unsupported construct is named in the reason so an over-strict
    deny is diagnosable.
    """

    def test_heredoc_denied(self):
        r = decompose("cat << EOF\nfoo\nEOF", initial_cwd="/tmp")
        assert r.spawns is None
        assert "heredoc" in r.reject_reason.lower()

    def test_here_string_denied(self):
        r = decompose("grep foo <<< 'input'", initial_cwd="/tmp")
        assert r.spawns is None
        assert "heredoc" in r.reject_reason.lower()

    def test_process_substitution_denied(self):
        r = decompose("diff <(ls a) <(ls b)", initial_cwd="/tmp")
        assert r.spawns is None
        assert "process substitution" in r.reject_reason.lower()

    def test_backticks_denied(self):
        r = decompose("echo `date`", initial_cwd="/tmp")
        assert r.spawns is None
        assert "backtick" in r.reject_reason.lower()

    def test_arithmetic_expansion_denied(self):
        r = decompose("echo $((1 + 2))", initial_cwd="/tmp")
        assert r.spawns is None
        assert "arithmetic" in r.reject_reason.lower()

    def test_backticks_inside_squote_not_flagged(self):
        # POSIX single-quote is atomic -- a backtick inside is a literal, not
        # command substitution. Must NOT trigger the raw-string check.
        r = decompose("echo 'hi `not-a-sub` bye'", initial_cwd="/tmp")
        assert r.reject_reason is None
        assert (r.spawns or [])[0].binary == "echo"

    def test_eval_rejected(self):
        r = decompose("eval 'echo hi'", initial_cwd="/tmp")
        assert r.spawns is None
        assert "eval" in r.reject_reason.lower()

    def test_source_rejected(self):
        r = decompose("source /tmp/script.sh", initial_cwd="/tmp")
        assert r.spawns is None
        assert "source" in r.reject_reason.lower()

    def test_dot_rejected(self):
        r = decompose(". /tmp/script.sh", initial_cwd="/tmp")
        assert r.spawns is None
        assert "dynamic execution" in r.reject_reason.lower()

    def test_exec_rejected(self):
        r = decompose("exec ls", initial_cwd="/tmp")
        assert r.spawns is None
        assert "exec" in r.reject_reason.lower()

    def test_control_flow_if_denied(self):
        r = decompose("if true; then echo hi; fi", initial_cwd="/tmp")
        assert r.spawns is None
        # Named as some unsupported kind; the specific label is bashlex-dependent.
        assert "unsupported" in r.reject_reason.lower()

    def test_malformed_command_returns_deny(self):
        # An unbalanced quote is a parse error -- fail-safe deny.
        r = decompose("echo 'unterminated", initial_cwd="/tmp")
        assert r.spawns is None
        # Message content varies by bashlex version; the shape is stable.
        assert r.reject_reason


class TestCommandSubstitution:
    """``$(...)`` is decomposable but bounded by MAX_SUBSTITUTION_DEPTH."""

    def test_sub_with_inner_spawn_denied(self):
        # ``echo $(pwd)`` -- the inner command produces a spawn, which is
        # refused (fail closed) with a diagnostic.
        r = decompose("echo $(pwd)", initial_cwd="/tmp")
        assert r.spawns is None
        assert "command substitution" in r.reject_reason.lower()

    def test_max_depth_constant_is_five(self):
        assert MAX_SUBSTITUTION_DEPTH == 5


# ── Engine: state-tracking builtins (cwd tracking) ───────────────────────────


class TestStateTrackingBuiltins:
    def test_bare_cd_denied(self):
        r = decompose("cd", initial_cwd="/tmp")
        assert r.spawns is None
        assert "no argument" in r.reject_reason.lower()

    def test_cd_with_var_denied(self):
        r = decompose("cd $HOME", initial_cwd="/tmp")
        assert r.spawns is None
        assert "variable" in r.reject_reason.lower()

    def test_cd_tilde_denied(self):
        r = decompose("cd ~", initial_cwd="/tmp")
        assert r.spawns is None
        assert "~" in r.reject_reason

    def test_cd_dash_denied(self):
        r = decompose("cd -", initial_cwd="/tmp")
        assert r.spawns is None
        assert "previous dir" in r.reject_reason.lower()

    def test_cd_absolute_updates_cwd(self):
        r = decompose("cd /etc; cat passwd", initial_cwd="/tmp")
        assert r.reject_reason is None
        assert r.spawns[-1].cwd == "/etc"
        # ``cd /etc; cat passwd`` -- the argv[1] is the raw ``passwd`` and the
        # spawn.cwd is ``/etc``, so a downstream policy can resolve to
        # /etc/passwd from those two facts.
        assert r.spawns[-1].argv == ("cat", "passwd")

    def test_cd_relative_updates_cwd(self):
        r = decompose("cd sub; cat f.txt", initial_cwd="/tmp")
        assert r.reject_reason is None
        assert r.spawns[-1].cwd == "/tmp/sub"

    def test_pipeline_cd_isolated(self):
        # cd in a subshell (pipe segment) does NOT propagate to the next.
        r = decompose("cd /etc | grep foo", initial_cwd="/tmp")
        assert r.reject_reason is None
        # grep runs in a subshell too; its cwd starts at the outer stack.
        assert (r.spawns or [])[0].cwd == "/tmp"

    def test_export_does_not_spawn_but_accepts(self):
        # export is state-tracking (env), no spawn. A command with only export
        # returns zero spawns and no reject reason.
        r = decompose("export FOO=bar", initial_cwd="/tmp")
        assert r.reject_reason is None
        assert r.spawns == []


# ── Engine: per-binary safety functions ──────────────────────────────────────


class TestEvaluatorPerBinary:
    def test_empty_policy_denies(self):
        # Missing entry = deny (fail-safe).
        r = evaluate("grep foo bar", initial_cwd="/tmp")
        assert r.allowed is False
        assert "no policy" in r.reason.lower()

    def test_permissive_policy_allows(self):
        pol = {"grep": _always_ok}
        r = evaluate("grep foo bar", initial_cwd="/tmp", policy=pol)
        assert r.allowed is True

    def test_reject_from_safety_fn(self):
        pol = {"grep": _always_no}
        r = evaluate("grep foo bar", initial_cwd="/tmp", policy=pol)
        assert r.allowed is False
        assert "policy rejected" in r.reason.lower()

    def test_all_must_pass_semantics(self):
        # grep passes, but awk has no policy -> the whole command rejects.
        pol = {"grep": _always_ok}
        r = evaluate("grep foo bar | awk '{print}'", initial_cwd="/tmp", policy=pol)
        assert r.allowed is False
        assert "awk" in r.reason

    def test_safety_fn_receives_argv_and_cwd(self):
        seen: list[tuple[list[str], str]] = []

        def spy(argv: list[str], cwd: str) -> bool:
            seen.append((argv, cwd))
            return True

        pol = {"cat": spy}
        r = evaluate("cd /etc; cat passwd", initial_cwd="/tmp", policy=pol)
        assert r.allowed is True
        assert seen == [(["cat", "passwd"], "/etc")]

    def test_synthetic_readonly_grep_policy(self):
        pol = {"grep": _grep_only_readonly}
        r_ok = evaluate("grep -r foo .", initial_cwd="/tmp", policy=pol)
        assert r_ok.allowed is True
        # Synthetic denied flag.
        r_no = evaluate("grep --exec-hack foo .", initial_cwd="/tmp", policy=pol)
        assert r_no.allowed is False

    def test_empty_command_allowed(self):
        r = evaluate("", initial_cwd="/tmp")
        assert r.allowed is True
        assert r.reason == "no spawns"

    def test_unsupported_construct_denies_via_evaluate(self):
        # Fail-safe deny surfaces through evaluate() too.
        pol = {"cat": _always_ok}
        r = evaluate("cat << EOF\nx\nEOF", initial_cwd="/tmp", policy=pol)
        assert r.allowed is False
        assert "heredoc" in r.reason.lower()


# ── Engine: plugin loading ───────────────────────────────────────────────────


class TestPolicyLoader:
    def test_bad_ref_format(self):
        with pytest.raises(ValueError, match="package.module:attribute"):
            shell_permissions.load_policy_from_module("not-a-ref")

    def test_load_module_dict(self, tmp_path, monkeypatch):
        # Create a tiny plugin module in a tmp path.
        pkg_dir = tmp_path / "plugin_pkg"
        pkg_dir.mkdir()
        (pkg_dir / "__init__.py").write_text("")
        (pkg_dir / "policy_mod.py").write_text(
            "def get_policy():\n"
            "    def _grep_ok(argv, cwd): return True\n"
            "    return {'grep': _grep_ok}\n"
        )
        monkeypatch.syspath_prepend(str(tmp_path))
        loaded = shell_permissions.load_policy_from_module(
            "plugin_pkg.policy_mod:get_policy"
        )
        assert "grep" in loaded
        assert loaded["grep"](["grep", "foo"], "/tmp") is True

    def test_wrong_return_type_raises(self, tmp_path, monkeypatch):
        pkg_dir = tmp_path / "plugin_bad"
        pkg_dir.mkdir()
        (pkg_dir / "__init__.py").write_text("")
        (pkg_dir / "policy_mod.py").write_text("get_policy = 42\n")
        monkeypatch.syspath_prepend(str(tmp_path))
        with pytest.raises(TypeError, match="expected dict"):
            shell_permissions.load_policy_from_module(
                "plugin_bad.policy_mod:get_policy"
            )


# ── SDK integration: create_bash_permission_evaluator ────────────────────────


class TestBashPermissionEvaluator:
    """The can_use_tool adapter: opt-in gate + allow/deny mapping."""

    @pytest.mark.anyio
    async def test_disabled_by_default_defers_to_allow(self, monkeypatch):
        # Env flag unset -> the evaluator does nothing and allows (no fallback).
        monkeypatch.delenv("CLAUDE_AGENT_SDK_SHELL_PERMISSIONS", raising=False)
        cb = create_bash_permission_evaluator(policy={"grep": _always_ok})
        # Even a command with no policy would be allowed while disabled.
        result = await cb("Bash", {"command": "rm -rf /"}, _ctx())
        assert isinstance(result, PermissionResultAllow)

    @pytest.mark.anyio
    async def test_disabled_defers_to_fallback(self, monkeypatch):
        monkeypatch.delenv("CLAUDE_AGENT_SDK_SHELL_PERMISSIONS", raising=False)
        calls: list[str] = []

        async def fallback(name, input_data, context):
            calls.append(name)
            return PermissionResultDeny(message="from fallback")

        cb = create_bash_permission_evaluator(
            policy={"grep": _always_ok}, fallback=fallback
        )
        result = await cb("Bash", {"command": "grep x y"}, _ctx())
        assert isinstance(result, PermissionResultDeny)
        assert result.message == "from fallback"
        assert calls == ["Bash"]

    @pytest.mark.anyio
    async def test_enabled_allows_when_policy_passes(self, monkeypatch):
        monkeypatch.setenv("CLAUDE_AGENT_SDK_SHELL_PERMISSIONS", "1")
        cb = create_bash_permission_evaluator(
            policy={"grep": _always_ok, "awk": _always_ok}, initial_cwd="/tmp"
        )
        result = await cb("Bash", {"command": "grep foo f | awk '{print}'"}, _ctx())
        assert isinstance(result, PermissionResultAllow)

    @pytest.mark.anyio
    async def test_enabled_denies_with_reason(self, monkeypatch):
        monkeypatch.setenv("CLAUDE_AGENT_SDK_SHELL_PERMISSIONS", "true")
        cb = create_bash_permission_evaluator(policy={"grep": _always_ok})
        # awk has no policy -> deny with a reason naming it.
        result = await cb("Bash", {"command": "grep foo f | awk '{print}'"}, _ctx())
        assert isinstance(result, PermissionResultDeny)
        assert "awk" in result.message

    @pytest.mark.anyio
    async def test_enabled_fail_safe_deny_on_unsupported(self, monkeypatch):
        monkeypatch.setenv("CLAUDE_AGENT_SDK_SHELL_PERMISSIONS", "on")
        cb = create_bash_permission_evaluator(policy={"cat": _always_ok})
        result = await cb("Bash", {"command": "cat << EOF\nx\nEOF"}, _ctx())
        assert isinstance(result, PermissionResultDeny)
        assert "heredoc" in result.message.lower()

    @pytest.mark.anyio
    async def test_non_bash_tool_defers(self, monkeypatch):
        monkeypatch.setenv("CLAUDE_AGENT_SDK_SHELL_PERMISSIONS", "1")
        cb = create_bash_permission_evaluator(policy={})
        # A different tool is never evaluated here; it defers (allow, no fallback).
        result = await cb("Read", {"file_path": "/etc/passwd"}, _ctx())
        assert isinstance(result, PermissionResultAllow)

    @pytest.mark.anyio
    async def test_missing_command_defers(self, monkeypatch):
        monkeypatch.setenv("CLAUDE_AGENT_SDK_SHELL_PERMISSIONS", "1")
        cb = create_bash_permission_evaluator(policy={})
        result = await cb("Bash", {}, _ctx())
        assert isinstance(result, PermissionResultAllow)

    @pytest.mark.anyio
    async def test_env_gate_can_be_disabled(self, monkeypatch):
        # env_var=None -> always evaluate, no environment gate.
        monkeypatch.delenv("CLAUDE_AGENT_SDK_SHELL_PERMISSIONS", raising=False)
        cb = create_bash_permission_evaluator(policy={}, env_var=None)
        result = await cb("Bash", {"command": "grep x y"}, _ctx())
        # Empty policy denies every spawn.
        assert isinstance(result, PermissionResultDeny)

    @pytest.mark.anyio
    async def test_custom_tool_and_command_key(self, monkeypatch):
        monkeypatch.setenv("CLAUDE_AGENT_SDK_SHELL_PERMISSIONS", "1")
        cb = create_bash_permission_evaluator(
            policy={"grep": _always_ok},
            tool_name="Shell",
            command_key="cmd",
            initial_cwd="/tmp",
        )
        result = await cb("Shell", {"cmd": "grep x y"}, _ctx())
        assert isinstance(result, PermissionResultAllow)
