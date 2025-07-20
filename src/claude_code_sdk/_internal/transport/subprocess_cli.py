"""Subprocess transport implementation using Claude Code CLI."""

import json
import logging
import os
import shutil
from collections.abc import AsyncIterable, AsyncIterator
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
        close_stdin_after_prompt: bool = False,
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
        self._pending_control_responses: dict[str, dict[str, Any]] = {}
        self._request_counter = 0
        self._close_stdin_after_prompt = close_stdin_after_prompt

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

        if self._options.mcp_servers:
            cmd.extend(
                ["--mcp-config", json.dumps({"mcpServers": self._options.mcp_servers})]
            )

        # Add prompt handling based on mode
        if self._is_streaming:
            # Streaming mode: use --input-format stream-json
            cmd.extend(["--input-format", "stream-json"])
        else:
            # String mode: use --print with the prompt
            cmd.extend(["--print", str(self._prompt)])

        return cmd

    async def connect(self) -> None:
        """Start subprocess."""
        if self._process:
            return

        cmd = self._build_command()
        try:
            # Enable stdin pipe for both modes (but we'll close it for string mode)
            self._process = await anyio.open_process(
                cmd,
                stdin=PIPE,
                stdout=PIPE,
                stderr=PIPE,
                cwd=self._cwd,
                env={**os.environ, "CLAUDE_CODE_ENTRYPOINT": "sdk-py"},
            )

            if self._process.stdout:
                self._stdout_stream = TextReceiveStream(self._process.stdout)
            if self._process.stderr:
                self._stderr_stream = TextReceiveStream(self._process.stderr)

            # Handle stdin based on mode
            if self._is_streaming:
                # Streaming mode: keep stdin open and start streaming task
                if self._process.stdin:
                    self._stdin_stream = TextSendStream(self._process.stdin)
                    # Start streaming messages to stdin in background
                    import asyncio

                    asyncio.create_task(self._stream_to_stdin())
            else:
                # String mode: close stdin immediately (backward compatible)
                if self._process.stdin:
                    await self._process.stdin.aclose()

        except FileNotFoundError as e:
            # Check if the error comes from the working directory or the CLI
            if self._cwd and not Path(self._cwd).exists():
                raise CLIConnectionError(
                    f"Working directory does not exist: {self._cwd}"
                ) from e
            raise CLINotFoundError(f"Claude Code not found at: {self._cli_path}") from e
        except Exception as e:
            raise CLIConnectionError(f"Failed to start Claude Code: {e}") from e

    async def disconnect(self) -> None:
        """Terminate subprocess."""
        if not self._process:
            return

        if self._process.returncode is None:
            try:
                self._process.terminate()
                with anyio.fail_after(5.0):
                    await self._process.wait()
            except TimeoutError:
                self._process.kill()
                await self._process.wait()
            except ProcessLookupError:
                pass

        self._process = None
        self._stdout_stream = None
        self._stderr_stream = None
        self._stdin_stream = None

    async def send_request(self, messages: list[Any], options: dict[str, Any]) -> None:
        """Send additional messages in streaming mode."""
        if not self._is_streaming:
            raise CLIConnectionError("send_request only works in streaming mode")

        if not self._stdin_stream:
            raise CLIConnectionError("stdin not available - stream may have ended")

        # Send each message as a user message
        for message in messages:
            # Ensure message has required structure
            if not isinstance(message, dict):
                message = {
                    "type": "user",
                    "message": {"role": "user", "content": str(message)},
                    "parent_tool_use_id": None,
                    "session_id": options.get("session_id", "default"),
                }

            await self._stdin_stream.send(json.dumps(message) + "\n")

    async def _stream_to_stdin(self) -> None:
        """Stream messages to stdin for streaming mode."""
        if not self._stdin_stream or not isinstance(self._prompt, AsyncIterable):
            return

        try:
            async for message in self._prompt:
                if not self._stdin_stream:
                    break
                await self._stdin_stream.send(json.dumps(message) + "\n")

            # Close stdin after prompt if requested (e.g., for query() one-shot mode)
            if self._close_stdin_after_prompt and self._stdin_stream:
                await self._stdin_stream.aclose()
                self._stdin_stream = None
            # Otherwise keep stdin open for send_request (ClaudeSDKClient interactive mode)
        except Exception as e:
            logger.debug(f"Error streaming to stdin: {e}")
            if self._stdin_stream:
                await self._stdin_stream.aclose()
                self._stdin_stream = None

    async def receive_messages(self) -> AsyncIterator[dict[str, Any]]:
        """Receive messages from CLI."""
        if not self._process or not self._stdout_stream:
            raise CLIConnectionError("Not connected")

        # Safety constants
        max_stderr_size = 10 * 1024 * 1024  # 10MB
        stderr_timeout = 30.0  # 30 seconds

        json_buffer = ""

        # Process stdout messages first
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

                        # Handle control responses separately
                        if data.get("type") == "control_response":
                            response = data.get("response", {})
                            request_id = response.get("request_id")
                            if request_id:
                                # Store the response for the pending request
                                self._pending_control_responses[request_id] = response
                            continue

                        try:
                            yield data
                        except GeneratorExit:
                            return
                    except json.JSONDecodeError:
                        # We are speculatively decoding the buffer until we get
                        # a full JSON object. If there is an actual issue, we
                        # raise an error after _MAX_BUFFER_SIZE.
                        continue

        except anyio.ClosedResourceError:
            pass
        except GeneratorExit:
            # Client disconnected - still need to clean up
            pass

        # Process stderr with safety limits
        stderr_lines = []
        stderr_size = 0

        if self._stderr_stream:
            try:
                # Use timeout to prevent hanging
                with anyio.fail_after(stderr_timeout):
                    async for line in self._stderr_stream:
                        line_text = line.strip()
                        line_size = len(line_text)

                        # Enforce memory limit
                        if stderr_size + line_size > max_stderr_size:
                            stderr_lines.append(
                                f"[stderr truncated after {stderr_size} bytes]"
                            )
                            # Drain rest of stream without storing
                            async for _ in self._stderr_stream:
                                pass
                            break

                        stderr_lines.append(line_text)
                        stderr_size += line_size

            except TimeoutError:
                stderr_lines.append(
                    f"[stderr collection timed out after {stderr_timeout}s]"
                )
            except anyio.ClosedResourceError:
                pass

        # Check process completion and handle errors
        try:
            returncode = await self._process.wait()
        except Exception:
            returncode = -1

        stderr_output = "\n".join(stderr_lines) if stderr_lines else ""

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

    def is_connected(self) -> bool:
        """Check if subprocess is running."""
        return self._process is not None and self._process.returncode is None

    async def interrupt(self) -> None:
        """Send interrupt control request (only works in streaming mode)."""
        if not self._is_streaming:
            raise CLIConnectionError(
                "Interrupt requires streaming mode (AsyncIterable prompt)"
            )

        if not self._stdin_stream:
            raise CLIConnectionError("Not connected or stdin not available")

        await self._send_control_request({"subtype": "interrupt"})

    async def _send_control_request(self, request: dict[str, Any]) -> dict[str, Any]:
        """Send a control request and wait for response."""
        if not self._stdin_stream:
            raise CLIConnectionError("Stdin not available")

        # Generate unique request ID
        self._request_counter += 1
        request_id = f"req_{self._request_counter}_{os.urandom(4).hex()}"

        # Build control request
        control_request = {
            "type": "control_request",
            "request_id": request_id,
            "request": request,
        }

        # Send request
        await self._stdin_stream.send(json.dumps(control_request) + "\n")

        # Wait for response with timeout
        try:
            with anyio.fail_after(30.0):  # 30 second timeout
                while request_id not in self._pending_control_responses:
                    await anyio.sleep(0.1)

                response = self._pending_control_responses.pop(request_id)

                if response.get("subtype") == "error":
                    raise CLIConnectionError(
                        f"Control request failed: {response.get('error')}"
                    )

                return response
        except TimeoutError:
            raise CLIConnectionError("Control request timed out") from None
