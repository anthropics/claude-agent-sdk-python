"""Review prompt construction for AI PR Reviewer.

Provides modular prompt construction with base templates, user customization,
and PR context injection.
"""

from dataclasses import dataclass

from .pr_context import PRContext
from .reviewer_config import ReviewerSettings


@dataclass
class PromptConfig:
    """Configuration options for prompt building."""

    include_commit_history: bool = True
    max_diff_lines: int | None = None  # None means no limit
    include_file_stats: bool = True


# Base review prompt template with placeholders
BASE_REVIEW_TEMPLATE = """You are a code reviewer. Review this pull request and provide structured feedback.

## Pull Request Information
- **Title**: {title}
- **Author**: {author}
- **Branch**: {head_branch} -> {base_branch}
- **Description**: {description}

{commit_history_section}

## Changed Files ({file_count} files, +{additions}/-{deletions} lines)
{diff_content}

## Reviewer Instructions
{custom_instructions}

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


def _format_commit_history(context: PRContext, include: bool = True) -> str:
    """
    Format the commit history section of the prompt.

    Args:
        context: The PR context containing commits.
        include: Whether to include the commit history section.

    Returns:
        Formatted commit history section or empty string if not included.
    """
    if not include or not context.commits:
        return ""

    commit_lines = [
        f"- {commit.sha[:7]}: {commit.message.split(chr(10))[0]}"
        for commit in context.commits
    ]
    commit_history = "\n".join(commit_lines)

    return f"## Commit History\n{commit_history}\n"


def _format_diff_content(
    context: PRContext,
    max_lines: int | None = None,
    include_stats: bool = True,
) -> str:
    """
    Format the diff content section of the prompt.

    Args:
        context: The PR context containing file changes.
        max_lines: Maximum number of diff lines to include (None for no limit).
        include_stats: Whether to include file-level stats in headers.

    Returns:
        Formatted diff content string.
    """
    diff_sections: list[str] = []
    total_lines = 0

    for file in context.files:
        if file.patch:
            patch_lines = file.patch.split("\n")

            # Check if we've hit the max lines limit
            if max_lines is not None:
                remaining = max_lines - total_lines
                if remaining <= 0:
                    diff_sections.append(
                        f"### File: {file.filename}\n(Diff truncated due to size limit)"
                    )
                    continue
                if len(patch_lines) > remaining:
                    patch_lines = patch_lines[:remaining]
                    patch_lines.append("... (truncated)")

            total_lines += len(patch_lines)
            patch_content = "\n".join(patch_lines)

            stats = f" (+{file.additions}/-{file.deletions})" if include_stats else ""

            diff_sections.append(
                f"### File: {file.filename}{stats}\n```diff\n{patch_content}\n```"
            )
        else:
            diff_sections.append(
                f"### File: {file.filename}\n(Binary file or no diff available)"
            )

    return "\n\n".join(diff_sections)


def _format_description(description: str | None) -> str:
    """
    Format the PR description for the prompt.

    Args:
        description: The PR body/description text.

    Returns:
        Formatted description or placeholder text.
    """
    if not description or not description.strip():
        return "(No description provided)"
    return description.strip()


def _inject_language_instruction(
    custom_instructions: str,
    language: str | None,
) -> str:
    """
    Inject language-specific instructions if a language is configured.

    Args:
        custom_instructions: The base custom instructions.
        language: Optional language code (e.g., "en", "es", "ja").

    Returns:
        Instructions with language preference injected if applicable.
    """
    if not language:
        return custom_instructions

    language_instruction = f"\n\nPlease provide your review in {language}."
    return custom_instructions + language_instruction


def build_review_prompt(
    context: PRContext,
    reviewer_settings: ReviewerSettings,
    config: PromptConfig | None = None,
) -> str:
    """
    Build a complete review prompt from PR context and reviewer settings.

    This function constructs a prompt that includes:
    - Base review instructions and response format
    - PR metadata (title, author, branches, description)
    - Optional commit history
    - File diffs with optional size limits
    - User-specific custom instructions from config

    Args:
        context: Complete PR context including metadata, commits, and files.
        reviewer_settings: The reviewer's custom settings including prompt.
        config: Optional configuration for prompt building behavior.

    Returns:
        The complete formatted prompt string.
    """
    if config is None:
        config = PromptConfig()

    # Format commit history section
    commit_history_section = _format_commit_history(
        context, include=config.include_commit_history
    )

    # Format diff content
    diff_content = _format_diff_content(
        context,
        max_lines=config.max_diff_lines,
        include_stats=config.include_file_stats,
    )

    # Format description
    description = _format_description(context.metadata.body)

    # Inject language preference if configured
    custom_instructions = _inject_language_instruction(
        reviewer_settings.prompt,
        reviewer_settings.language,
    )

    # Build the complete prompt from template
    prompt = BASE_REVIEW_TEMPLATE.format(
        title=context.metadata.title,
        author=context.metadata.author,
        head_branch=context.metadata.head_branch,
        base_branch=context.metadata.base_branch,
        description=description,
        commit_history_section=commit_history_section,
        file_count=len(context.files),
        additions=context.total_additions,
        deletions=context.total_deletions,
        diff_content=diff_content,
        custom_instructions=custom_instructions,
    )

    return prompt
