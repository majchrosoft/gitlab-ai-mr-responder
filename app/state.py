from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class DiscussionState:
    discussion_id: str

    iterations: int = 0
    automatic_iterations: int = 0
    manual_iterations: int = 0

    processed_note_ids: list[int] = field(default_factory=list)
    processed_comment_fingerprints: list[str] = field(
        default_factory=list
    )

    processed_continue_note_ids: list[int] = field(
        default_factory=list
    )

    last_processed_sha: str | None = None

    status: str = "new"

    last_ai_commit: str | None = None

    retry_pending: bool = False


@dataclass
class MergeRequestState:
    project_id: int
    merge_request_iid: int

    ai_commits: list[str] = field(default_factory=list)

    discussions: dict[str, DiscussionState] = field(
        default_factory=dict
    )


@dataclass
class State:
    merge_requests: dict[str, MergeRequestState] = field(
        default_factory=dict
    )


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.state = State()

        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return

        try:
            with self.path.open(
                "r",
                encoding="utf-8",
            ) as file:
                raw = json.load(file)
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"Unable to load state file {self.path}: {exc}"
            ) from exc

        self.state = self._deserialize_state(raw)

    def save(self) -> None:
        self.path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        temporary_path = self.path.with_suffix(
            self.path.suffix + ".tmp"
        )

        try:
            with temporary_path.open(
                "w",
                encoding="utf-8",
            ) as file:
                json.dump(
                    asdict(self.state),
                    file,
                    indent=2,
                    ensure_ascii=False,
                    sort_keys=True,
                )
                file.write("\n")

            temporary_path.replace(self.path)

        except OSError as exc:
            raise RuntimeError(
                f"Unable to save state file {self.path}: {exc}"
            ) from exc

    def get_merge_request(
        self,
        project_id: int,
        merge_request_iid: int,
    ) -> MergeRequestState:
        key = self.merge_request_key(
            project_id,
            merge_request_iid,
        )

        merge_request = self.state.merge_requests.get(key)

        if merge_request is None:
            merge_request = MergeRequestState(
                project_id=project_id,
                merge_request_iid=merge_request_iid,
            )

            self.state.merge_requests[key] = merge_request

        return merge_request

    def get_discussion(
        self,
        project_id: int,
        merge_request_iid: int,
        discussion_id: str,
    ) -> DiscussionState:
        merge_request = self.get_merge_request(
            project_id,
            merge_request_iid,
        )

        discussion = merge_request.discussions.get(
            discussion_id
        )

        if discussion is None:
            discussion = DiscussionState(
                discussion_id=discussion_id,
            )

            merge_request.discussions[discussion_id] = discussion

        return discussion

    def mark_note_processed(
        self,
        discussion: DiscussionState,
        note_id: int,
    ) -> None:
        if note_id not in discussion.processed_note_ids:
            discussion.processed_note_ids.append(note_id)

    def is_note_processed(
        self,
        discussion: DiscussionState,
        note_id: int,
    ) -> bool:
        return note_id in discussion.processed_note_ids

    def mark_fingerprint_processed(
        self,
        discussion: DiscussionState,
        fingerprint: str,
    ) -> None:
        if (
            fingerprint
            not in discussion.processed_comment_fingerprints
        ):
            discussion.processed_comment_fingerprints.append(
                fingerprint
            )

    def is_fingerprint_processed(
        self,
        discussion: DiscussionState,
        fingerprint: str,
    ) -> bool:
        return (
            fingerprint
            in discussion.processed_comment_fingerprints
        )

    def mark_continue_note_processed(
        self,
        discussion: DiscussionState,
        note_id: int,
    ) -> None:
        if (
            note_id
            not in discussion.processed_continue_note_ids
        ):
            discussion.processed_continue_note_ids.append(
                note_id
            )

    def is_continue_note_processed(
        self,
        discussion: DiscussionState,
        note_id: int,
    ) -> bool:
        return (
            note_id
            in discussion.processed_continue_note_ids
        )

    def record_automatic_iteration(
        self,
        discussion: DiscussionState,
        sha: str,
    ) -> None:
        discussion.iterations += 1
        discussion.automatic_iterations += 1
        discussion.last_processed_sha = sha
        discussion.status = "processing"
        discussion.retry_pending = False

    def record_manual_iteration(
        self,
        discussion: DiscussionState,
        sha: str,
    ) -> None:
        discussion.iterations += 1
        discussion.manual_iterations += 1
        discussion.last_processed_sha = sha
        discussion.status = "processing"
        discussion.retry_pending = False

    def record_tests_passed(
        self,
        discussion: DiscussionState,
    ) -> None:
        discussion.status = "tests_passed"

    def record_tests_failed(
        self,
        discussion: DiscussionState,
    ) -> None:
        discussion.status = "tests_failed"
        discussion.retry_pending = True

    def clear_retry_pending(
        self,
        discussion: DiscussionState,
    ) -> None:
        discussion.retry_pending = False

    def record_replied(
        self,
        discussion: DiscussionState,
    ) -> None:
        discussion.status = "replied"
        discussion.retry_pending = False

    def record_commit_started(
        self,
        discussion: DiscussionState,
    ) -> None:
        discussion.status = "committing"

    def record_ai_commit(
        self,
        merge_request: MergeRequestState,
        commit_sha: str,
    ) -> None:
        if commit_sha not in merge_request.ai_commits:
            merge_request.ai_commits.append(commit_sha)

    def record_commit_created(
        self,
        discussion: DiscussionState,
        commit_sha: str,
    ) -> None:
        discussion.last_ai_commit = commit_sha
        discussion.status = "committed"

    def record_push_started(
        self,
        discussion: DiscussionState,
    ) -> None:
        discussion.status = "pushing"

    def record_push_completed(
        self,
        discussion: DiscussionState,
    ) -> None:
        discussion.status = "pushed"

    def record_push_failed(
        self,
        discussion: DiscussionState,
    ) -> None:
        discussion.status = "push_failed"

    def record_completed_iteration(
        self,
        discussion: DiscussionState,
        commit_sha: str | None = None,
    ) -> None:
        discussion.status = "completed"
        discussion.retry_pending = False

        if commit_sha:
            discussion.last_ai_commit = commit_sha

    def block_discussion(
        self,
        discussion: DiscussionState,
        reason: str,
    ) -> None:
        discussion.status = f"blocked:{reason}"

    def get_ai_commit_count(
        self,
        merge_request: MergeRequestState,
    ) -> int:
        return len(merge_request.ai_commits)

    @staticmethod
    def merge_request_key(
        project_id: int,
        merge_request_iid: int,
    ) -> str:
        return f"{project_id}:{merge_request_iid}"

    @staticmethod
    def comment_fingerprint(
        discussion_id: str,
        note_id: int,
        body: str,
        sha: str | None,
        file_path: str | None = None,
        line: int | None = None,
    ) -> str:
        normalized_body = " ".join(body.split())

        raw = "|".join(
            [
                discussion_id,
                str(note_id),
                normalized_body,
                sha or "",
                file_path or "",
                str(line)
                if line is not None
                else "",
            ]
        )

        return hashlib.sha256(
            raw.encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _deserialize_state(
        raw: dict[str, Any],
    ) -> State:
        merge_requests: dict[str, MergeRequestState] = {}

        for key, mr_raw in raw.get(
            "merge_requests",
            {},
        ).items():
            discussions: dict[str, DiscussionState] = {}

            for discussion_id, discussion_raw in mr_raw.get(
                "discussions",
                {},
            ).items():
                discussions[discussion_id] = DiscussionState(
                    discussion_id=str(
                        discussion_raw.get(
                            "discussion_id",
                            discussion_id,
                        )
                    ),
                    iterations=int(
                        discussion_raw.get(
                            "iterations",
                            0,
                        )
                    ),
                    automatic_iterations=int(
                        discussion_raw.get(
                            "automatic_iterations",
                            0,
                        )
                    ),
                    manual_iterations=int(
                        discussion_raw.get(
                            "manual_iterations",
                            discussion_raw.get(
                                "manual_continuations",
                                0,
                            ),
                        )
                    ),
                    processed_note_ids=[
                        int(note_id)
                        for note_id in discussion_raw.get(
                            "processed_note_ids",
                            [],
                        )
                    ],
                    processed_comment_fingerprints=[
                        str(fingerprint)
                        for fingerprint in discussion_raw.get(
                            "processed_comment_fingerprints",
                            [],
                        )
                    ],
                    processed_continue_note_ids=[
                        int(note_id)
                        for note_id in discussion_raw.get(
                            "processed_continue_note_ids",
                            [],
                        )
                    ],
                    last_processed_sha=discussion_raw.get(
                        "last_processed_sha"
                    ),
                    status=str(
                        discussion_raw.get(
                            "status",
                            "new",
                        )
                    ),
                    last_ai_commit=discussion_raw.get(
                        "last_ai_commit"
                    ),
                    retry_pending=bool(
                        discussion_raw.get(
                            "retry_pending",
                            False,
                        )
                    ),
                )

            merge_requests[key] = MergeRequestState(
                project_id=int(
                    mr_raw["project_id"]
                ),
                merge_request_iid=int(
                    mr_raw["merge_request_iid"]
                ),
                ai_commits=[
                    str(commit)
                    for commit in mr_raw.get(
                        "ai_commits",
                        [],
                    )
                ],
                discussions=discussions,
            )

        return State(
            merge_requests=merge_requests,
        )
