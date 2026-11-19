"""Tests verifying that heavy optional dependencies (mcp, jsonschema) are lazily loaded."""

import subprocess
import sys


def test_import_does_not_load_mcp() -> None:
    """Importing claude_agent_sdk should not load mcp or jsonschema."""
    code = """
import sys
import claude_agent_sdk

assert "mcp" not in sys.modules, f"mcp unexpectedly loaded: {list(k for k in sys.modules if 'mcp' in k)}"
assert "jsonschema" not in sys.modules, "jsonschema unexpectedly loaded"
assert "claude_agent_sdk._internal._mcp_compat" not in sys.modules
assert "claude_agent_sdk._internal.sdk_mcp_bridge" not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True
    )
    assert result.returncode == 0, (
        f"Subprocess failed:\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}"
    )


def test_tool_decorator_does_not_load_mcp() -> None:
    """Defining tools with @tool does not trigger mcp import."""
    code = """
import sys
from claude_agent_sdk import tool

@tool("demo", "A demo tool", {"val": str})
async def demo_tool(args):
    return {"content": [{"type": "text", "text": args["val"]}]}

assert "mcp" not in sys.modules, "tool decorator should not load mcp"
assert demo_tool.name == "demo"
assert demo_tool.description == "A demo tool"
"""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True
    )
    assert result.returncode == 0, (
        f"Subprocess failed:\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}"
    )


def test_query_without_mcp_servers_does_not_load_mcp() -> None:
    """Query object creation without sdk_mcp_servers does not load mcp or bridge."""
    code = """
import sys
from claude_agent_sdk._internal.query import Query
from claude_agent_sdk._internal.transport import Transport

class DummyTransport(Transport):
    async def connect(self): pass
    async def write(self, data): pass
    async def read_messages(self): yield {}
    async def close(self): pass
    async def end_input(self): pass
    def is_ready(self): return True

q = Query(DummyTransport(), is_streaming_mode=False)
assert "mcp" not in sys.modules, "Query init should not load mcp"
assert "claude_agent_sdk._internal.sdk_mcp_bridge" not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True
    )
    assert result.returncode == 0, (
        f"Subprocess failed:\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}"
    )


def test_tool_annotations_lazy_loading() -> None:
    """Accessing ToolAnnotations loads mcp on demand and behaves correctly."""
    code = """
import sys
import claude_agent_sdk

assert "mcp" not in sys.modules
from claude_agent_sdk import ToolAnnotations

assert "mcp" in sys.modules
anno = ToolAnnotations(readOnlyHint=True, maxResultSizeChars=1000)
assert anno.maxResultSizeChars == 1000
data = anno.model_dump(by_alias=True, exclude_none=True)
assert data.get("readOnlyHint") is True
assert data.get("maxResultSizeChars") == 1000
"""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True
    )
    assert result.returncode == 0, (
        f"Subprocess failed:\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}"
    )


def test_create_sdk_mcp_server_lazy_loading() -> None:
    """Calling create_sdk_mcp_server loads mcp and jsonschema on demand."""
    code = """
import sys
import claude_agent_sdk

assert "mcp" not in sys.modules
assert "jsonschema" not in sys.modules

from claude_agent_sdk import tool, create_sdk_mcp_server

@tool("echo", "Echo text", {"text": str})
async def echo(args):
    return {"content": [{"type": "text", "text": args["text"]}]}

server_config = create_sdk_mcp_server("test_echo", tools=[echo])
assert "mcp" in sys.modules
assert "jsonschema" in sys.modules
assert server_config["name"] == "test_echo"
assert server_config["type"] == "sdk"
assert server_config["instance"] is not None
"""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True
    )
    assert result.returncode == 0, (
        f"Subprocess failed:\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}"
    )


def test_dir_includes_lazy_exports() -> None:
    """dir(claude_agent_sdk) includes all exported names without forcing premature load."""
    code = """
import sys
import claude_agent_sdk

names = dir(claude_agent_sdk)
assert "ToolAnnotations" in names
assert "create_sdk_mcp_server" in names
assert "query" in names
assert "mcp" not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True
    )
    assert result.returncode == 0, (
        f"Subprocess failed:\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}"
    )


def test_get_type_hints_resolves_after_mcp_loaded() -> None:
    """get_type_hints resolves SdkMcpTool and tool annotations once mcp is loaded."""
    code = """
import sys
import typing
from claude_agent_sdk import SdkMcpTool, tool, create_sdk_mcp_server

# After MCP server initialization, _McpToolAnnotations is populated in module globals
create_sdk_mcp_server("test_hints", tools=[])

tool_hints = typing.get_type_hints(tool)
assert "annotations" in tool_hints
assert tool_hints["annotations"] is not None

sdk_tool_hints = typing.get_type_hints(SdkMcpTool)
assert "annotations" in sdk_tool_hints
assert sdk_tool_hints["annotations"] is not None
"""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True
    )
    assert result.returncode == 0, (
        f"Subprocess failed:\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}"
    )


def test_tool_annotations_pickle_roundtrip() -> None:
    """ToolAnnotations instances can be pickled and unpickled."""
    code = """
import pickle
from claude_agent_sdk import ToolAnnotations

anno = ToolAnnotations(readOnlyHint=True, maxResultSizeChars=256)
data = pickle.dumps(anno)
loaded = pickle.loads(data)

assert isinstance(loaded, ToolAnnotations)
assert loaded.maxResultSizeChars == 256
dumped = loaded.model_dump(by_alias=True, exclude_none=True)
assert dumped.get("readOnlyHint") is True
assert dumped.get("maxResultSizeChars") == 256
"""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True
    )
    assert result.returncode == 0, (
        f"Subprocess failed:\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}"
    )


def test_get_type_hints_resolves_before_mcp_loaded() -> None:
    """get_type_hints resolves SdkMcpTool and tool annotations before mcp is loaded."""
    code = """
import sys
import typing
import claude_agent_sdk
from claude_agent_sdk import SdkMcpTool, tool

assert "mcp" not in sys.modules, "mcp should not be loaded on import"

tool_hints = typing.get_type_hints(tool)
assert "annotations" in tool_hints
assert tool_hints["annotations"] is not None
assert "mcp" not in sys.modules, "get_type_hints should not trigger mcp load"

sdk_tool_hints = typing.get_type_hints(SdkMcpTool)
assert "annotations" in sdk_tool_hints
assert sdk_tool_hints["annotations"] is not None
assert "mcp" not in sys.modules, "get_type_hints should not trigger mcp load"
"""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True
    )
    assert result.returncode == 0, (
        f"Subprocess failed:\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}"
    )
