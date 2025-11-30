"""End-to-end tests for structured output with real Claude API calls.

These tests verify that the output_schema feature works correctly by making
actual API calls to Claude with JSON Schema validation.
"""

import tempfile

import pytest

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ResultMessage,
    query,
)


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_simple_structured_output():
    """Test structured output with file counting requiring tool use."""

    # Define schema for file analysis
    schema = {
        "type": "object",
        "properties": {
            "file_count": {"type": "number"},
            "has_tests": {"type": "boolean"},
            "test_file_count": {"type": "number"},
        },
        "required": ["file_count", "has_tests"],
    }

    options = ClaudeAgentOptions(
        output_format={"type": "json_schema", "schema": schema},
        permission_mode="acceptEdits",
        cwd=".",  # Use current directory
    )

    # Agent must use Glob/Bash to count files
    result_message = None
    async for message in query(
        prompt="Count how many Python files are in src/claude_agent_sdk/ and check if there are any test files. Use tools to explore the filesystem.",
        options=options,
    ):
        if isinstance(message, ResultMessage):
            result_message = message

    # Verify result
    assert result_message is not None, "No result message received"
    assert not result_message.is_error, f"Query failed: {result_message.result}"
    assert result_message.subtype == "success"

    # Verify structured output is present and valid
    assert result_message.structured_output is not None, "No structured output in result"
    assert "file_count" in result_message.structured_output
    assert "has_tests" in result_message.structured_output
    assert isinstance(result_message.structured_output["file_count"], (int, float))
    assert isinstance(result_message.structured_output["has_tests"], bool)

    # Should find Python files in src/
    assert result_message.structured_output["file_count"] > 0


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_nested_structured_output():
    """Test structured output with nested objects and arrays."""

    # Define a schema with nested structure
    schema = {
        "type": "object",
        "properties": {
            "analysis": {
                "type": "object",
                "properties": {
                    "word_count": {"type": "number"},
                    "character_count": {"type": "number"},
                },
                "required": ["word_count", "character_count"],
            },
            "words": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
        "required": ["analysis", "words"],
    }

    options = ClaudeAgentOptions(
        output_format={"type": "json_schema", "schema": schema},
        permission_mode="acceptEdits",
    )

    result_message = None
    async for message in query(
        prompt="Analyze this text: 'Hello world'. Provide word count, character count, and list of words.",
        options=options,
    ):
        if isinstance(message, ResultMessage):
            result_message = message

    # Verify result
    assert result_message is not None
    assert not result_message.is_error
    assert result_message.structured_output is not None

    # Check nested structure
    output = result_message.structured_output
    assert "analysis" in output
    assert "words" in output
    assert output["analysis"]["word_count"] == 2
    assert output["analysis"]["character_count"] == 11  # "Hello world"
    assert len(output["words"]) == 2


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_structured_output_with_enum():
    """Test structured output with enum constraints requiring code analysis."""

    schema = {
        "type": "object",
        "properties": {
            "has_tests": {"type": "boolean"},
            "test_framework": {
                "type": "string",
                "enum": ["pytest", "unittest", "nose", "unknown"],
            },
            "test_count": {"type": "number"},
        },
        "required": ["has_tests", "test_framework"],
    }

    options = ClaudeAgentOptions(
        output_format={"type": "json_schema", "schema": schema},
        permission_mode="acceptEdits",
        cwd=".",
    )

    result_message = None
    async for message in query(
        prompt="Search for test files in the tests/ directory. Determine which test framework is being used (pytest/unittest/nose) and count how many test files exist. Use Grep to search for framework imports.",
        options=options,
    ):
        if isinstance(message, ResultMessage):
            result_message = message

    # Verify result
    assert result_message is not None
    assert not result_message.is_error
    assert result_message.structured_output is not None

    # Check enum values are valid
    output = result_message.structured_output
    assert output["test_framework"] in ["pytest", "unittest", "nose", "unknown"]
    assert isinstance(output["has_tests"], bool)

    # This repo uses pytest
    assert output["has_tests"] is True
    assert output["test_framework"] == "pytest"


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_structured_output_with_tools():
    """Test structured output when agent uses tools."""

    # Schema for file analysis
    schema = {
        "type": "object",
        "properties": {
            "file_count": {"type": "number"},
            "has_readme": {"type": "boolean"},
        },
        "required": ["file_count", "has_readme"],
    }

    options = ClaudeAgentOptions(
        output_format={"type": "json_schema", "schema": schema},
        permission_mode="acceptEdits",
        cwd=tempfile.gettempdir(),  # Cross-platform temp directory
    )

    result_message = None
    async for message in query(
        prompt="Count how many files are in the current directory and check if there's a README file. Use tools as needed.",
        options=options,
    ):
        if isinstance(message, ResultMessage):
            result_message = message

    # Verify result
    assert result_message is not None
    assert not result_message.is_error
    assert result_message.structured_output is not None

    # Check structure
    output = result_message.structured_output
    assert "file_count" in output
    assert "has_readme" in output
    assert isinstance(output["file_count"], (int, float))
    assert isinstance(output["has_readme"], bool)
    assert output["file_count"] >= 0  # Should be non-negative
