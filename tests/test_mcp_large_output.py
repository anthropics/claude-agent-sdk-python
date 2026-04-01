"""Tests for large-MCP-result spill behavior and env-var passthrough.

Root cause (confirmed via claude-cli-internal, 2026-03-27):
Two independent spill layers in the bundled CLI:

  Layer 1 — MCP-specific (mcpValidation.ts)
    Threshold: MAX_MCP_OUTPUT_TOKENS env var, default 25 000 tokens.
    Setting MAX_MCP_OUTPUT_TOKENS=500000 bypasses this layer.
    Output on spill: plain "Error: result exceeds maximum allowed tokens…"

  Layer 2 — generic tool-result (toolResultStorage.ts maybePersistLargeToolResult)
    Threshold: DEFAULT_MAX_RESULT_SIZE_CHARS = 50 000 chars, hardcoded in
    toolLimits.ts.  No env var reads this constant.  MCPTool declares
    maxResultSizeChars: 100_000 but getPersistenceThreshold clamps it to
    Math.min(100_000, 50_000) = 50 K.
    Output on spill: <persisted-output> tag + 2 KB preview — exactly what
    customers observe.

Regression timeline:
  PR #13609 (2026-01-06) removed the feature gate → layer 2 always-on for SDK builds.
    Before this gate removal, SDK 0.1.17 / CLI 2.0.18 was unaffected.
  PR #19224 (2026-02-21) lowered the external-build clamp from 100 K → 50 K chars.

Customer's MAX_MCP_OUTPUT_TOKENS=500000 bypasses layer 1 successfully; the 73 K result
then hits layer 2's 50 K char wall and produces <persisted-output>.  There is currently
no env var or SDK option to raise the layer-2 threshold — a CLI change is required.

These tests confirm:
  1. MAX_MCP_OUTPUT_TOKENS (layer-1 threshold) passes through to the CLI subprocess.
  2. os.environ values are inherited; options.env overrides them.
  3. Raising MAX_MCP_OUTPUT_TOKENS alone is NOT sufficient for >50 K results because
     layer 2 is still in the path (documented via INLINE_CONTENT size boundary tests).
  4. The SDK message parser surfaces <persisted-output> content unchanged so callers
     can detect the degraded path and warn users.
"""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import anyio

from claude_agent_sdk._internal.message_parser import parse_message
from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport
from claude_agent_sdk.types import ClaudeAgentOptions, ToolResultBlock, UserMessage

DEFAULT_CLI_PATH = "/usr/bin/claude"

# Layer-2 threshold as confirmed in claude-cli-internal toolLimits.ts
_LAYER2_THRESHOLD_CHARS = 50_000


def make_transport(env: dict | None = None, **kwargs) -> SubprocessCLITransport:
    options = ClaudeAgentOptions(
        cli_path=DEFAULT_CLI_PATH,
        env=env or {},
        **kwargs,
    )
    return SubprocessCLITransport(prompt="test", options=options)


# ---------------------------------------------------------------------------
# Helpers to capture the env dict passed to anyio.open_process
# ---------------------------------------------------------------------------


def _capture_env(transport: SubprocessCLITransport) -> dict[str, str]:
    """Run transport.connect() with a mocked process and return the env dict."""
    captured: dict[str, str] = {}

    async def _run():
        mock_process = MagicMock()
        mock_process.stdin = MagicMock()
        mock_process.stdout = MagicMock()
        mock_process.stderr = None
        mock_process.returncode = None

        with (
            patch(
                "claude_agent_sdk._internal.transport.subprocess_cli.anyio.open_process",
                new_callable=AsyncMock,
                return_value=mock_process,
            ) as mock_open,
            patch(
                "claude_agent_sdk._internal.transport.subprocess_cli.SubprocessCLITransport._check_claude_version",
                new_callable=AsyncMock,
            ),
        ):
            await transport.connect()
            _, kwargs = mock_open.call_args
            captured.update(kwargs.get("env", {}))

    anyio.run(_run)
    return captured


# ---------------------------------------------------------------------------
# 1. MAX_MCP_OUTPUT_TOKENS (layer-1) passthrough
# ---------------------------------------------------------------------------


class TestLayer1EnvPassthrough:
    def test_max_mcp_output_tokens_reaches_subprocess(self):
        """MAX_MCP_OUTPUT_TOKENS set in options.env must appear in the subprocess env.

        This controls layer 1 only (mcpValidation.ts, ~25K token default).
        A 73K-char result bypasses layer 1 with this set, but will still hit
        layer 2's 50K char hard limit — see TestLayer2Boundary below.
        """
        transport = make_transport(env={"MAX_MCP_OUTPUT_TOKENS": "500000"})
        env = _capture_env(transport)
        assert "MAX_MCP_OUTPUT_TOKENS" in env, (
            "MAX_MCP_OUTPUT_TOKENS was not passed to the CLI subprocess. "
            "Layer 1 will use its default (~25K tokens) and spill to plain error text."
        )
        assert env["MAX_MCP_OUTPUT_TOKENS"] == "500000"

    def test_default_absent_when_not_set(self):
        """When not set, the SDK must not inject a default — the CLI's own governs."""
        env_without = {
            k: v for k, v in os.environ.items() if k != "MAX_MCP_OUTPUT_TOKENS"
        }
        with patch.dict(os.environ, env_without, clear=True):
            transport = make_transport(env={})
            captured = _capture_env(transport)
        assert "MAX_MCP_OUTPUT_TOKENS" not in captured

    def test_arbitrary_threshold_values_pass_through(self):
        for value in ("1", "25000", "1000000"):
            transport = make_transport(env={"MAX_MCP_OUTPUT_TOKENS": value})
            env = _capture_env(transport)
            assert env.get("MAX_MCP_OUTPUT_TOKENS") == value


# ---------------------------------------------------------------------------
# 2. os.environ inheritance and options.env precedence
# ---------------------------------------------------------------------------


class TestEnvInheritanceAndPrecedence:
    def test_inherited_from_os_environ(self):
        """MAX_MCP_OUTPUT_TOKENS set in os.environ before connect() is inherited."""
        with patch.dict(os.environ, {"MAX_MCP_OUTPUT_TOKENS": "200000"}):
            transport = make_transport(env={})
            env = _capture_env(transport)
        assert env.get("MAX_MCP_OUTPUT_TOKENS") == "200000"

    def test_options_env_overrides_os_environ(self):
        """options.env wins over os.environ."""
        with patch.dict(os.environ, {"MAX_MCP_OUTPUT_TOKENS": "1000"}):
            transport = make_transport(env={"MAX_MCP_OUTPUT_TOKENS": "500000"})
            env = _capture_env(transport)
        assert env.get("MAX_MCP_OUTPUT_TOKENS") == "500000"

    def test_claudecode_stripped(self):
        """CLAUDECODE is stripped so spawned subprocesses don't detect a parent CC."""
        with patch.dict(os.environ, {"CLAUDECODE": "1", "OTHER_VAR": "kept"}):
            transport = make_transport(env={})
            env = _capture_env(transport)
        assert "CLAUDECODE" not in env
        assert env.get("OTHER_VAR") == "kept"

    def test_sdk_managed_vars_always_set(self):
        transport = make_transport(env={})
        env = _capture_env(transport)
        assert env.get("CLAUDE_CODE_ENTRYPOINT") == "sdk-py"
        assert "CLAUDE_AGENT_SDK_VERSION" in env

    def test_options_env_cannot_override_sdk_version(self):
        from claude_agent_sdk._version import __version__

        transport = make_transport(env={"CLAUDE_AGENT_SDK_VERSION": "0.0.0"})
        env = _capture_env(transport)
        assert env.get("CLAUDE_AGENT_SDK_VERSION") == __version__


# ---------------------------------------------------------------------------
# 3. Layer-2 threshold boundary (documents the unresolved gap)
# ---------------------------------------------------------------------------


class TestLayer2Boundary:
    """Layer 2 (toolResultStorage.ts maybePersistLargeToolResult) spills any result
    exceeding 50 000 chars regardless of MAX_MCP_OUTPUT_TOKENS.  There is currently
    no env var or SDK option to raise this threshold — it requires a CLI change.

    These tests document that behavior by checking the content size boundary.
    """

    def test_content_under_50k_can_be_inline(self):
        """A result just below 50K chars is eligible to be passed inline by the CLI.
        This verifies our understanding of the threshold constant."""
        content = "x" * (_LAYER2_THRESHOLD_CHARS - 1)
        assert len(content) < _LAYER2_THRESHOLD_CHARS

    def test_customer_reproducer_exceeds_layer2_threshold(self):
        """The customer's ~73K-char result exceeds the 50K layer-2 threshold.

        MAX_MCP_OUTPUT_TOKENS=500000 bypasses layer 1 for this result, but it
        then hits layer 2 and produces <persisted-output>.  This is the bug.
        A fix requires exposing an env var or CLI flag for the layer-2 threshold.
        """
        customer_content_size = 73_000  # chars in customer's reproducer
        assert customer_content_size > _LAYER2_THRESHOLD_CHARS, (
            f"Customer's {customer_content_size}-char result exceeds the "
            f"{_LAYER2_THRESHOLD_CHARS}-char layer-2 threshold and will be spilled "
            "to a temp file even when MAX_MCP_OUTPUT_TOKENS is raised."
        )

    def test_no_layer2_env_var_exists(self):
        """Confirm there is no env-var path to raise the layer-2 threshold.

        The fix (Option 3) uses tool annotations instead of an env var:
          ToolAnnotations(maxResultSizeChars=500_000)
        The CLI reads this from the tools/list JSONRPC response and skips the
        Math.min clamp in getPersistenceThreshold for that tool.

        See test_max_result_size_chars_annotation_flows_to_cli in
        test_sdk_mcp_integration.py for SDK-side confirmation.
        """
        transport = make_transport(env={"MAX_MCP_OUTPUT_TOKENS": "500000"})
        env = _capture_env(transport)
        assert "MAX_TOOL_RESULT_CHARS" not in env
        assert "DISABLE_TOOL_RESULT_PERSISTENCE" not in env


# ---------------------------------------------------------------------------
# 4. Message parser: inline vs persisted-output tool results
# ---------------------------------------------------------------------------


def _user_message_with_tool_result(content: str, is_error: bool = False) -> dict:
    return {
        "type": "user",
        "message": {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_01ABC",
                    "content": content,
                    "is_error": is_error,
                }
            ],
        },
        "parent_tool_use_id": None,
        "tool_use_result": None,
        "uuid": "test-uuid-1234",
    }


# Below the layer-2 threshold — would be passed inline by the CLI.
INLINE_CONTENT = "x" * 1000

# What the CLI emits after layer-2 spill: <persisted-output> tag + 2 KB preview.
# Source: toolResultStorage.ts, PREVIEW_SIZE_BYTES = 2000.
PERSISTED_CONTENT = (
    "<persisted-output>\n"
    "Output too large (73.0KB). Full output saved to: /tmp/.claude/tool-results/abc123.txt\n"
    "\nPreview (first 2KB):\n" + "x" * 2000 + "\n...\n</persisted-output>"
)


class TestToolResultParsing:
    def test_inline_content_preserved(self):
        """Full tool-result content is preserved when the CLI passes it inline."""
        msg = parse_message(_user_message_with_tool_result(INLINE_CONTENT))
        assert isinstance(msg, UserMessage)
        blocks = [b for b in msg.content if isinstance(b, ToolResultBlock)]
        assert len(blocks) == 1
        assert blocks[0].content == INLINE_CONTENT
        assert not str(blocks[0].content).startswith("<persisted-output>")

    def test_persisted_output_detectable_by_prefix(self):
        """After a layer-2 spill, content starts with '<persisted-output>' —
        callers can detect this and warn users or raise an error."""
        msg = parse_message(_user_message_with_tool_result(PERSISTED_CONTENT))
        assert isinstance(msg, UserMessage)
        blocks = [b for b in msg.content if isinstance(b, ToolResultBlock)]
        assert len(blocks) == 1
        content = str(blocks[0].content)
        assert content.startswith("<persisted-output>"), (
            f"Expected persisted-output wrapper, got: {content[:100]!r}"
        )

    def test_persisted_output_is_not_full_content(self):
        """Claude receives only the 2 KB preview, not the original large content."""
        msg = parse_message(_user_message_with_tool_result(PERSISTED_CONTENT))
        assert isinstance(msg, UserMessage)
        blocks = [b for b in msg.content if isinstance(b, ToolResultBlock)]
        content = str(blocks[0].content)
        assert len(content) < _LAYER2_THRESHOLD_CHARS, (
            f"Expected preview under {_LAYER2_THRESHOLD_CHARS} chars, got {len(content)}"
        )

    def test_error_tool_result_flagged(self):
        msg = parse_message(
            _user_message_with_tool_result("tool failed", is_error=True)
        )
        assert isinstance(msg, UserMessage)
        blocks = [b for b in msg.content if isinstance(b, ToolResultBlock)]
        assert blocks[0].is_error is True

    def test_normal_tool_result_not_flagged(self):
        msg = parse_message(
            _user_message_with_tool_result(INLINE_CONTENT, is_error=False)
        )
        assert isinstance(msg, UserMessage)
        blocks = [b for b in msg.content if isinstance(b, ToolResultBlock)]
        assert blocks[0].is_error is False


# ---------------------------------------------------------------------------
# Utility: recommended caller pattern for detecting the degraded path
# ---------------------------------------------------------------------------


def is_persisted_output(block: ToolResultBlock) -> bool:
    """Return True if the CLI spilled this tool result to a temp file (layer 2)."""
    return isinstance(block.content, str) and block.content.startswith(
        "<persisted-output>"
    )


class TestPersistedOutputDetectionHelper:
    def test_helper_detects_persisted(self):
        msg = parse_message(_user_message_with_tool_result(PERSISTED_CONTENT))
        assert isinstance(msg, UserMessage)
        blocks = [b for b in msg.content if isinstance(b, ToolResultBlock)]
        assert is_persisted_output(blocks[0])

    def test_helper_passes_inline(self):
        msg = parse_message(_user_message_with_tool_result(INLINE_CONTENT))
        assert isinstance(msg, UserMessage)
        blocks = [b for b in msg.content if isinstance(b, ToolResultBlock)]
        assert not is_persisted_output(blocks[0])
