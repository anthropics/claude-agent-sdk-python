#!/usr/bin/env python3
"""MCP server for GitHub PR review operations.

This server provides tools for Claude to post inline comments and submit reviews
on GitHub pull requests.

Environment variables required:
- GITHUB_TOKEN: GitHub access token
- GITHUB_OWNER: Repository owner
- GITHUB_REPO: Repository name
- PR_NUMBER: Pull request number
- HEAD_SHA: Head commit SHA
"""

import logging
import os
import sys

import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create MCP server
server = Server("github-review")


def get_env(name: str) -> str:
    """Get required environment variable."""
    value = os.environ.get(name)
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def get_headers() -> dict[str, str]:
    """Get GitHub API headers."""
    return {
        "Authorization": f"Bearer {get_env('GITHUB_TOKEN')}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


@server.list_tools()
async def list_tools() -> list[Tool]:
    """List available tools."""
    return [
        Tool(
            name="create_inline_comment",
            description="Create an inline comment on a specific line in a pull request file",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "The relative path to the file to comment on",
                    },
                    "line": {
                        "type": "integer",
                        "description": "The line number to comment on (in the new version of the file)",
                    },
                    "body": {
                        "type": "string",
                        "description": "The comment body text",
                    },
                    "side": {
                        "type": "string",
                        "enum": ["LEFT", "RIGHT"],
                        "default": "RIGHT",
                        "description": "Which side of the diff to comment on. RIGHT for new code, LEFT for old code",
                    },
                },
                "required": ["path", "line", "body"],
            },
        ),
        Tool(
            name="submit_review",
            description="Submit a pull request review with an overall verdict",
            inputSchema={
                "type": "object",
                "properties": {
                    "body": {
                        "type": "string",
                        "description": "The review summary comment",
                    },
                    "event": {
                        "type": "string",
                        "enum": ["APPROVE", "REQUEST_CHANGES", "COMMENT"],
                        "description": "The review verdict: APPROVE, REQUEST_CHANGES, or COMMENT",
                    },
                },
                "required": ["body", "event"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Handle tool calls."""
    owner = get_env("GITHUB_OWNER")
    repo = get_env("GITHUB_REPO")
    pr_number = int(get_env("PR_NUMBER"))
    head_sha = get_env("HEAD_SHA")

    async with httpx.AsyncClient() as client:
        if name == "create_inline_comment":
            response = await client.post(
                f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/comments",
                headers=get_headers(),
                json={
                    "commit_id": head_sha,
                    "path": arguments["path"],
                    "line": arguments["line"],
                    "side": arguments.get("side", "RIGHT"),
                    "body": arguments["body"],
                },
            )

            if response.status_code == 201:
                data = response.json()
                return [
                    TextContent(
                        type="text",
                        text=f"Created inline comment on {arguments['path']}:{arguments['line']}\nURL: {data.get('html_url', 'N/A')}",
                    )
                ]
            else:
                return [
                    TextContent(
                        type="text",
                        text=f"Failed to create comment: {response.status_code} - {response.text}",
                    )
                ]

        elif name == "submit_review":
            response = await client.post(
                f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/reviews",
                headers=get_headers(),
                json={
                    "commit_id": head_sha,
                    "body": arguments["body"],
                    "event": arguments["event"],
                },
            )

            if response.status_code == 200:
                data = response.json()
                return [
                    TextContent(
                        type="text",
                        text=f"Submitted review with verdict: {arguments['event']}\nURL: {data.get('html_url', 'N/A')}",
                    )
                ]
            else:
                return [
                    TextContent(
                        type="text",
                        text=f"Failed to submit review: {response.status_code} - {response.text}",
                    )
                ]

        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]


async def main() -> None:
    """Run the MCP server."""
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
