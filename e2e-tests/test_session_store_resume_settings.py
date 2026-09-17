"""End-to-end test: SessionStore resume carries user settings.json into the
resumed subprocess.

On a store-backed resume the SDK runs the CLI under a temporary
``CLAUDE_CONFIG_DIR`` seeded from the caller's real one. User ``settings.json``
must be part of that seed — it carries ``apiKeyHelper`` (auth), ``env``,
``hooks`` and ``permissions``. This test uses ``outputStyle`` as the
observable: it is echoed verbatim on the ``init`` system message, has no
precedence chain that could mask it, and needs no shell or tool call.
"""

import json
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from claude_agent_sdk import (
    ClaudeAgentOptions,
    InMemorySessionStore,
    ResultMessage,
    SystemMessage,
    query,
)
from claude_agent_sdk._internal.session_resume import _write_redacted_credentials


@pytest.fixture
def config_dir(tmp_path: Path) -> Iterator[Path]:
    """A fresh caller CLAUDE_CONFIG_DIR.

    CI authenticates via environment variables, which the subprocess inherits.
    For a developer running this locally with a file-based OAuth login, carry
    the access token across (refresh token redacted, so the throwaway dir can
    never consume the real login's single-use refresh token).
    """
    d = tmp_path / "caller-config"
    d.mkdir()
    creds = Path.home() / ".claude" / ".credentials.json"
    if creds.is_file():
        _write_redacted_credentials(creds.read_text(), d / ".credentials.json")
    yield d
    shutil.rmtree(d, ignore_errors=True)


async def _run(options: ClaudeAgentOptions, prompt: str) -> tuple[dict, ResultMessage]:
    init: dict | None = None
    result: ResultMessage | None = None
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, SystemMessage) and message.subtype == "init":
            init = message.data
        elif isinstance(message, ResultMessage):
            result = message
    assert init is not None, "no init message"
    assert result is not None, "no result message"
    assert not result.is_error, result
    return init, result


@pytest.mark.e2e
@pytest.mark.anyio
async def test_store_resume_applies_user_settings(tmp_path: Path, config_dir: Path):
    store = InMemorySessionStore()
    cwd = tmp_path / "project"
    cwd.mkdir()

    def options(**kw: object) -> ClaudeAgentOptions:
        return ClaudeAgentOptions(
            cwd=str(cwd),
            session_store=store,
            env={"CLAUDE_CONFIG_DIR": str(config_dir)},
            max_turns=1,
            **kw,  # type: ignore[arg-type]
        )

    # 1) Seed a session into the store with no user settings present.
    first_init, first = await _run(options(), "Reply with exactly: one")
    assert first_init.get("output_style") == "default"

    # 2) Give the caller's config dir a settings.json, and drop the on-disk
    #    transcript so the resume can only succeed through the store
    #    materialization path (which runs under a *different*, temporary
    #    CLAUDE_CONFIG_DIR seeded from this one).
    (config_dir / "settings.json").write_text(
        json.dumps({"outputStyle": "Explanatory"})
    )
    shutil.rmtree(config_dir / "projects", ignore_errors=True)

    resumed_init, resumed = await _run(
        options(resume=first.session_id), "Reply with exactly: two"
    )

    assert resumed.session_id == first.session_id, "did not resume the session"
    assert resumed_init.get("output_style") == "Explanatory", (
        "user settings.json was not applied on store resume: "
        f"init output_style={resumed_init.get('output_style')!r}"
    )
