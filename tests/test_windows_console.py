"""Tests for Windows console window suppression."""

import sys
from unittest.mock import patch

import pytest

from claude_agent_sdk._internal.transport.subprocess_cli import (
    _should_suppress_console_window,
)


class TestShouldSuppressConsoleWindow:
    """Tests for _should_suppress_console_window helper."""

    def test_returns_false_on_non_windows(self):
        """Should return False on non-Windows platforms."""
        with patch("sys.platform", "linux"):
            result = _should_suppress_console_window()
            assert result is False

    def test_returns_false_on_non_windows_macos(self):
        """Should return False on macOS."""
        with patch("sys.platform", "darwin"):
            result = _should_suppress_console_window()
            assert result is False

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-only test")
    def test_returns_true_when_no_console_on_windows(self):
        """Should return True on Windows when GetConsoleWindow returns 0."""
        # Mock ctypes to simulate no console
        with patch("ctypes.windll.kernel32.GetConsoleWindow") as mock_get_console:
            mock_get_console.return_value = 0  # No console
            result = _should_suppress_console_window()
            assert result is True

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-only test")
    def test_returns_false_when_console_exists_on_windows(self):
        """Should return False on Windows when GetConsoleWindow returns non-zero."""
        # Mock ctypes to simulate console exists
        with patch("ctypes.windll.kernel32.GetConsoleWindow") as mock_get_console:
            mock_get_console.return_value = 12345  # Console handle exists
            result = _should_suppress_console_window()
            assert result is False
