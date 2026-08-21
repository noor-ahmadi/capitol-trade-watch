from __future__ import annotations

import json
from io import BytesIO
from typing import Any
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request

import pytest

from capitol_trade_watch.alerts import DisclosureAlert, filing_marker
from capitol_trade_watch.github_issues import (
    GITHUB_API_VERSION,
    GitHubIssueClient,
    GitHubIssueError,
)


@pytest.fixture
def alert() -> DisclosureAlert:
    return DisclosureAlert(
        document_id="20030630",
        title="Nancy Pelosi PTR 20030630 (filed 2025-07-09)",
        body=f"{filing_marker('20030630')}\n\nTwo transactions.\n",
    )


class FakeResponse:
    def __init__(self, status: int, document: Any) -> None:
        self.status = status
        self._body = json.dumps(document).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class FakeGitHubApi:
    def __init__(
        self,
        issues: list[dict[str, Any]] | None = None,
        *,
        drop_assignment_on_create: bool = False,
    ) -> None:
        self.issues = issues or []
        self.drop_assignment_on_create = drop_assignment_on_create
        self.requests: list[Request] = []
        self.timeouts: list[float] = []

    def __call__(self, request: Request, *, timeout: float) -> FakeResponse:
        self.requests.append(request)
        self.timeouts.append(timeout)
        url = urlsplit(request.full_url)
        method = request.get_method()

        if method == "GET" and url.path == "/repos/noor/project/issues":
            query = parse_qs(url.query)
            assert query["state"] == ["all"]
            assert query["per_page"] == ["100"]
            page = int(query["page"][0])
            start = (page - 1) * 100
            return FakeResponse(200, self.issues[start : start + 100])

        if method == "POST" and url.path == "/repos/noor/project/issues":
            payload = _request_payload(request)
            assignees = [] if self.drop_assignment_on_create else [
                {"login": payload["assignees"][0]}
            ]
            issue = _issue(
                number=max((item["number"] for item in self.issues), default=0) + 1,
                body=payload["body"],
                assignees=assignees,
                title=payload["title"],
            )
            self.issues.append(issue)
            return FakeResponse(201, issue)

        if method == "POST" and url.path.endswith("/assignees"):
            issue_number = int(url.path.split("/")[-2])
            payload = _request_payload(request)
            issue = next(item for item in self.issues if item["number"] == issue_number)
            existing_logins = {
                assignee["login"].casefold() for assignee in issue["assignees"]
            }
            for login in payload["assignees"]:
                if login.casefold() not in existing_logins:
                    issue["assignees"].append({"login": login})
            return FakeResponse(201, issue)

        raise AssertionError(f"unexpected request: {method} {request.full_url}")


def test_publish_creates_once_then_reuses_the_issue(
    alert: DisclosureAlert,
) -> None:
    api = FakeGitHubApi()
    client = GitHubIssueClient(
        repository="noor/project",
        token="test-token",
        assignee="noor",
        opener=api,
        timeout=4,
    )

    first = client.publish(alert)
    second = client.publish(alert)

    assert (first.created, first.issue_number) == (True, 1)
    assert (second.created, second.issue_number) == (False, 1)
    assert first.issue_url == "https://github.com/noor/project/issues/1"
    create_requests = [
        request
        for request in api.requests
        if request.get_method() == "POST"
        and urlsplit(request.full_url).path == "/repos/noor/project/issues"
    ]
    assert len(create_requests) == 1
    payload = _request_payload(create_requests[0])
    assert payload == {
        "title": alert.title,
        "body": alert.body,
        "assignees": ["noor"],
    }
    headers = {
        name.casefold(): value for name, value in create_requests[0].header_items()
    }
    assert headers["authorization"] == "Bearer test-token"
    assert headers["x-github-api-version"] == GITHUB_API_VERSION
    assert api.issues[0]["assignees"] == [{"login": "noor"}]
    assert api.timeouts == [4, 4, 4]


def test_publish_repairs_a_missing_assignment(alert: DisclosureAlert) -> None:
    existing = _issue(
        number=7,
        body=alert.body,
        assignees=[],
        title=alert.title,
    )
    api = FakeGitHubApi([existing])
    client = GitHubIssueClient(
        repository="noor/project",
        token="test-token",
        assignee="noor",
        opener=api,
    )

    result = client.publish(alert)

    assert result.created is False
    assert result.issue_number == 7
    assert existing["assignees"] == [{"login": "noor"}]
    assert any(
        urlsplit(request.full_url).path.endswith("/issues/7/assignees")
        for request in api.requests
    )


def test_publish_checks_every_page_and_ignores_pull_requests(
    alert: DisclosureAlert,
) -> None:
    pull_request = _issue(
        number=1,
        body=alert.body,
        assignees=[{"login": "noor"}],
        title="not the filing issue",
    )
    pull_request["pull_request"] = {"url": "https://api.github.test/pulls/1"}
    filler = [
        _issue(
            number=number,
            body="unrelated",
            assignees=[{"login": "noor"}],
            title="unrelated",
        )
        for number in range(2, 101)
    ]
    filing_issue = _issue(
        number=101,
        body=alert.body,
        assignees=[{"login": "noor"}],
        title=alert.title,
    )
    api = FakeGitHubApi([pull_request, *filler, filing_issue])
    client = GitHubIssueClient(
        repository="noor/project",
        token="test-token",
        assignee="noor",
        opener=api,
    )

    result = client.publish(alert)

    assert (result.created, result.issue_number) == (False, 101)
    get_requests = [
        request for request in api.requests if request.get_method() == "GET"
    ]
    assert len(get_requests) == 2


def test_publish_refuses_existing_duplicates(alert: DisclosureAlert) -> None:
    issues = [
        _issue(
            number=number,
            body=alert.body,
            assignees=[{"login": "noor"}],
            title=alert.title,
        )
        for number in (4, 9)
    ]
    client = GitHubIssueClient(
        repository="noor/project",
        token="test-token",
        assignee="noor",
        opener=FakeGitHubApi(issues),
    )

    with pytest.raises(GitHubIssueError, match="multiple issues: 4, 9"):
        client.publish(alert)


def test_environment_client_uses_only_github_actions_values(
    alert: DisclosureAlert,
) -> None:
    api = FakeGitHubApi(drop_assignment_on_create=True)
    client = GitHubIssueClient.from_environment(
        environ={
            "GITHUB_TOKEN": "built-in-token",
            "GITHUB_REPOSITORY": "noor/project",
            "GITHUB_REPOSITORY_OWNER": "noor",
            "GITHUB_API_URL": "https://api.github.test",
        },
        opener=api,
    )

    result = client.publish(alert)

    assert result.created is True
    assert api.issues[0]["assignees"] == [{"login": "noor"}]
    assert all(
        request.full_url.startswith("https://api.github.test/")
        for request in api.requests
    )
    assert any(
        urlsplit(request.full_url).path.endswith("/issues/1/assignees")
        for request in api.requests
    )


def test_api_errors_do_not_expose_the_token(alert: DisclosureAlert) -> None:
    token = "do-not-print-this-token"

    def failing_opener(request: Request, *, timeout: float) -> FakeResponse:
        body = json.dumps({"message": f"bad credential {token}"}).encode("utf-8")
        raise HTTPError(request.full_url, 403, "forbidden", {}, BytesIO(body))

    client = GitHubIssueClient(
        repository="noor/project",
        token=token,
        assignee="noor",
        opener=failing_opener,
    )

    with pytest.raises(GitHubIssueError) as error_info:
        client.publish(alert)

    assert "HTTP 403" in str(error_info.value)
    assert token not in str(error_info.value)
    assert "[redacted]" in str(error_info.value)


def _issue(
    *,
    number: int,
    body: str,
    assignees: list[dict[str, str]],
    title: str,
) -> dict[str, Any]:
    return {
        "number": number,
        "title": title,
        "body": body,
        "state": "open",
        "html_url": f"https://github.com/noor/project/issues/{number}",
        "assignees": assignees,
    }


def _request_payload(request: Request) -> dict[str, Any]:
    assert request.data is not None
    payload = json.loads(request.data.decode("utf-8"))
    assert isinstance(payload, dict)
    return payload
