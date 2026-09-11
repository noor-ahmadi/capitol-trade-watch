from __future__ import annotations

import json
from dataclasses import replace
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
from capitol_trade_watch.health import HEALTH_ISSUE_MARKER, build_health_report
from capitol_trade_watch.monitor import report_monitor_health


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
        ignore_state_updates: bool = False,
    ) -> None:
        self.issues = issues or []
        self.drop_assignment_on_create = drop_assignment_on_create
        self.ignore_state_updates = ignore_state_updates
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

        if method == "PATCH" and url.path.startswith("/repos/noor/project/issues/"):
            issue_number = int(url.path.split("/")[-1])
            payload = _request_payload(request)
            issue = next(item for item in self.issues if item["number"] == issue_number)
            if self.ignore_state_updates:
                payload.pop("state", None)
            issue.update(payload)
            return FakeResponse(200, issue)

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


def test_health_issue_reuses_failures_closes_on_recovery_and_reopens(
    alert: DisclosureAlert,
) -> None:
    api = FakeGitHubApi()
    client = GitHubIssueClient(
        repository="noor/project", token="test-token", assignee="noor", opener=api
    )
    environ = {"GITHUB_RUN_ID": "12345", "GITHUB_REPOSITORY": "noor/project"}
    failed = build_health_report(healthy=False, environ=environ)
    recovered = build_health_report(healthy=True, environ=environ)
    assert client.report_health(recovered) is None
    assert api.issues == []

    client.publish(alert)
    first = client.report_health(failed)
    assert first is not None and first.created
    assert first.issue_number == 2
    assert api.issues[1]["state"] == "open"
    assert api.issues[1]["assignees"] == [{"login": "noor"}]
    first_failure_body = api.issues[1]["body"]

    environ["GITHUB_RUN_ID"] = "12346"
    failed_again = build_health_report(healthy=False, environ=environ)
    before = len(api.requests)
    repeated = client.report_health(failed_again)
    assert repeated is not None and not repeated.created
    assert repeated.issue_number == first.issue_number
    assert api.issues[1]["body"] == first_failure_body
    assert all(request.get_method() == "GET" for request in api.requests[before:])

    recovered = build_health_report(healthy=True, environ=environ)
    client.report_health(recovered)
    assert api.issues[1]["state"] == "closed"
    assert api.issues[1]["state_reason"] == "completed"
    assert api.issues[1]["body"] == recovered.body
    before = len(api.requests)
    client.report_health(recovered)
    assert all(request.get_method() == "GET" for request in api.requests[before:])

    reopened = client.report_health(failed_again)
    assert reopened is not None and not reopened.created
    assert reopened.issue_number == first.issue_number
    assert len(api.issues) == 2
    assert api.issues[1]["state"] == "open"
    assert api.issues[1]["state_reason"] == "reopened"
    assert api.issues[1]["title"] == failed_again.title
    assert api.issues[1]["body"] == failed_again.body
    assert api.issues[0]["body"] == alert.body
    assert api.issues[0]["state"] == "open"
    assert not any("/comments" in request.full_url for request in api.requests)


def test_health_notice_repairs_assignment_and_uses_the_workflow_environment() -> None:
    api = FakeGitHubApi(drop_assignment_on_create=True)
    client = GitHubIssueClient(
        repository="noor/project", token="test-token", assignee="noor", opener=api
    )
    environ = {"GITHUB_RUN_ID": "12345", "GITHUB_REPOSITORY": "noor/project"}

    result = report_monitor_health(healthy=False, environ=environ, client=client)

    assert result is not None and result.created
    assert api.issues[0]["assignees"] == [{"login": "noor"}]
    assert "/actions/runs/12345" in api.issues[0]["body"]
    api.issues[0]["assignees"] = []
    reused = report_monitor_health(healthy=False, environ=environ, client=client)
    assert reused is not None and not reused.created
    assert len(api.issues) == 1
    assert api.issues[0]["assignees"] == [{"login": "noor"}]


def test_health_recovery_searches_every_page_and_ignores_pull_requests() -> None:
    environ = {"GITHUB_RUN_ID": "12345", "GITHUB_REPOSITORY": "noor/project"}
    report = build_health_report(healthy=True, environ=environ)
    issues = [
        _issue(number=number, body="unrelated", assignees=[], title="unrelated")
        for number in range(1, 102)
    ]
    issues[0].update(body=HEALTH_ISSUE_MARKER, pull_request={"url": "pull/1"})
    issues[100]["body"] = HEALTH_ISSUE_MARKER
    api = FakeGitHubApi(issues)
    client = GitHubIssueClient(
        repository="noor/project", token="test-token", assignee="noor", opener=api
    )

    result = client.report_health(report)

    assert result is not None and result.issue_number == 101
    assert issues[100]["state"] == "closed"
    assert all(issue["state"] == "open" for issue in issues[:100])
    assert len([request for request in api.requests if request.get_method() == "GET"]) == 2


@pytest.mark.parametrize("healthy", [False, True])
def test_health_updates_refuse_duplicate_issues(healthy: bool) -> None:
    api = FakeGitHubApi([
        _issue(number=number, body=HEALTH_ISSUE_MARKER, assignees=[], title="health")
        for number in (4, 9)
    ])
    client = GitHubIssueClient(
        repository="noor/project", token="test-token", assignee="noor", opener=api
    )
    report = build_health_report(
        healthy=healthy,
        environ={"GITHUB_RUN_ID": "12345", "GITHUB_REPOSITORY": "noor/project"},
    )

    with pytest.raises(GitHubIssueError, match="multiple monitor health issues.*4, 9"):
        client.report_health(report)
    assert all(request.get_method() == "GET" for request in api.requests)


@pytest.mark.parametrize("body", ["missing marker", HEALTH_ISSUE_MARKER * 2])
def test_health_report_requires_its_marker_before_contacting_github(body: str) -> None:
    api = FakeGitHubApi()
    client = GitHubIssueClient(
        repository="noor/project", token="test-token", assignee="noor", opener=api
    )
    report = build_health_report(
        healthy=False,
        environ={"GITHUB_RUN_ID": "12345", "GITHUB_REPOSITORY": "noor/project"},
    )

    with pytest.raises(GitHubIssueError, match="must contain its marker once"):
        client.report_health(replace(report, body=body))
    assert api.requests == []


@pytest.mark.parametrize("healthy", [False, True])
def test_health_updates_verify_github_changed_the_issue_state(healthy: bool) -> None:
    issue = _issue(
        number=4, body=HEALTH_ISSUE_MARKER, assignees=[{"login": "noor"}], title="health"
    )
    issue["state"] = "open" if healthy else "closed"
    api = FakeGitHubApi([issue], ignore_state_updates=True)
    client = GitHubIssueClient(
        repository="noor/project", token="test-token", assignee="noor", opener=api
    )
    report = build_health_report(
        healthy=healthy,
        environ={"GITHUB_RUN_ID": "12345", "GITHUB_REPOSITORY": "noor/project"},
    )

    with pytest.raises(GitHubIssueError, match="did not mark health issue 4"):
        client.report_health(report)


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
