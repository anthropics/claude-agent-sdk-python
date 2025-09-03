"""Subprocess transport implementation using Claude Code CLI."""

import json
import logging
import os
import shutil
import tempfile
from collections import deque
from collections.abc import AsyncIterable, AsyncIterator
from contextlib import suppress
from pathlib import Path
from subprocess import PIPE
from typing import Any

import anyio
from anyio.abc import Process
from anyio.streams.text import TextReceiveStream, TextSendStream

from ..._errors import CLIConnectionError, CLINotFoundError, ProcessError
from ..._errors import CLIJSONDecodeError as SDKJSONDecodeError
from ...types import ClaudeCodeOptions
from . import Transport

logger = logging.getLogger(__name__)

_MAX_BUFFER_SIZE = 1024 * 1024  # 1MB buffer limit


class SubprocessCLITransport(Transport):
    """Subprocess transport using Claude Code CLI."""

    def __init__(
        self,
        prompt: str | AsyncIterable[dict[str, Any]],
        options: ClaudeCodeOptions,
        cli_path: str | Path | None = None,
    ):
        self._prompt = prompt
        self._is_streaming = not isinstance(prompt, str)
        self._options = options
        self._cli_path = str(cli_path) if cli_path else self._find_cli()
        self._cwd = str(options.cwd) if options.cwd else None
        self._process: Process | None = None
        self._stdout_stream: TextReceiveStream | None = None
        self._stderr_stream: TextReceiveStream | None = None
        self._stdin_stream: TextSendStream | None = None
        self._stderr_file: Any = None  # tempfile.NamedTemporaryFile
        self._ready = False

    def _find_cli(self) -> str:
        """Find Claude Code CLI binary."""
        if cli := shutil.which("claude"):
            return cli

        locations = [
            Path.home() / ".npm-global/bin/claude",
            Path("/usr/local/bin/claude"),
            Path.home() / ".local/bin/claude",
            Path.home() / "node_modules/.bin/claude",
            Path.home() / ".yarn/bin/claude",
        ]

        for path in locations:
            if path.exists() and path.is_file():
                return str(path)

        node_installed = shutil.which("node") is not None

        if not node_installed:
            error_msg = "Claude Code requires Node.js, which is not installed.\n\n"
            error_msg += "Install Node.js from: https://nodejs.org/\n"
            error_msg += "\nAfter installing Node.js, install Claude Code:\n"
            error_msg += "  npm install -g @anthropic-ai/claude-code"
            raise CLINotFoundError(error_msg)

        raise CLINotFoundError(
            "Claude Code not found. Install with:\n"
            "  npm install -g @anthropic-ai/claude-code\n"
            "\nIf already installed locally, try:\n"
            '  export PATH="$HOME/node_modules/.bin:$PATH"\n'
            "\nOr specify the path when creating transport:\n"
            "  SubprocessCLITransport(..., cli_path='/path/to/claude')"
        )

    def _build_command(self) -> list[str]:
        """Build CLI command with arguments."""
        cmd = [self._cli_path, "--output-format", "stream-json", "--verbose"]

        if self._options.system_prompt:
            cmd.extend(["--system-prompt", self._options.system_prompt])

        if self._options.append_system_prompt:
            cmd.extend(["--append-system-prompt", self._options.append_system_prompt])

        if self._options.allowed_tools:
            cmd.extend(["--allowedTools", ",".join(self._options.allowed_tools)])

        if self._options.max_turns:
            cmd.extend(["--max-turns", str(self._options.max_turns)])

        if self._options.disallowed_tools:
            cmd.extend(["--disallowedTools", ",".join(self._options.disallowed_tools)])

        if self._options.model:
            cmd.extend(["--model", self._options.model])

        if self._options.permission_prompt_tool_name:
            cmd.extend(
                ["--permission-prompt-tool", self._options.permission_prompt_tool_name]
            )

        if self._options.permission_mode:
            cmd.extend(["--permission-mode", self._options.permission_mode])

        if self._options.continue_conversation:
            cmd.append("--continue")

        if self._options.resume:
            cmd.extend(["--resume", self._options.resume])

        if self._options.settings:
            cmd.extend(["--settings", self._options.settings])

        if self._options.add_dirs:
            # Convert all paths to strings and add each directory
            for directory in self._options.add_dirs:
                cmd.extend(["--add-dir", str(directory)])

        if self._options.mcp_servers:
            if isinstance(self._options.mcp_servers, dict):
                # Filter out SDK servers - they're handled in-process
                external_servers = {
                    name: config
                    for name, config in self._options.mcp_servers.items()
                    if not (isinstance(config, dict) and config.get("type") == "sdk")
                }

                # Only pass external servers to CLI
                if external_servers:
                    cmd.extend(
                        [
                            "--mcp-config",
                            json.dumps({"mcpServers": external_servers}),
                        ]
                    )
            else:
                # String or Path format: pass directly as file path or JSON string
                cmd.extend(["--mcp-config", str(self._options.mcp_servers)])

        # Add extra args for future CLI flags
        for flag, value in self._options.extra_args.items():
            if value is None:
                # Boolean flag without value
                cmd.append(f"--{flag}")
            else:
                # Flag with value
                cmd.extend([f"--{flag}", str(value)])

        # Add prompt handling based on mode
        if self._is_streaming:
            # Streaming mode: use --input-format stream-json
            cmd.extend(["--input-format", "stream-json"])
        else:
            # String mode: use --print with the prompt
            cmd.extend(["--print", "--", str(self._prompt)])

        return cmd

    async def connect(self) -> None:
        """Start subprocess."""
        if self._process:
            return

        cmd = self._build_command()
        try:
            # Create a temp file for stderr to avoid pipe buffer deadlock
            # We can't use context manager as we need it for the subprocess lifetime
            self._stderr_file = tempfile.NamedTemporaryFile(  # noqa: SIM115
                mode="w+", prefix="claude_stderr_", suffix=".log", delete=False
            )

            # Merge environment variables: system -> user -> SDK required
            process_env = {
                **os.environ,
                **self._options.env,  # User-provided env vars
                "CLAUDE_CODE_ENTRYPOINT": "sdk-py",
            }

            if self._cwd:
                process_env["PWD"] = self._cwd

            self._process = await anyio.open_process(
                cmd,
                stdin=PIPE,
                stdout=PIPE,
                stderr=self._stderr_file,
                cwd=self._cwd,
                env=process_env,
            )

            if self._process.stdout:
                self._stdout_stream = TextReceiveStream(self._process.stdout)

            # Setup stdin for streaming mode
            if self._is_streaming and self._process.stdin:
                self._stdin_stream = TextSendStream(self._process.stdin)
            elif not self._is_streaming and self._process.stdin:
                # String mode: close stdin immediately
                await self._process.stdin.aclose()

            self._ready = True

        except FileNotFoundError as e:
            # Check if the error comes from the working directory or the CLI
            if self._cwd and not Path(self._cwd).exists():
                raise CLIConnectionError(
                    f"Working directory does not exist: {self._cwd}"
                ) from e
            raise CLINotFoundError(f"Claude Code not found at: {self._cli_path}") from e
        except Exception as e:
            raise CLIConnectionError(f"Failed to start Claude Code: {e}") from e

    async def close(self) -> None:
        """Close the transport and clean up resources."""
        self._ready = False

        if not self._process:
            return

        # Close stdin first if it's still open
        if self._stdin_stream:
            with suppress(Exception):
                await self._stdin_stream.aclose()
            self._stdin_stream = None

        if self._process.stdin:
            with suppress(Exception):
                await self._process.stdin.aclose()

        # Terminate and wait for process
        if self._process.returncode is None:
            with suppress(ProcessLookupError):
                self._process.terminate()
                # Wait for process to finish with timeout
                with suppress(Exception):
                    # Just try to wait, but don't block if it fails
                    await self._process.wait()

        # Clean up temp file
        if self._stderr_file:
            try:
                self._stderr_file.close()
                Path(self._stderr_file.name).unlink()
            except Exception:
                pass
            self._stderr_file = None

        self._process = None
        self._stdout_stream = None
        self._stderr_stream = None
        self._stdin_stream = None

    async def write(self, data: str) -> None:
        """Write raw data to the transport."""
        if not self._stdin_stream:
            raise CLIConnectionError("Cannot write: stdin not available")

        await self._stdin_stream.send(data)

    async def end_input(self) -> None:
        """End the input stream (close stdin)."""
        if self._stdin_stream:
            with suppress(Exception):
                await self._stdin_stream.aclose()
            self._stdin_stream = None
        if self._process and self._process.stdin:
            with suppress(Exception):
                await self._process.stdin.aclose()

    def read_messages(self) -> AsyncIterator[dict[str, Any]]:
        """Read and parse messages from the transport."""
        return self._read_messages_impl()

    async def _read_messages_impl(self) -> AsyncIterator[dict[str, Any]]:
        """Internal implementation of read_messages."""
        if not self._process or not self._stdout_stream:
            raise CLIConnectionError("Not connected")

        json_buffer = ""

        # Process stdout messages
        try:
            async for line in self._stdout_stream:
                line_str = line.strip()
                if not line_str:
                    continue

                json_lines = line_str.split("\n")

                for json_line in json_lines:
                    json_line = json_line.strip()
                    if not json_line:
                        continue

                    # Keep accumulating partial JSON until we can parse it
                    json_buffer += json_line

                    if len(json_buffer) > _MAX_BUFFER_SIZE:
                        json_buffer = ""
                        raise SDKJSONDecodeError(
                            f"JSON message exceeded maximum buffer size of {_MAX_BUFFER_SIZE} bytes",
                            ValueError(
                                f"Buffer size {len(json_buffer)} exceeds limit {_MAX_BUFFER_SIZE}"
                            ),
                        )

                    try:
                        data = json.loads(json_buffer)
                        json_buffer = ""
                        yield data
                    except json.JSONDecodeError:
                        # We are speculatively decoding the buffer until we get
                        # a full JSON object. If there is an actual issue, we
                        # raise an error after _MAX_BUFFER_SIZE.
                        continue

        except anyio.ClosedResourceError:
            pass
        except GeneratorExit:
            # Client disconnected
            pass

        # Read stderr from temp file (keep only last N lines for memory efficiency)
        stderr_lines: deque[str] = deque(maxlen=100)  # Keep last 100 lines
        if self._stderr_file:
            try:
                # Flush any pending writes
                self._stderr_file.flush()
                # Read from the beginning
                self._stderr_file.seek(0)
                for line in self._stderr_file:
                    line_text = line.strip()
                    if line_text:
                        stderr_lines.append(line_text)
            except Exception:
                pass

        # Check process completion and handle errors
        try:
            returncode = await self._process.wait()
        except Exception:
            returncode = -1

        # Convert deque to string for error reporting
        stderr_output = "\n".join(list(stderr_lines)) if stderr_lines else ""
        if len(stderr_lines) == stderr_lines.maxlen:
            stderr_output = (
                f"[stderr truncated, showing last {stderr_lines.maxlen} lines]\n"
                + stderr_output
            )

        # Use exit code for error detection, not string matching
        if returncode is not None and returncode != 0:
            raise ProcessError(
                f"Command failed with exit code {returncode}",
                exit_code=returncode,
                stderr=stderr_output,
            )
        elif stderr_output:
            # Log stderr for debugging but don't fail on non-zero exit
            logger.debug(f"Process stderr: {stderr_output}")

    def is_ready(self) -> bool:
        """Check if transport is ready for communication."""
        return (
            self._ready
            and self._process is not None
            and self._process.returncode is None
        )

    # Remove interrupt and control request methods - these now belong in Query class
