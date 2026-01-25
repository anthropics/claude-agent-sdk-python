"""Unit tests for AI PR Reviewer components."""

import pytest

from ai_pr_reviewer.reviewer.config import (
    Reviewer, ReviewerConfig, TriggerCondition, load_reviewer_config,
)
from ai_pr_reviewer.reviewer.queue import (
    JobStatus, ReviewJob, ReviewQueue, create_job_id,
)
from ai_pr_reviewer.reviewer.runner import build_review_prompt


# === Config Tests ===

class TestLoadReviewerConfig:
    def test_parse_basic_config(self) -> None:
        config = load_reviewer_config("auto_review: true\nreviewers:\n  default:\n    prompt: 'Review'")
        assert config.auto_review is True
        assert "default" in config.reviewers

    def test_parse_with_triggers(self) -> None:
        yaml = "reviewers:\n  sec:\n    prompt: p\n    triggers:\n      - paths: ['*.py']\n      - labels: ['sec']"
        config = load_reviewer_config(yaml)
        assert len(config.reviewers["sec"].triggers) == 2

    def test_empty_config(self) -> None:
        config = load_reviewer_config("")
        assert config.auto_review is False and config.reviewers == {}

    def test_invalid_yaml(self) -> None:
        with pytest.raises(ValueError, match="Invalid YAML"):
            load_reviewer_config("invalid: yaml: :")


class TestReviewerMatches:
    def test_path_pattern_match(self) -> None:
        r = Reviewer(name="s", prompt="p", triggers=[TriggerCondition(paths=["src/*.py"])])
        assert r.matches(["src/a.py"], []) and not r.matches(["lib/b.py"], [])

    def test_label_match(self) -> None:
        r = Reviewer(name="p", prompt="p", triggers=[TriggerCondition(labels=["perf"])])
        assert r.matches([], ["perf"]) and not r.matches([], ["bug"])

    def test_no_triggers_no_match(self) -> None:
        assert not Reviewer(name="d", prompt="p", triggers=[]).matches(["any.py"], ["any"])


class TestGetTriggeredReviewers:
    def test_auto_review_triggers_all_without_conditions(self) -> None:
        cfg = ReviewerConfig(auto_review=True, reviewers={
            "a": Reviewer(name="a", prompt="p"), "b": Reviewer(name="b", prompt="p")
        })
        assert len(cfg.get_triggered_reviewers([], [])) == 2

    def test_auto_review_respects_conditional_triggers(self) -> None:
        cfg = ReviewerConfig(auto_review=True, reviewers={
            "d": Reviewer(name="d", prompt="p"),
            "s": Reviewer(name="s", prompt="p", triggers=[TriggerCondition(paths=["auth/*"])]),
        })
        assert len(cfg.get_triggered_reviewers(["README.md"], [])) == 1
        assert len(cfg.get_triggered_reviewers(["auth/login.py"], [])) == 2


# === Queue Tests ===

def _make_job(job_id: str = "o/r#1#d", pr_number: int = 1) -> ReviewJob:
    return ReviewJob(
        job_id=job_id, owner="o", repo="r", pr_number=pr_number, pr_title="t",
        pr_body="", head_ref="h", head_sha="s", base_ref="b", installation_id=1,
        reviewer_name="d", reviewer_prompt="p", reviewer_persona="Test User",
        changed_files=[], labels=[],
    )


class TestReviewQueue:
    @pytest.mark.asyncio
    async def test_enqueue_dequeue(self) -> None:
        q = ReviewQueue()
        q.enqueue(_make_job())
        assert q.queue_size() == 1
        job = await q.dequeue()
        assert job.status == JobStatus.PROCESSING

    def test_duplicate_skipped(self) -> None:
        q = ReviewQueue()
        q.enqueue(_make_job("x"))
        q.enqueue(_make_job("x"))
        assert q.queue_size() == 1

    def test_complete_and_fail(self) -> None:
        q = ReviewQueue()
        q.enqueue(_make_job("a"))
        q.complete("a")
        assert q.get_job("a").status == JobStatus.COMPLETED

        q.enqueue(_make_job("b", pr_number=2))
        q.fail("b", "err")
        assert q.get_job("b").status == JobStatus.FAILED and q.get_job("b").error == "err"


def test_create_job_id() -> None:
    assert create_job_id("owner", "repo", 42, "sec") == "owner/repo#42#sec"


# === Runner Tests ===

class TestBuildReviewPrompt:
    def test_prompt_includes_reviewer_info(self) -> None:
        prompt = build_review_prompt(
            reviewer_name="security", reviewer_prompt="Check vulns", pr_title="Add login",
            pr_body="New feature", pr_number=5, base_ref="main", head_ref="feat",
            changed_files=["auth.py"], reviewer_persona="Stella [Security Expert]",
        )
        assert all(x in prompt for x in ["**Stella [Security Expert]**", "Pull Request #5", "auth.py"])

    def test_prompt_with_empty_body(self) -> None:
        prompt = build_review_prompt(
            reviewer_name="d", reviewer_prompt="R", pr_title="F", pr_body="",
            pr_number=1, base_ref="m", head_ref="f", changed_files=[],
        )
        assert "(No description provided)" in prompt
