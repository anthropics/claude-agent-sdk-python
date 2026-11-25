"""Optional pinned Loom client qualification; all HTTP responses are synthetic.

Run explicitly in the optional-example CI job, not in the default test tree.
No Claude process, external server, database or model is called.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from loom import Agent

ROOT = Path(__file__).resolve().parents[2]


def load_file(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Reuse the SDK's real initialize/list/call bridge, not a second test protocol.
native = load_file(
    "_loom_native_mcp_test_harness", ROOT / "tests/test_sdk_mcp_integration.py"
)
example = load_file("_loom_optional_client_example", ROOT / "examples/loom_memory.py")


@pytest.fixture
def anyio_backend() -> str:
    # The optional Loom client uses asyncio for request lifetimes and cleanup.
    return "asyncio"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("tool", "arguments", "path", "response"),
    [
        (
            "remember",
            {"memory_key": "style", "value": "The user prefers concise answers."},
            "/v1/memory.set",
            {"memory_id": "example-memory", "version": 1, "noop": True},
        ),
        (
            "recall",
            {"query": "answer style", "top_k": 2},
            "/v1/memory.search",
            {"abstain": True, "pending_extraction": True, "results": []},
        ),
        (
            "recall",
            {"query": "answer style", "top_k": 2},
            "/v1/memory.search",
            {
                "abstain": False,
                "retrieval_confidence": 0.8,
                "results": [
                    {
                        "memory_key": "style",
                        "content_excerpt": "The user prefers concise answers.",
                        "similarity": 0.9,
                        "source_class": "external",
                        "verified": False,
                    }
                ],
            },
        ),
    ],
)
async def test_real_loom_client_preserves_scope_receipts_and_retrieval(
    monkeypatch: pytest.MonkeyPatch,
    tool: str,
    arguments: dict[str, Any],
    path: str,
    response: dict[str, Any],
) -> None:
    requests: list[httpx.Request] = []
    original_client = httpx.AsyncClient

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.path == path
        requests.append(request)
        return httpx.Response(200, json=response)

    def mocked_http(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        return original_client(
            *args, **kwargs, transport=httpx.MockTransport(respond), trust_env=False
        )

    monkeypatch.setattr("loom.transport.httpx.AsyncClient", mocked_http)
    async with Agent(
        org="example-org",
        namespace="example-memory",
        agent="example-agent",
        base_url="https://loom.example",
        api_key="example-token",
        auto_run=False,
        mentor_brief=False,
        coalesce_sets=False,
    ) as agent:
        config = example.create_loom_memory_server(agent, user_id="example-user")
        async with native.connected(config) as client:
            result = await client.call_tool("srv", tool, arguments)
    assert not result.get("isError")
    assert len(requests) == 1
    body = json.loads(requests[0].content)
    assert {
        key: body[key] for key in ("org", "namespace", "agent", "user_id", "session_id")
    } == {
        "org": "example-org",
        "namespace": "example-memory",
        "agent": "example-agent",
        "user_id": "example-user",
        "session_id": "-",
    }
    returned = json.loads(native.texts(result)[0])
    if tool == "remember":
        assert body["memory_key"] == arguments["memory_key"]
        assert body["value"] == arguments["value"]
        assert returned["noop"] is True
        assert returned["stored"] is False
    elif response["abstain"]:
        assert returned["abstain"] is True
        assert returned["pending_extraction"] is True
        assert returned["memories"] == []
    else:
        assert returned["retrieval_confidence"] == 0.8
        assert returned["memories"][0]["source_class"] == "external"
        assert returned["memories"][0]["verified"] is False
        assert returned["memories"][0]["content"] == "The user prefers concise answers."


@pytest.mark.anyio
async def test_real_loom_refusal_is_an_mcp_error_not_a_write_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []
    original_client = httpx.AsyncClient

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/memory.set"
        requests.append(request)
        return httpx.Response(403, json={"detail": {"code": "FORBIDDEN"}})

    def mocked_http(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        return original_client(
            *args, **kwargs, transport=httpx.MockTransport(respond), trust_env=False
        )

    monkeypatch.setattr("loom.transport.httpx.AsyncClient", mocked_http)
    async with Agent(
        org="example-org",
        namespace="example-memory",
        agent="example-agent",
        base_url="https://loom.example",
        api_key="example-token",
        auto_run=False,
        mentor_brief=False,
        coalesce_sets=False,
    ) as agent:
        config = example.create_loom_memory_server(agent, user_id="example-user")
        async with native.connected(config) as client:
            result = await client.call_tool(
                "srv", "remember", {"memory_key": "style", "value": "A fact"}
            )
    assert result["isError"] is True
    assert len(requests) == 1
    assert all('"stored":true' not in text for text in native.texts(result))
