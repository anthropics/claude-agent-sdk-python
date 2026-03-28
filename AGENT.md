# Claude Agent SDK Python Repository Analysis

This repository hosts the Python Software Development Kit for interacting with Claude agents, designed to facilitate the integration of Claude's conversational and tool-use capabilities into Python applications.

## Key Technologies and Frameworks
*   **Python (3.10+):** The primary programming language.
*   **Anyio:** Provides asynchronous programming primitives for non-blocking operations.
*   **Multi-Content Protocol (MCP):** Essential for communication between the SDK and Claude agents, particularly for tool invocation.
*   **Claude Code CLI:** The command-line interface for Claude Code, bundled with the SDK for core functionality.

## Main Features
*   **Agent Querying:** Programmatic querying of Claude Code with text prompts.
*   **Interactive Sessions:** Supports bidirectional, interactive conversations with Claude agents via `ClaudeSDKClient`.
*   **Custom Tooling:** Enables developers to define and integrate custom tools as in-process SDK MCP servers, enhancing Claude's capabilities.
*   **Lifecycle Hooks:** Provides hooks for deterministic processing and automated feedback during the Claude agent's execution flow.
*   **Error Handling:** Comprehensive error handling for various issues related to CLI interaction and process management.

## Architectural Patterns
*   **Client-Server Architecture:** The Python SDK acts as a client that communicates with the Claude Code CLI (the server).
*   **Extensibility/Plugin Pattern:** Custom tools and hooks serve as extension points, allowing developers to extend and customize the agent's behavior.
*   **Asynchronous Design:** Leverages `anyio` for efficient, non-blocking I/O operations, crucial for interactive agent communication.
*   **In-Process Communication:** Offers the ability to run custom tool servers directly within the application's process, optimizing performance by reducing inter-process communication overhead.
