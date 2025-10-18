"""
UDS Agent Process IO implementation.

Provides a Unix Domain Socket interface to control ClaudeAgent instances.
"""

import asyncio
import json
import os
import signal
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from claude_agent_sdk.agent import ClaudeAgent


class UDSAgentProcessIO:
    """Unix Domain Socket based Agent Process IO implementation.

    This class wraps a ClaudeAgent and exposes its functionality via UDS.
    It does NOT manage logging or agent lifecycle - only UDS communication.
    """

    def __init__(
        self,
        agent: "ClaudeAgent",
        base_dir: str = "/tmp",
    ):
        """
        Initialize UDS Agent Process IO.

        Args:
            agent: ClaudeAgent instance to expose via UDS
            base_dir: Base directory for socket files
        """
        self.agent = agent
        self.base_dir = Path(base_dir)
        self.socket_path = self.base_dir / f"{agent.agent_id}.sock"

        # Server state
        self.server: asyncio.Server | None = None
        self.running = False
        self.started_at = datetime.now()
        self.monitor_task: asyncio.Task | None = None

    async def start(self, initial_query: str | None = None) -> None:
        """Start the UDS server with the agent.

        Args:
            initial_query: Optional query to send to agent on startup
        """
        self.running = True

        # Clean up existing socket if present
        if self.socket_path.exists():
            self.socket_path.unlink()

        # Start UDS server
        self.server = await asyncio.start_unix_server(
            self._handle_connection,
            path=str(self.socket_path)
        )

        # Set socket permissions (owner only)
        os.chmod(self.socket_path, 0o700)

        # Setup signal handlers for graceful shutdown
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, lambda: asyncio.create_task(self.stop()))
            except NotImplementedError:
                pass  # Windows doesn't support add_signal_handler

        # Output server_info as JSON to stdout
        server_info = self.get_server_info()
        import sys
        sys.stdout.write(json.dumps(server_info) + '\n')
        sys.stdout.flush()

        # Print startup instructions to stderr
        _print_startup_instructions(server_info)

        # Start monitoring task if initial query provided
        if initial_query:
            self.monitor_task = asyncio.create_task(self._start_monitor(initial_query))

        await self._serve_forever()

    async def stop(self) -> None:
        """Stop the UDS server."""
        self.running = False

        # Close server
        if self.server:
            self.server.close()
            await self.server.wait_closed()

        # Cancel the monitor task
        if self.monitor_task and not self.monitor_task.done():
            self.monitor_task.cancel()
            try:
                await self.monitor_task
            except asyncio.CancelledError:
                pass

        # Clean up socket file
        if self.socket_path.exists():
            self.socket_path.unlink()

    def get_server_info(self) -> dict[str, Any]:
        """Get server connection information for reconnection."""
        return {
            "type": "server_info",
            "agent_id": self.agent.agent_id,
            "pid": os.getpid(),
            "socket_path": str(self.socket_path),
            "log_file": str(self.agent.log_protocol.log_file_path),
            "started_at": self.started_at.isoformat()
        }

    async def _serve_forever(self):
        """Run the server until it's closed."""
        if self.server:
            async with self.server:
                await self.server.serve_forever()

    async def _handle_connection(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        """Handle a single client connection."""
        try:
            while True:
                # Read line from client
                line_bytes = await reader.readline()
                if not line_bytes:
                    break

                # Parse request
                try:
                    line = line_bytes.decode('utf-8').strip()
                    if not line:
                        continue

                    request = json.loads(line)

                    # Handle command
                    await self._handle_command(request, writer)

                except json.JSONDecodeError as e:
                    # Send error response for malformed JSON
                    error_response = {
                        "id": None,
                        "ok": False,
                        "error": "invalid_json",
                        "detail": str(e)
                    }
                    await self._send_response(writer, error_response)

        except asyncio.CancelledError:
            pass
        except Exception:
            pass
        finally:
            writer.close()
            await writer.wait_closed()

    async def _start_monitor(self, query: str) -> None:
        """Start monitoring the agent with initial query."""
        try:
            # Send initial query
            await self.agent.query(f"@CLAUDE.md\n{query}")

            # Monitor agent messages
            async for message in self.agent.receive(is_monitor=True, is_sub_agent=True):
                pass  # Messages are logged by agent's log_protocol

        except Exception:
            pass  # Silently handle errors to not crash server

    async def _handle_command(self, request: dict[str, Any], writer: asyncio.StreamWriter):
        """Process a command and send response."""
        request_id = request.get('id')
        command = request.get('cmd')

        try:
            if command == 'query':
                # Handle query command
                prompt = request.get('prompt', '')
                await self.agent.query(prompt)

                # Send completion
                await self._send_response(writer, {
                    "id": request_id,
                    "ok": True,
                    "done": True
                })

            elif command == 'status':
                # Get agent status
                server_info = self.get_server_info()

                response = {
                    "id": request_id,
                    "ok": True,
                    "result": {
                        **server_info,
                        "session_id": self.agent.session_id,
                        "uptime_seconds": (datetime.now() - self.started_at).total_seconds()
                    }
                }
                await self._send_response(writer, response)

            elif command == 'stop':
                # Stop server
                await self._send_response(writer, {
                    "id": request_id,
                    "ok": True,
                    "message": "Server stopping"
                })
                asyncio.create_task(self.stop())

            else:
                # Unknown command
                await self._send_response(writer, {
                    "id": request_id,
                    "ok": False,
                    "error": "unknown_command",
                    "detail": f"Command '{command}' is not supported"
                })

        except Exception as e:
            # Send error response
            await self._send_response(writer, {
                "id": request_id,
                "ok": False,
                "error": "command_failed",
                "detail": str(e)
            })

    async def _send_response(self, writer: asyncio.StreamWriter, response: dict[str, Any]):
        """Send JSON response to client."""
        try:
            response_bytes = (json.dumps(response) + '\n').encode('utf-8')
            writer.write(response_bytes)
            await writer.drain()
        except Exception:
            pass  # Silently handle send errors


def _print_startup_instructions(server_info: dict[str, Any]) -> None:
    """Prints a user-friendly guide with useful commands to stderr."""
    import sys
    pid = server_info["pid"]
    socket_path = server_info["socket_path"]
    log_file = server_info["log_file"]

    info_message = f"""
+------------------------------------------------------------------+
|      🚀 UDS Agent Server Started and Ready for Action 🚀       |
+------------------------------------------------------------------+
| Agent ID:     {server_info["agent_id"]}
| Process ID:   {pid}
| Socket Path:  {socket_path}
| Log File:     {log_file}
+------------------------------------------------------------------+
| Useful Commands:                                                 |
|------------------------------------------------------------------|
| MONITOR LOGS:                                                  |
|   tail -f {log_file}                                             |
|                                                                  |
| CHECK STATUS:                                                    |
|   echo '{{"id": "status-check", "cmd": "status"}}' | socat - UNIX-CONNECT:{socket_path}
|                                                                  |
| SEND A QUERY:                                                    |
|   echo '{{"id": "query-1", "cmd": "query", "prompt": "Hello!"}}' | socat - UNIX-CONNECT:{socket_path}
|                                                                  |
| STOP SERVER (Graceful):                                          |
|   echo '{{"id": "stop-cmd", "cmd": "stop"}}' | socat - UNIX-CONNECT:{socket_path}
|                                                                  |
| STOP SERVER (Forceful):                                          |
|   kill {pid}                                                     |
+------------------------------------------------------------------+
    """
    print(info_message, file=sys.stderr, flush=True)


# Helper function to run an agent
async def start_uds_io(
    agent: "ClaudeAgent",
    base_dir: str = "/tmp",
    query: str | None = None
) -> None:
    """Start a UDS-based agent process.

    Args:
        agent_type: Type of agent to start
        base_dir: Base directory for socket files
        query: Optional initial query to execute
    """

    # Create UDSAgentProcessIO
    io = UDSAgentProcessIO(
        agent=agent,
        base_dir=base_dir
    )

    # Start the server
    await io.start(initial_query=query)
