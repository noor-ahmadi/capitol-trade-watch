"""Publish filing alerts as idempotent, assigned GitHub issues."""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from capitol_trade_watch.alerts import DisclosureAlert, filing_marker
from capitol_trade_watch.house_index import USER_AGENT

GITHUB_API_VERSION = "2026-03-10"
_PER_PAGE = 100
_MAX_PAGES = 100


class GitHubIssueError(RuntimeError):
    """Raised when an alert cannot be published exactly once and assigned."""


@dataclass(frozen=True, slots=True)
class PublishResult:
    """The GitHub issue reused or created for one filing."""

    created: bool
    issue_number: int
    issue_url: str


class GitHubIssueClient:
    """Create one assigned issue per House document using the GitHub REST API."""

    def __init__(
        self,
        *,
        repository: str,
        token: str,
        assignee: str,
        opener: Callable[..., Any] = urlopen,
        api_url: str = "https://api.github.com",
        timeout: float = 15.0,
    ) -> None:
        self._owner, self._repository = _split_repository(repository)
        if not token.strip():
            raise GitHubIssueError("GitHub token is missing")
        if not assignee.strip():
            raise GitHubIssueError("GitHub issue assignee is missing")
        if not api_url.startswith("https://"):
            raise GitHubIssueError("GitHub API URL must use HTTPS")

        self._token = token.strip()
        self._assignee = assignee.strip()
        self._opener = opener
        self._api_url = api_url.rstrip("/")
        self._timeout = timeout

    @classmethod
    def from_environment(
        cls,
        *,
        environ: Mapping[str, str] | None = None,
        opener: Callable[..., Any] = urlopen,
        timeout: float = 15.0,
    ) -> GitHubIssueClient:
        """Build a client from standard GitHub Actions environment values."""
        values = os.environ if environ is None else environ
        repository = _required_environment(values, "GITHUB_REPOSITORY")
        owner = values.get("GITHUB_REPOSITORY_OWNER") or repository.split("/", 1)[0]
        return cls(
            repository=repository,
            token=_required_environment(values, "GITHUB_TOKEN"),
            assignee=owner,
            opener=opener,
            api_url=values.get("GITHUB_API_URL", "https://api.github.com"),
            timeout=timeout,
        )

    def publish(self, alert: DisclosureAlert) -> PublishResult:
        """Reuse or create the single assigned issue for an alert."""
        marker = filing_marker(alert.document_id)
        if alert.body.count(marker) != 1:
            raise GitHubIssueError(
                f"alert for House document {alert.document_id} must contain "
                "its marker once"
            )

        matches = [
            issue
            for issue in self._list_issues()
            if marker in _issue_body(issue) and "pull_request" not in issue
        ]
        if len(matches) > 1:
            issue_numbers = ", ".join(
                str(_issue_number(issue)) for issue in matches
            )
            raise GitHubIssueError(
                f"House document {alert.document_id} already has multiple issues: "
                f"{issue_numbers}"
            )
        if matches:
            issue = self._ensure_assignee(matches[0])
            return self._publish_result(issue, created=False)

        issue = self._create_issue(alert)
        issue = self._ensure_assignee(issue)
        return self._publish_result(issue, created=True)

    def _list_issues(self) -> list[dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        for page in range(1, _MAX_PAGES + 1):
            response = self._request_json(
                "GET",
                (
                    f"{self._repository_path}/issues"
                    f"?state=all&per_page={_PER_PAGE}&page={page}"
                ),
                expected_status=200,
            )
            if not isinstance(response, list):
                raise GitHubIssueError(
                    "GitHub returned an invalid repository issue list"
                )
            page_issues = [
                issue for issue in response if isinstance(issue, dict)
            ]
            if len(page_issues) != len(response):
                raise GitHubIssueError("GitHub returned a malformed repository issue")
            issues.extend(page_issues)
            if len(page_issues) < _PER_PAGE:
                return issues
        raise GitHubIssueError("repository issue pagination exceeded the safety limit")

    def _create_issue(self, alert: DisclosureAlert) -> dict[str, Any]:
        response = self._request_json(
            "POST",
            f"{self._repository_path}/issues",
            payload={
                "title": alert.title,
                "body": alert.body,
                "assignees": [self._assignee],
            },
            expected_status=201,
        )
        if not isinstance(response, dict):
            raise GitHubIssueError("GitHub returned an invalid created issue")
        return response

    def _ensure_assignee(self, issue: dict[str, Any]) -> dict[str, Any]:
        if _has_assignee(issue, self._assignee):
            return issue

        issue_number = _issue_number(issue)
        response = self._request_json(
            "POST",
            f"{self._repository_path}/issues/{issue_number}/assignees",
            payload={"assignees": [self._assignee]},
            expected_status=201,
        )
        if not isinstance(response, dict) or not _has_assignee(
            response,
            self._assignee,
        ):
            raise GitHubIssueError(
                f"GitHub did not assign issue {issue_number} to {self._assignee}"
            )
        return response

    def _publish_result(
        self,
        issue: dict[str, Any],
        *,
        created: bool,
    ) -> PublishResult:
        issue_number = _issue_number(issue)
        issue_url = issue.get("html_url")
        if not isinstance(issue_url, str) or not issue_url.startswith("https://"):
            raise GitHubIssueError(
                f"GitHub issue {issue_number} has no valid public URL"
            )
        return PublishResult(
            created=created,
            issue_number=issue_number,
            issue_url=issue_url,
        )

    @property
    def _repository_path(self) -> str:
        owner = quote(self._owner, safe="")
        repository = quote(self._repository, safe="")
        return f"/repos/{owner}/{repository}"

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        expected_status: int,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(
            f"{self._api_url}{path}",
            data=data,
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
                "X-GitHub-Api-Version": GITHUB_API_VERSION,
            },
        )
        try:
            response = self._opener(request, timeout=self._timeout)
            with response:
                status = getattr(response, "status", 200)
                response_bytes = response.read()
        except HTTPError as error:
            error_bytes = error.read()
            message = _github_error_message(error_bytes).replace(
                self._token,
                "[redacted]",
            )
            detail = f": {message}" if message else ""
            raise GitHubIssueError(
                f"GitHub API request failed with HTTP {error.code}{detail}"
            ) from error
        except (TimeoutError, URLError, OSError) as error:
            raise GitHubIssueError(
                f"could not reach the GitHub API: {error}"
            ) from error

        if status != expected_status:
            raise GitHubIssueError(
                f"GitHub API returned HTTP {status}; expected {expected_status}"
            )
        try:
            return json.loads(response_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise GitHubIssueError("GitHub returned invalid JSON") from error


def _split_repository(repository: str) -> tuple[str, str]:
    parts = repository.strip().split("/")
    if len(parts) != 2 or not all(parts):
        raise GitHubIssueError(
            "GitHub repository must use the OWNER/REPOSITORY form"
        )
    return parts[0], parts[1]


def _required_environment(values: Mapping[str, str], name: str) -> str:
    value = values.get(name, "").strip()
    if not value:
        raise GitHubIssueError(f"required environment value is missing: {name}")
    return value


def _issue_body(issue: dict[str, Any]) -> str:
    body = issue.get("body")
    return body if isinstance(body, str) else ""


def _issue_number(issue: dict[str, Any]) -> int:
    number = issue.get("number")
    if type(number) is not int or number <= 0:
        raise GitHubIssueError("GitHub returned an issue with an invalid number")
    return number


def _has_assignee(issue: dict[str, Any], expected_login: str) -> bool:
    assignees = issue.get("assignees")
    if not isinstance(assignees, list):
        return False
    return any(
        isinstance(assignee, dict)
        and isinstance(assignee.get("login"), str)
        and assignee["login"].casefold() == expected_login.casefold()
        for assignee in assignees
    )


def _github_error_message(response_bytes: bytes) -> str:
    try:
        document = json.loads(response_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return ""
    if not isinstance(document, dict) or not isinstance(document.get("message"), str):
        return ""
    return " ".join(document["message"].split())
