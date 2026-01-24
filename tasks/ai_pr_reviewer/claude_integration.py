"""Claude Code Python SDK integration for AI PR Reviewer.

Provides integration with Claude Code SDK to generate AI-powered code reviews.
"""

import json
import logging
from dataclasses import dataclass
from typing import Any

from claude_agent_sdk import ClaudeAgentOptions, query
from claude_agent_sdk.types import (
    AssistantMessage,
    ResultMessage,
    TextBlock,
)

from .pr_context import PRContext
from .reviewer_config import ReviewerSettings

logger = logging.getLogger(__name__)


@dataclass
class InlineComment:
    """Represents an inline comment on a specific file and line."""

    file_path: str
    line_number: int
    body: str
    suggestion: str | None = None  # Optional code suggestion


@dataclass
class ReviewOutput:
    """Structured output from Claude's review."""

    summary: str
    overall_assessment: str  # "approve", "request_changes", or "comment"
    key_findings: list[str]
    inline_comments: list[InlineComment]


class ClaudeIntegrationError(Exception):
    """Raised when Claude integration fails."""

    pass


class ClaudeSDKError(ClaudeIntegrationError):
    """Raised when the Claude SDK returns an error."""

    def __init__(self, message: str, error_type: str | None = None) -> None:
        super().__init__(message)
        self.error_type = error_type


class ReviewParseError(ClaudeIntegrationError):
    """Raised when the review response cannot be parsed."""

    pass


def _build_review_prompt(
    context: PRContext,
    reviewer_settings: ReviewerSettings,
) -> str:
    """
    Build the prompt for Claude to generate a code review.

    Args:
        context: Complete PR context including metadata, commits, and files.
        reviewer_settings: The reviewer's custom settings including prompt.

    Returns:
        The formatted prompt string.
    """
    # Build diff section
    diff_sections: list[str] = []
    for file in context.files:
        if file.patch:
            diff_sections.append(
                f"### File: {file.filename}\n```diff\n{file.patch}\n```"
            )
        else:
            diff_sections.append(
                f"### File: {file.filename}\n(Binary file or no diff available)"
            )

    diff_content = "\n\n".join(diff_sections)

    # Build commit history section
    commit_lines = [
        f"- {commit.sha[:7]}: {commit.message.split(chr(10))[0]}"
        for commit in context.commits
    ]
    commit_history = "\n".join(commit_lines) if commit_lines else "No commits"

    # Build the prompt
    prompt = f"""You are a code reviewer. Review this pull request and provide structured feedback.

## Pull Request Information
- **Title**: {context.metadata.title}
- **Author**: {context.metadata.author}
- **Branch**: {context.metadata.head_branch} → {context.metadata.base_branch}
- **Description**: {context.metadata.body or "(No description provided)"}

## Commit History
{commit_history}

## Changed Files ({len(context.files)} files, +{context.total_additions}/-{context.total_deletions} lines)
{diff_content}

## Reviewer Instructions
{reviewer_settings.prompt}

## Response Format
Respond with a JSON object in the following format:
```json
{{
  "summary": "A concise summary of your overall review",
  "overall_assessment": "approve" | "request_changes" | "comment",
  "key_findings": ["Finding 1", "Finding 2", ...],
  "inline_comments": [
    {{
      "file_path": "path/to/file.py",
      "line_number": 42,
      "body": "Comment about this line",
      "suggestion": "optional code suggestion"
    }}
  ]
}}
```

Important:
- overall_assessment must be one of: "approve", "request_changes", "comment"
- line_number must reference lines in the diff (the right side line numbers for additions/modifications)
- suggestion is optional and should only be provided when you have a specific code fix
- Keep inline_comments focused on important issues, not minor style nitpicks
"""

    return prompt


def _parse_review_response(response_text: str) -> ReviewOutput:
    """
    Parse Claude's response into a structured ReviewOutput.

    Args:
        response_text: The raw response text from Claude.

    Returns:
        A ReviewOutput with parsed data.

    Raises:
        ReviewParseError: If the response cannot be parsed.
    """
    # Try to extract JSON from the response
    json_str = _extract_json_from_response(response_text)

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        raise ReviewParseError(f"Invalid JSON in response: {e}") from e

    # Validate required fields
    if not isinstance(data, dict):
        raise ReviewParseError("Response must be a JSON object")

    summary = data.get("summary")
    if not isinstance(summary, str):
        raise ReviewParseError("Missing or invalid 'summary' field")

    overall_assessment = data.get("overall_assessment")
    if overall_assessment not in ("approve", "request_changes", "comment"):
        raise ReviewParseError(
            f"Invalid 'overall_assessment': {overall_assessment}. "
            "Must be 'approve', 'request_changes', or 'comment'"
        )

    key_findings = data.get("key_findings", [])
    if not isinstance(key_findings, list):
        raise ReviewParseError("'key_findings' must be a list")

    # Parse inline comments
    inline_comments: list[InlineComment] = []
    raw_comments = data.get("inline_comments", [])
    if not isinstance(raw_comments, list):
        raise ReviewParseError("'inline_comments' must be a list")

    for i, comment_data in enumerate(raw_comments):
        if not isinstance(comment_data, dict):
            raise ReviewParseError(f"inline_comments[{i}] must be an object")

        file_path = comment_data.get("file_path")
        if not isinstance(file_path, str):
            raise ReviewParseError(f"inline_comments[{i}].file_path must be a string")

        line_number = comment_data.get("line_number")
        if not isinstance(line_number, int):
            raise ReviewParseError(
                f"inline_comments[{i}].line_number must be an integer"
            )

        body = comment_data.get("body")
        if not isinstance(body, str):
            raise ReviewParseError(f"inline_comments[{i}].body must be a string")

        suggestion = comment_data.get("suggestion")
        if suggestion is not None and not isinstance(suggestion, str):
            raise ReviewParseError(
                f"inline_comments[{i}].suggestion must be a string or null"
            )

        inline_comments.append(
            InlineComment(
                file_path=file_path,
                line_number=line_number,
                body=body,
                suggestion=suggestion,
            )
        )

    return ReviewOutput(
        summary=summary,
        overall_assessment=overall_assessment,
        key_findings=[str(f) for f in key_findings],
        inline_comments=inline_comments,
    )


def _extract_json_from_response(text: str) -> str:
    """
    Extract JSON from a response that may contain markdown code blocks.

    Args:
        text: The response text that may contain JSON in a code block.

    Returns:
        The extracted JSON string.

    Raises:
        ReviewParseError: If no JSON can be found.
    """
    # Try to find JSON in code blocks first
    # Look for ```json ... ``` or ``` ... ```
    import re

    # Pattern for ```json ... ``` blocks
    json_block_pattern = r"```(?:json)?\s*\n?([\s\S]*?)\n?```"
    matches = re.findall(json_block_pattern, text)

    for match in matches:
        stripped = str(match).strip()
        if stripped.startswith("{"):
            return stripped

    # If no code block, try to find raw JSON object
    # Find the first { and last }
    first_brace = text.find("{")
    last_brace = text.rfind("}")

    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        return text[first_brace : last_brace + 1]

    raise ReviewParseError("Could not find JSON in response")


async def generate_review(
    context: PRContext,
    reviewer_settings: ReviewerSettings,
    model: str | None = None,
    max_tokens: int | None = None,
) -> ReviewOutput:
    """
    Generate a code review using Claude Code SDK.

    Args:
        context: Complete PR context including metadata, commits, and files.
        reviewer_settings: The reviewer's custom settings including prompt.
        model: Optional model override (defaults to SDK default).
        max_tokens: Optional max tokens for response.

    Returns:
        A ReviewOutput with the structured review.

    Raises:
        ClaudeSDKError: If the Claude SDK returns an error.
        ReviewParseError: If the response cannot be parsed.
    """
    prompt = _build_review_prompt(context, reviewer_settings)

    # Configure SDK options
    options = ClaudeAgentOptions(
        model=model,
        permission_mode="default",
    )

    logger.info(
        f"Generating review for PR #{context.metadata.number} "
        f"with reviewer '{reviewer_settings.username}'"
    )

    # Collect response text from Claude
    response_text = ""
    result_message: ResultMessage | None = None

    try:
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        response_text += block.text
            elif isinstance(message, ResultMessage):
                result_message = message
                if message.is_error:
                    error_info = message.result or "Unknown error"
                    raise ClaudeSDKError(
                        f"Claude SDK error: {error_info}",
                        error_type="sdk_error",
                    )
    except ClaudeSDKError:
        raise
    except Exception as e:
        raise ClaudeSDKError(
            f"Failed to communicate with Claude SDK: {e}",
            error_type="connection_error",
        ) from e

    if not response_text:
        raise ClaudeSDKError(
            "No response received from Claude",
            error_type="empty_response",
        )

    cost = 0.0
    if result_message is not None and result_message.total_cost_usd is not None:
        cost = result_message.total_cost_usd
    logger.info(f"Received response from Claude (cost: ${cost:.4f})")

    # Parse the response into structured output
    return _parse_review_response(response_text)


def create_review_processor(
    get_pr_context: Any,
    get_installation_token: Any,
) -> Any:
    """
    Create a review processor that implements ReviewProcessorProtocol.

    This factory function creates a processor that can be used with QueueWorker.

    Args:
        get_pr_context: Async function to fetch PR context.
        get_installation_token: Async function to get installation access token.

    Returns:
        A review processor instance.
    """
    from .review_queue import ReviewJob

    class ReviewProcessor:
        """Processes review jobs using Claude SDK."""

        async def process_review(self, job: ReviewJob) -> None:
            """Process a review job by generating and posting a review."""
            trigger = job.trigger

            logger.info(
                f"Processing review for PR #{trigger.pr_number} "
                f"in {trigger.repository_full_name} "
                f"by reviewer '{trigger.reviewer.username}'"
            )

            # Get installation token
            token = await get_installation_token(trigger.installation_id)

            # Fetch PR context
            context = await get_pr_context(
                owner=trigger.repository_owner,
                repo=trigger.repository_name,
                pr_number=trigger.pr_number,
                token=token,
            )

            # Generate review using Claude
            review_output = await generate_review(
                context=context,
                reviewer_settings=trigger.reviewer,
            )

            logger.info(
                f"Generated review for PR #{trigger.pr_number}: "
                f"assessment={review_output.overall_assessment}, "
                f"findings={len(review_output.key_findings)}, "
                f"inline_comments={len(review_output.inline_comments)}"
            )

            # Note: Posting the review to GitHub will be handled by US-012

    return ReviewProcessor()
