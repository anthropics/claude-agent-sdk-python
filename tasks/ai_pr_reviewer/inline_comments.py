"""Inline comment generation for AI PR Reviewer.

Provides functionality to parse Claude's file-specific suggestions,
map them to correct file paths and line numbers, and format code
suggestion blocks for GitHub's review API.
"""

import re
from dataclasses import dataclass

from .claude_integration import InlineComment
from .pr_context import FileChange


class InlineCommentError(Exception):
    """Raised when inline comment processing fails."""

    pass


class InvalidLineNumberError(InlineCommentError):
    """Raised when a line number is outside the valid diff range."""

    pass


class FileNotFoundInPRError(InlineCommentError):
    """Raised when a file path doesn't match any file in the PR."""

    pass


@dataclass
class MappedInlineComment:
    """An inline comment mapped to GitHub's review API format.

    GitHub's PR review API requires specific positioning:
    - path: The relative path to the file
    - line: The line in the diff to comment on (for single-line comments)
    - start_line: The starting line for multi-line comments (optional)
    - body: The comment body, potentially with ```suggestion blocks
    """

    path: str
    line: int
    body: str
    start_line: int | None = None
    side: str = "RIGHT"  # RIGHT for additions, LEFT for deletions


@dataclass
class DiffHunk:
    """Represents a parsed diff hunk with line number mappings."""

    start_line_old: int
    line_count_old: int
    start_line_new: int
    line_count_new: int
    lines: list[tuple[str, int | None, int | None]]  # (type, old_line, new_line)


def parse_diff_hunks(patch: str) -> list[DiffHunk]:
    """
    Parse a unified diff patch into hunks with line number mappings.

    Args:
        patch: The unified diff patch string.

    Returns:
        List of DiffHunk objects with line number information.
    """
    hunks: list[DiffHunk] = []

    # Match hunk headers: @@ -start,count +start,count @@
    hunk_header_pattern = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

    current_hunk_lines: list[tuple[str, int | None, int | None]] = []
    current_hunk_header: tuple[int, int, int, int] | None = None
    old_line = 0
    new_line = 0

    for line in patch.split("\n"):
        match = hunk_header_pattern.match(line)
        if match:
            # Save previous hunk if exists
            if current_hunk_header is not None:
                hunks.append(
                    DiffHunk(
                        start_line_old=current_hunk_header[0],
                        line_count_old=current_hunk_header[1],
                        start_line_new=current_hunk_header[2],
                        line_count_new=current_hunk_header[3],
                        lines=current_hunk_lines,
                    )
                )

            # Parse new hunk header
            start_old = int(match.group(1))
            count_old = int(match.group(2)) if match.group(2) else 1
            start_new = int(match.group(3))
            count_new = int(match.group(4)) if match.group(4) else 1

            current_hunk_header = (start_old, count_old, start_new, count_new)
            current_hunk_lines = []
            old_line = start_old
            new_line = start_new
        elif current_hunk_header is not None:
            if line.startswith("+"):
                # Addition: only new line number
                current_hunk_lines.append(("+", None, new_line))
                new_line += 1
            elif line.startswith("-"):
                # Deletion: only old line number
                current_hunk_lines.append(("-", old_line, None))
                old_line += 1
            elif line.startswith(" ") or line == "":
                # Context line: both line numbers
                current_hunk_lines.append((" ", old_line, new_line))
                old_line += 1
                new_line += 1
            # Ignore lines starting with \ (no newline at end of file)

    # Save final hunk
    if current_hunk_header is not None:
        hunks.append(
            DiffHunk(
                start_line_old=current_hunk_header[0],
                line_count_old=current_hunk_header[1],
                start_line_new=current_hunk_header[2],
                line_count_new=current_hunk_header[3],
                lines=current_hunk_lines,
            )
        )

    return hunks


def is_line_in_diff(
    line_number: int,
    hunks: list[DiffHunk],
    side: str = "RIGHT",
) -> bool:
    """
    Check if a line number is within the diff hunks.

    Args:
        line_number: The line number to check.
        hunks: List of parsed diff hunks.
        side: Which side to check - "RIGHT" for new lines, "LEFT" for old lines.

    Returns:
        True if the line is within the diff, False otherwise.
    """
    for hunk in hunks:
        for _line_type, old_line, new_line in hunk.lines:
            if side == "RIGHT" and new_line == line_number:
                return True
            if side == "LEFT" and old_line == line_number:
                return True
    return False


def find_closest_valid_line(
    target_line: int,
    hunks: list[DiffHunk],
    side: str = "RIGHT",
) -> int | None:
    """
    Find the closest valid line in the diff to the target line.

    Args:
        target_line: The desired line number.
        hunks: List of parsed diff hunks.
        side: Which side to check - "RIGHT" for new lines, "LEFT" for old lines.

    Returns:
        The closest valid line number, or None if no valid lines exist.
    """
    valid_lines: list[int] = []

    for hunk in hunks:
        for _line_type, old_line, new_line in hunk.lines:
            if side == "RIGHT" and new_line is not None:
                valid_lines.append(new_line)
            elif side == "LEFT" and old_line is not None:
                valid_lines.append(old_line)

    if not valid_lines:
        return None

    # Find the closest line
    return min(valid_lines, key=lambda x: abs(x - target_line))


def format_suggestion_block(suggestion: str) -> str:
    """
    Format a code suggestion in GitHub's suggestion block format.

    Args:
        suggestion: The suggested code replacement.

    Returns:
        Markdown formatted suggestion block.
    """
    return f"```suggestion\n{suggestion}\n```"


def build_comment_body(
    body: str,
    suggestion: str | None = None,
) -> str:
    """
    Build the full comment body with optional suggestion block.

    Args:
        body: The main comment text.
        suggestion: Optional code suggestion to include.

    Returns:
        Formatted comment body.
    """
    if suggestion is None:
        return body

    # Append the suggestion block after the comment
    return f"{body}\n\n{format_suggestion_block(suggestion)}"


def normalize_file_path(file_path: str) -> str:
    """
    Normalize a file path for comparison.

    Args:
        file_path: The file path to normalize.

    Returns:
        Normalized file path (lowercase, forward slashes, no leading slash).
    """
    # Remove leading slash or ./
    normalized = file_path.lstrip("./").lstrip("/")
    # Convert backslashes to forward slashes
    normalized = normalized.replace("\\", "/")
    return normalized.lower()


def find_matching_file(
    file_path: str,
    files: list[FileChange],
) -> FileChange | None:
    """
    Find a matching file in the PR file changes.

    Handles various path formats and attempts fuzzy matching.

    Args:
        file_path: The file path from Claude's response.
        files: List of file changes in the PR.

    Returns:
        The matching FileChange, or None if not found.
    """
    normalized_target = normalize_file_path(file_path)

    for file in files:
        # Exact match (normalized)
        if normalize_file_path(file.filename) == normalized_target:
            return file

        # Check if target is a suffix of the actual path (partial path match)
        if normalize_file_path(file.filename).endswith(normalized_target):
            return file

        # Check for renamed files
        if (
            file.previous_filename
            and normalize_file_path(file.previous_filename) == normalized_target
        ):
            return file

    return None


def map_inline_comment(
    comment: InlineComment,
    files: list[FileChange],
    strict_validation: bool = False,
) -> MappedInlineComment | None:
    """
    Map an InlineComment to GitHub's review API format.

    Validates the file path and line number against the PR diff.

    Args:
        comment: The inline comment from Claude's response.
        files: List of file changes in the PR.
        strict_validation: If True, raises errors for invalid comments.
                          If False, returns None for invalid comments.

    Returns:
        A MappedInlineComment ready for the GitHub API, or None if invalid.

    Raises:
        FileNotFoundInPRError: If strict_validation and file not found.
        InvalidLineNumberError: If strict_validation and line not in diff.
    """
    # Find the matching file
    file = find_matching_file(comment.file_path, files)

    if file is None:
        if strict_validation:
            raise FileNotFoundInPRError(f"File '{comment.file_path}' not found in PR")
        return None

    # Check if file has a patch (non-binary)
    if file.patch is None:
        if strict_validation:
            raise InvalidLineNumberError(
                f"Cannot comment on binary file: {file.filename}"
            )
        return None

    # Parse the diff to validate line numbers
    hunks = parse_diff_hunks(file.patch)

    # Check if the line is in the diff
    if not is_line_in_diff(comment.line_number, hunks, side="RIGHT"):
        # Try to find the closest valid line
        closest_line = find_closest_valid_line(comment.line_number, hunks)
        if closest_line is None:
            if strict_validation:
                raise InvalidLineNumberError(
                    f"Line {comment.line_number} is not in the diff for {file.filename}"
                )
            return None

        # Use the closest valid line (only in non-strict mode)
        line_to_use = closest_line
    else:
        line_to_use = comment.line_number

    # Build the comment body with optional suggestion
    body = build_comment_body(comment.body, comment.suggestion)

    return MappedInlineComment(
        path=file.filename,  # Use the actual filename from the PR
        line=line_to_use,
        body=body,
        side="RIGHT",
    )


def map_inline_comments(
    comments: list[InlineComment],
    files: list[FileChange],
    strict_validation: bool = False,
) -> list[MappedInlineComment]:
    """
    Map a list of InlineComments to GitHub's review API format.

    Invalid comments are filtered out in non-strict mode.

    Args:
        comments: List of inline comments from Claude's response.
        files: List of file changes in the PR.
        strict_validation: If True, raises on first invalid comment.

    Returns:
        List of MappedInlineComments ready for the GitHub API.
    """
    mapped: list[MappedInlineComment] = []

    for comment in comments:
        result = map_inline_comment(comment, files, strict_validation)
        if result is not None:
            mapped.append(result)

    return mapped


def extract_suggestion_from_body(body: str) -> tuple[str, str | None]:
    """
    Extract a suggestion block from comment body if present.

    This handles cases where Claude includes a suggestion block inline
    in the comment body rather than in the dedicated suggestion field.

    Args:
        body: The comment body text.

    Returns:
        Tuple of (clean_body, suggestion) where suggestion is None if not found.
    """
    # Pattern for ```suggestion ... ``` blocks
    suggestion_pattern = re.compile(
        r"```suggestion\s*\n([\s\S]*?)\n```",
        re.IGNORECASE,
    )

    match = suggestion_pattern.search(body)
    if match:
        suggestion = match.group(1)
        # Remove the suggestion block from the body
        clean_body = suggestion_pattern.sub("", body).strip()
        return clean_body, suggestion

    return body, None


def normalize_inline_comment(comment: InlineComment) -> InlineComment:
    """
    Normalize an inline comment by extracting embedded suggestions.

    If the comment body contains a ```suggestion block but the suggestion
    field is None, extract it and populate the suggestion field.

    Args:
        comment: The inline comment to normalize.

    Returns:
        A normalized InlineComment.
    """
    if comment.suggestion is not None:
        # Already has a suggestion, don't modify
        return comment

    clean_body, extracted_suggestion = extract_suggestion_from_body(comment.body)

    if extracted_suggestion is not None:
        return InlineComment(
            file_path=comment.file_path,
            line_number=comment.line_number,
            body=clean_body,
            suggestion=extracted_suggestion,
        )

    return comment


def normalize_inline_comments(
    comments: list[InlineComment],
) -> list[InlineComment]:
    """
    Normalize a list of inline comments.

    Args:
        comments: List of inline comments to normalize.

    Returns:
        List of normalized inline comments.
    """
    return [normalize_inline_comment(c) for c in comments]
