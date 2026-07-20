"""Subprocess transport implementation using Claude Code CLI."""

import atexit
import json
import logging
import os
import platform
import re
import shutil
import signal
from collections.abc import AsyncIterable, AsyncIterator
from contextlib import suppress
from pathlib import Path
from subprocess import PIPE
from typing import Any, cast

import anyio
from anyio.abc import Process
from anyio.streams.text import TextReceiveStream, TextSendStream

from ..._errors import CLIConnectionError, CLINotFoundError, ProcessError
from ..._errors import CLIJSONDecodeError as SDKJSONDecodeError
from ..._version import __version__
from ...types import ClaudeAgentOptions, SystemPromptFile, SystemPromptPreset
from .._task_compat import TaskHandle, spawn_detached
from . import Transport

logger = logging.getLogger(__name__)

_DEFAULT_MAX_BUFFER_SIZE = 1024 * 1024  # 1MB buffer limit
MINIMUM_CLAUDE_CODE_VERSION = "2.0.0"

# cmd.exe metacharacters (plus the quote character cmd.exe uses to toggle
# its quoting state, and "!", which expands like "%" when delayed expansion
# is enabled). subprocess.list2cmdline quotes arguments for the MSVCRT argv
# rules only -- it adds quotes only around whitespace -- so in a
# whitespace-free argument these characters reach a cmd.exe command line
# verbatim. See _reject_windows_batch_cli / _reject_windows_cmd_metacharacters.
_CMD_EXE_METACHARACTERS = '&|<>^%!"'

# Track live CLI subprocesses so we can terminate them when the parent Python
# process exits. This mirrors the TypeScript SDK's parent-exit cleanup and
# prevents orphaned `claude` processes from leaking when callers crash or exit
# before awaiting close().
_ACTIVE_CHILDREN: set[Process] = set()


def _kill_active_children() -> None:
    for p in list(_ACTIVE_CHILDREN):
        with suppress(Exception):
            p.send_signal(signal.SIGTERM)  # On Windows anyio maps this to terminate()
    _ACTIVE_CHILDREN.clear()


atexit.register(_kill_active_children)


class _LineFramer:
    """Reassembles complete lines from a stream that yields arbitrary chunks.

    anyio's TextReceiveStream yields CHUNKS (one per receive() call, up to 64KiB
    on the asyncio backend), not lines, so a large line spans several chunks and
    a chunk boundary can fall anywhere — including inside a JSON string value.
    Splitting on "\\n" and never stripping a chunk is what keeps whitespace at
    the seam intact.
    """

    def __init__(self) -> None:
        # Only ever holds fragments of the line currently being received, none
        # of which contain a newline. Accumulating in a list and joining once a
        # newline arrives is O(total) without relying on CPython's in-place
        # `str +=` realloc optimization, which other implementations lack.
        self._pending: list[str] = []
        self.pending_len = 0

    def push(self, chunk: str) -> list[str]:
        """Add a chunk, returning any lines it completed."""
        self._pending.append(chunk)
        self.pending_len += len(chunk)
        if "\n" not in chunk:
            return []

        *lines, tail = "".join(self._pending).split("\n")
        self._pending, self.pending_len = [tail], len(tail)
        return lines

    def flush(self) -> str:
        """Take the trailing partial line, if any."""
        line = "".join(self._pending)
        self._pending, self.pending_len = [], 0
        return line


def _parse_stdout_line(line: str) -> dict[str, Any] | None:
    """Parse one complete line of the CLI's NDJSON stdout.

    Returns None for lines that carry no message: blank lines, and non-JSON
    output such as ``[SandboxDebug] ...`` that some CLI builds write to stdout
    (#347). A line that looks like JSON but does not parse is corrupt — with
    proper line framing there is no later data that could complete it — so it
    raises rather than silently dropping a message.
    """
    # `line` is a complete line, so surrounding whitespace (e.g. the "\r" of a
    # CRLF) is meaningless. Only chunks must never be stripped.
    line = line.strip()
    if not line:
        return None
    if not line.startswith("{"):
        logger.debug("Skipping non-JSON line from CLI stdout: %s", line[:200])
        return None

    try:
        data: dict[str, Any] = json.loads(line)
    except json.JSONDecodeError as e:
        raise SDKJSONDecodeError(line, e) from e
    return data


class SubprocessCLITransport(Transport):
    """Subprocess transport using Claude Code CLI."""

    def __init__(
        self,
        prompt: str | AsyncIterable[dict[str, Any]],
        options: ClaudeAgentOptions,
    ):
        self._prompt = prompt
        # Always use streaming mode internally (matching TypeScript SDK)
        # This allows agents and other large configs to be sent via initialize request
        self._is_streaming = True
        self._options = options
        self._cli_path: str | None = (
            str(options.cli_path) if options.cli_path is not None else None
        )
        self._cwd = str(options.cwd) if options.cwd else None
        self._process: Process | None = None
        self._stdout_stream: TextReceiveStream | None = None
        self._stdin_stream: TextSendStream | None = None
        self._stderr_stream: TextReceiveStream | None = None
        self._stderr_task: TaskHandle | None = None
        self._ready = False
        self._exit_error: Exception | None = None  # Track process exit errors
        self._max_buffer_size = (
            options.max_buffer_size
            if options.max_buffer_size is not None
            else _DEFAULT_MAX_BUFFER_SIZE
        )
        self._write_lock: anyio.Lock = anyio.Lock()

    def _find_cli(self) -> str:
        """Find Claude Code CLI binary."""
        # First, check for bundled CLI
        bundled_cli = self._find_bundled_cli()
        if bundled_cli:
            return bundled_cli

        # Fall back to system-wide search
        which_hit: str | None = None
        if cli := shutil.which("claude"):
            if platform.system() != "Windows" or self._is_windows_native_exe(cli):
                return cli
            # Windows resolved something CreateProcess cannot run directly
            # as the CLI: npm's claude.cmd shim (which connect() refuses to
            # spawn) or an extensionless wrapper script from a git-bash /
            # WSL setup (which fails at spawn with WinError 193). shutil.which
            # walks PATH directory-major, so such an entry in an early PATH
            # directory shadows a native claude.exe installed in a later
            # one (within one directory the default PATHEXT would prefer
            # .EXE, so the shadowing is purely the directory order). Prefer
            # any discoverable native executable, and keep this hit only as
            # the last resort so a shim-only machine still gets the
            # explanatory batch-script refusal from connect(). The claude.exe
            # probe is vetted too: PATHEXT resolution can append an
            # extension and hand back "claude.exe.cmd".
            exe = shutil.which("claude.exe")
            if exe and self._is_windows_native_exe(exe):
                return exe
            which_hit = cli

        if platform.system() == "Windows":
            # Only the native installer's claude.exe. Path.exists() does
            # no PATHEXT resolution, so the .exe name must be probed
            # explicitly. The POSIX-shaped entries below are deliberately
            # not probed on Windows: an extensionless match (a WSL / git-bash
            # script artifact at ~/.local/bin/claude) would preempt the
            # explanatory batch-script refusal with an opaque spawn
            # failure, and a rooted-but-driveless "/usr/local/bin/claude"
            # resolves against the current drive (C:\usr\local\bin\...),
            # a location another local user can create -- a
            # binary-planting probe.
            locations = [Path.home() / ".local/bin/claude.exe"]
        else:
            locations = [
                Path.home() / ".npm-global/bin/claude",
                Path("/usr/local/bin/claude"),
                Path.home() / ".local/bin/claude",
                Path.home() / "node_modules/.bin/claude",
                Path.home() / ".yarn/bin/claude",
                Path.home() / ".claude/local/claude",
            ]

        for path in locations:
            if path.exists() and path.is_file():
                return str(path)

        if which_hit is not None:
            # No native executable was discoverable anywhere: return the
            # original which() hit so connect() raises the batch-script
            # refusal (with its remediation) for a shim, or the spawn error
            # for a wrapper script, rather than a bare not-found error.
            return which_hit

        if platform.system() == "Windows":
            # npm's Windows install is a claude.cmd shim, which connect()
            # refuses (_reject_windows_batch_cli), so do not recommend it.
            raise CLINotFoundError(
                "Claude Code not found. Install the native claude.exe with "
                "(PowerShell):\n"
                "  irm https://claude.ai/install.ps1 | iex\n"
                "\nOr install the claude-agent-sdk wheel for a platform that "
                "bundles claude.exe (e.g. Windows x64), or provide the path to "
                "a claude.exe via ClaudeAgentOptions:\n"
                "  ClaudeAgentOptions(cli_path='C:\\\\path\\\\to\\\\claude.exe')\n"
                "\n(npm install -g @anthropic-ai/claude-code produces a claude.cmd "
                "shim, which this SDK refuses to run on Windows.)"
            )
        raise CLINotFoundError(
            "Claude Code not found. Install with:\n"
            "  npm install -g @anthropic-ai/claude-code\n"
            "\nIf already installed locally, try:\n"
            '  export PATH="$HOME/node_modules/.bin:$PATH"\n'
            "\nOr provide the path via ClaudeAgentOptions:\n"
            "  ClaudeAgentOptions(cli_path='/path/to/claude')"
        )

    def _find_bundled_cli(self) -> str | None:
        """Find bundled CLI binary if it exists."""
        # Determine the CLI binary name based on platform
        cli_name = "claude.exe" if platform.system() == "Windows" else "claude"

        # Get the path to the bundled CLI
        # The _bundled directory is in the same package as this module
        bundled_path = Path(__file__).parent.parent.parent / "_bundled" / cli_name

        if bundled_path.exists() and bundled_path.is_file():
            logger.info(f"Using bundled Claude Code CLI: {bundled_path}")
            return str(bundled_path)

        return None

    @staticmethod
    def _is_windows_native_exe(cli_path: str) -> bool:
        """Whether cli_path's final component names an image CreateProcess
        runs directly (.exe / .com), used only to decide which discovery
        result to prefer. It is not a security gate: every returned path
        still passes _reject_windows_batch_cli in connect().
        """
        name = cli_path.replace("\\", "/").rsplit("/", 1)[-1]
        return name.rstrip(". ").lower().endswith((".exe", ".com"))

    @staticmethod
    def _is_windows_batch_cli(cli_path: str) -> bool:
        """Whether cli_path names a .bat/.cmd batch script on Windows.

        Always False off Windows. See _reject_windows_batch_cli for why
        spawning such a script is refused.
        """
        if platform.system() != "Windows":
            return False
        # Deliberately NOT pathlib: PureWindowsPath and PurePosixPath parse
        # several of the cases below differently (".cmd" has suffix ".cmd"
        # on POSIX but "" on Windows), and the tests run on POSIX CI while
        # the code runs on Windows. Plain string logic behaves identically
        # on both.
        #
        # Classify EVERY path component, not only the final one. Win32 opens
        # a path after lexical normalization -- "." / ".." collapsing,
        # repeated separators, and position-dependent trailing dot/space
        # trimming (a middle ".. " or "..." stays a literal name while a
        # final one trims to ".." or vanishes) -- and any attempt to
        # re-derive the effective final component here is a race against
        # that ruleset: get one rule slightly wrong and a spelling such as
        # "claude.cmd\\...\\.." resolves to claude.cmd on Windows while the
        # simulation lands on some other name. Refusing whenever ANY
        # component carries a batch extension closes that whole class
        # outright, because every normalization trick still has to spell
        # the .bat/.cmd component somewhere in the string. It costs
        # nothing legitimate: no real claude.exe lives beneath a directory
        # named like a batch file.
        #
        # Within a component, Win32 finds the extension with a last-dot
        # scan over the WHOLE component, stream spec included --
        # "claude:evil.cmd" has extension ".cmd" -- while an NTFS stream
        # spec also opens its base file -- "claude.cmd:stream" opens
        # claude.cmd -- and a drive prefix ("C:claude.cmd") rides in the
        # same component. Splitting each component on ":" covers all of
        # these: colons cannot appear in real file names, so no legitimate
        # segment is over-refused. Trailing dots and spaces, which Windows
        # strips at path resolution, are stripped per segment (the same
        # normalization Rust's CVE-2024-24576 fix applies), and a bare
        # ".cmd" counts as a batch extension (as Win32 PathFindExtension
        # treats it, and pathlib does not).
        return any(
            segment.rstrip(". ").lower().endswith((".bat", ".cmd"))
            for component in cli_path.replace("\\", "/").split("/")
            for segment in component.split(":")
        )

    @staticmethod
    def _reject_windows_batch_cli(cli_path: str) -> None:
        """Refuse to execute a .bat/.cmd script as the CLI on Windows.

        Windows has no shebang mechanism: CreateProcess runs batch scripts
        by silently rewriting the spawn into a 'cmd.exe /c' invocation, and
        cmd.exe re-parses the whole command line at execution time.
        subprocess.list2cmdline quotes arguments for the MSVCRT argv rules
        only, not for cmd.exe, so cmd.exe metacharacters inside an argument
        value -- for example a session title passed to --resume -- reach
        cmd.exe unescaped and can execute injected commands. Reliable
        escaping for cmd.exe does not exist (%VAR% expands even inside
        double quotes), so spawning a batch script with runtime-provided
        arguments cannot be made safe. Refusing is the same remediation
        Node.js shipped for this vulnerability class (CVE-2024-27980,
        "BatBadBut").

        In practice this refuses npm's claude.cmd shim, which _find_cli
        returns only when no native claude.exe is discoverable (for
        example sdist installs on a machine with just the npm shim). The
        alternatives in the error message avoid cmd.exe entirely.
        """
        if not SubprocessCLITransport._is_windows_batch_cli(cli_path):
            return
        raise CLIConnectionError(
            f"Refusing to execute batch script {cli_path!r}: Windows runs "
            ".bat/.cmd files via cmd.exe, which can execute commands "
            "injected through CLI arguments, and no reliable escaping for "
            "cmd.exe exists. Use a native claude executable instead: "
            "install Claude Code natively "
            "(irm https://claude.ai/install.ps1 | iex), point "
            "ClaudeAgentOptions(cli_path=...) at a claude.exe, or install "
            "the claude-agent-sdk wheel for a platform that bundles "
            "claude.exe (e.g. Windows x64)."
        )

    @staticmethod
    def _reject_windows_cmd_metacharacters(option_name: str, value: str) -> None:
        """Defense in depth for Windows: reject cmd.exe metacharacters.

        With batch-script spawning refused (_reject_windows_batch_cli),
        these characters are harmless: list2cmdline quotes correctly for
        native executables. They are rejected anyway so that resume /
        session_id values, which applications commonly take from external
        input, stay inert even if a cmd.exe hop is ever reintroduced
        between the SDK and the CLI. No format is imposed beyond this
        (resume values may be arbitrary session titles, not only UUIDs),
        and POSIX behavior is unchanged.
        """
        if platform.system() != "Windows":
            return
        bad = sorted({c for c in value if c in _CMD_EXE_METACHARACTERS or c in "\r\n"})
        if bad:
            raise ValueError(
                f"{option_name} value {value!r} contains characters that "
                f"are unsafe to pass on a Windows command line: {bad!r}"
            )

    def _build_settings_value(self) -> str | None:
        """Build settings value, merging sandbox settings if provided.

        Returns the settings value as either:
        - A JSON string (if sandbox is provided or settings is JSON)
        - A file path (if only settings path is provided without sandbox)
        - None if neither settings nor sandbox is provided
        """
        has_settings = self._options.settings is not None
        has_sandbox = self._options.sandbox is not None

        if not has_settings and not has_sandbox:
            return None

        # If only settings path and no sandbox, pass through as-is
        if has_settings and not has_sandbox:
            return self._options.settings

        # If we have sandbox settings, we need to merge into a JSON object
        settings_obj: dict[str, Any] = {}

        if has_settings:
            assert self._options.settings is not None
            settings_str = self._options.settings.strip()
            # Check if settings is a JSON string or a file path
            if settings_str.startswith("{") and settings_str.endswith("}"):
                # Parse JSON string
                try:
                    settings_obj = json.loads(settings_str)
                except json.JSONDecodeError:
                    # If parsing fails, treat as file path
                    logger.warning(
                        f"Failed to parse settings as JSON, treating as file path: {settings_str}"
                    )
                    # Read the file
                    settings_path = Path(settings_str)
                    if settings_path.exists():
                        with settings_path.open(encoding="utf-8") as f:
                            settings_obj = json.load(f)
            else:
                # It's a file path - read and parse
                settings_path = Path(settings_str)
                if settings_path.exists():
                    with settings_path.open(encoding="utf-8") as f:
                        settings_obj = json.load(f)
                else:
                    logger.warning(f"Settings file not found: {settings_path}")

        # Merge sandbox settings
        if has_sandbox:
            settings_obj["sandbox"] = self._options.sandbox

        return json.dumps(settings_obj)

    def _apply_skills_defaults(
        self,
    ) -> tuple[list[str], list[str] | None]:
        """Compute effective allowed_tools and setting_sources for skills.

        When ``options.skills`` is ``"all"``, injects the bare ``Skill`` tool;
        when it is a list, injects ``Skill(name)`` for each entry. In either
        case ``setting_sources`` defaults to ``["user", "project"]`` when
        unset so the CLI discovers installed skills without the caller having
        to wire up both options manually. ``None`` is a no-op.

        Does not mutate the original options object.
        """
        allowed_tools: list[str] = list(self._options.allowed_tools)
        setting_sources: list[str] | None = (
            list(self._options.setting_sources)
            if self._options.setting_sources is not None
            else None
        )

        skills = self._options.skills
        if skills is None:
            return allowed_tools, setting_sources

        if skills == "all":
            if "Skill" not in allowed_tools:
                allowed_tools.append("Skill")
        else:
            for name in skills:
                pattern = f"Skill({name})"
                if pattern not in allowed_tools:
                    allowed_tools.append(pattern)

        if setting_sources is None:
            setting_sources = ["user", "project"]

        return allowed_tools, setting_sources

    def _build_command(self) -> list[str]:
        """Build CLI command with arguments."""
        if self._cli_path is None:
            raise CLINotFoundError("CLI path not resolved. Call connect() first.")
        cmd = [self._cli_path, "--output-format", "stream-json", "--verbose"]

        if self._options.system_prompt is None:
            cmd.extend(["--system-prompt", ""])
        elif isinstance(self._options.system_prompt, str):
            cmd.extend(["--system-prompt", self._options.system_prompt])
        else:
            sp = self._options.system_prompt
            if sp.get("type") == "file":
                cmd.extend(["--system-prompt-file", cast(SystemPromptFile, sp)["path"]])
            elif sp.get("type") == "preset" and "append" in sp:
                cmd.extend(
                    ["--append-system-prompt", cast(SystemPromptPreset, sp)["append"]]
                )

        # Handle tools option (base set of tools)
        if self._options.tools is not None:
            tools = self._options.tools
            if isinstance(tools, list):
                if len(tools) == 0:
                    cmd.extend(["--tools", ""])
                else:
                    cmd.extend(["--tools", ",".join(tools)])
            else:
                # Preset object - 'claude_code' preset maps to 'default'
                cmd.extend(["--tools", "default"])

        effective_allowed_tools, effective_setting_sources = (
            self._apply_skills_defaults()
        )

        if effective_allowed_tools:
            cmd.extend(["--allowedTools", ",".join(effective_allowed_tools)])

        if self._options.max_turns:
            cmd.extend(["--max-turns", str(self._options.max_turns)])

        if self._options.max_budget_usd is not None:
            cmd.extend(["--max-budget-usd", str(self._options.max_budget_usd)])

        if self._options.disallowed_tools:
            cmd.extend(["--disallowedTools", ",".join(self._options.disallowed_tools)])

        if self._options.task_budget is not None:
            cmd.extend(["--task-budget", str(self._options.task_budget["total"])])

        if self._options.model:
            cmd.extend(["--model", self._options.model])

        if self._options.fallback_model:
            cmd.extend(["--fallback-model", self._options.fallback_model])

        if self._options.betas:
            cmd.extend(["--betas", ",".join(self._options.betas)])

        if self._options.permission_prompt_tool_name:
            cmd.extend(
                ["--permission-prompt-tool", self._options.permission_prompt_tool_name]
            )

        if self._options.permission_mode:
            cmd.extend(["--permission-mode", self._options.permission_mode])

        if self._options.continue_conversation:
            cmd.append("--continue")

        # Pass these as --flag=value rather than as two argv tokens. The CLI
        # declares --resume with an optional value, so in the two-token form a
        # dash-leading value is not bound to the flag and is instead parsed as
        # a separate CLI flag -- letting an untrusted value inject arbitrary
        # flags. The equals form always binds the value to the flag.
        if self._options.resume:
            self._reject_windows_cmd_metacharacters("resume", self._options.resume)
            cmd.append(f"--resume={self._options.resume}")

        if self._options.session_id:
            self._reject_windows_cmd_metacharacters(
                "session_id", self._options.session_id
            )
            cmd.append(f"--session-id={self._options.session_id}")

        # Handle settings and sandbox: merge sandbox into settings if both are provided
        settings_value = self._build_settings_value()
        if settings_value:
            cmd.extend(["--settings", settings_value])

        if self._options.add_dirs:
            # Convert all paths to strings and add each directory
            for directory in self._options.add_dirs:
                cmd.extend(["--add-dir", str(directory)])

        if self._options.mcp_servers:
            if isinstance(self._options.mcp_servers, dict):
                # Process all servers, stripping instance field from SDK servers
                servers_for_cli: dict[str, Any] = {}
                for name, config in self._options.mcp_servers.items():
                    if isinstance(config, dict) and config.get("type") == "sdk":
                        # For SDK servers, pass everything except the instance field
                        sdk_config: dict[str, object] = {
                            k: v for k, v in config.items() if k != "instance"
                        }
                        servers_for_cli[name] = sdk_config
                    else:
                        # For external servers, pass as-is
                        servers_for_cli[name] = config

                # Pass all servers to CLI
                if servers_for_cli:
                    cmd.extend(
                        [
                            "--mcp-config",
                            json.dumps({"mcpServers": servers_for_cli}),
                        ]
                    )
            else:
                # String or Path format: pass directly as file path or JSON string
                cmd.extend(["--mcp-config", str(self._options.mcp_servers)])

        if self._options.include_partial_messages:
            cmd.append("--include-partial-messages")

        if self._options.include_hook_events:
            cmd.append("--include-hook-events")

        if self._options.strict_mcp_config:
            cmd.append("--strict-mcp-config")

        if self._options.fork_session:
            cmd.append("--fork-session")

        if self._options.session_store is not None:
            cmd.append("--session-mirror")

        # Agents are always sent via initialize request (matching TypeScript SDK)
        # No --agents CLI flag needed

        if effective_setting_sources is not None:
            cmd.append(f"--setting-sources={','.join(effective_setting_sources)}")

        # Add plugin directories
        if self._options.plugins:
            for plugin in self._options.plugins:
                if plugin["type"] == "local":
                    cmd.extend(["--plugin-dir", plugin["path"]])
                else:
                    raise ValueError(f"Unsupported plugin type: {plugin['type']}")

        # Add extra args for future CLI flags
        for flag, value in self._options.extra_args.items():
            if value is None:
                # Boolean flag without value
                cmd.append(f"--{flag}")
            elif str(value).startswith("-"):
                # In the two-token form, a dash-leading value is not bound
                # to its flag when the CLI declares the option with an
                # optional value -- it parses as a separate flag instead
                # (the same injection the --resume change above closes).
                # The equals form always binds. Mirrors the equivalent
                # guard in the TypeScript SDK.
                cmd.append(f"--{flag}={value}")
            else:
                # Flag with value
                cmd.extend([f"--{flag}", str(value)])

        # Resolve thinking config -> --thinking / --max-thinking-tokens
        # `thinking` takes precedence over the deprecated `max_thinking_tokens`
        if self._options.thinking is not None:
            t = self._options.thinking
            if t["type"] == "adaptive":
                cmd.extend(["--thinking", "adaptive"])
            elif t["type"] == "enabled":
                cmd.extend(["--max-thinking-tokens", str(t["budget_tokens"])])
            elif t["type"] == "disabled":
                cmd.extend(["--thinking", "disabled"])

            # Narrow off the Disabled variant first so mypy knows `t["display"]` is a str
            # rather than widening to `object` across the union.
            if t["type"] != "disabled" and "display" in t:
                cmd.extend(["--thinking-display", t["display"]])
        elif self._options.max_thinking_tokens is not None:
            cmd.extend(
                ["--max-thinking-tokens", str(self._options.max_thinking_tokens)]
            )

        if self._options.effort is not None:
            cmd.extend(["--effort", self._options.effort])

        # Extract schema from output_format structure if provided
        # Expected: {"type": "json_schema", "schema": {...}}
        if (
            self._options.output_format is not None
            and isinstance(self._options.output_format, dict)
            and self._options.output_format.get("type") == "json_schema"
        ):
            schema = self._options.output_format.get("schema")
            if schema is not None:
                cmd.extend(["--json-schema", json.dumps(schema)])

        # Always use streaming mode with stdin (matching TypeScript SDK)
        # This allows agents and other large configs to be sent via initialize request
        cmd.extend(["--input-format", "stream-json"])

        return cmd

    async def connect(self) -> None:
        """Start subprocess."""
        if self._process:
            return

        if self._cli_path is None:
            self._cli_path = await anyio.to_thread.run_sync(self._find_cli)

        # Validate the resolved CLI before anything is spawned with it --
        # this guards the version probe below as well as the main spawn.
        self._reject_windows_batch_cli(self._cli_path)

        if not os.environ.get("CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK"):
            await self._check_claude_version()

        cmd = self._build_command()
        try:
            # Merge environment variables. CLAUDE_CODE_ENTRYPOINT defaults to
            # sdk-py regardless of inherited process env; options.env can override
            # it. CLAUDE_AGENT_SDK_VERSION is always set by the SDK.
            # Filter out CLAUDECODE so SDK-spawned subprocesses don't think
            # they're running inside a Claude Code parent (see #573).
            inherited_env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
            process_env = {
                **inherited_env,
                "CLAUDE_CODE_ENTRYPOINT": "sdk-py",
                **self._options.env,
                "CLAUDE_AGENT_SDK_VERSION": __version__,
            }

            # Propagate active OTEL trace context to the CLI so its spans
            # parent under the caller's distributed trace. No-op if
            # opentelemetry-api is not installed or there's no active span.
            try:
                from opentelemetry import propagate

                carrier: dict[str, str] = {}
                propagate.inject(carrier)
                if "traceparent" in carrier:
                    # Active span present: scrub stale inherited W3C context
                    # (CI/k8s ambient env) before writing the fresh values, so
                    # an inherited TRACESTATE isn't paired with a new
                    # TRACEPARENT. Explicit ClaudeAgentOptions.env always wins.
                    # Gate on the traceparent key (not carrier truthiness) so a
                    # baggage-only / non-W3C carrier doesn't scrub a valid
                    # inherited TRACEPARENT.
                    for key in ("TRACEPARENT", "TRACESTATE"):
                        if key not in self._options.env:
                            process_env.pop(key, None)
                    for k, v in carrier.items():
                        key = k.upper()
                        if key not in self._options.env:
                            process_env[key] = v
            except Exception:  # noqa: BLE001 - best-effort tracing must never break connect()
                logger.debug("OTEL trace context injection failed", exc_info=True)

            # Enable file checkpointing if requested
            if self._options.enable_file_checkpointing:
                process_env["CLAUDE_CODE_ENABLE_SDK_FILE_CHECKPOINTING"] = "true"

            if self._cwd:
                process_env["PWD"] = self._cwd

            # Pipe stderr only when the caller registered a callback.
            stderr_dest = PIPE if self._options.stderr is not None else None

            self._process = await anyio.open_process(
                cmd,
                stdin=PIPE,
                stdout=PIPE,
                stderr=stderr_dest,
                cwd=self._cwd,
                env=process_env,
                user=self._options.user,
            )
            _ACTIVE_CHILDREN.add(self._process)

            if self._process.stdout:
                self._stdout_stream = TextReceiveStream(self._process.stdout)

            # Setup stderr stream if piped
            if stderr_dest is PIPE and self._process.stderr:
                self._stderr_stream = TextReceiveStream(self._process.stderr)
                # Spawn the stderr reader via spawn_detached (not a manually-
                # entered TaskGroup) so cleanup has no trio task-affinity —
                # same pattern as Query._read_task.
                self._stderr_task = spawn_detached(self._handle_stderr())

            # Setup stdin for streaming (always used now)
            if self._process.stdin:
                self._stdin_stream = TextSendStream(self._process.stdin)

            self._ready = True

        except FileNotFoundError as e:
            # Check if the error comes from the working directory or the CLI
            if self._cwd and not Path(self._cwd).exists():
                error = CLIConnectionError(
                    f"Working directory does not exist: {self._cwd}"
                )
                self._exit_error = error
                raise error from e
            error = CLINotFoundError(f"Claude Code not found at: {self._cli_path}")
            self._exit_error = error
            raise error from e
        except Exception as e:
            error = CLIConnectionError(f"Failed to start Claude Code: {e}")
            self._exit_error = error
            raise error from e

    async def _handle_stderr(self) -> None:
        """Handle stderr stream - read and invoke callbacks."""
        if not self._stderr_stream:
            return

        def emit(line: str) -> None:
            line = line.rstrip()
            if not line:
                return

            # Call the stderr callback if provided. Isolate per-line so a
            # raise in the user's callback doesn't terminate the loop and
            # silently drop every subsequent line for the rest of the
            # session.
            if self._options.stderr:
                try:
                    self._options.stderr(line)
                except Exception:
                    logger.debug("stderr callback raised; continuing", exc_info=True)

        # `options.stderr` is documented to receive lines, but the stream yields
        # chunks, so frame the lines here rather than handing the callback
        # whatever a read happened to return.
        framer = _LineFramer()
        try:
            async for chunk in self._stderr_stream:
                for line in framer.push(chunk):
                    emit(line)
                # A producer that never emits a newline can't grow the buffer
                # without bound; flush it as a partial line instead.
                if framer.pending_len > self._max_buffer_size:
                    emit(framer.flush())
        except anyio.ClosedResourceError:
            pass  # Stream closed, exit normally
        except Exception:
            logger.debug("stderr stream read failed", exc_info=True)
        finally:
            # In a `finally` so the last partial line still reaches the callback
            # when close() cancels this task: cancellation arrives as a
            # BaseException, which neither `except` above catches. A diagnostic
            # written without a trailing newline before the CLI stalled is
            # exactly what the caller needs at that moment. `emit` is
            # synchronous, so it is safe to run during cancellation unwind.
            emit(framer.flush())

    async def close(self) -> None:
        """Close the transport and clean up resources.

        The whole body runs inside a shielded cancel scope. Cleanup is
        routinely reached while the caller's task is being cancelled (e.g.
        `async with ClaudeSDKClient()` unwinding on cancel), and an
        unshielded close() would abort at the first await — before the
        terminate/kill escalation ran — orphaning the CLI child, which then
        surfaces as `<defunct>` once nothing is left to wait() on it.

        Every await in *this* scope is bounded (~20s worst case), so an anyio
        cancellation is delayed but never blocked: the stream `aclose()`s are a
        non-blocking `close()` plus a checkpoint on both anyio backends (they
        never await `wait_closed()`, so undrained stdin cannot wedge them), the
        stderr task is cancelled before it is awaited, and the lock acquire and
        every process `wait()` carry an explicit deadline.

        Caveat: an anyio shield only defers cancellation that *originates from
        an anyio cancel scope*. A raw asyncio cancellation (`asyncio.wait_for` /
        `asyncio.timeout` firing, a bare `task.cancel()`, loop shutdown) is
        still delivered at the next await in here, and the escalation below only
        catches `TimeoutError` — so it would be skipped. That is a pre-existing
        limitation of the shield on the asyncio backend rather than something
        this scope introduces, and it is still strictly better than before: the
        `finally` keeps a still-running child in `_ACTIVE_CHILDREN` for the
        atexit reaper instead of dropping it. Making the escalation robust to a
        foreign `CancelledError` is a follow-up.
        """
        if not self._process:
            self._ready = False
            return

        with anyio.CancelScope(shield=True):
            # Cancel stderr reader if active
            if self._stderr_task is not None and not self._stderr_task.done():
                self._stderr_task.cancel()
                with suppress(Exception):
                    await self._stderr_task.wait()
            self._stderr_task = None

            # Close stdin stream (hold the write lock to prevent a race with
            # concurrent writes). Bounded: a writer blocked on a full stdin
            # pipe must not pin the shielded scope forever.
            lock_held = False
            with anyio.move_on_after(5):
                await self._write_lock.acquire()
                lock_held = True
            try:
                self._ready = False  # Set inside lock to prevent TOCTOU with write()
                if self._stdin_stream:
                    with suppress(Exception):
                        await self._stdin_stream.aclose()
                    self._stdin_stream = None
            finally:
                if lock_held:
                    self._write_lock.release()

            if self._stderr_stream:
                with suppress(Exception):
                    await self._stderr_stream.aclose()
                self._stderr_stream = None

            # Wait for graceful shutdown after stdin EOF, then terminate if
            # needed. The subprocess needs time to flush its session file after
            # receiving EOF on stdin. Without this grace period, SIGTERM can
            # interrupt the write and cause the last assistant message to be
            # lost (see #625).
            try:
                if self._process.returncode is None:
                    try:
                        with anyio.fail_after(5):
                            await self._process.wait()
                    except TimeoutError:
                        # Graceful shutdown timed out — force terminate
                        with suppress(ProcessLookupError):
                            self._process.terminate()
                        try:
                            with anyio.fail_after(5):
                                await self._process.wait()
                        except TimeoutError:
                            # SIGTERM handler blocked — force kill (SIGKILL)
                            with suppress(ProcessLookupError):
                                self._process.kill()
                            with suppress(Exception), anyio.fail_after(5):
                                await self._process.wait()
            finally:
                # Only stop tracking a child we actually reaped. A still-running
                # process (kill raced, or wait timed out) stays in the set so the
                # atexit reaper gets a chance at it — dropping it here is what
                # turned a cancelled close() into a leaked child. The reaper only
                # sends SIGTERM, so this rescues a child that is on its way out,
                # not one that survived SIGKILL.
                if self._process.returncode is not None:
                    _ACTIVE_CHILDREN.discard(self._process)

            self._process = None
            self._stdout_stream = None
            self._stdin_stream = None
            self._stderr_stream = None
            self._exit_error = None

    async def write(self, data: str) -> None:
        """Write raw data to the transport."""
        async with self._write_lock:
            # All checks inside lock to prevent TOCTOU races with close()/end_input()
            if not self._ready or not self._stdin_stream:
                raise CLIConnectionError("ProcessTransport is not ready for writing")

            if self._process and self._process.returncode is not None:
                raise CLIConnectionError(
                    f"Cannot write to terminated process (exit code: {self._process.returncode})"
                )

            if self._exit_error:
                raise CLIConnectionError(
                    f"Cannot write to process that exited with error: {self._exit_error}"
                ) from self._exit_error

            try:
                await self._stdin_stream.send(data)
            except Exception as e:
                self._ready = False
                self._exit_error = CLIConnectionError(
                    f"Failed to write to process stdin: {e}"
                )
                raise self._exit_error from e

    async def end_input(self) -> None:
        """End the input stream (close stdin)."""
        async with self._write_lock:
            if self._stdin_stream:
                with suppress(Exception):
                    await self._stdin_stream.aclose()
                self._stdin_stream = None

    def read_messages(self) -> AsyncIterator[dict[str, Any]]:
        """Read and parse messages from the transport."""
        return self._read_messages_impl()

    async def _read_messages_impl(self) -> AsyncIterator[dict[str, Any]]:
        """Internal implementation of read_messages."""
        if not self._process or not self._stdout_stream:
            raise CLIConnectionError("Not connected")

        # The CLI writes NDJSON: one message per line. Frame the lines out of
        # the chunks the stream actually yields (see _LineFramer).
        framer = _LineFramer()

        def guard(length: int) -> None:
            """Bound a single message, whether it is complete yet or not."""
            if length > self._max_buffer_size:
                raise SDKJSONDecodeError(
                    f"JSON message exceeded maximum buffer size of {self._max_buffer_size} bytes",
                    ValueError(
                        f"Buffer size {length} exceeds limit {self._max_buffer_size}"
                    ),
                )

        try:
            async for chunk in self._stdout_stream:
                for line in framer.push(chunk):
                    guard(len(line))
                    data = _parse_stdout_line(line)
                    if data is not None:
                        yield data
                guard(framer.pending_len)

        except anyio.ClosedResourceError:
            pass
        except GeneratorExit:
            # Client disconnected: return without falling through to the
            # process-exit check, since awaiting there would make CPython
            # complain that the async generator ignored GeneratorExit.
            return

        # Flush whatever is left. The CLI terminates every message with "\n", so
        # a residual tail means either a producer that omits the final newline
        # (yield it) or one cut off mid-write (unrecoverable — drop it).
        tail = framer.flush()
        try:
            data = _parse_stdout_line(tail)
        except SDKJSONDecodeError:
            logger.debug("Dropping truncated JSON at end of CLI stdout: %s", tail[:200])
            data = None
        if data is not None:
            yield data

        # Check process completion and handle errors
        try:
            returncode = await self._process.wait()
        except Exception:
            returncode = -1

        # Use exit code for error detection
        if returncode is not None and returncode != 0:
            self._exit_error = ProcessError(
                f"Command failed with exit code {returncode}",
                exit_code=returncode,
                stderr="Check stderr output for details",
            )
            raise self._exit_error

    async def _check_claude_version(self) -> None:
        """Check Claude Code version and warn if below minimum."""
        if self._cli_path is None:
            raise CLINotFoundError("CLI path not resolved. Call connect() first.")
        version_process = None
        try:
            with anyio.fail_after(2):  # 2 second timeout
                version_process = await anyio.open_process(
                    [self._cli_path, "-v"],
                    stdout=PIPE,
                    stderr=PIPE,
                )

                if version_process.stdout:
                    stdout_bytes = await version_process.stdout.receive()
                    version_output = stdout_bytes.decode().strip()

                    match = re.match(r"([0-9]+\.[0-9]+\.[0-9]+)", version_output)
                    if match:
                        version = match.group(1)
                        version_parts = [int(x) for x in version.split(".")]
                        min_parts = [
                            int(x) for x in MINIMUM_CLAUDE_CODE_VERSION.split(".")
                        ]

                        if version_parts < min_parts:
                            logger.warning(
                                "Claude Code version %s at %s is unsupported in the Agent SDK. "
                                "Minimum required version is %s. "
                                "Some features may not work correctly.",
                                version,
                                self._cli_path,
                                MINIMUM_CLAUDE_CODE_VERSION,
                            )
        except Exception:
            pass
        finally:
            if version_process:
                with suppress(Exception):
                    version_process.terminate()
                with suppress(Exception):
                    await version_process.wait()

    def is_ready(self) -> bool:
        """Check if transport is ready for communication."""
        return self._ready
