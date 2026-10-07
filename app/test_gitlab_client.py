from __future__ import annotations

import json

import pytest

from gitlab_client import (
    CreatedMergeRequest,
    Discussion,
    DiscussionNote,
    GitLabApiError,
    GitLabClient,
)


class FakeResponse:
    def __init__(self, status_code: int, payload: object) -> None:
        self.status_code = status_code
        self._payload = payload
        self._is_json = not isinstance(payload, str)
        self.text = payload if isinstance(payload, str) else json.dumps(payload)

    def json(self) -> object:
        if not self._is_json:
            raise ValueError("invalid JSON")

        return self._payload


def make_client(
    responses: list[tuple[int, object]],
) -> tuple[GitLabClient, list[dict]]:
    client = GitLabClient(
        base_url="https://gitlab.example.com",
        token="secret-token",
    )

    calls: list[dict] = []
    iterator = iter(responses)

    def fake_request(method: str, url: str, **kwargs: object) -> FakeResponse:
        calls.append({"method": method, "url": url, **kwargs})
        status, payload = next(iterator)

        return FakeResponse(status, payload)

    client.session.request = fake_request

    return client, calls


def test_create_merge_request_success() -> None:
    client, _calls = make_client(
        [
            (
                201,
                {
                    "iid": 42,
                    "title": "fix: something",
                    "web_url": "https://gitlab.example.com/mr/42",
                    "source_branch": "ai/fix",
                    "target_branch": "fix/KCM-749/big-protocols-failure",
                    "diff_refs": {"head_sha": "abc123"},
                },
            )
        ]
    )

    created = client.create_merge_request(
        project_id=7,
        source_branch="ai/fix",
        target_branch="fix/KCM-749/big-protocols-failure",
        title="fix: something",
        description="desc",
    )

    assert created == CreatedMergeRequest(
        iid=42,
        title="fix: something",
        web_url="https://gitlab.example.com/mr/42",
        source_branch="ai/fix",
        target_branch="fix/KCM-749/big-protocols-failure",
        sha="abc123",
    )


def test_create_merge_request_null_diff_refs_does_not_raise() -> None:
    client, _calls = make_client(
        [
            (
                201,
                {
                    "iid": 42,
                    "title": "fix: something",
                    "web_url": "https://gitlab.example.com/mr/42",
                    "source_branch": "ai/fix",
                    "target_branch": "main",
                    "diff_refs": None,
                },
            )
        ]
    )

    created = client.create_merge_request(
        project_id=7,
        source_branch="ai/fix",
        target_branch="main",
        title="fix: something",
        description="desc",
    )

    assert created.sha == ""
    assert created.iid == 42
    assert created.web_url == "https://gitlab.example.com/mr/42"


def test_create_merge_request_error_response_includes_status_and_body() -> None:
    client, _calls = make_client(
        [(422, '{"message": "merge request already exists"}')]
    )

    with pytest.raises(GitLabApiError) as exc_info:
        client.create_merge_request(
            project_id=7,
            source_branch="ai/fix",
            target_branch="main",
            title="fix: something",
            description="desc",
        )

    message = str(exc_info.value)

    assert "422" in message
    assert "merge request already exists" in message
    assert "secret-token" not in message


def test_create_merge_request_non_json_success_raises() -> None:
    client, _calls = make_client([(201, "<html>not json</html>")])

    with pytest.raises(GitLabApiError) as exc_info:
        client.create_merge_request(
            project_id=7,
            source_branch="ai/fix",
            target_branch="main",
            title="fix: something",
            description="desc",
        )

    assert "invalid JSON" in str(exc_info.value)


def test_create_merge_request_unexpected_payload_raises() -> None:
    client, _calls = make_client([(201, ["not", "a", "dict"])])

    with pytest.raises(GitLabApiError) as exc_info:
        client.create_merge_request(
            project_id=7,
            source_branch="ai/fix",
            target_branch="main",
            title="fix: something",
            description="desc",
        )

    assert "unexpected payload" in str(exc_info.value)


def test_create_merge_request_missing_iid_raises() -> None:
    client, _calls = make_client([(201, {"title": "no iid here"})])

    with pytest.raises(GitLabApiError) as exc_info:
        client.create_merge_request(
            project_id=7,
            source_branch="ai/fix",
            target_branch="main",
            title="fix: something",
            description="desc",
        )

    assert "missing the merge request 'iid'" in str(exc_info.value)


def test_create_merge_request_note_posts_top_level_note() -> None:
    client, calls = make_client([(201, {"id": 999})])

    note_id = client.create_merge_request_note(
        project_id=14,
        merge_request_iid=667,
        body="top level comment",
    )

    assert note_id == 999
    assert calls[0]["method"] == "POST"
    assert (
        calls[0]["url"]
        == "https://gitlab.example.com/api/v4"
        "/projects/14/merge_requests/667/notes"
    )
    assert calls[0]["json"] == {"body": "top level comment"}


def test_post_discussion_reply_posts_to_discussion_notes() -> None:
    client, calls = make_client([(201, {"id": 1000})])

    note_id = client.post_discussion_reply(
        project_id=14,
        merge_request_iid=667,
        discussion_id="abc123def456",
        body="reply body",
    )

    assert note_id == 1000
    assert calls[0]["method"] == "POST"
    assert (
        calls[0]["url"]
        == "https://gitlab.example.com/api/v4"
        "/projects/14/merge_requests/667"
        "/discussions/abc123def456/notes"
    )
    assert calls[0]["json"] == {"body": "reply body"}


def test_post_discussion_reply_url_encodes_discussion_id() -> None:
    client, calls = make_client([(201, {"id": 1001})])

    note_id = client.post_discussion_reply(
        project_id=14,
        merge_request_iid=667,
        discussion_id="a1b2/c3?d4",
        body="reply",
    )

    assert note_id == 1001
    assert "/discussions/a1b2%2Fc3%3Fd4/notes" in calls[0]["url"]


def test_post_discussion_reply_missing_discussion_id_raises() -> None:
    client, _calls = make_client([])

    with pytest.raises(GitLabApiError) as exc_info:
        client.post_discussion_reply(
            project_id=14,
            merge_request_iid=667,
            discussion_id=None,
            body="reply",
        )

    assert "without a discussion_id" in str(exc_info.value)


def test_post_discussion_reply_api_error_propagates() -> None:
    client, _calls = make_client(
        [
            (
                403,
                '{"error": "insufficient_granular_scope"}',
            )
        ]
    )

    with pytest.raises(GitLabApiError) as exc_info:
        client.post_discussion_reply(
            project_id=14,
            merge_request_iid=667,
            discussion_id="abc123",
            body="reply",
        )

    assert "403" in str(exc_info.value)
    assert "insufficient_granular_scope" in str(exc_info.value)
    assert "secret-token" not in str(exc_info.value)


def test_post_discussion_reply_returns_none_without_id() -> None:
    client, _calls = make_client([(201, {"id": None})])

    assert (
        client.post_discussion_reply(
            project_id=14,
            merge_request_iid=667,
            discussion_id="abc123",
            body="reply",
        )
        is None
    )


def test_find_discussion_id_for_note_returns_parent_discussion() -> None:
    discussions = [
        Discussion(
            id="disc-1",
            notes=[
                make_note(1, "disc-1"),
                make_note(2, "disc-1"),
            ],
        ),
        Discussion(
            id="disc-2",
            notes=[make_note(3, "disc-2")],
        ),
    ]

    assert (
        GitLabClient.find_discussion_id_for_note(
            discussions,
            2,
        )
        == "disc-1"
    )
    assert (
        GitLabClient.find_discussion_id_for_note(
            discussions,
            3,
        )
        == "disc-2"
    )


def test_find_discussion_id_for_note_missing_returns_none() -> None:
    discussions = [
        Discussion(id="disc-1", notes=[make_note(1, "disc-1")])
    ]

    assert (
        GitLabClient.find_discussion_id_for_note(
            discussions,
            404,
        )
        is None
    )


def make_note(note_id: int, discussion_id: str) -> DiscussionNote:
    return DiscussionNote(
        id=note_id,
        discussion_id=discussion_id,
        body="body",
        author_username=None,
        author_id=None,
        created_at="2026-01-01T00:00:00Z",
        updated_at=None,
        system=False,
        resolvable=False,
        resolved=False,
        position=None,
    )


def test_get_merge_request_discussions_maps_notes() -> None:
    client, _calls = make_client(
        [
            (
                200,
                [
                    {
                        "id": "disc-1",
                        "individual_note": True,
                        "notes": [
                            {
                                "id": 10,
                                "body": "hello",
                                "system": False,
                                "resolvable": True,
                                "resolved": False,
                                "author": {
                                    "id": 5,
                                    "username": "hum",
                                },
                            }
                        ],
                    }
                ],
            )
        ]
    )

    discussions = client.get_merge_request_discussions(
        project_id=14,
        merge_request_iid=667,
    )

    assert len(discussions) == 1
    assert discussions[0].id == "disc-1"
    assert discussions[0].notes[0].id == 10
    assert discussions[0].notes[0].discussion_id == "disc-1"
    assert (
        GitLabClient.find_discussion_id_for_note(
            discussions, 10
        )
        == "disc-1"
    )


def test_create_merge_request_recovers_when_parsing_fails() -> None:
    client, calls = make_client(
        [
            (201, {"iid": "not-an-int", "diff_refs": None}),
            (200, [{"iid": 42, "web_url": "https://gitlab.example.com/mr/42"}]),
        ]
    )

    created = client.create_merge_request(
        project_id=7,
        source_branch="ai/fix",
        target_branch="main",
        title="fix: something",
        description="desc",
    )

    assert created.iid == 42
    assert created.sha == ""
    assert len(calls) == 2
    assert calls[1]["method"] == "GET"
