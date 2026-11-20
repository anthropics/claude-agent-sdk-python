"""Tests for combining the sandbox option with the settings option."""

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from claude_agent_sdk import CLIConnectionError, SandboxSettings
from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport
from claude_agent_sdk.types import ClaudeAgentOptions

DEFAULT_CLI_PATH = "/usr/bin/claude"


def settings_arg(**kwargs: Any) -> dict[str, Any]:
    """Build the command for these options and return the parsed --settings value."""
    transport = SubprocessCLITransport(
        prompt="test", options=ClaudeAgentOptions(cli_path=DEFAULT_CLI_PATH, **kwargs)
    )
    cmd = transport._build_command()
    return json.loads(cmd[cmd.index("--settings") + 1])


def merged_sandbox(inline: Any, option: SandboxSettings) -> dict[str, Any]:
    """Return the sandbox block sent when both settings and sandbox are set."""
    return settings_arg(settings=json.dumps({"sandbox": inline}), sandbox=option)[
        "sandbox"
    ]


class TestSandboxMerge:
    def test_keys_only_in_settings_are_kept(self):
        result = merged_sandbox(
            {
                "enabled": True,
                "excludedCommands": ["docker"],
                "network": {"allowLocalBinding": True},
                "filesystem": {"allowWrite": ["/tmp/out"]},
            },
            {"network": {"allowedDomains": ["example.com"]}},
        )
        assert result == {
            "enabled": True,
            "excludedCommands": ["docker"],
            "network": {"allowLocalBinding": True, "allowedDomains": ["example.com"]},
            "filesystem": {"allowWrite": ["/tmp/out"]},
        }

    def test_allow_unsandboxed_commands_false_from_settings_is_kept(self):
        result = merged_sandbox(
            {"enabled": True, "allowUnsandboxedCommands": False}, {"enabled": True}
        )
        assert result["allowUnsandboxedCommands"] is False

    def test_other_settings_are_kept(self):
        parsed = settings_arg(
            settings=json.dumps(
                {"permissions": {"deny": ["Bash(rm:*)"]}, "sandbox": {"enabled": False}}
            ),
            sandbox={"enabled": True},
        )
        assert parsed == {
            "permissions": {"deny": ["Bash(rm:*)"]},
            "sandbox": {"enabled": True},
        }

    def test_option_value_replaces_settings_value(self):
        result = merged_sandbox(
            {
                "enabled": False,
                "excludedCommands": ["docker", "git"],
                "network": {"allowedDomains": ["a.example"], "allowLocalBinding": True},
                "filesystem": {"allowWrite": ["/a"], "allowRead": ["/b"]},
                "ignoreViolations": {"*": ["/x"]},
            },
            {
                "enabled": True,
                "excludedCommands": ["git"],
                "network": {
                    "allowedDomains": ["b.example"],
                    "allowLocalBinding": False,
                },
                "filesystem": {"allowWrite": ["/c"], "allowRead": []},
                "ignoreViolations": {"npm": ["/y"]},
            },
        )
        assert result["enabled"] is True
        assert result["excludedCommands"] == ["git"]
        assert result["network"] == {
            "allowedDomains": ["b.example"],
            "allowLocalBinding": False,
        }
        assert result["filesystem"] == {"allowWrite": ["/c"], "allowRead": []}
        assert result["ignoreViolations"] == {"*": ["/x"], "npm": ["/y"]}

    def test_option_list_for_an_ignore_violations_command_replaces_settings_list(self):
        result = merged_sandbox(
            {"ignoreViolations": {"*": ["/x"], "npm": ["/old", "/y"]}},
            {"ignoreViolations": {"npm": ["/y"], "git": ["/z"]}},
        )
        assert result["ignoreViolations"] == {
            "*": ["/x"],
            "npm": ["/y"],
            "git": ["/z"],
        }

    def test_none_in_option_keeps_settings_value(self):
        option: dict[str, Any] = {
            "enabled": None,
            "network": {"allowedDomains": None, "allowLocalBinding": True},
        }
        result = merged_sandbox(
            {"enabled": True, "network": {"allowedDomains": ["a.example"]}},
            option,
        )
        assert result == {
            "enabled": True,
            "network": {"allowedDomains": ["a.example"], "allowLocalBinding": True},
        }

    def test_none_without_a_settings_value_is_left_out(self):
        option: dict[str, Any] = {
            "enabled": True,
            "network": {"allowedDomains": None, "allowLocalBinding": True},
            "ripgrep": {"command": "/usr/bin/rg", "args": None},
            "credentials": {
                "files": [{"path": "~/.netrc", "mode": "deny", "extract": None}]
            },
            "excludedCommands": ["docker", None],
        }
        assert settings_arg(sandbox=option)["sandbox"] == {
            "enabled": True,
            "excludedCommands": ["docker"],
            "network": {"allowLocalBinding": True},
            "ripgrep": {"command": "/usr/bin/rg"},
            "credentials": {"files": [{"path": "~/.netrc", "mode": "deny"}]},
        }
        result = merged_sandbox(
            {"credentials": {"files": [{"path": "~/.a", "mode": "deny"}]}}, option
        )
        assert result["credentials"] == {
            "files": [
                {"path": "~/.a", "mode": "deny"},
                {"path": "~/.netrc", "mode": "deny"},
            ]
        }

    def test_restriction_lists_are_combined(self):
        result = merged_sandbox(
            {
                "filesystem": {"denyRead": ["~/.ssh", "~/.aws"], "denyWrite": ["/etc"]},
                "network": {"deniedDomains": ["bad.example", "old.example"]},
                "credentials": {
                    "files": [{"path": "~/.netrc", "mode": "deny"}],
                    "envVars": [{"name": "TOKEN", "mode": "deny"}],
                },
            },
            {
                "filesystem": {
                    "denyRead": ["~/.aws", "~/.gnupg"],
                    "denyWrite": ["/usr"],
                },
                "network": {"deniedDomains": ["bad.example", "worse.example"]},
                "credentials": {
                    "files": [{"path": "~/.netrc", "mode": "deny"}],
                    "envVars": [{"name": "KEY", "mode": "deny"}],
                },
            },
        )
        assert result["filesystem"] == {
            "denyRead": ["~/.ssh", "~/.aws", "~/.gnupg"],
            "denyWrite": ["/etc", "/usr"],
        }
        assert result["network"] == {
            "deniedDomains": ["bad.example", "old.example", "worse.example"]
        }
        assert result["credentials"] == {
            "files": [
                {"path": "~/.netrc", "mode": "deny"},
                {"path": "~/.netrc", "mode": "deny"},
            ],
            "envVars": [
                {"name": "TOKEN", "mode": "deny"},
                {"name": "KEY", "mode": "deny"},
            ],
        }

    def test_aws_pairs_are_replaced_not_combined(self):
        result = merged_sandbox(
            {"credentials": {"awsPairs": [{"accessKeyIdVar": "A"}]}},
            {"credentials": {"awsPairs": [{"accessKeyIdVar": "B"}]}},
        )
        assert result["credentials"]["awsPairs"] == [{"accessKeyIdVar": "B"}]

    def test_tuples_in_option_follow_the_same_rules(self):
        option: dict[str, Any] = {
            "filesystem": {"denyRead": ("~/.aws",)},
            "network": {"deniedDomains": ("bad.example",)},
            "credentials": {"files": ({"path": "~/.netrc", "mode": "deny"},)},
        }
        result = merged_sandbox(
            {
                "filesystem": {"denyRead": ["~/.ssh"]},
                "network": {"httpProxyPort": 8080},
            },
            option,
        )
        assert result["filesystem"]["denyRead"] == ["~/.ssh", "~/.aws"]
        assert "httpProxyPort" not in result["network"]
        result = merged_sandbox(
            {"filesystem": {"disabled": True}},
            {"credentials": option["credentials"]},
        )
        assert "disabled" not in result["filesystem"]

    def test_restriction_list_replaces_a_settings_value_that_is_not_a_list(self):
        result = merged_sandbox(
            {"filesystem": {"denyRead": "~/.ssh"}},
            {"filesystem": {"denyRead": ["~/.aws"]}},
        )
        assert result["filesystem"] == {"denyRead": ["~/.aws"]}

    def test_ripgrep_and_tls_terminate_are_replaced_whole(self):
        result = merged_sandbox(
            {
                "ripgrep": {"command": "/opt/rg", "args": ["--hidden"]},
                "network": {
                    "tlsTerminate": {"caCertPath": "/a.pem", "caKeyPath": "/a.key"}
                },
            },
            {
                "ripgrep": {"command": "/usr/bin/rg"},
                "network": {"tlsTerminate": {"caCertPath": "/b.pem"}},
            },
        )
        assert result["ripgrep"] == {"command": "/usr/bin/rg"}
        assert result["network"]["tlsTerminate"] == {"caCertPath": "/b.pem"}

    @pytest.mark.parametrize("inline", [None, "on", ["enabled"], 1])
    def test_settings_sandbox_that_is_not_an_object_is_ignored(self, inline: Any):
        assert merged_sandbox(inline, {"enabled": True}) == {"enabled": True}

    def test_caller_option_is_not_changed(self):
        option: SandboxSettings = {
            "enabled": True,
            "filesystem": {"denyRead": ["~/.aws"]},
            "network": {"allowedDomains": ["example.com"]},
            "credentials": {"files": [{"path": "~/.netrc", "mode": "deny"}]},
            "ripgrep": {"command": "/usr/bin/rg", "args": ["--hidden"]},
        }
        before = copy.deepcopy(option)
        transport = SubprocessCLITransport(
            prompt="test",
            options=ClaudeAgentOptions(
                cli_path=DEFAULT_CLI_PATH,
                settings=json.dumps(
                    {
                        "sandbox": {
                            "filesystem": {"denyRead": ["~/.ssh"], "disabled": True},
                            "network": {"httpProxyPort": 8080},
                            "credentials": {
                                "files": [{"path": "~/.a", "mode": "deny"}]
                            },
                        }
                    }
                ),
                sandbox=option,
            ),
        )
        transport._build_command()
        transport._build_command()
        assert option == before

    @pytest.mark.parametrize("settings", [None, "{}", '{"sandbox": {}}'])
    def test_option_copied_whole_is_not_changed(self, settings: str | None):
        option: SandboxSettings = {
            "network": {
                "allowedDomains": ["example.com"],
                "httpProxyPort": None,
                "socksProxyPort": None,
            },
            "filesystem": {"denyRead": ["~/.ssh"], "disabled": None},
            "credentials": {"files": [{"path": "~/.netrc", "mode": "deny"}]},
        }
        before = copy.deepcopy(option)
        transport = SubprocessCLITransport(
            prompt="test",
            options=ClaudeAgentOptions(
                cli_path=DEFAULT_CLI_PATH, settings=settings, sandbox=option
            ),
        )
        cmd = transport._build_command()
        assert option == before
        assert json.loads(cmd[cmd.index("--settings") + 1])["sandbox"] == {
            "network": {"allowedDomains": ["example.com"]},
            "filesystem": {"denyRead": ["~/.ssh"]},
            "credentials": {"files": [{"path": "~/.netrc", "mode": "deny"}]},
        }

    def test_sandbox_only_is_unchanged(self):
        assert settings_arg(sandbox={"enabled": True}) == {"sandbox": {"enabled": True}}

    def test_fail_if_unavailable_is_not_added(self):
        result = merged_sandbox({"network": {}}, {"enabled": True})
        assert "failIfUnavailable" not in result


class TestSandboxFilesystemDisabled:
    @pytest.mark.parametrize(
        "option",
        [
            {"filesystem": {"denyRead": ["~/.ssh"]}},
            {"filesystem": {}},
            {"credentials": {"files": [{"path": "~/.netrc", "mode": "deny"}]}},
            {
                "credentials": {
                    "files": [
                        {"path": "~/.a", "mode": "mask"},
                        {"path": "~/.b", "mode": "deny"},
                    ]
                }
            },
        ],
    )
    def test_settings_disabled_is_dropped_when_option_configures_filesystem(
        self, option: SandboxSettings
    ):
        result = merged_sandbox(
            {"filesystem": {"disabled": True, "allowWrite": ["/tmp"]}}, option
        )
        assert "disabled" not in result["filesystem"]
        assert result["filesystem"]["allowWrite"] == ["/tmp"]

    @pytest.mark.parametrize(
        "option",
        [
            {"enabled": True},
            {"network": {"allowedDomains": ["example.com"]}},
            {"credentials": {"files": [{"path": "~/.netrc", "mode": "mask"}]}},
            {"credentials": {"envVars": [{"name": "TOKEN", "mode": "deny"}]}},
        ],
    )
    def test_settings_disabled_is_kept_when_option_leaves_filesystem_alone(
        self, option: SandboxSettings
    ):
        result = merged_sandbox({"filesystem": {"disabled": True}}, option)
        assert result["filesystem"] == {"disabled": True}

    def test_settings_disabled_is_kept_when_option_filesystem_is_none(self):
        option: dict[str, Any] = {"filesystem": None}
        result = merged_sandbox({"filesystem": {"disabled": True}}, option)
        assert result["filesystem"] == {"disabled": True}

    @pytest.mark.parametrize("value", [True, False])
    def test_option_disabled_wins(self, value: bool):
        result = merged_sandbox(
            {"filesystem": {"disabled": True}},
            {"filesystem": {"disabled": value, "denyRead": ["~/.ssh"]}},
        )
        assert result["filesystem"] == {"disabled": value, "denyRead": ["~/.ssh"]}

    def test_only_the_boolean_true_is_dropped(self):
        result = merged_sandbox(
            {"filesystem": {"disabled": 1}}, {"filesystem": {"denyRead": ["~/.ssh"]}}
        )
        assert result["filesystem"] == {"disabled": 1, "denyRead": ["~/.ssh"]}


class TestSandboxProxyPorts:
    INLINE = {"network": {"httpProxyPort": 8080, "socksProxyPort": 1080}}

    @pytest.mark.parametrize(
        "network",
        [
            {"allowedDomains": ["example.com"]},
            {"deniedDomains": ["bad.example"]},
            {"strictAllowlist": True},
            {"allowedDomains": []},
            {"allowedDomains": [], "strictAllowlist": True},
        ],
    )
    def test_settings_ports_are_dropped_when_option_limits_domains(
        self, network: dict[str, Any]
    ):
        result = merged_sandbox(self.INLINE, {"network": network})
        assert "httpProxyPort" not in result["network"]
        assert "socksProxyPort" not in result["network"]

    @pytest.mark.parametrize(
        "network",
        [
            {"allowLocalBinding": True},
            {"strictAllowlist": False},
            {"strictAllowlist": 1},
            {"deniedDomains": []},
            {"allowedDomains": None},
            {"allowedDomains": None, "deniedDomains": None},
        ],
    )
    def test_settings_ports_are_kept_otherwise(self, network: dict[str, Any]):
        result = merged_sandbox(self.INLINE, {"network": network})
        assert result["network"]["httpProxyPort"] == 8080
        assert result["network"]["socksProxyPort"] == 1080

    def test_ports_set_by_option_are_kept(self):
        result = merged_sandbox(
            self.INLINE,
            {"network": {"allowedDomains": ["example.com"], "httpProxyPort": 9090}},
        )
        assert result["network"] == {
            "allowedDomains": ["example.com"],
            "httpProxyPort": 9090,
        }
        result = merged_sandbox(
            self.INLINE,
            {"network": {"deniedDomains": ["bad.example"], "socksProxyPort": 9091}},
        )
        assert result["network"] == {
            "deniedDomains": ["bad.example"],
            "socksProxyPort": 9091,
        }

    def test_option_without_network_keeps_ports(self):
        result = merged_sandbox(self.INLINE, {"enabled": True})
        assert result["network"] == self.INLINE["network"]


class TestSettingsFileWithSandbox:
    def write_settings(self, path: Path, content: Any) -> None:
        path.write_text(json.dumps(content), encoding="utf-8")

    def test_file_contents_are_merged(self, tmp_path: Path):
        settings_file = tmp_path / "settings.json"
        self.write_settings(
            settings_file,
            {
                "permissions": {"deny": ["Read(//etc/**)"]},
                "sandbox": {
                    "filesystem": {"denyRead": ["~/.ssh"], "disabled": True},
                    "network": {"httpProxyPort": 8080},
                },
            },
        )
        parsed = settings_arg(
            settings=str(settings_file),
            sandbox={
                "enabled": True,
                "filesystem": {"denyRead": ["~/.aws"]},
                "network": {"allowedDomains": ["example.com"]},
            },
        )
        assert parsed == {
            "permissions": {"deny": ["Read(//etc/**)"]},
            "sandbox": {
                "enabled": True,
                "filesystem": {"denyRead": ["~/.ssh", "~/.aws"]},
                "network": {"allowedDomains": ["example.com"]},
            },
        }

    def test_allow_unsandboxed_commands_false_from_file_is_kept(self, tmp_path: Path):
        settings_file = tmp_path / "settings.json"
        self.write_settings(
            settings_file,
            {"sandbox": {"enabled": True, "allowUnsandboxedCommands": False}},
        )
        parsed = settings_arg(settings=str(settings_file), sandbox={"enabled": True})
        assert parsed["sandbox"]["allowUnsandboxedCommands"] is False

    def test_file_without_a_sandbox_block_gets_the_option(self, tmp_path: Path):
        settings_file = tmp_path / "settings.json"
        self.write_settings(settings_file, {"permissions": {"deny": ["Bash"]}})
        parsed = settings_arg(settings=str(settings_file), sandbox={"enabled": True})
        assert parsed == {
            "permissions": {"deny": ["Bash"]},
            "sandbox": {"enabled": True},
        }

    @pytest.mark.parametrize("content", ["[1, 2]", '"text"', "1", "null"])
    def test_file_that_is_not_an_object_raises(self, tmp_path: Path, content: str):
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(content, encoding="utf-8")
        with pytest.raises(CLIConnectionError, match="must contain a JSON object"):
            settings_arg(settings=str(settings_file), sandbox={"enabled": True})


class TestSettingsInExtraArgs:
    def command(self, **kwargs: Any) -> list[str]:
        transport = SubprocessCLITransport(
            prompt="test",
            options=ClaudeAgentOptions(cli_path=DEFAULT_CLI_PATH, **kwargs),
        )
        return transport._build_command()

    def test_sandbox_is_merged_into_extra_args_settings(self):
        cmd = self.command(
            extra_args={"settings": '{"model": "a", "sandbox": {"enabled": false}}'},
            sandbox={"enabled": True},
        )
        assert cmd.count("--settings") == 1
        assert not any(arg.startswith("--settings=") for arg in cmd)
        assert json.loads(cmd[cmd.index("--settings") + 1]) == {
            "model": "a",
            "sandbox": {"enabled": True},
        }

    def test_extra_args_settings_file_is_merged(self, tmp_path: Path):
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({"model": "a"}), encoding="utf-8")
        cmd = self.command(
            extra_args={"settings": str(settings_file)}, sandbox={"enabled": True}
        )
        assert cmd.count("--settings") == 1
        assert json.loads(cmd[cmd.index("--settings") + 1]) == {
            "model": "a",
            "sandbox": {"enabled": True},
        }

    def test_settings_option_takes_precedence_over_extra_args(self):
        cmd = self.command(
            settings='{"model": "a"}',
            extra_args={"settings": '{"model": "b"}', "debug-to-stderr": None},
            sandbox={"enabled": True},
        )
        assert cmd.count("--settings") == 1
        assert json.loads(cmd[cmd.index("--settings") + 1]) == {
            "model": "a",
            "sandbox": {"enabled": True},
        }
        assert "--debug-to-stderr" in cmd

    def test_relative_extra_args_path_is_read_from_cwd(self, tmp_path: Path):
        (tmp_path / "settings.json").write_text('{"model": "a"}', encoding="utf-8")
        cmd = self.command(
            extra_args={"settings": "settings.json"},
            sandbox={"enabled": True},
            cwd=tmp_path,
        )
        assert json.loads(cmd[cmd.index("--settings") + 1]) == {
            "model": "a",
            "sandbox": {"enabled": True},
        }

    def test_extra_args_settings_path_object_is_read(self, tmp_path: Path):
        settings_file = tmp_path / "settings.json"
        settings_file.write_text('{"model": "a"}', encoding="utf-8")
        cmd = self.command(
            extra_args={"settings": settings_file}, sandbox={"enabled": True}
        )
        assert json.loads(cmd[cmd.index("--settings") + 1])["model"] == "a"

    def test_extra_args_settings_that_cannot_be_loaded_raise(self, tmp_path: Path):
        with pytest.raises(CLIConnectionError, match="Failed to load settings"):
            self.command(
                extra_args={"settings": str(tmp_path / "missing.json")},
                sandbox={"enabled": True},
            )

    def test_valueless_extra_args_settings_is_not_sent(self):
        cmd = self.command(extra_args={"settings": None}, sandbox={"enabled": True})
        assert cmd.count("--settings") == 1
        assert json.loads(cmd[cmd.index("--settings") + 1]) == {
            "sandbox": {"enabled": True}
        }

    def test_extra_args_settings_without_sandbox_is_unchanged(self):
        cmd = self.command(
            settings='{"model": "a"}', extra_args={"settings": '{"model": "b"}'}
        )
        values = [cmd[i + 1] for i, arg in enumerate(cmd) if arg == "--settings"]
        assert values == ['{"model": "a"}', '{"model": "b"}']
