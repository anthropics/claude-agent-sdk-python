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
    display_name: str = ""  # Human-like name, e.g. "Stella [Unittest Sentinel]"
    avatar: str = ""  # Emoji avatar, e.g. "🔒" or "🧪"
    triggers: list[TriggerCondition] = field(default_factory=list)

    @property
    def persona(self) -> str:
        """Get avatar + display name, or fall back to name."""
        name = self.display_name or self.name
        return f"{self.avatar} {name}" if self.avatar else name

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
            # For auto_review, trigger all reviewers without specific triggers
            # This allows all configured reviewers to run on every PR
            if self.auto_review and not reviewer.triggers:
                triggered.append(reviewer)
                continue

            # Check if reviewer matches conditions (path patterns or labels)
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

        display_name = config.get("display_name", "")
        avatar = config.get("avatar", "")
        reviewers[name] = Reviewer(
            name=name, prompt=prompt, display_name=display_name, avatar=avatar, triggers=triggers
        )

    return ReviewerConfig(auto_review=auto_review, reviewers=reviewers)
