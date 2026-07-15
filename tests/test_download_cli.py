"""Tests for scripts/download_cli.py version validation and install invocation."""

import importlib.util
import os
import subprocess
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

SCRIPT_PATH = Path(__file__).parent.parent / "scripts" / "download_cli.py"

# scripts/ is not a package, so load download_cli.py by path
_spec = importlib.util.spec_from_file_location("download_cli", SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
download_cli = importlib.util.module_from_spec(_spec)
sys.modules["download_cli"] = download_cli
_spec.loader.exec_module(download_cli)

DEV_VERSION = "2.1.146-dev.20260519.t105443.shaece3dab"
ENV_VAR = download_cli.INSTALL_VERSION_ENV_VAR


class TestGetCliVersion:
    """CLAUDE_CLI_VERSION must be a dist-tag or a concrete version.

    The grammar mirrors the installer's own
    (`stable|latest|N.N.N(-suffix)?`), narrowed: anything this accepts but the
    installer rejects is an error deferred to install time, where it surfaces
    behind a retry loop as a misleading download failure.
    """

    def test_default_is_latest(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CLAUDE_CLI_VERSION", raising=False)
        assert download_cli.get_cli_version() == "latest"

    @pytest.mark.parametrize(
        "version",
        [
            "latest",
            # `stable` is a moving tag the installer resolves, like `latest`:
            # allowed here (a download resolves it at install time) and
            # rejected by update_cli_version.py (a pin must name one concrete
            # build).
            "stable",
            "1.2.3",
            "2.1.195",
            DEV_VERSION,
            "1.2.3-beta.1",
            "1.2.3-rc.1+build.4",
        ],
    )
    def test_accepted(self, monkeypatch: pytest.MonkeyPatch, version: str) -> None:
        monkeypatch.setenv("CLAUDE_CLI_VERSION", version)
        assert download_cli.get_cli_version() == version

    @pytest.mark.parametrize(
        ("version", "suggestion"),
        [
            ("LATEST", "latest"),
            ("Latest", "latest"),
            ("STABLE", "stable"),
            ("Stable", "stable"),
        ],
    )
    def test_dist_tags_are_case_sensitive(
        self, monkeypatch: pytest.MonkeyPatch, version: str, suggestion: str
    ) -> None:
        """The installer's own grammar is case-sensitive, so "Latest" is not a
        tag it would resolve. Reject it -- naming the spelling that works --
        rather than quietly turning it into a live install."""
        monkeypatch.setenv("CLAUDE_CLI_VERSION", version)
        with pytest.raises(ValueError, match="Invalid CLAUDE_CLI_VERSION") as excinfo:
            download_cli.get_cli_version()

        assert f"Did you mean {suggestion!r}?" in str(excinfo.value)

    @pytest.mark.parametrize(
        ("version", "expected"),
        [
            ("1.2.3\n", "1.2.3"),
            ("1.2.3\r\n", "1.2.3"),
            (" 1.2.3 ", "1.2.3"),
            ("\tlatest\n", "latest"),
            (f"  {DEV_VERSION}\r\n", DEV_VERSION),
        ],
    )
    def test_surrounding_whitespace_is_stripped(
        self, monkeypatch: pytest.MonkeyPatch, version: str, expected: str
    ) -> None:
        """A trailing newline from a file read or a CRLF checkout is
        unambiguous in intent; the stripped value is what is used downstream."""
        monkeypatch.setenv("CLAUDE_CLI_VERSION", version)
        assert download_cli.get_cli_version() == expected

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
            "1.0.0 2.0.0",
            "$VERSION",
            "../../etc/passwd",
            "",
            ".1.2.3",
            # Not versions at all -- the old allowlist took these for concrete
            # versions and let the installer reject them 11 seconds later.
            "0",
            "1.2",
            "1.2.3.4",
            "1.2.3+build.4",  # the installer's grammar has no bare "+" suffix
            "v2.1.207",
            "next",
            "beta",
            "nightly",
        ],
    )
    def test_rejected(self, monkeypatch: pytest.MonkeyPatch, version: str) -> None:
        monkeypatch.setenv("CLAUDE_CLI_VERSION", version)
        with pytest.raises(ValueError, match="Invalid CLAUDE_CLI_VERSION"):
            download_cli.get_cli_version()

    @pytest.mark.parametrize("version", ["v2.1.207", "V1.2.3"])
    def test_leading_v_is_named_not_normalized(
        self, monkeypatch: pytest.MonkeyPatch, version: str
    ) -> None:
        """The likeliest typo gets told what to type, and is never silently
        rewritten into a different version than the caller asked for."""
        monkeypatch.setenv("CLAUDE_CLI_VERSION", version)
        with pytest.raises(ValueError) as excinfo:
            download_cli.get_cli_version()

        message = str(excinfo.value)
        assert f"Did you mean {version[1:]!r}?" in message
        assert "no leading 'v'" in message

    @pytest.mark.parametrize("version", ["next", "beta", "nightly"])
    def test_unsupported_dist_tag_is_named(
        self, monkeypatch: pytest.MonkeyPatch, version: str
    ) -> None:
        monkeypatch.setenv("CLAUDE_CLI_VERSION", version)
        with pytest.raises(ValueError) as excinfo:
            download_cli.get_cli_version()

        message = str(excinfo.value)
        assert "not a supported dist-tag" in message
        assert "'latest'" in message
        assert "'stable'" in message

    @pytest.mark.parametrize("version", ["2.1.207", DEV_VERSION, "1.0.0-alpha"])
    def test_pattern_admits_every_published_version_shape(self, version: str) -> None:
        """The tightened pattern is about false *accepts*; it must not start
        rejecting anything real, releases or dev builds."""
        assert download_cli.VERSION_PATTERN.fullmatch(version)

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


SHEBANG_BODY = b"#!/bin/bash\necho install\n"

_RunSideEffect = Callable[..., MagicMock]


def _fake_curl(body: bytes = SHEBANG_BODY) -> _RunSideEffect:
    """A subprocess.run side effect that makes `curl -o PATH` write ``body``."""

    def side_effect(command: list[str], **kwargs: object) -> MagicMock:
        if command[0] == "curl" and "-o" in command:
            Path(command[command.index("-o") + 1]).write_bytes(body)
        return MagicMock()

    return side_effect


@contextmanager
def _unix_install(
    version: str, side_effect: _RunSideEffect | None = None
) -> Iterator[MagicMock]:
    """Pin download_cli() to the Unix path, yielding the patched subprocess.run.

    ``side_effect`` defaults to a curl that writes a real shebang script; pass
    one that raises to exercise a failing command.
    """
    with (
        patch.object(download_cli.platform, "system", return_value="Linux"),
        patch.object(
            download_cli.subprocess,
            "run",
            side_effect=side_effect or _fake_curl(),
        ) as mock_run,
        patch.dict(download_cli.os.environ, {"CLAUDE_CLI_VERSION": version}),
    ):
        yield mock_run


def _run_unix_download(version: str, body: bytes = SHEBANG_BODY) -> list[list[str]]:
    """Run download_cli() on the Unix path, returning the argv of each subprocess."""
    with _unix_install(version, _fake_curl(body)) as mock_run:
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
        with _unix_install("1.2.3") as mock_run:
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

    def test_stable_is_passed_as_an_argument(self) -> None:
        """`latest` is the installer's default and so is expressed by passing
        nothing; every other accepted value, `stable` included, is an argv
        element the installer resolves."""
        _, bash_cmd = _run_unix_download("stable")
        assert bash_cmd == ["bash", bash_cmd[1], "stable"]

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
        with _unix_install("1.2.3") as mock_run:
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
            _unix_install("1.2.3", _fake_curl(body)) as mock_run,
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
            _unix_install("1.2.3", fake_run) as run,
            pytest.raises(SystemExit) as excinfo,
        ):
            download_cli.download_cli()

        assert excinfo.value.code == 1
        # curl attempted 3 times, bash never reached.
        assert [call.args[0][0] for call in run.call_args_list] == ["curl"] * 3

    def test_a_failing_install_retries_the_whole_attempt(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A failing bash re-runs curl too: the script is re-downloaded, so a
        truncated or half-written body is not reused across attempts."""
        error = subprocess.CalledProcessError(
            1, ["bash"], output=b"", stderr=b"curl: (6) could not resolve\n"
        )

        curl = _fake_curl()

        def fake_run(command: list[str], **kwargs: object) -> MagicMock:
            if command[0] == "curl":
                return curl(command, **kwargs)
            raise error

        with (
            _unix_install("1.2.3", fake_run) as run,
            pytest.raises(SystemExit) as excinfo,
        ):
            download_cli.download_cli()

        assert excinfo.value.code == 1
        assert [call.args[0][0] for call in run.call_args_list] == [
            "curl",
            "bash",
        ] * download_cli.MAX_INSTALL_ATTEMPTS
        err = capsys.readouterr().err
        assert f"after {download_cli.MAX_INSTALL_ATTEMPTS} attempts" in err
        assert "could not resolve" in err


SCRIPT_ENV_VAR = download_cli.INSTALL_SCRIPT_ENV_VAR

PS_BODY = b"# install.ps1\nWrite-Host installing\n"


def _fake_irm(body: bytes = PS_BODY) -> _RunSideEffect:
    """A subprocess.run side effect making the download step write ``body``.

    The Windows download step names its output file only in the environment, so
    that is where the fake finds the path to write -- the same indirection the
    real PowerShell command resolves.
    """

    def side_effect(command: list[str], **kwargs: Any) -> MagicMock:
        env = kwargs.get("env") or {}
        path = env.get(SCRIPT_ENV_VAR)
        if path and "Invoke-RestMethod" in command[-1]:
            Path(path).write_bytes(body)
        return MagicMock()

    return side_effect


@contextmanager
def _windows_install(
    version: str, side_effect: _RunSideEffect | None = None
) -> Iterator[MagicMock]:
    """Pin download_cli() to the Windows path, yielding the patched run."""
    with (
        patch.object(download_cli.platform, "system", return_value="Windows"),
        patch.object(
            download_cli.subprocess,
            "run",
            side_effect=side_effect or _fake_irm(),
        ) as mock_run,
        patch.dict(download_cli.os.environ, {"CLAUDE_CLI_VERSION": version}),
    ):
        yield mock_run


def _run_windows_download(version: str, body: bytes = PS_BODY) -> tuple[Any, Any]:
    """Run download_cli() on Windows, returning its (download, install) calls."""
    with _windows_install(version, _fake_irm(body)) as mock_run:
        download_cli.download_cli()

    download_call, install_call = mock_run.call_args_list
    return download_call, install_call


@pytest.mark.usefixtures("no_sleep")
class TestWindowsInstall:
    """The PowerShell branch downloads install.ps1, checks the body, then runs
    it -- the same download / verify / execute shape as the Unix path -- and
    routes the version through the same validation."""

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

    def test_downloads_then_executes_script(self) -> None:
        download_call, install_call = _run_windows_download("1.2.3")

        download_cmd = download_call.args[0]
        assert download_cmd[0] == "powershell"
        assert (
            "Invoke-RestMethod -Uri https://claude.ai/install.ps1" in (download_cmd[-1])
        )

        # The download writes install.ps1 into a temp dir; the install step runs
        # that same file, both reaching it through the environment.
        script_path = download_call.kwargs["env"][SCRIPT_ENV_VAR]
        assert Path(script_path).name == "install.ps1"
        assert install_call.kwargs["env"][SCRIPT_ENV_VAR] == script_path
        assert install_call.args[0][-1].startswith(f"& $env:{SCRIPT_ENV_VAR}")

    def test_script_path_is_never_in_the_powershell_command_text(self) -> None:
        """A temp path can contain spaces (Windows: "C:\\Users\\Foo Bar\\..."),
        so it goes through the environment too, never into the command text."""
        download_call, install_call = _run_windows_download("1.2.3")

        script_path = download_call.kwargs["env"][SCRIPT_ENV_VAR]
        for call in (download_call, install_call):
            for arg in call.args[0]:
                assert script_path not in arg, f"script path interpolated into {arg!r}"

    def test_valid_version_reaches_powershell_command(self) -> None:
        _, install_call = _run_windows_download(DEV_VERSION)
        cmd = install_call.args[0]
        assert cmd[0] == "powershell"
        assert cmd[-1].endswith(f"$env:{ENV_VAR}")
        assert install_call.kwargs["stdin"] is subprocess.DEVNULL

    @pytest.mark.parametrize("version", ["1.2.3", DEV_VERSION])
    def test_version_is_never_in_the_powershell_command_text(
        self, version: str
    ) -> None:
        """Regression guard: the version must reach PowerShell through the
        environment, never spliced into the `-Command` string it parses."""
        for call in _run_windows_download(version):
            for arg in call.args[0]:
                assert version not in arg, f"version interpolated into {arg!r}"

    @pytest.mark.parametrize("version", ["1.2.3", DEV_VERSION])
    def test_version_is_carried_in_the_environment(self, version: str) -> None:
        _, install_call = _run_windows_download(version)

        env = install_call.kwargs["env"]
        assert env is not None, "PowerShell must be given an explicit environment"
        assert env[ENV_VAR] == version
        # The child still needs PATH, SystemRoot, etc. to run at all.
        assert "CLAUDE_CLI_VERSION" in env

    def test_command_references_the_env_var_it_sets(self) -> None:
        """The name in the command text and the name in the environment are the
        same constant, so a rename cannot desynchronize them silently."""
        _, install_call = _run_windows_download("1.2.3")
        assert f"$env:{ENV_VAR}" in install_call.args[0][-1]
        assert ENV_VAR in install_call.kwargs["env"]

    def test_stdin_is_devnull(self) -> None:
        for call in _run_windows_download("1.2.3"):
            assert call.kwargs["stdin"] is subprocess.DEVNULL

    def test_latest_passes_no_version_argument(self) -> None:
        """`latest` invokes the installer with no argument at all -- not with an
        empty string -- and sets no version in the child's environment."""
        _, install_call = _run_windows_download("latest")

        command = install_call.args[0][-1]
        assert command == f"& $env:{SCRIPT_ENV_VAR}"
        assert ENV_VAR not in command
        assert ENV_VAR not in install_call.kwargs["env"]

    def test_stable_is_passed_as_an_argument(self) -> None:
        _, install_call = _run_windows_download("stable")
        assert install_call.args[0][-1] == (f"& $env:{SCRIPT_ENV_VAR} $env:{ENV_VAR}")
        assert install_call.kwargs["env"][ENV_VAR] == "stable"

    def test_never_pipes_the_response_body_into_iex(self) -> None:
        """Regression guard for the body-integrity gap: an unchecked body must
        never be executed straight off the wire."""
        for version in ("latest", "1.2.3"):
            for call in _run_windows_download(version):
                command = call.args[0][-1]
                assert "iex" not in command
                assert "scriptblock" not in command

    @pytest.mark.parametrize(
        "body",
        [
            b"<!DOCTYPE html>\n<html><body>Not found</body></html>",
            b"\n  <html>oops</html>",
            b'<?xml version="1.0"?><Error/>',
            b"",
            b"   \n\t\n",
        ],
    )
    def test_non_powershell_body_is_rejected_before_execution(
        self, body: bytes, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """claude.ai answers unknown paths with HTTP 200 + HTML, which
        Invoke-RestMethod cannot detect either. Such a body must never be run,
        and -- being deterministic -- must not be retried."""
        with (
            _windows_install("1.2.3", _fake_irm(body)) as mock_run,
            pytest.raises(SystemExit) as excinfo,
        ):
            download_cli.download_cli()

        assert excinfo.value.code == 1
        assert "Refusing to execute it" in capsys.readouterr().err
        # The download ran once; the installer never, and nothing was retried.
        assert len(mock_run.call_args_list) == 1

    @pytest.mark.parametrize(
        "body",
        [
            PS_BODY,
            # A UTF-8 BOM is legal, and common, in a real .ps1.
            b"\xef\xbb\xbf# install.ps1\nWrite-Host hi\n",
            # A block comment opens with '<' -- and about_Comment_Based_Help
            # puts exactly such a help block at the top of a script, ahead of
            # param(), which is what real installers do. The '<' check must not
            # mistake it for an HTML error page.
            b"<#\n.SYNOPSIS\nInstalls Claude Code.\n#>\nparam([string]$Version)\n",
            b"\xef\xbb\xbf\r\n<#\n.SYNOPSIS\nInstalls Claude Code.\n#>\n",
        ],
    )
    def test_powershell_body_is_executed(self, body: bytes) -> None:
        """A valid installer body reaches PowerShell."""
        download_call, install_call = _run_windows_download("1.2.3", body=body)
        assert "Invoke-RestMethod" in download_call.args[0][-1]
        assert install_call.args[0][-1].startswith(f"& $env:{SCRIPT_ENV_VAR}")


@pytest.mark.usefixtures("no_sleep")
class TestMissingBinaryFailsFast:
    """A command that cannot be started at all is deterministic.

    The Unix path exec's `curl` and `bash` directly, so a missing binary raises
    FileNotFoundError rather than exiting 127 through a shell. That must be a
    one-line error and an exit 1 -- not a traceback, and not three attempts and
    a misleading "Error downloading CLI after 3 attempts".
    """

    def _missing(self, name: str) -> _RunSideEffect:
        def side_effect(command: list[str], **kwargs: object) -> MagicMock:
            raise FileNotFoundError(2, "No such file or directory", name)

        return side_effect

    def test_missing_curl_is_not_retried(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with (
            _unix_install("1.2.3", self._missing("curl")) as mock_run,
            pytest.raises(SystemExit) as excinfo,
        ):
            download_cli.download_cli()

        assert excinfo.value.code == 1
        assert len(mock_run.call_args_list) == 1
        err = capsys.readouterr().err
        assert "could not run the install command" in err
        assert "curl" in err
        assert "after 3 attempts" not in err

    def test_missing_bash_is_not_retried(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        curl = _fake_curl()

        def side_effect(command: list[str], **kwargs: object) -> MagicMock:
            if command[0] == "curl":
                return curl(command, **kwargs)
            raise FileNotFoundError(2, "No such file or directory", "bash")

        with (
            _unix_install("1.2.3", side_effect) as mock_run,
            pytest.raises(SystemExit) as excinfo,
        ):
            download_cli.download_cli()

        assert excinfo.value.code == 1
        assert [call.args[0][0] for call in mock_run.call_args_list] == ["curl", "bash"]
        assert "could not run the install command" in capsys.readouterr().err

    def test_missing_powershell_is_not_retried(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with (
            _windows_install("1.2.3", self._missing("powershell")) as mock_run,
            pytest.raises(SystemExit) as excinfo,
        ):
            download_cli.download_cli()

        assert excinfo.value.code == 1
        assert len(mock_run.call_args_list) == 1
        assert "could not run the install command" in capsys.readouterr().err

    def test_other_os_errors_are_not_retried_either(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """PermissionError (a noexec tmpdir, say) is just as deterministic."""

        def side_effect(command: list[str], **kwargs: object) -> MagicMock:
            raise PermissionError(13, "Permission denied", "curl")

        with (
            _unix_install("1.2.3", side_effect) as mock_run,
            pytest.raises(SystemExit) as excinfo,
        ):
            download_cli.download_cli()

        assert excinfo.value.code == 1
        assert len(mock_run.call_args_list) == 1
        assert "could not run the install command" in capsys.readouterr().err


class TestCommandLine:
    """The script itself reports a bad version rather than raising through.

    build_wheel.py runs this file as `python scripts/download_cli.py`, so an
    uncaught ValueError would surface as a traceback in a build log. Only the
    rejected path is exercised here: it fails in get_cli_version() before any
    curl or installer runs, so the test touches no network.
    """

    def _run(self, version: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT_PATH)],
            env={**os.environ, "CLAUDE_CLI_VERSION": version},
            capture_output=True,
            text=True,
        )

    def test_invalid_version_exits_nonzero_without_a_traceback(self) -> None:
        result = self._run("1.0.0; id")

        assert result.returncode == 1
        assert "Invalid CLAUDE_CLI_VERSION" in result.stderr
        assert "Traceback" not in result.stderr

    def test_error_names_the_offending_value(self) -> None:
        """Named in the one-line message, not merely in a traceback frame.

        Anchored to the start of a line: an uncaught raise renders the same
        text under `ValueError: `, which *contains* `Error: ` as a substring,
        so a plain `in` check would pass on the very traceback this guards
        against. Scanning lines rather than stderr as a whole also keeps it
        immune to any warning Python prints first.
        """
        stderr = self._run("1.0.0; id").stderr

        (error_line,) = [
            line for line in stderr.splitlines() if line.startswith("Error: ")
        ]
        assert "1.0.0; id" in error_line

    def test_nothing_is_installed_before_the_version_is_rejected(self) -> None:
        """The banner prints, then it bails -- no download is announced."""
        result = self._run("--help")

        assert result.returncode == 1
        assert "Downloading Claude Code CLI version" not in result.stdout
