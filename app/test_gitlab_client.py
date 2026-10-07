from __future__ import annotations

import json

import pytest

from gitlab_client import CreatedMergeRequest, GitLabApiError, GitLabClient


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
