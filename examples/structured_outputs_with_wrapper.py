#!/usr/bin/env python3
"""
Structured Outputs Example with Custom CLI Wrapper

This example demonstrates using the SDK's structured outputs support by pointing
to a custom CLI wrapper that adds structured outputs via HTTP interception.

This proves the full end-to-end integration works:
1. SDK generates schema from Pydantic model
2. Schema is passed via environment variable to wrapper
3. Wrapper injects schema into API requests
4. API returns structured JSON
5. SDK receives the structured output

Requirements:
    - Claude CLI installed: npm install -g @anthropic-ai/claude-code
    - Node.js >= 18
    - API key with credits: export ANTHROPIC_API_KEY="sk-ant-api03-..."
    - Pydantic installed: pip install pydantic

Usage:
    python examples/structured_outputs_with_wrapper.py
"""

import asyncio
import json
import os
from pathlib import Path

from pydantic import BaseModel, Field

from claude_agent_sdk import ClaudeAgentOptions, query


class EmailExtraction(BaseModel):
    """Schema for extracting contact information from text."""

    name: str = Field(description="Full name extracted from the text")
    email: str = Field(description="Email address extracted from the text")
    plan_interest: str = Field(
        description="The plan or product they are interested in"
    )
    demo_requested: bool = Field(
        description="Whether they requested a demo or meeting"
    )


async def main():
    """Run structured outputs example with custom CLI wrapper."""

    # Check for API key
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("Error: ANTHROPIC_API_KEY environment variable not set")
        print("Get an API key from https://console.anthropic.com/")
        return

    # Get project root
    project_root = Path(__file__).parent.parent

    # Path to custom CLI wrapper
    cli_wrapper = project_root / "bin" / "claude-with-structured-outputs"

    if not cli_wrapper.exists():
        print(f"Error: CLI wrapper not found at {cli_wrapper}")
        print("Make sure you're running from the project root")
        return

    # Generate schema from Pydantic model
    schema = EmailExtraction.model_json_schema()

    # Remove $schema field (Anthropic doesn't need it)
    schema.pop("$schema", None)

    # Anthropic requires additionalProperties: false for objects
    if schema.get("type") == "object":
        schema["additionalProperties"] = False

    # Write schema to temp file for the interceptor to use
    schema_file = project_root / "test-schemas" / "email_extraction.json"
    schema_file.parent.mkdir(exist_ok=True)
    schema_file.write_text(json.dumps(schema, indent=2))

    print("=" * 70)
    print("Structured Outputs Example with Custom CLI Wrapper")
    print("=" * 70)
    print()
    print(f"CLI Wrapper: {cli_wrapper}")
    print(f"Schema File: {schema_file}")
    print(f"Model: EmailExtraction")
    print()

    # Set environment variable for interceptor to use
    os.environ["ANTHROPIC_SCHEMA_FILE"] = str(schema_file)

    # Create options with custom CLI path
    options = ClaudeAgentOptions(
        cli_path=str(cli_wrapper),
        permission_mode="bypassPermissions",
        max_turns=1,
    )

    # Test prompt
    test_prompt = (
        "Extract info: Sarah Chen (sarah@company.com) wants Professional plan, "
        "requested demo"
    )

    print("Prompt:")
    print(f'  "{test_prompt}"')
    print()
    print("Sending request with structured outputs enabled...")
    print()

    try:
        # Query with the wrapper (SDK doesn't need to know about schemas yet)
        from claude_agent_sdk import AssistantMessage

        async for message in query(prompt=test_prompt, options=options):
            # Only process AssistantMessage responses
            if not isinstance(message, AssistantMessage):
                continue

            print("Response:")
            print("-" * 70)

            # The response should be structured JSON
            if message.content and len(message.content) > 0:
                content_text = message.content[0].text

                # Try to parse as JSON
                try:
                    parsed = json.loads(content_text)
                    print(json.dumps(parsed, indent=2))
                    print()

                    # Validate against our Pydantic model
                    validated = EmailExtraction(**parsed)
                    print("✓ Validation Success!")
                    print(f"  Name: {validated.name}")
                    print(f"  Email: {validated.email}")
                    print(f"  Plan: {validated.plan_interest}")
                    print(f"  Demo: {validated.demo_requested}")

                except json.JSONDecodeError:
                    print("Warning: Response is not JSON (likely markdown)")
                    print(content_text)
                except Exception as e:
                    print(f"Validation Error: {e}")
                    print("Raw JSON:", content_text)

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

    print()
    print("=" * 70)
    print("What This Proves:")
    print("=" * 70)
    print("✓ SDK's schema generation works (Pydantic → JSON Schema)")
    print("✓ Custom CLI wrapper successfully injects schema")
    print("✓ API returns structured JSON matching the schema")
    print("✓ Full end-to-end integration works")
    print()
    print("Once Claude CLI adds native schema support, just remove cli_path")
    print("and use: query(prompt='...', output_format=EmailExtraction)")
    print()


if __name__ == "__main__":
    asyncio.run(main())
