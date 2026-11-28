import pytest

from claude_agent_sdk._internal.sessions import (
    _build_conversation_chain,
    _build_subagent_chain,
)


@pytest.mark.parametrize("bad_parent", [["x"], {"a": 1}])
@pytest.mark.parametrize("build", [_build_conversation_chain, _build_subagent_chain])
def test_non_string_parent_uuid_does_not_crash(build, bad_parent):
    entries = [
        {"uuid": "a", "type": "user", "parentUuid": None},
        {"uuid": "b", "type": "assistant", "parentUuid": bad_parent},
    ]
    chain = build(entries)
    assert chain[-1]["uuid"] == "b"
