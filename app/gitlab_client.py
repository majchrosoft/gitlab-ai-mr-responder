from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlparse

import requests


class GitLabApiError(RuntimeError):
    pass


@dataclass(frozen=True)
class GitLabProject:
    id: int
    path: str
    path_with_namespace: str
    web_url: str


@dataclass(frozen=True)
class MergeRequest:
    iid: int
    title: str
    sha: str
    source_branch: str
    target_branch: str
    web_url: str


@dataclass(frozen=True)
class DiscussionNote:
    id: int
    discussion_id: str
    body: str
    author_username: str | None
    author_id: int | None
    created_at: str | None
    updated_at: str | None
    system: bool
    resolvable: bool
    resolved: bool
    position: dict[str, Any] | None


@dataclass(frozen=True)
class Discussion:
    id: str
    notes: list[DiscussionNote]


@dataclass(frozen=True)
class CreatedMergeRequest:
    iid: int
    title: str
    web_url: str
    source_branch: str
    target_branch: str
    sha: str


class GitLabClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        timeout: int = 30,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

        self.session = requests.Session()
        self.session.headers.update(
            {
                "PRIVATE-TOKEN": token,
                "Accept": "application/json",
            }
        )

    def _request_raw(
        self,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> requests.Response:
        url = f"{self.base_url}/api/v4{path}"

        try:
            return self.session.request(
                method=method,
                url=url,
                timeout=self.timeout,
                **kwargs,
            )
        except requests.RequestException as exc:
            raise GitLabApiError(
                f"GitLab request failed: {exc}"
            ) from exc

    @staticmethod
    def _parse_response(
        response: requests.Response,
        method: str,
        path: str,
    ) -> Any:
        if response.status_code >= 400:
            body = response.text.strip()

            raise GitLabApiError(
                "GitLab API returned "
                f"{response.status_code} for "
                f"{method} {path}: {body}"
            )

        try:
            return response.json()
        except ValueError as exc:
            raise GitLabApiError(
                "GitLab API returned invalid JSON for "
                f"{method} {path}: "
                f"{response.text.strip()}"
            ) from exc

    def _request(
        self,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> Any:
        response = self._request_raw(
            method,
            path,
            **kwargs,
        )

        return self._parse_response(
            response,
            method,
            path,
        )

    @staticmethod
    def project_path_from_url(
        project_url: str,
    ) -> str:
        parsed = urlparse(
            project_url.rstrip("/")
        )

        if not parsed.scheme or not parsed.netloc:
            raise GitLabApiError(
                f"Invalid GitLab project URL: {project_url}"
            )

        project_path = parsed.path.strip("/")

        if not project_path:
            raise GitLabApiError(
                f"GitLab project URL has no project path: "
                f"{project_url}"
            )

        return project_path

    def get_project(
        self,
        project_url: str,
    ) -> GitLabProject:
        project_path = self.project_path_from_url(
            project_url
        )

        encoded_project_path = quote(
            project_path,
            safe="",
        )

        data = self._request(
            "GET",
            f"/projects/{encoded_project_path}",
        )

        return GitLabProject(
            id=int(data["id"]),
            path=str(data["path"]),
            path_with_namespace=str(
                data["path_with_namespace"]
            ),
            web_url=str(data["web_url"]),
        )

    def get_open_merge_requests(
        self,
        project_id: int,
    ) -> list[MergeRequest]:
        merge_requests: list[MergeRequest] = []

        page = 1

        while True:
            data = self._request(
                "GET",
                f"/projects/{project_id}/merge_requests",
                params={
                    "state": "opened",
                    "scope": "all",
                    "per_page": 100,
                    "page": page,
                },
            )

            if not data:
                break

            for item in data:
                title = str(item["title"])

                if title.lower().startswith("draft:"):
                    continue

                merge_requests.append(
                    MergeRequest(
                        iid=int(item["iid"]),
                        title=title,
                        sha=str(item["sha"]),
                        source_branch=str(
                            item["source_branch"]
                        ),
                        target_branch=str(
                            item["target_branch"]
                        ),
                        web_url=str(
                            item["web_url"]
                        ),
                    )
                )

            if len(data) < 100:
                break

            page += 1

        return merge_requests

    @staticmethod
    def find_discussion_id_for_note(
        discussions: list[Discussion],
        note_id: int,
    ) -> str | None:
        for discussion in discussions:
            for note in discussion.notes:
                if note.id == note_id:
                    return discussion.id

        return None

    def create_merge_request_note(
        self,
        project_id: int,
        merge_request_iid: int,
        body: str,
    ) -> int | None:
        """Create a new top-level merge request note."""
        data = self._request(
            "POST",
            (
                f"/projects/{project_id}"
                f"/merge_requests/{merge_request_iid}"
                "/notes"
            ),
            json={"body": body},
        )

        if isinstance(data, dict) and data.get("id") is not None:
            return int(data["id"])

        return None

    def post_discussion_reply(
        self,
        project_id: int,
        merge_request_iid: int,
        discussion_id: str | None,
        body: str,
    ) -> int | None:
        """Reply inside an existing merge request discussion."""
        if not discussion_id:
            raise GitLabApiError(
                "Cannot reply to a discussion without "
                "a discussion_id. Use "
                "create_merge_request_note() to create "
                "a new top-level note instead."
            )

        encoded_discussion_id = quote(
            discussion_id,
            safe="",
        )

        data = self._request(
            "POST",
            (
                f"/projects/{project_id}"
                f"/merge_requests/{merge_request_iid}"
                f"/discussions/{encoded_discussion_id}"
                "/notes"
            ),
            json={"body": body},
        )

        if isinstance(data, dict) and data.get("id") is not None:
            return int(data["id"])

        return None

    def create_merge_request(
        self,
        project_id: int,
        source_branch: str,
        target_branch: str,
        title: str,
        description: str,
    ) -> CreatedMergeRequest:
        response = self._request_raw(
            "POST",
            f"/projects/{project_id}/merge_requests",
            json={
                "source_branch": source_branch,
                "target_branch": target_branch,
                "title": title,
                "description": description,
            },
        )

        path = f"/projects/{project_id}/merge_requests"

        try:
            data = self._parse_response(
                response,
                "POST",
                path,
            )
        except GitLabApiError as exc:
            raise GitLabApiError(
                f"{exc} (response status: "
                f"{response.status_code})"
            ) from exc

        if not isinstance(data, dict):
            raise GitLabApiError(
                "GitLab create merge request returned "
                f"unexpected payload (status "
                f"{response.status_code}): {data!r}"
            )

        if data.get("iid") is None:
            raise GitLabApiError(
                "GitLab create merge request response "
                "is missing the merge request 'iid' "
                f"(status {response.status_code}): "
                f"{response.text.strip()}"
            )

        try:
            return self._parse_created_merge_request(
                data,
                title=title,
                source_branch=source_branch,
                target_branch=target_branch,
            )
        except Exception as exc:
            return self._recover_created_merge_request(
                exc,
                project_id=project_id,
                source_branch=source_branch,
                target_branch=target_branch,
            )

    @staticmethod
    def _parse_created_merge_request(
        data: dict[str, Any],
        title: str,
        source_branch: str,
        target_branch: str,
    ) -> CreatedMergeRequest:
        diff_refs = data.get("diff_refs")

        if not isinstance(diff_refs, dict):
            diff_refs = {}

        return CreatedMergeRequest(
            iid=int(data["iid"]),
            title=str(data.get("title", title)),
            web_url=str(data.get("web_url", "")),
            source_branch=str(
                data.get("source_branch", source_branch)
            ),
            target_branch=str(
                data.get("target_branch", target_branch)
            ),
            sha=str(diff_refs.get("head_sha", "")),
        )

    def _recover_created_merge_request(
        self,
        parsing_error: Exception,
        project_id: int,
        source_branch: str,
        target_branch: str,
    ) -> CreatedMergeRequest:
        try:
            data = self._request(
                "GET",
                f"/projects/{project_id}/merge_requests",
                params={
                    "source_branch": source_branch,
                    "target_branch": target_branch,
                    "state": "all",
                },
            )
        except GitLabApiError as exc:
            raise GitLabApiError(
                "GitLab merge request response parsing "
                f"failed: {parsing_error}. Could not "
                "confirm whether the merge request was "
                f"created: {exc}"
            ) from parsing_error

        created = None

        if isinstance(data, list):
            for item in data:
                if not isinstance(item, dict):
                    continue

                if item.get("iid") is None:
                    continue

                created = item
                break

        if created is None:
            raise GitLabApiError(
                "GitLab merge request response parsing "
                f"failed: {parsing_error}, and no merge "
                "request was found for "
                f"{source_branch} -> {target_branch}. "
                "Operation failed."
            ) from parsing_error

        return self._parse_created_merge_request(
            created,
            title=f"merge request from {source_branch}",
            source_branch=source_branch,
            target_branch=target_branch,
        )

    def get_merge_request_discussions(
        self,
        project_id: int,
        merge_request_iid: int,
    ) -> list[Discussion]:
        discussions: list[Discussion] = []

        page = 1

        while True:
            data = self._request(
                "GET",
                (
                    f"/projects/{project_id}"
                    f"/merge_requests/{merge_request_iid}"
                    "/discussions"
                ),
                params={
                    "per_page": 100,
                    "page": page,
                },
            )

            if not data:
                break

            for discussion_data in data:
                discussion_id = str(
                    discussion_data["id"]
                )

                notes: list[DiscussionNote] = []

                for note_data in discussion_data.get(
                    "notes",
                    [],
                ):
                    author = note_data.get(
                        "author"
                    )

                    position = note_data.get(
                        "position"
                    )

                    notes.append(
                        DiscussionNote(
                            id=int(note_data["id"]),
                            discussion_id=(
                                discussion_id
                            ),
                            body=str(
                                note_data.get(
                                    "body",
                                    "",
                                )
                            ),
                            author_username=(
                                str(
                                    author["username"]
                                )
                                if author
                                and author.get(
                                    "username"
                                )
                                else None
                            ),
                            author_id=(
                                int(author["id"])
                                if author
                                and author.get("id")
                                is not None
                                else None
                            ),
                            created_at=(
                                str(
                                    note_data[
                                        "created_at"
                                    ]
                                )
                                if note_data.get(
                                    "created_at"
                                )
                                else None
                            ),
                            updated_at=(
                                str(
                                    note_data[
                                        "updated_at"
                                    ]
                                )
                                if note_data.get(
                                    "updated_at"
                                )
                                else None
                            ),
                            system=bool(
                                note_data.get(
                                    "system",
                                    False,
                                )
                            ),
                            resolvable=bool(
                                note_data.get(
                                    "resolvable",
                                    False,
                                )
                            ),
                            resolved=bool(
                                note_data.get(
                                    "resolved",
                                    False,
                                )
                            ),
                            position=position,
                        )
                    )

                discussions.append(
                    Discussion(
                        id=discussion_id,
                        notes=notes,
                    )
                )

            if len(data) < 100:
                break

            page += 1

        return discussions
