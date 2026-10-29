"""Tests for the app label handed to the Claude Code subprocess."""

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import anyio
import pytest

from claude_agent_sdk import ClaudeAgentOptions
from claude_agent_sdk._internal import _client_app
from claude_agent_sdk._internal._client_app import (
    DERIVED_CLIENT_APP_ENV,
    EXPLICIT_CLIENT_APP_ENV,
    _format_client_app,
    _normalize,
    derive_client_app,
)
from claude_agent_sdk._internal.transport.subprocess_cli import (
    SubprocessCLITransport,
)


@pytest.fixture(autouse=True)
def _clear_derivation_cache():
    derive_client_app.cache_clear()
    yield
    derive_client_app.cache_clear()


def _install_package(
    site: Path,
    *,
    package: str,
    distribution: str,
    version: str = "1.2.3",
    requires: tuple[str, ...] = ("claude-agent-sdk>=0.2",),
) -> None:
    """Lay out a package and its dist-info, as an installer would."""
    (site / package).mkdir(parents=True)
    (site / package / "__init__.py").write_text("")
    (site / package / "__main__.py").write_text(
        "from claude_agent_sdk._internal._client_app import derive_client_app\n"
        "print(derive_client_app())\n"
    )
    dist_info = site / f"{distribution.replace('-', '_')}-{version}.dist-info"
    dist_info.mkdir()
    (dist_info / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: {distribution}\nVersion: {version}\n"
        + "".join(f"Requires-Dist: {item}\n" for item in requires)
    )
    (dist_info / "top_level.txt").write_text(f"{package}\n")


def _run_module(site: Path, module: str) -> str:
    python_path = os.pathsep.join(
        [str(site), str(Path(_client_app.__file__).parents[2])]
    )
    result = subprocess.run(
        [sys.executable, "-m", module],
        capture_output=True,
        check=True,
        cwd=site,
        env={**os.environ, "PYTHONPATH": python_path},
        text=True,
    )
    return result.stdout.strip()


class TestNormalize:
    @pytest.mark.parametrize(
        "value",
        [
            "my-app",
            "my-app/1.2.3",
            "@acme/my-app/1.2.3-beta.1+build.5",
            "my_app/1!2.0",
            "a" * 128,
        ],
    )
    def test_keeps_a_label_as_it_is(self, value):
        assert _normalize(value) == value

    @pytest.mark.parametrize(
        "value",
        [
            "",
            "café",
            "my-app/€",
            "my-app\n",
            "my app",
            "my-app), evil/(x",
            "/home/alice/my-app",
            "../my-app",
            "C:\\Users\\alice\\my-app",
            "home/alice/my-app",
            "a" * 129,
        ],
    )
    def test_drops_rather_than_repairs(self, value):
        assert _normalize(value) is None

    @pytest.mark.parametrize(
        "name", ["index", "main", "cli", "server", "app", "node", "App"]
    )
    def test_skips_generic_names(self, name):
        assert _format_client_app(name, "1.0.0") is None

    def test_falls_back_to_the_name_without_a_usable_version(self):
        assert _format_client_app("my-app", None) == "my-app"
        assert _format_client_app("my-app", "1 (dev)") == "my-app"

    def test_strips_a_local_version_part(self):
        assert _format_client_app("my-app", "1.2.3+alice.laptop") == "my-app/1.2.3"
        assert _format_client_app("my-app", "+alice.laptop") == "my-app"


class TestDeriveFromMain:
    def test_names_the_distribution_behind_python_m(self, tmp_path):
        _install_package(tmp_path, package="my_agent", distribution="my-agent")
        assert _run_module(tmp_path, "my_agent") == "my-agent/1.2.3"

    def test_uses_the_top_level_package_of_a_submodule(self, tmp_path):
        _install_package(tmp_path, package="my_agent", distribution="my-agent")
        (tmp_path / "my_agent" / "tools").mkdir()
        (tmp_path / "my_agent" / "tools" / "__init__.py").write_text("")
        (tmp_path / "my_agent" / "tools" / "__main__.py").write_text(
            (tmp_path / "my_agent" / "__main__.py").read_text()
        )
        assert _run_module(tmp_path, "my_agent.tools") == "my-agent/1.2.3"

    def test_skips_a_tool_that_does_not_depend_on_the_sdk(self, tmp_path):
        _install_package(
            tmp_path,
            package="uvicorn_like",
            distribution="uvicorn-like",
            requires=("click>=8",),
        )
        assert _run_module(tmp_path, "uvicorn_like") == "None"

    def test_does_not_mistake_a_similarly_named_dependency(self, tmp_path):
        _install_package(
            tmp_path,
            package="other",
            distribution="other-tool",
            requires=("claude-agent-sdk-extras>=1",),
        )
        assert _run_module(tmp_path, "other") == "None"

    def test_skips_a_tool_that_only_offers_the_sdk_as_an_extra(self, tmp_path):
        _install_package(
            tmp_path,
            package="launcher",
            distribution="launcher-tool",
            requires=('claude-agent-sdk>=0.2; extra == "claude"',),
        )
        assert _run_module(tmp_path, "launcher") == "None"

    def test_keeps_an_sdk_requirement_with_another_marker(self, tmp_path):
        _install_package(
            tmp_path,
            package="my_agent",
            distribution="my-agent",
            requires=('claude-agent-sdk>=0.2; python_version >= "3.10"',),
        )
        assert _run_module(tmp_path, "my_agent") == "my-agent/1.2.3"

    def test_skips_a_directory_run(self, tmp_path):
        _install_package(tmp_path, package="stray", distribution="stray-agent")
        (tmp_path / "__main__.py").write_text(
            "from claude_agent_sdk._internal._client_app import derive_client_app\n"
            "print(derive_client_app())\n"
        )
        dist_info = tmp_path / "stray_agent-1.2.3.dist-info"
        (dist_info / "top_level.txt").write_text("__main__\n")
        python_path = os.pathsep.join(
            [str(tmp_path), str(Path(_client_app.__file__).parents[2])]
        )
        result = subprocess.run(
            [sys.executable, str(tmp_path)],
            capture_output=True,
            check=True,
            env={**os.environ, "PYTHONPATH": python_path},
            text=True,
        )
        assert result.stdout.strip() == "None"

    def test_skips_a_namespace_package_shared_by_two_distributions(self):
        with (
            patch.object(
                sys.modules["__main__"],
                "__spec__",
                SimpleNamespace(name="acme.support"),
                create=True,
            ),
            patch(
                "importlib.metadata.packages_distributions",
                return_value={"acme": ["acme-billing", "acme-support"]},
            ),
            patch("importlib.metadata.requires", return_value=["claude-agent-sdk"]),
            patch("importlib.metadata.version", return_value="1.0.0"),
        ):
            assert derive_client_app() is None

    def test_skips_a_generic_distribution_name(self, tmp_path):
        _install_package(tmp_path, package="app", distribution="app")
        assert _run_module(tmp_path, "app") == "None"

    def test_skips_a_package_with_no_distribution(self, tmp_path):
        _install_package(tmp_path, package="my_agent", distribution="my-agent")
        for dist_info in tmp_path.glob("*.dist-info"):
            for path in dist_info.iterdir():
                path.unlink()
            dist_info.rmdir()
        assert _run_module(tmp_path, "my_agent") == "None"

    def test_skips_a_plain_script(self, tmp_path):
        script = tmp_path / "secret_project.py"
        script.write_text(
            "from claude_agent_sdk._internal._client_app import derive_client_app\n"
            "print(derive_client_app())\n"
        )
        result = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True,
            check=True,
            env={
                **os.environ,
                "PYTHONPATH": str(Path(_client_app.__file__).parents[2]),
            },
            text=True,
        )
        assert result.stdout.strip() == "None"

    def test_never_raises(self):
        main = MagicMock()
        main.__spec__ = MagicMock()
        main.__spec__.name = "my_agent.__main__"
        with (
            patch.dict(sys.modules, {"__main__": main}),
            patch(
                "importlib.metadata.packages_distributions",
                side_effect=RuntimeError("boom"),
            ),
        ):
            assert derive_client_app() is None


def _env_passed_to_subprocess(
    options: ClaudeAgentOptions,
    derived: str | None = "my-agent/1.2.3",
    ambient: dict[str, str] | None = None,
    cli_version: bytes = b"2.1.500 (Claude Code)",
) -> dict[str, str]:
    async def _connect() -> dict[str, str]:
        with (
            patch("anyio.open_process", new_callable=AsyncMock) as mock_open_process,
            patch(
                "claude_agent_sdk._internal.transport.subprocess_cli.derive_client_app",
                return_value=derived,
            ),
            patch(
                "claude_agent_sdk._internal.transport.subprocess_cli"
                ".DERIVED_CLIENT_APP_MINIMUM_CLAUDE_CODE_VERSION",
                "2.1.500",
            ),
            patch.dict(os.environ, ambient or {}),
        ):
            version_process = MagicMock()
            version_process.stdout.receive = AsyncMock(return_value=cli_version)
            version_process.wait = AsyncMock()
            process = MagicMock()
            process.stdin.aclose = AsyncMock()
            process.returncode = None
            skips_probe = bool(os.environ.get("CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK"))
            mock_open_process.side_effect = (
                [process] if skips_probe else [version_process, process]
            )
            transport = SubprocessCLITransport(prompt="test", options=options)
            await transport.connect()
            return mock_open_process.call_args_list[-1].kwargs["env"]

    return anyio.run(_connect)


@pytest.fixture
def _no_ambient_label():
    without_labels = {
        key: value
        for key, value in os.environ.items()
        if key not in (EXPLICIT_CLIENT_APP_ENV, DERIVED_CLIENT_APP_ENV)
    }
    with patch.dict(os.environ, without_labels, clear=True):
        yield


@pytest.mark.usefixtures("_no_ambient_label")
class TestPrecedence:
    @staticmethod
    def _options(**kwargs) -> ClaudeAgentOptions:
        return ClaudeAgentOptions(cli_path="/usr/bin/claude", **kwargs)

    def test_passes_a_derived_label_apart_from_the_explicit_variable(self):
        env = _env_passed_to_subprocess(self._options())
        assert env[DERIVED_CLIENT_APP_ENV] == "my-agent/1.2.3"
        assert EXPLICIT_CLIENT_APP_ENV not in env

    def test_sets_nothing_when_nothing_is_derived(self):
        env = _env_passed_to_subprocess(self._options(), derived=None)
        assert DERIVED_CLIENT_APP_ENV not in env
        assert EXPLICIT_CLIENT_APP_ENV not in env

    def test_client_app_is_explicit_and_stops_derivation(self):
        env = _env_passed_to_subprocess(self._options(client_app="chosen/1.0"))
        assert env[EXPLICIT_CLIENT_APP_ENV] == "chosen/1.0"
        assert DERIVED_CLIENT_APP_ENV not in env

    def test_client_app_wins_over_options_env(self):
        env = _env_passed_to_subprocess(
            self._options(
                client_app="chosen/1.0",
                env={EXPLICIT_CLIENT_APP_ENV: "from-env"},
            )
        )
        assert env[EXPLICIT_CLIENT_APP_ENV] == "chosen/1.0"

    def test_options_env_stops_derivation(self):
        env = _env_passed_to_subprocess(
            self._options(env={EXPLICIT_CLIENT_APP_ENV: "from-env"})
        )
        assert env[EXPLICIT_CLIENT_APP_ENV] == "from-env"
        assert DERIVED_CLIENT_APP_ENV not in env

    def test_inherited_env_stops_derivation(self):
        env = _env_passed_to_subprocess(
            self._options(), ambient={EXPLICIT_CLIENT_APP_ENV: "from-parent"}
        )
        assert env[EXPLICIT_CLIENT_APP_ENV] == "from-parent"
        assert DERIVED_CLIENT_APP_ENV not in env

    def test_empty_env_value_is_the_opt_out(self):
        env = _env_passed_to_subprocess(
            self._options(env={EXPLICIT_CLIENT_APP_ENV: ""})
        )
        assert env[EXPLICIT_CLIENT_APP_ENV] == ""
        assert DERIVED_CLIENT_APP_ENV not in env

    def test_empty_client_app_is_the_opt_out(self):
        env = _env_passed_to_subprocess(self._options(client_app=""))
        assert env[EXPLICIT_CLIENT_APP_ENV] == ""
        assert DERIVED_CLIENT_APP_ENV not in env

    def test_replaces_a_derived_label_inherited_from_an_outer_session(self):
        env = _env_passed_to_subprocess(
            self._options(), ambient={DERIVED_CLIENT_APP_ENV: "outer-app"}
        )
        assert env[DERIVED_CLIENT_APP_ENV] == "my-agent/1.2.3"

    def test_removes_an_inherited_derived_label_on_opt_out(self):
        env = _env_passed_to_subprocess(
            self._options(client_app=""),
            ambient={DERIVED_CLIENT_APP_ENV: "outer-app"},
        )
        assert DERIVED_CLIENT_APP_ENV not in env

    def test_derives_nothing_for_a_cli_below_the_minimum(self):
        env = _env_passed_to_subprocess(
            self._options(),
            ambient={DERIVED_CLIENT_APP_ENV: "outer-app"},
            cli_version=b"2.1.499 (Claude Code)",
        )
        assert DERIVED_CLIENT_APP_ENV not in env

    def test_derives_nothing_for_a_cli_of_unknown_version(self):
        env = _env_passed_to_subprocess(self._options(), cli_version=b"unparseable")
        assert DERIVED_CLIENT_APP_ENV not in env

    def test_derives_nothing_when_the_version_check_is_skipped(self):
        env = _env_passed_to_subprocess(
            self._options(), ambient={"CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK": "1"}
        )
        assert DERIVED_CLIENT_APP_ENV not in env

    def test_a_failed_scan_does_not_stop_connect(self):
        with patch(
            "claude_agent_sdk._internal.transport.subprocess_cli"
            ".anyio.to_thread.run_sync",
            side_effect=RuntimeError("can't start new thread"),
        ):
            env = _env_passed_to_subprocess(self._options())
        assert DERIVED_CLIENT_APP_ENV not in env
