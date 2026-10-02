"""A minimal, read-only GitHub REST client."""

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

TIMEOUT = 30.0
# Responses are arbitrary JSON, shaped by each endpoint.
Json = Any
NEXT_LINK = re.compile(r'<([^>]+)>;\s*rel="next"')


class GitHubError(Exception):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class GitHub:
    def __init__(self, api: str, token: str) -> None:
        self.api = api.rstrip('/')
        self.token = token

    def _open(self, url: str) -> tuple[bytes, str | None]:
        request = urllib.request.Request(url)
        request.add_header('Accept', 'application/vnd.github+json')
        # Not sent on redirects: log downloads redirect to signed blob URLs,
        # which reject a second form of authorization.
        request.add_unredirected_header(
            'Authorization', f'Bearer {self.token}'
        )
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as resp:
                body: bytes = resp.read()
                link: str | None = resp.headers.get('Link')
                return body, link
        except OSError as e:
            status = e.code if isinstance(e, urllib.error.HTTPError) else None
            raise GitHubError(f'GET {url}: {e}', status) from e

    def _url(self, path: str, params: dict[str, str] | None) -> str:
        query = f'?{urllib.parse.urlencode(params)}' if params else ''
        return f'{self.api}/{path.lstrip("/")}{query}'

    def get(self, path: str, params: dict[str, str] | None = None) -> Json:
        body, _ = self._open(self._url(path, params))
        return json.loads(body)

    def get_all(
        self, path: str, params: dict[str, str] | None = None
    ) -> list[Json]:
        """Every item of a paginated list endpoint."""
        items: list[Json] = []
        url: str | None = self._url(path, {'per_page': '100'} | (params or {}))
        while url:
            body, link = self._open(url)
            items.extend(json.loads(body))
            match = NEXT_LINK.search(link or '')
            url = match.group(1) if match else None
        return items

    def get_bytes(self, path: str) -> bytes:
        body, _ = self._open(self._url(path, None))
        return body
