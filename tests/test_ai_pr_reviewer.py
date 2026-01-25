"""Unit tests for AI PR Reviewer components."""

import asyncio

import pytest

from ai_pr_reviewer.reviewer.config import (
    Reviewer,
    ReviewerConfig,
    TriggerCondition,
    load_reviewer_config,
)
from ai_pr_reviewer.reviewer.queue import (
    JobStatus,
    ReviewJob,
    ReviewQueue,
    create_job_id,
)
from ai_pr_reviewer.reviewer.runner import build_review_prompt


# === Config Tests ===


class TestLoadReviewerConfig:
    """Tests for .ai-reviewer.yml parsing."""

    def test_parse_basic_config(self) -> None:
        yaml_content = """
auto_review: true
reviewers:
  default:
    prompt: "General code review"
"""
        config = load_reviewer_config(yaml_content)
        assert config.auto_review is True
        assert "default" in config.reviewers
        assert config.reviewers["default"].prompt == "General code review"

    def test_parse_with_triggers(self) -> None:
        yaml_content = """
reviewers:
  security:
    prompt: "Security review"
    triggers:
      - paths: ["src/auth/**", "*.py"]
      - labels: ["security"]
"""
        config = load_reviewer_config(yaml_content)
        reviewer = config.reviewers["security"]
        assert len(reviewer.triggers) == 2
        assert reviewer.triggers[0].paths == ["src/auth/**", "*.py"]
        assert reviewer.triggers[1].labels == ["security"]

    def test_empty_config(self) -> None:
        config = load_reviewer_config("")
        assert config.auto_review is False
        assert config.reviewers == {}

    def test_invalid_yaml(self) -> None:
        with pytest.raises(ValueError, match="Invalid YAML"):
            load_reviewer_config("invalid: yaml: content:")


class TestReviewerMatches:
    """Tests for reviewer trigger matching."""

    def test_path_pattern_match(self) -> None:
        reviewer = Reviewer(
            name="security",
            prompt="test",
            triggers=[TriggerCondition(paths=["src/auth/*.py"])],
        )
        assert reviewer.matches(["src/auth/login.py"], []) is True
        assert reviewer.matches(["src/utils/helper.py"], []) is False

    def test_label_match(self) -> None:
        reviewer = Reviewer(
            name="perf",
            prompt="test",
            triggers=[TriggerCondition(labels=["performance"])],
        )
        assert reviewer.matches([], ["performance"]) is True
        assert reviewer.matches([], ["bug"]) is False

    def test_no_triggers_no_match(self) -> None:
        reviewer = Reviewer(name="default", prompt="test", triggers=[])
        assert reviewer.matches(["any.py"], ["any"]) is False


class TestGetTriggeredReviewers:
    """Tests for auto_review behavior."""

    def test_auto_review_triggers_all_without_conditions(self) -> None:
        config = ReviewerConfig(
            auto_review=True,
            reviewers={
                "default": Reviewer(name="default", prompt="p1"),
                "unittest": Reviewer(name="unittest", prompt="p2"),
            },
        )
        triggered = config.get_triggered_reviewers([], [])
        assert len(triggered) == 2

    def test_auto_review_respects_conditional_triggers(self) -> None:
        config = ReviewerConfig(
            auto_review=True,
            reviewers={
                "default": Reviewer(name="default", prompt="p1"),
                "security": Reviewer(
                    name="security",
                    prompt="p2",
                    triggers=[TriggerCondition(paths=["src/auth/*"])],
                ),
            },
        )
        # Only default triggers (security needs path match)
        triggered = config.get_triggered_reviewers(["README.md"], [])
        assert len(triggered) == 1
        assert triggered[0].name == "default"

        # Both trigger when path matches
        triggered = config.get_triggered_reviewers(["src/auth/login.py"], [])
        assert len(triggered) == 2


# === Queue Tests ===


class TestReviewQueue:
    """Tests for review job queue."""

    def test_enqueue_and_dequeue(self) -> None:
        queue = ReviewQueue()
        job = ReviewJob(
            job_id="test/repo#1#default",
            owner="test",
            repo="repo",
            pr_number=1,
            pr_title="Test PR",
            pr_body="",
            head_ref="feature",
            head_sha="abc123",
            base_ref="main",
            installation_id=123,
            reviewer_name="default",
            reviewer_prompt="Review this",
            changed_files=["file.py"],
            labels=[],
        )
        queue.enqueue(job)
        assert queue.queue_size() == 1

        dequeued = asyncio.get_event_loop().run_until_complete(queue.dequeue())
        assert dequeued.job_id == job.job_id
        assert dequeued.status == JobStatus.PROCESSING

    def test_duplicate_job_skipped(self) -> None:
        queue = ReviewQueue()
        job = ReviewJob(
            job_id="test/repo#1#default",
            owner="test",
            repo="repo",
            pr_number=1,
            pr_title="Test",
            pr_body="",
            head_ref="f",
            head_sha="a",
            base_ref="m",
            installation_id=1,
            reviewer_name="default",
            reviewer_prompt="p",
            changed_files=[],
            labels=[],
        )
        queue.enqueue(job)
        queue.enqueue(job)  # Duplicate
        assert queue.queue_size() == 1

    def test_complete_and_fail(self) -> None:
        queue = ReviewQueue()
        job = ReviewJob(
            job_id="x",
            owner="o",
            repo="r",
            pr_number=1,
            pr_title="t",
            pr_body="",
            head_ref="h",
            head_sha="s",
            base_ref="b",
            installation_id=1,
            reviewer_name="n",
            reviewer_prompt="p",
            changed_files=[],
            labels=[],
        )
        queue.enqueue(job)
        queue.complete("x")
        assert queue.get_job("x").status == JobStatus.COMPLETED

        job2 = ReviewJob(
            job_id="y",
            owner="o",
            repo="r",
            pr_number=2,
            pr_title="t",
            pr_body="",
            head_ref="h",
            head_sha="s",
            base_ref="b",
            installation_id=1,
            reviewer_name="n",
            reviewer_prompt="p",
            changed_files=[],
            labels=[],
        )
        queue.enqueue(job2)
        queue.fail("y", "error msg")
        assert queue.get_job("y").status == JobStatus.FAILED
        assert queue.get_job("y").error == "error msg"


def test_create_job_id() -> None:
    job_id = create_job_id("owner", "repo", 42, "security")
    assert job_id == "owner/repo#42#security"


# === Runner Tests ===


class TestBuildReviewPrompt:
    """Tests for prompt building."""

    def test_prompt_includes_reviewer_name(self) -> None:
        prompt = build_review_prompt(
            reviewer_name="security",
            reviewer_prompt="Check for vulnerabilities",
            pr_title="Add login",
            pr_body="New feature",
            pr_number=5,
            base_ref="main",
            head_ref="feature",
            changed_files=["auth.py", "login.py"],
        )
        assert "**security**" in prompt
        assert "[security]" in prompt
        assert "PR #5" in prompt
        assert "Add login" in prompt
        assert "- auth.py" in prompt
        assert "- login.py" in prompt
        assert "Check for vulnerabilities" in prompt

    def test_prompt_with_empty_body(self) -> None:
        prompt = build_review_prompt(
            reviewer_name="default",
            reviewer_prompt="Review",
            pr_title="Fix",
            pr_body="",
            pr_number=1,
            base_ref="main",
            head_ref="fix",
            changed_files=[],
        )
        assert "(No description provided)" in prompt
