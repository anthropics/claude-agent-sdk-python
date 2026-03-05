#!/usr/bin/env python3
"""Examples of using cloud session storage with Claude Agent SDK.

This file demonstrates how to use S3 and GCS session storage backends to
persist session transcripts to cloud storage. This enables:

- Horizontal scaling across multiple servers with shared sessions
- Support for ephemeral filesystems (containers, serverless)
- Session resume from cloud storage
- Persistent conversation history

Installation:
    For S3 (AWS, DigitalOcean Spaces, Cloudflare R2, MinIO):
    pip install claude-agent-sdk[s3]

    For GCS (Google Cloud Storage):
    pip install claude-agent-sdk[gcs]

WARNING: Cloud storage operations add latency (50-500ms+ per operation).
For production at scale, see session_storage_cached.py for caching patterns.
"""

import asyncio
import os

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
from claude_agent_sdk.types import AssistantMessage, ResultMessage, TextBlock


def display_message(msg):
    """Display messages in a standardized format."""
    if isinstance(msg, AssistantMessage):
        for block in msg.content:
            if isinstance(block, TextBlock):
                print(f"Claude: {block.text}")
    elif isinstance(msg, ResultMessage):
        print("Result ended")


async def example_s3_basic():
    """Basic S3 usage with AWS credentials.

    This example shows standard AWS S3 configuration. The SDK will use
    standard AWS credential chain (environment vars, IAM roles, etc).
    """
    print("=== S3 Basic Example ===\n")

    # Import only needed when using S3
    from claude_agent_sdk.session_storage import S3Config, S3SessionStorage

    # Configure S3 storage
    storage = S3SessionStorage(
        S3Config(
            bucket="my-claude-sessions",
            prefix="claude-sessions",  # Organize sessions under this prefix
            region="us-east-1",  # Optional: specify AWS region
            # aws_access_key_id="AKIAIOSFODNN7EXAMPLE",  # Optional: explicit credentials
            # aws_secret_access_key="wJalrXUtnFEMI/...",  # Optional: explicit credentials
        )
    )

    # Use storage in options
    options = ClaudeAgentOptions(
        session_storage=storage,
        # Optional: specify local transcript directory
        # transcript_dir="/tmp/claude-transcripts",
    )

    # Start a conversation - transcript is synced to S3
    async with ClaudeSDKClient(options=options) as client:
        print("User: What is the capital of France?")
        await client.query("What is the capital of France?")

        async for msg in client.receive_response():
            display_message(msg)

        # Follow-up question - same session
        print("\nUser: What's the population?")
        await client.query("What's the population?")

        async for msg in client.receive_response():
            display_message(msg)

    print("\n")


async def example_digitalocean_spaces():
    """DigitalOcean Spaces configuration (S3-compatible).

    DigitalOcean Spaces uses the S3 API with a custom endpoint.
    This pattern works for any S3-compatible service.
    """
    print("=== DigitalOcean Spaces Example ===\n")

    from claude_agent_sdk.session_storage import S3Config, S3SessionStorage

    # DigitalOcean Spaces configuration
    storage = S3SessionStorage(
        S3Config(
            bucket="my-space-name",  # Your Spaces name
            prefix="claude-sessions",
            endpoint_url="https://nyc3.digitaloceanspaces.com",  # Spaces endpoint
            region="nyc3",  # Spaces region
            # Get these from: https://cloud.digitalocean.com/account/api/tokens
            aws_access_key_id=os.getenv("DO_SPACES_KEY", "your-spaces-key"),
            aws_secret_access_key=os.getenv("DO_SPACES_SECRET", "your-spaces-secret"),
        )
    )

    options = ClaudeAgentOptions(session_storage=storage)

    async with ClaudeSDKClient(options=options) as client:
        print("User: Hello! Remember this: my favorite color is blue.")
        await client.query("Hello! Remember this: my favorite color is blue.")

        async for msg in client.receive_response():
            display_message(msg)

    print(
        "\nNote: Transcript is now stored in DigitalOcean Spaces and can be resumed from any server.\n"
    )


async def example_cloudflare_r2():
    """Cloudflare R2 configuration (S3-compatible, no egress fees).

    Cloudflare R2 is fully S3-compatible and has zero egress fees,
    making it cost-effective for high-traffic applications.
    """
    print("=== Cloudflare R2 Example ===\n")

    from claude_agent_sdk.session_storage import S3Config, S3SessionStorage

    # Cloudflare R2 configuration
    storage = S3SessionStorage(
        S3Config(
            bucket="my-r2-bucket",
            prefix="claude-sessions",
            # R2 endpoint format: https://<account-id>.r2.cloudflarestorage.com
            endpoint_url=os.getenv(
                "R2_ENDPOINT", "https://abc123.r2.cloudflarestorage.com"
            ),
            # Get credentials from Cloudflare dashboard > R2 > Manage R2 API Tokens
            aws_access_key_id=os.getenv("R2_ACCESS_KEY_ID", "your-r2-access-key"),
            aws_secret_access_key=os.getenv(
                "R2_SECRET_ACCESS_KEY", "your-r2-secret-key"
            ),
            # R2 doesn't require region, but some S3 clients expect it
            region="auto",
        )
    )

    options = ClaudeAgentOptions(session_storage=storage)

    async with ClaudeSDKClient(options=options) as client:
        print("User: What's 2 + 2?")
        await client.query("What's 2 + 2?")

        async for msg in client.receive_response():
            display_message(msg)

    print("\nNote: R2 has zero egress fees - cost-effective for production.\n")


async def example_minio():
    """MinIO configuration (self-hosted S3-compatible storage).

    MinIO is an open-source S3-compatible object storage server
    that you can self-host on-premise or in your own cloud.
    """
    print("=== MinIO Example ===\n")

    from claude_agent_sdk.session_storage import S3Config, S3SessionStorage

    # MinIO configuration (local or self-hosted)
    _storage = S3SessionStorage(
        S3Config(
            bucket="claude-sessions",
            prefix="sessions",
            endpoint_url="http://localhost:9000",  # MinIO server endpoint
            aws_access_key_id=os.getenv("MINIO_ACCESS_KEY", "minioadmin"),
            aws_secret_access_key=os.getenv("MINIO_SECRET_KEY", "minioadmin"),
            # MinIO doesn't require region
        )
    )

    # In a real application:
    # options = ClaudeAgentOptions(session_storage=storage)

    print("Note: This example assumes MinIO is running on localhost:9000")
    print("To start MinIO: docker run -p 9000:9000 minio/minio server /data\n")

    # In a real application, you would use the client normally
    print("Storage configured with MinIO - ready for use.\n")


async def example_gcs_basic():
    """Basic Google Cloud Storage usage.

    GCS uses Application Default Credentials (ADC) by default, which works
    seamlessly in GCP environments (Compute Engine, GKE, Cloud Run, etc.).
    """
    print("=== GCS Basic Example ===\n")

    from claude_agent_sdk.session_storage import GCSConfig, GCSSessionStorage

    # Configure GCS storage
    storage = GCSSessionStorage(
        GCSConfig(
            bucket="my-claude-sessions",  # Your GCS bucket name
            prefix="claude-sessions",
            project="my-gcp-project",  # Optional: GCP project ID
            # credentials_path="/path/to/service-account.json",  # Optional: explicit credentials
        )
    )

    options = ClaudeAgentOptions(session_storage=storage)

    async with ClaudeSDKClient(options=options) as client:
        print("User: What is 10 * 7?")
        await client.query("What is 10 * 7?")

        async for msg in client.receive_response():
            display_message(msg)

    print("\n")


async def example_gcs_with_credentials():
    """GCS with explicit service account credentials.

    Use this when you need to specify credentials explicitly,
    for example in local development or CI/CD environments.
    """
    print("=== GCS with Service Account Example ===\n")

    from claude_agent_sdk.session_storage import GCSConfig, GCSSessionStorage

    # Configure with service account JSON file
    _storage = GCSSessionStorage(
        GCSConfig(
            bucket="my-claude-sessions",
            prefix="claude-prod",
            project="my-gcp-project",
            # Path to service account JSON key file
            credentials_path=os.getenv(
                "GOOGLE_APPLICATION_CREDENTIALS", "/path/to/service-account.json"
            ),
        )
    )

    # In a real application:
    # options = ClaudeAgentOptions(session_storage=storage)

    print("Storage configured with GCS service account credentials.")
    print(
        "Note: Get credentials from: https://console.cloud.google.com/iam-admin/serviceaccounts\n"
    )


async def example_session_resume():
    """Resume a session from cloud storage.

    When you provide a session_id that already exists in cloud storage,
    the SDK automatically downloads the transcript and resumes the conversation.
    """
    print("=== Session Resume Example ===\n")

    from claude_agent_sdk.session_storage import S3Config, S3SessionStorage

    storage = S3SessionStorage(
        S3Config(
            bucket="my-claude-sessions",
            prefix="claude-sessions",
            region="us-east-1",
        )
    )

    # First conversation - create a session
    session_id = "user-123-conversation"

    print("--- Starting new session ---")
    options = ClaudeAgentOptions(
        session_storage=storage,
        session_id=session_id,  # Specify session ID
    )

    async with ClaudeSDKClient(options=options) as client:
        print("User: My name is Alice and I love Python.")
        await client.query("My name is Alice and I love Python.")

        async for msg in client.receive_response():
            display_message(msg)

    print("\n--- Resuming session on different server ---")

    # Resume the same session (could be on a different server/container)
    options_resume = ClaudeAgentOptions(
        session_storage=storage,
        session_id=session_id,  # Same session ID - will download from cloud
    )

    async with ClaudeSDKClient(options=options_resume) as client:
        print("User: What's my name? What language do I love?")
        await client.query("What's my name? What language do I love?")

        async for msg in client.receive_response():
            display_message(msg)

    print(
        "\nNote: Claude remembers the conversation because it was resumed from cloud storage.\n"
    )


async def example_list_sessions():
    """List sessions stored in cloud storage.

    Useful for admin interfaces, debugging, or cleanup operations.
    """
    print("=== List Sessions Example ===\n")

    from claude_agent_sdk.session_storage import S3Config, S3SessionStorage

    storage = S3SessionStorage(
        S3Config(
            bucket="my-claude-sessions",
            prefix="claude-sessions",
            region="us-east-1",
        )
    )

    # List all sessions
    sessions = await storage.list_sessions(limit=10)

    print(f"Found {len(sessions)} sessions:\n")
    for session_meta in sessions:
        print(f"Session ID: {session_meta.session_id}")
        print(f"  Size: {session_meta.size_bytes:,} bytes")
        print(f"  Updated: {session_meta.updated_at}")
        print(f"  Storage key: {session_meta.storage_key}")
        print()

    # List sessions with prefix filter
    user_sessions = await storage.list_sessions(prefix="user-123-", limit=5)
    print(f"Found {len(user_sessions)} sessions for user-123")


async def example_production_tips():
    """Production deployment tips.

    This example demonstrates best practices for production use.
    """
    print("=== Production Tips ===\n")

    print("Production configuration example:")
    print()
    print("from claude_agent_sdk.session_storage import S3SessionStorage, S3Config")
    print()
    print("storage = S3SessionStorage(")
    print("    S3Config(")
    print("        bucket='prod-claude-sessions',")
    print("        prefix='claude/v1',  # Version your storage structure")
    print("        region='us-east-1',")
    print("    ),")
    print("    max_retries=3,  # Retry failed operations")
    print("    retry_delay=1.0,  # Base delay between retries (exponential backoff)")
    print(")")
    print()
    print("Production best practices:")
    print()
    print("1. LATENCY WARNING:")
    print("   - S3/GCS operations add 50-500ms+ per operation")
    print("   - For high-throughput, use caching (see session_storage_cached.py)")
    print()
    print("2. CREDENTIALS:")
    print("   - Use IAM roles in AWS (no hardcoded credentials)")
    print("   - Use workload identity in GCP")
    print("   - Use environment variables for keys")
    print()
    print("3. BUCKET CONFIGURATION:")
    print("   - Enable versioning for data safety")
    print("   - Set lifecycle policies to archive/delete old sessions")
    print("   - Configure CORS if accessing from browser")
    print()
    print("4. MONITORING:")
    print("   - Track upload/download latencies")
    print("   - Monitor storage costs")
    print("   - Alert on error rates")
    print()
    print("5. SESSION IDs:")
    print("   - Use meaningful IDs: user-{user_id}-{timestamp}")
    print("   - Include tenant/org ID for multi-tenant apps")
    print("   - Avoid PII in session IDs (stored in S3 key)")
    print()


async def main():
    """Run all examples with error handling."""
    examples = {
        "s3_basic": (
            example_s3_basic,
            "Basic S3 usage with AWS",
        ),
        "digitalocean": (
            example_digitalocean_spaces,
            "DigitalOcean Spaces (S3-compatible)",
        ),
        "cloudflare_r2": (
            example_cloudflare_r2,
            "Cloudflare R2 (S3-compatible, zero egress)",
        ),
        "minio": (
            example_minio,
            "MinIO (self-hosted S3-compatible)",
        ),
        "gcs_basic": (
            example_gcs_basic,
            "Basic GCS usage",
        ),
        "gcs_credentials": (
            example_gcs_with_credentials,
            "GCS with service account",
        ),
        "session_resume": (
            example_session_resume,
            "Resume session from cloud storage",
        ),
        "list_sessions": (
            example_list_sessions,
            "List and inspect stored sessions",
        ),
        "production": (
            example_production_tips,
            "Production deployment best practices",
        ),
    }

    print("Claude Agent SDK - Session Storage Examples")
    print("=" * 50)
    print()
    print("Available examples:")
    for name, (_, description) in examples.items():
        print(f"  {name:20} - {description}")
    print()
    print(
        "Note: These examples demonstrate the API without requiring actual cloud credentials."
    )
    print(
        "      In real usage, ensure credentials are configured via environment variables or IAM roles."
    )
    print()
    print("=" * 50)
    print()

    # Run non-network examples
    await example_production_tips()


if __name__ == "__main__":
    # Set up basic logging to see what's happening
    import logging

    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s - %(name)s - %(message)s"
    )

    asyncio.run(main())
