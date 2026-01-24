"""Review summary generation for AI PR Reviewer.

Generates markdown-formatted review summaries from Claude's review output
for posting to GitHub PRs.
"""

from dataclasses import dataclass

from .claude_integration import ReviewOutput

# Assessment labels and display text
ASSESSMENT_LABELS: dict[str, str] = {
    "approve": "Approved",
    "request_changes": "Changes Requested",
    "comment": "Comments",
}

ASSESSMENT_ICONS: dict[str, str] = {
    "approve": ":white_check_mark:",
    "request_changes": ":x:",
    "comment": ":speech_balloon:",
}


@dataclass
class ReviewSummary:
    """A formatted review summary ready for posting to GitHub."""

    markdown: str
    persona_username: str
    overall_assessment: str  # "approve", "request_changes", or "comment"

    @property
    def assessment_label(self) -> str:
        """Human-readable assessment label."""
        return ASSESSMENT_LABELS.get(self.overall_assessment, "Comments")


def _format_persona_header(username: str) -> str:
    """
    Format the persona header for the review.

    Args:
        username: The GitHub username of the AI reviewer persona.

    Returns:
        Formatted header markdown string.
    """
    return f"## :robot: Review on behalf of @{username}"


def _format_assessment_section(overall_assessment: str) -> str:
    """
    Format the overall assessment section.

    Args:
        overall_assessment: One of "approve", "request_changes", or "comment".

    Returns:
        Formatted assessment section markdown.
    """
    label = ASSESSMENT_LABELS.get(overall_assessment, "Comments")
    icon = ASSESSMENT_ICONS.get(overall_assessment, ":speech_balloon:")

    return f"### Overall Assessment: {icon} {label}"


def _format_summary_section(summary: str) -> str:
    """
    Format the summary section.

    Args:
        summary: The review summary text from Claude.

    Returns:
        Formatted summary section markdown.
    """
    return f"### Summary\n\n{summary}"


def _format_key_findings_section(key_findings: list[str]) -> str:
    """
    Format the key findings section as a bulleted list.

    Args:
        key_findings: List of key findings from the review.

    Returns:
        Formatted key findings section markdown, or empty string if no findings.
    """
    if not key_findings:
        return ""

    findings_list = "\n".join(f"- {finding}" for finding in key_findings)
    return f"### Key Findings\n\n{findings_list}"


def generate_review_summary(
    review_output: ReviewOutput,
    reviewer_username: str,
) -> ReviewSummary:
    """
    Generate a markdown-formatted review summary from Claude's review output.

    The summary includes:
    - Persona header indicating the AI reviewer
    - Overall assessment (approve, request changes, or comment)
    - Summary of the review
    - List of key findings

    Args:
        review_output: The structured review output from Claude.
        reviewer_username: The GitHub username of the AI reviewer persona.

    Returns:
        A ReviewSummary with the formatted markdown and metadata.
    """
    sections: list[str] = []

    # Persona header
    sections.append(_format_persona_header(reviewer_username))

    # Overall assessment
    sections.append(_format_assessment_section(review_output.overall_assessment))

    # Summary
    sections.append(_format_summary_section(review_output.summary))

    # Key findings (only if present)
    findings_section = _format_key_findings_section(review_output.key_findings)
    if findings_section:
        sections.append(findings_section)

    # Join sections with double newlines
    markdown = "\n\n".join(sections)

    return ReviewSummary(
        markdown=markdown,
        persona_username=reviewer_username,
        overall_assessment=review_output.overall_assessment,
    )
