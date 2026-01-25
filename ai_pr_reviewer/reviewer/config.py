"""Reviewer configuration from .ai-reviewer.yml."""

import fnmatch
import logging
from dataclasses import dataclass, field
from typing import Any

import yaml

logger = logging.getLogger(__name__)


@dataclass
class TriggerCondition:
    """Condition that triggers a reviewer."""

    paths: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)


@dataclass
class Reviewer:
    """A reviewer configuration."""

    name: str
    prompt: str
    triggers: list[TriggerCondition] = field(default_factory=list)

    def matches(
        self, changed_files: list[str], labels: list[str], is_default: bool = False
    ) -> bool:
        """Check if this reviewer should be triggered."""
        # Default reviewer triggers on auto_review without conditions
        if is_default and not self.triggers:
            return True

        # No triggers means never auto-trigger (must be explicitly requested)
        if not self.triggers:
            return False

        for trigger in self.triggers:
            # Check path patterns
            if trigger.paths:
                for pattern in trigger.paths:
                    for file in changed_files:
                        if fnmatch.fnmatch(file, pattern):
                            return True

            # Check labels
            if trigger.labels:
                for label in trigger.labels:
                    if label in labels:
                        return True

        return False


@dataclass
class ReviewerConfig:
    """Repository reviewer configuration."""

    auto_review: bool = False
    reviewers: dict[str, Reviewer] = field(default_factory=dict)

    def get_triggered_reviewers(
        self, changed_files: list[str], labels: list[str]
    ) -> list[Reviewer]:
        """Get list of reviewers that should be triggered."""
        triggered = []

        for name, reviewer in self.reviewers.items():
            is_default = name == "default"

            # For auto_review, always trigger default reviewer
            if self.auto_review and is_default:
                triggered.append(reviewer)
                continue

            # Check if reviewer matches conditions
            if reviewer.matches(changed_files, labels):
                triggered.append(reviewer)

        return triggered


def load_reviewer_config(yaml_content: str) -> ReviewerConfig:
    """Parse .ai-reviewer.yml content into ReviewerConfig."""
    try:
        data: dict[str, Any] = yaml.safe_load(yaml_content) or {}
    except yaml.YAMLError as e:
        logger.error(f"Failed to parse .ai-reviewer.yml: {e}")
        raise ValueError(f"Invalid YAML: {e}") from e

    auto_review = data.get("auto_review", False)
    reviewers_data = data.get("reviewers", {})

    reviewers: dict[str, Reviewer] = {}
    for name, config in reviewers_data.items():
        if not isinstance(config, dict):
            continue

        prompt = config.get("prompt", "")
        triggers_data = config.get("triggers", [])

        triggers = []
        for trigger_data in triggers_data:
            if isinstance(trigger_data, dict):
                triggers.append(
                    TriggerCondition(
                        paths=trigger_data.get("paths", []),
                        labels=trigger_data.get("labels", []),
                    )
                )

        reviewers[name] = Reviewer(name=name, prompt=prompt, triggers=triggers)

    return ReviewerConfig(auto_review=auto_review, reviewers=reviewers)
