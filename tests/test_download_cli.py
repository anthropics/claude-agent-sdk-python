"""Tests for scripts/download_cli.py version validation and install invocation."""

import importlib.util
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# scripts/ is not a package, so load download_cli.py by path
_spec = importlib.util.spec_from_file_location(
    "download_cli",
    Path(__file__).parent.parent / "scripts" / "download_cli.py",
)
assert _spec is not None and _spec.loader is not None
download_cli = importlib.util.module_from_spec(_spec)
sys.modules["download_cli"] = download_cli
_spec.loader.exec_module(download_cli)

DEV_VERSION = "2.1.146-dev.20260519.t105443.shaece3dab"
ENV_VAR = download_cli.INSTALL_VERSION_ENV_VAR


class TestGetCliVersion:
    """CLAUDE_CLI_VERSION must be 'latest' or a plain version token."""

    def test_default_is_latest(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CLAUDE_CLI_VERSION", raising=False)
        assert download_cli.get_cli_version() == "latest"

    @pytest.mark.parametrize(
        "version",
        [
            "latest",
            "1.2.3",
            "2.1.195",
            DEV_VERSION,
            "1.2.3+build.4",
            "0",
        ],
    )
    def test_accepted(self, monkeypatch: pytest.MonkeyPatch, version: str) -> None:
        monkeypatch.setenv("CLAUDE_CLI_VERSION", version)
        assert download_cli.get_cli_version() == version

    @pytest.mark.parametrize(
        "version",
        [
            "1.0.0; touch /tmp/pwned",
            "--help",
            "-s",
            "$(id)",
            "`id`",
            "1.0.0 && id",
            "1.0.0 | id",
            "1.0.0\nid",
            "1.0.0\n",
            "1.0.0 2.0.0",
            "$VERSION",
            "../../etc/passwd",
            "",
            ".1.2.3",
        ],
    )
    def test_rejected(self, monkeypatch: pytest.MonkeyPatch, version: str) -> None:
        monkeypatch.setenv("CLAUDE_CLI_VERSION", version)
        with pytest.raises(ValueError, match="Invalid CLAUDE_CLI_VERSION"):
            download_cli.get_cli_version()

    def test_error_names_the_offending_value(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CLAUDE_CLI_VERSION", "1.0.0; id")
        with pytest.raises(ValueError) as excinfo:
            download_cli.get_cli_version()
        assert "1.0.0; id" in str(excinfo.value)

    @pytest.mark.parametrize(
        "char",
        [" ", ";", "$", "`", '"', "'", "(", ")", "&", "|", "\n", "\r"],
    )
    def test_version_pattern_admits_no_shell_metacharacters(self, char: str) -> None:
        """VERSION_PATTERN must never admit a shell metacharacter.

        No install path depends on this any more: the Unix path passes the
        version as its own argv element, and the Windows path hands it to
        PowerShell in the environment rather than in the command text. The
        allowlist is still the invariant we want -- a version that can express
        `;` or `$` is not a version -- so keep it as defense in depth. Widening
        the pattern (to permit `_` or `~`, say) must not let any of these
        through.
        """
        assert not download_cli.VERSION_PATTERN.fullmatch(char)
        assert not download_cli.VERSION_PATTERN.fullmatch(f"1.2.3{char}")
        assert not download_cli.VERSION_PATTERN.fullmatch(f"1.2.3{char}whoami")

    def test_version_pattern_is_unanchored(self) -> None:
        """The pattern must stay unanchored so a future swap of fullmatch() for
        match() fails loudly on a prefix, rather than silently reintroducing
        the trailing-newline bypass that `^...$` + match() allows."""
        assert "^" not in download_cli.VERSION_PATTERN.pattern
        assert "$" not in download_cli.VERSION_PATTERN.pattern
        assert not download_cli.VERSION_PATTERN.fullmatch("1.0.0\n")

    def test_script_validates_when_run_directly(self, tmp_path: Path) -> None:
        """build_wheel.py runs this file as a subprocess, so the shared
        validation module must import without scripts/ being a package.

        PATH is emptied so that if validation ever regresses this reaches a
        missing `curl` instead of really installing the CLI; sys.executable is
        absolute, so python itself still starts. The timeout bounds the retry
        sleeps on that path.
        """
        script = Path(__file__).parent.parent / "scripts" / "download_cli.py"
        result = subprocess.run(
            [sys.executable, str(script)],
            env=os.environ | {"CLAUDE_CLI_VERSION": "1.0.0; id", "PATH": str(tmp_path)},
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode != 0
        assert "Invalid CLAUDE_CLI_VERSION" in result.stderr
        assert "ModuleNotFoundError" not in result.stderr


@pytest.fixture
def no_sleep() -> Iterator[None]:
    """Skip the jitter and retry sleeps."""
    with patch.object(download_cli.time, "sleep"):
        yield


def _fake_curl(body: bytes = b"#!/bin/bash\necho install\n") -> object:
    """A subprocess.run side effect that makes `curl -o PATH` write ``body``."""

    def side_effect(command: list[str], **kwargs: object) -> MagicMock:
        if command[0] == "curl" and "-o" in command:
            Path(command[command.index("-o") + 1]).write_bytes(body)
        return MagicMock()

    return side_effect


def _run_unix_download(
    version: str, body: bytes = b"#!/bin/bash\necho install\n"
) -> list[list[str]]:
    """Run download_cli() on the Unix path, returning the argv of each subprocess."""
    with (
        patch.object(download_cli.platform, "system", return_value="Linux"),
        patch.object(
            download_cli.subprocess, "run", side_effect=_fake_curl(body)
        ) as mock_run,
        patch.dict(download_cli.os.environ, {"CLAUDE_CLI_VERSION": version}),
    ):
        download_cli.download_cli()
    return [call.args[0] for call in mock_run.call_args_list]


@pytest.mark.usefixtures("no_sleep")
class TestUnixInstall:
    """The Unix path downloads install.sh, then executes it without a shell."""

    def test_downloads_then_executes_script(self) -> None:
        curl_cmd, bash_cmd = _run_unix_download("1.2.3")

        assert curl_cmd[0] == "curl"
        assert curl_cmd[-1] == "https://claude.ai/install.sh"
        assert bash_cmd[0] == "bash"

        # curl writes install.sh into a temp dir; bash executes that same file.
        script_path = curl_cmd[curl_cmd.index("-o") + 1]
        assert Path(script_path).name == "install.sh"
        assert bash_cmd[1] == script_path

    def test_retry_flags_preserved(self) -> None:
        curl_cmd, _ = _run_unix_download("1.2.3")
        assert "--retry-all-errors" in curl_cmd
        assert curl_cmd[curl_cmd.index("--retry") + 1] == "5"
        assert curl_cmd[curl_cmd.index("--retry-delay") + 1] == "2"

    @pytest.mark.parametrize("flag", ["f", "s", "S", "L"])
    def test_curl_short_flags_present(self, flag: str) -> None:
        """`install.sh` is a cross-host 302, so -L is required; -f/-s/-S keep the
        original error and quiet behavior. Checked per letter rather than as the
        literal "-fsSL" so splitting the cluster can't silently drop one."""
        curl_cmd, _ = _run_unix_download("1.2.3")
        clusters = [
            arg[1:]
            for arg in curl_cmd
            if arg.startswith("-") and not arg.startswith("--")
        ]
        assert any(flag in cluster for cluster in clusters), (
            f"curl -{flag} missing from {curl_cmd!r}"
        )

    def test_stdin_is_devnull(self) -> None:
        """install.sh runs `claude install`, which branches on `[ -t 0 ]`. The
        old `curl | bash` gave it a pipe; it must never inherit a real TTY."""
        with (
            patch.object(download_cli.platform, "system", return_value="Linux"),
            patch.object(
                download_cli.subprocess, "run", side_effect=_fake_curl()
            ) as mock_run,
            patch.dict(download_cli.os.environ, {"CLAUDE_CLI_VERSION": "1.2.3"}),
        ):
            download_cli.download_cli()

        assert mock_run.call_args_list
        for call in mock_run.call_args_list:
            assert call.kwargs["stdin"] is subprocess.DEVNULL, (
                f"{call.args[0][0]} may inherit the caller's TTY"
            )

    def test_latest_passes_no_version_argument(self) -> None:
        _, bash_cmd = _run_unix_download("latest")
        assert bash_cmd[0] == "bash"
        assert len(bash_cmd) == 2

    @pytest.mark.parametrize("version", ["1.2.3", DEV_VERSION])
    def test_version_is_its_own_argv_element(self, version: str) -> None:
        """Regression guard: the version must never be interpolated into a
        command string, and the Unix path must never invoke a shell."""
        commands = _run_unix_download(version)

        for cmd in commands:
            assert isinstance(cmd, list), f"argv must be a list, got {cmd!r}"
            # No `bash -c <string>` / `sh -c <string>` anywhere.
            assert "-c" not in cmd, f"shell string reintroduced: {cmd!r}"
            # The version may only appear as a standalone argv element.
            for arg in cmd:
                assert arg == version or version not in arg, (
                    f"version interpolated into {arg!r}"
                )

        bash_cmd = commands[-1]
        assert bash_cmd == ["bash", bash_cmd[1], version]

    def test_never_uses_shell_true(self) -> None:
        with (
            patch.object(download_cli.platform, "system", return_value="Linux"),
            patch.object(
                download_cli.subprocess, "run", side_effect=_fake_curl()
            ) as mock_run,
            patch.dict(download_cli.os.environ, {"CLAUDE_CLI_VERSION": "1.2.3"}),
        ):
            download_cli.download_cli()

        for call in mock_run.call_args_list:
            assert call.kwargs.get("shell") is not True
            assert call.kwargs["check"] is True

    @pytest.mark.parametrize(
        "body",
        [
            b"<!DOCTYPE html>\n<html><body>Not found</body></html>",
            b"",
            b"#",
            b"\x7fELF",
        ],
    )
    def test_non_shebang_body_is_rejected_before_bash(
        self, body: bytes, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """claude.ai serves HTTP 200 + HTML for unknown paths, which `curl -f`
        cannot detect. Such a body must never reach bash."""
        with (
            patch.object(download_cli.platform, "system", return_value="Linux"),
            patch.object(
                download_cli.subprocess, "run", side_effect=_fake_curl(body)
            ) as mock_run,
            patch.dict(download_cli.os.environ, {"CLAUDE_CLI_VERSION": "1.2.3"}),
            pytest.raises(SystemExit) as excinfo,
        ):
            download_cli.download_cli()

        assert excinfo.value.code == 1
        assert "does not start with a shebang" in capsys.readouterr().err
        # curl ran once, bash never; the bad body is not retried.
        assert [call.args[0][0] for call in mock_run.call_args_list] == ["curl"]

    def test_shebang_body_is_executed(self) -> None:
        commands = _run_unix_download("1.2.3", body=b"#!/bin/sh\nexit 0\n")
        assert [cmd[0] for cmd in commands] == ["curl", "bash"]

    def test_curl_failure_is_not_masked(self) -> None:
        """A failing download must fail the build, not fall through to bash."""
        error = subprocess.CalledProcessError(1, ["curl"], output=b"", stderr=b"boom")

        def fake_run(command: list[str], **kwargs: object) -> MagicMock:
            if command[0] == "curl":
                raise error
            return MagicMock()

        with (
            patch.object(download_cli.platform, "system", return_value="Linux"),
            patch.object(download_cli.subprocess, "run", side_effect=fake_run) as run,
            patch.dict(download_cli.os.environ, {"CLAUDE_CLI_VERSION": "1.2.3"}),
            pytest.raises(SystemExit) as excinfo,
        ):
            download_cli.download_cli()

        assert excinfo.value.code == 1
        # curl attempted 3 times, bash never reached.
        assert [call.args[0][0] for call in run.call_args_list] == ["curl"] * 3


def _run_windows_download(version: str) -> Any:
    """Run download_cli() on the Windows path, returning the single run() call."""
    with (
        patch.object(download_cli.platform, "system", return_value="Windows"),
        patch.object(download_cli.subprocess, "run") as mock_run,
        patch.dict(download_cli.os.environ, {"CLAUDE_CLI_VERSION": version}),
    ):
        download_cli.download_cli()

    (call,) = mock_run.call_args_list
    return call


@pytest.mark.usefixtures("no_sleep")
class TestWindowsInstall:
    """The PowerShell branch routes through the same validation."""

    def test_rejects_injected_version_before_running_anything(self) -> None:
        with (
            patch.object(download_cli.platform, "system", return_value="Windows"),
            patch.object(download_cli.subprocess, "run") as mock_run,
            patch.dict(
                download_cli.os.environ,
                {"CLAUDE_CLI_VERSION": "1.0.0; Write-Host pwned"},
            ),
            pytest.raises(ValueError, match="Invalid CLAUDE_CLI_VERSION"),
        ):
            download_cli.download_cli()

        mock_run.assert_not_called()

    def test_valid_version_reaches_powershell_command(self) -> None:
        call = _run_windows_download(DEV_VERSION)
        cmd = call.args[0]
        assert cmd[0] == "powershell"
        assert cmd[-1].endswith(f"$env:{ENV_VAR}")
        assert call.kwargs["stdin"] is subprocess.DEVNULL

    @pytest.mark.parametrize("version", ["1.2.3", DEV_VERSION])
    def test_version_is_never_in_the_powershell_command_text(
        self, version: str
    ) -> None:
        """Regression guard: the version must reach PowerShell through the
        environment, never spliced into the `-Command` string it parses."""
        call = _run_windows_download(version)

        for arg in call.args[0]:
            assert version not in arg, f"version interpolated into {arg!r}"

    @pytest.mark.parametrize("version", ["1.2.3", DEV_VERSION])
    def test_version_is_carried_in_the_environment(self, version: str) -> None:
        call = _run_windows_download(version)

        env = call.kwargs["env"]
        assert env is not None, "PowerShell must be given an explicit environment"
        assert env[ENV_VAR] == version
        # The child still needs PATH, SystemRoot, etc. to run at all.
        assert "CLAUDE_CLI_VERSION" in env

    def test_command_references_the_env_var_it_sets(self) -> None:
        """The name in the command text and the name in the environment are the
        same constant, so a rename cannot desynchronize them silently."""
        call = _run_windows_download("1.2.3")
        assert f"$env:{ENV_VAR}" in call.args[0][-1]
        assert ENV_VAR in call.kwargs["env"]

    def test_latest_uses_plain_installer(self) -> None:
        call = _run_windows_download("latest")
        assert call.args[0][-1] == "irm https://claude.ai/install.ps1 | iex"

    def test_latest_passes_no_version_argument(self) -> None:
        """`latest` invokes the installer with no argument at all -- not with an
        empty string -- and sets no version in the child's environment."""
        call = _run_windows_download("latest")

        command = call.args[0][-1]
        assert "scriptblock" not in command
        assert "$env:" not in command
        # env=None inherits ours, so no version is injected there either.
        assert call.kwargs["env"] is None
