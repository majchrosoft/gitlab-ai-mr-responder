from __future__ import annotations

from dataclasses import dataclass

from state import (
    DiscussionState,
    MergeRequestState,
)


@dataclass(frozen=True)
class IterationDecision:
    allowed: bool
    reason: str


class IterationGuard:
    def __init__(
        self,
        max_auto_iterations: int,
        max_total_iterations: int,
        max_ai_commits_per_merge_request: int,
    ) -> None:
        self.max_auto_iterations = max_auto_iterations
        self.max_total_iterations = max_total_iterations
        self.max_ai_commits_per_merge_request = (
            max_ai_commits_per_merge_request
        )

    def can_start_automatic_iteration(
        self,
        discussion: DiscussionState,
        merge_request: MergeRequestState,
    ) -> IterationDecision:
        if discussion.iterations >= self.max_total_iterations:
            return IterationDecision(
                allowed=False,
                reason="maximum_total_iterations_reached",
            )

        if (
            discussion.automatic_iterations
            < self.max_auto_iterations
        ):
            return IterationDecision(
                allowed=True,
                reason="automatic_iteration_available",
            )

        return IterationDecision(
            allowed=False,
            reason="waiting_for_ai_continue",
        )

    def can_start_manual_iteration(
        self,
        discussion: DiscussionState,
        merge_request: MergeRequestState,
    ) -> IterationDecision:
        if discussion.iterations >= self.max_total_iterations:
            return IterationDecision(
                allowed=False,
                reason="maximum_total_iterations_reached",
            )

        if (
            len(merge_request.ai_commits)
            >= self.max_ai_commits_per_merge_request
        ):
            return IterationDecision(
                allowed=False,
                reason="maximum_ai_commits_per_merge_request_reached",
            )

        return IterationDecision(
            allowed=True,
            reason="manual_continuation_available",
        )

    def can_create_ai_commit(
        self,
        merge_request: MergeRequestState,
    ) -> IterationDecision:
        if (
            len(merge_request.ai_commits)
            >= self.max_ai_commits_per_merge_request
        ):
            return IterationDecision(
                allowed=False,
                reason="maximum_ai_commits_per_merge_request_reached",
            )

        return IterationDecision(
            allowed=True,
            reason="ai_commit_available",
        )

    def has_reached_total_limit(
        self,
        discussion: DiscussionState,
    ) -> bool:
        return (
            discussion.iterations
            >= self.max_total_iterations
        )
