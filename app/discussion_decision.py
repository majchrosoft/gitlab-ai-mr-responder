from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from gitlab_client import Discussion, DiscussionNote
from iteration_guard import IterationGuard
from state import (
    DiscussionState,
    MergeRequestState,
    StateStore,
)


AI_NAMESPACE = "gitlab-ai-mr-responder"
AI_COMMENT_MARKER = f"<!-- {AI_NAMESPACE}:v1 -->"
AI_REVIEWER_MARKER = "<!-- klickcheck-reviewer:"
AI_CONTINUE_COMMAND = "AI-CONTINUE"


class DiscussionAction(str, Enum):
    IGNORE = "ignore"
    WAIT = "wait"
    FIX = "fix"
    CONTINUE = "continue"
    BLOCKED = "blocked"


class NoteAuthorType(str, Enum):
    HUMAN = "human"
    AI_RESPONDER = "ai_responder"
    AI_REVIEWER = "ai_reviewer"
    SYSTEM = "system"


@dataclass(frozen=True)
class DiscussionDecision:
    action: DiscussionAction
    reason: str
    note: DiscussionNote | None = None
    continue_note: DiscussionNote | None = None


class DiscussionDecisionEngine:
    def __init__(
        self,
        state_store: StateStore,
        iteration_guard: IterationGuard,
    ) -> None:
        self.state_store = state_store
        self.iteration_guard = iteration_guard

    def decide(
        self,
        discussion: Discussion,
        discussion_state: DiscussionState,
        merge_request_state: MergeRequestState,
    ) -> DiscussionDecision:
        notes = self._sorted_notes(discussion)

        if not notes:
            return DiscussionDecision(
                action=DiscussionAction.IGNORE,
                reason="discussion_has_no_notes",
            )

        # AI-CONTINUE always has priority over normal note handling.
        continue_note = self._find_new_continue_note(
            discussion,
            discussion_state,
        )

        if continue_note is not None:
            return self._decide_after_continue(
                discussion_state=discussion_state,
                merge_request_state=merge_request_state,
                continue_note=continue_note,
                notes=notes,
            )

        latest_note = self._latest_unprocessed_note(
            notes,
            discussion_state,
        )

        if latest_note is None:
            return DiscussionDecision(
                action=DiscussionAction.WAIT,
                reason="no_new_notes",
            )

        author_type = self.classify_note(latest_note)

        if author_type == NoteAuthorType.SYSTEM:
            return DiscussionDecision(
                action=DiscussionAction.IGNORE,
                reason="system_note",
                note=latest_note,
            )

        # Our own AI comment must never trigger another automatic
        # iteration. The human must explicitly use AI-CONTINUE.
        if author_type == NoteAuthorType.AI_RESPONDER:
            return DiscussionDecision(
                action=DiscussionAction.WAIT,
                reason="waiting_for_human_response_to_ai_comment",
                note=latest_note,
            )

        # A klickcheck-reviewer comment is an external review input.
        # It is NOT our AI responder and therefore can trigger the
        # first automatic iteration.
        if author_type == NoteAuthorType.AI_REVIEWER:
            return self._decide_for_review_comment(
                discussion_state=discussion_state,
                merge_request_state=merge_request_state,
                note=latest_note,
            )

        # Normal human comments are also treated as review input.
        return self._decide_for_human_comment(
            discussion_state=discussion_state,
            merge_request_state=merge_request_state,
            note=latest_note,
        )

    def classify_note(
        self,
        note: DiscussionNote,
    ) -> NoteAuthorType:
        if note.system:
            return NoteAuthorType.SYSTEM

        if self._is_ai_responder_comment(note):
            return NoteAuthorType.AI_RESPONDER

        if self._is_ai_reviewer_comment(note):
            return NoteAuthorType.AI_REVIEWER

        return NoteAuthorType.HUMAN

    def mark_decision_processed(
        self,
        discussion: Discussion,
        discussion_state: DiscussionState,
        decision: DiscussionDecision,
    ) -> None:
        if decision.note is not None:
            self.state_store.mark_note_processed(
                discussion_state,
                decision.note.id,
            )

        if decision.continue_note is not None:
            self.state_store.mark_continue_note_processed(
                discussion_state,
                decision.continue_note.id,
            )

        for note in discussion.notes:
            if note.system:
                self.state_store.mark_note_processed(
                    discussion_state,
                    note.id,
                )

    def _decide_for_review_comment(
        self,
        discussion_state: DiscussionState,
        merge_request_state: MergeRequestState,
        note: DiscussionNote,
    ) -> DiscussionDecision:
        guard_decision = (
            self.iteration_guard.can_start_automatic_iteration(
                discussion_state,
                merge_request_state,
            )
        )

        if not guard_decision.allowed:
            return DiscussionDecision(
                action=self._blocked_or_wait_action(
                    guard_decision.reason
                ),
                reason=guard_decision.reason,
                note=note,
            )

        return DiscussionDecision(
            action=DiscussionAction.FIX,
            reason="new_reviewer_comment",
            note=note,
        )

    def _decide_for_human_comment(
        self,
        discussion_state: DiscussionState,
        merge_request_state: MergeRequestState,
        note: DiscussionNote,
    ) -> DiscussionDecision:
        guard_decision = (
            self.iteration_guard.can_start_automatic_iteration(
                discussion_state,
                merge_request_state,
            )
        )

        if not guard_decision.allowed:
            return DiscussionDecision(
                action=self._blocked_or_wait_action(
                    guard_decision.reason
                ),
                reason=guard_decision.reason,
                note=note,
            )

        return DiscussionDecision(
            action=DiscussionAction.FIX,
            reason="new_human_review_comment",
            note=note,
        )

    def _decide_after_continue(
        self,
        discussion_state: DiscussionState,
        merge_request_state: MergeRequestState,
        continue_note: DiscussionNote,
        notes: list[DiscussionNote],
    ) -> DiscussionDecision:
        guard_decision = (
            self.iteration_guard.can_start_manual_iteration(
                discussion_state,
                merge_request_state,
            )
        )

        if not guard_decision.allowed:
            return DiscussionDecision(
                action=self._blocked_or_wait_action(
                    guard_decision.reason
                ),
                reason=guard_decision.reason,
                continue_note=continue_note,
            )

        return DiscussionDecision(
            action=DiscussionAction.CONTINUE,
            reason="ai_continue_command",
            continue_note=continue_note,
            note=self._latest_review_note(notes),
        )

    def _find_new_continue_note(
        self,
        discussion: Discussion,
        discussion_state: DiscussionState,
    ) -> DiscussionNote | None:
        for note in reversed(
            self._sorted_notes(discussion)
        ):
            if note.system:
                continue

            if self._is_ai_responder_comment(note):
                continue

            if self._is_ai_continue_command(note.body):
                if self.state_store.is_continue_note_processed(
                    discussion_state,
                    note.id,
                ):
                    continue

                return note

        return None

    def _latest_unprocessed_note(
        self,
        notes: list[DiscussionNote],
        discussion_state: DiscussionState,
    ) -> DiscussionNote | None:
        for note in reversed(notes):
            if note.system:
                continue

            if self.state_store.is_note_processed(
                discussion_state,
                note.id,
            ):
                continue

            return note

        return None

    @staticmethod
    def _latest_review_note(
        notes: list[DiscussionNote],
    ) -> DiscussionNote | None:
        for note in reversed(notes):
            if note.system:
                continue

            if AI_COMMENT_MARKER in note.body:
                continue

            if AI_CONTINUE_COMMAND in note.body.upper():
                continue

            return note

        return None

    @staticmethod
    def _classify_note_static(
        note: DiscussionNote,
    ) -> NoteAuthorType:
        if note.system:
            return NoteAuthorType.SYSTEM

        if AI_COMMENT_MARKER in note.body:
            return NoteAuthorType.AI_RESPONDER

        if AI_REVIEWER_MARKER in note.body:
            return NoteAuthorType.AI_REVIEWER

        return NoteAuthorType.HUMAN

    def _is_ai_responder_comment(
        self,
        note: DiscussionNote,
    ) -> bool:
        return AI_COMMENT_MARKER in note.body

    def _is_ai_reviewer_comment(
        self,
        note: DiscussionNote,
    ) -> bool:
        return AI_REVIEWER_MARKER in note.body

    @staticmethod
    def _is_ai_continue_command(
        body: str,
    ) -> bool:
        lines = [
            line.strip().upper()
            for line in body.splitlines()
        ]

        return any(
            line == AI_CONTINUE_COMMAND
            for line in lines
        )

    @staticmethod
    def _sorted_notes(
        discussion: Discussion,
    ) -> list[DiscussionNote]:
        return sorted(
            discussion.notes,
            key=lambda note: (
                note.created_at or "",
                note.id,
            ),
        )

    @staticmethod
    def _blocked_or_wait_action(
        reason: str,
    ) -> DiscussionAction:
        if reason in {
            "maximum_total_iterations_reached",
            "maximum_ai_commits_per_merge_request_reached",
        }:
            return DiscussionAction.BLOCKED

        return DiscussionAction.WAIT
