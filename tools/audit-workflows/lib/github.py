import hashlib
import json
import os
import random
import re
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def key(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def local_path(value: str | Path) -> Path:
    path = str(Path(value).resolve())
    if os.name == "nt" and not path.startswith("\\\\?\\"):
        path = "\\\\?\\UNC\\" + path[2:] if path.startswith("\\\\") else "\\\\?\\" + path
    return Path(path)


def write_text(path: Path, value: str) -> None:
    path = local_path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f"{path.name}.{uuid.uuid4()}.tmp")
    try:
        with os.fdopen(os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "w", encoding="utf-8", newline="") as output:
            output.write(value)
        for attempt in range(5):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if os.name != "nt" or attempt == 4:
                    raise
                time.sleep(0.05 * (attempt + 1))
    finally:
        temporary.unlink(missing_ok=True)


def write_json(path: Path, value: object) -> None:
    write_text(path, json.dumps(value, indent=2, ensure_ascii=True, allow_nan=False) + "\n")


def read_json(path: Path, fallback=None):
    try:
        return json.loads(local_path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return fallback


class ApiError(Exception):
    def __init__(self, status: int, endpoint: str, reason: str):
        super().__init__(f"GitHub request failed ({status or 'transport'}): {reason}")
        self.status = status
        self.endpoint = endpoint
        self.reason = reason


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


def transport(address: str, headers: dict) -> dict:
    opener = build_opener(_NoRedirect())
    request = Request(address, headers=headers, method="GET")
    try:
        with opener.open(request, timeout=120) as response:
            return {"status": response.status, "headers": dict(response.headers), "body": response.read()}
    except HTTPError as error:
        with error:
            return {"status": error.code, "headers": dict(error.headers), "body": error.read()}


def _redact(value):
    if isinstance(value, list):
        return [_redact(entry) for entry in value]
    if isinstance(value, dict):
        return {name: _redact(content) for name, content in value.items()
                if name.lower() not in {"token", "temp_clone_token", "authorization", "access_token", "refresh_token"}}
    return value


class GitHub:
    def __init__(self, *, directory: Path, base_url: str = "https://api.github.com", version: str = "2026-03-10",
                 token_command: list[str] | None = None, auth_mode: str = "app", fetch_impl: Callable = transport,
                 wait: Callable = time.sleep, max_attempts: int = 4, max_wait_ms: int = 300_000):
        base = urlsplit(base_url)
        if base.scheme != "https" or not base.hostname or base.username or base.password or base.query or base.fragment:
            raise ValueError("API URL must use HTTPS, without credentials, query or fragment")
        if auth_mode not in ("app", "user"):
            raise ValueError("authMode must be app or user")
        if token_command is not None and (not isinstance(token_command, list) or not token_command or
                                          not all(isinstance(argument, str) for argument in token_command)):
            raise ValueError("tokenCommand must be an argument array")
        self.base_url = base_url.rstrip("/")
        self.version = version
        self.directory = local_path(directory)
        self.cli_auth = auth_mode == "user" and token_command is None
        host = "github.com" if base.hostname == "api.github.com" else base.hostname
        self.token_command = ["gh", "auth", "token", "--hostname", host] if self.cli_auth else token_command
        self.fetch = fetch_impl
        self.wait = wait
        self.max_attempts = max_attempts
        self.max_wait_ms = max_wait_ms
        self.token = None if auth_mode == "user" else os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        self.expires_at = None

    def refresh_token(self) -> None:
        if not self.token_command:
            raise ValueError("Provide GITHUB_TOKEN or an approved tokenCommand; never put secrets in config")
        environment = os.environ.copy()
        if self.cli_auth:
            for name in ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN"):
                environment.pop(name, None)
            environment["GH_PROMPT_DISABLED"] = "1"
        try:
            response = subprocess.run(self.token_command, check=True, capture_output=True, text=True,
                                      timeout=60, shell=False, env=environment)
            stdout = response.stdout
            if len(stdout) > 64 * 1024:
                raise ValueError("Provider output too large")
        except (OSError, subprocess.SubprocessError, ValueError):
            if self.cli_auth:
                raise ValueError("GitHub CLI authentication failed; install gh and run gh auth login for the configured host. Provider output is not logged.") from None
            raise ValueError("Approved token provider failed; its output is not logged") from None
        try:
            content = json.loads(stdout)
        except json.JSONDecodeError:
            content = {"token": stdout.strip()}
        if not isinstance(content, dict) or not isinstance(content.get("token"), str) or not content["token"] or re.search(r"\s", content["token"]):
            raise ValueError("Token provider must return a token or JSON with token and optional expires_at")
        self.token = content["token"]
        self.expires_at = None
        if content.get("expires_at"):
            try:
                expiry = datetime.fromisoformat(content["expires_at"].replace("Z", "+00:00"))
                if expiry.tzinfo is None:
                    raise ValueError("Missing timezone")
                self.expires_at = expiry.timestamp()
            except (AttributeError, TypeError, ValueError):
                raise ValueError("Token provider returned an invalid expires_at") from None

    def log(self, event: dict) -> None:
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self.directory / "requests.jsonl"
        with os.fdopen(os.open(path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600), "a", encoding="utf-8") as output:
            output.write(json.dumps({"at": timestamp(), **event}) + "\n")

    def get(self, endpoint: str, *, cache: bool = True) -> dict:
        address = endpoint if endpoint.startswith("https:") else self.base_url + endpoint
        url = urlsplit(address)
        base = urlsplit(self.base_url)
        if (url.scheme, url.netloc.lower()) != (base.scheme, base.netloc.lower()) or url.username or url.password or url.fragment or not url.path.startswith(base.path.rstrip("/") + "/"):
            raise ValueError("Refusing an API URL outside the configured host/path")
        relative = url.path + ("?" + url.query if url.query else "")
        cache_path = self.directory / "cache" / f"{key(address)}.json"
        if cache:
            saved = read_json(cache_path)
            if saved is not None:
                return saved
        if not self.token or (self.expires_at and self.expires_at - time.time() < 60):
            self.refresh_token()
        refreshed = False
        for attempt in range(1, self.max_attempts + 1):
            try:
                response = self.fetch(address, {"Accept": "application/vnd.github+json", "Authorization": f"Bearer {self.token}",
                                                "X-GitHub-Api-Version": self.version, "User-Agent": "reusable-workflow-audit"})
            except (OSError, URLError, TimeoutError):
                self.log({"endpoint": relative, "status": 0, "attempt": attempt, "reason": "transport_failure"})
                if attempt == self.max_attempts:
                    raise ApiError(0, relative, "transport_failure") from None
                self.wait(min(1000 * 2 ** attempt, self.max_wait_ms) / 1000)
                continue
            raw_headers = {name.lower(): value for name, value in response["headers"].items()}
            headers = {name: raw_headers.get(name) for name in ["link", "retry-after", "x-ratelimit-remaining", "x-ratelimit-reset", "x-github-request-id", "location"]}
            invalid_json = False
            try:
                body = json.loads(response["body"])
            except (ValueError, UnicodeError):
                body = {"message": "Non-JSON response"}
                invalid_json = True
            status = response["status"]
            result = {"status": status, "headers": headers, "body": _redact(body), "collectedAt": timestamp()}
            write_json(self.directory / "http" / f"{uuid.uuid4()}.json", {"endpoint": relative, **result})
            self.log({"endpoint": relative, "status": status, "attempt": attempt, "requestId": headers["x-github-request-id"]})
            if status == 200:
                if invalid_json:
                    if attempt == self.max_attempts:
                        raise ApiError(status, relative, "invalid_json_response")
                    self.wait(min(1000 * 2 ** attempt, self.max_wait_ms) / 1000)
                    continue
                if cache:
                    write_json(cache_path, result)
                return result
            if status == 401 and self.token_command and not refreshed and attempt < self.max_attempts:
                self.refresh_token()
                refreshed = True
                continue
            message = str(body.get("message", "")) if isinstance(body, dict) else ""
            limited = status == 429 or (status == 403 and (headers["x-ratelimit-remaining"] == "0" or headers["retry-after"] or "secondary rate limit" in message.lower()))
            transient = status in (500, 502, 503, 504)
            delay = 1000 * 2 ** attempt
            if limited:
                if re.fullmatch(r"\d+", headers["retry-after"] or ""):
                    delay = (int(headers["retry-after"]) + 1) * 1000
                elif headers["x-ratelimit-remaining"] == "0" and re.fullmatch(r"\d+", headers["x-ratelimit-reset"] or ""):
                    delay = max(1000, int(headers["x-ratelimit-reset"]) * 1000 - time.time() * 1000 + 1000)
                else:
                    delay = 60_000
            if (not limited and not transient) or attempt == self.max_attempts or delay > self.max_wait_ms:
                if limited:
                    reason = "rate_limited"
                elif status == 409 and "git repository is empty" in message.lower():
                    reason = "confirmed_empty_repository"
                else:
                    reason = {404: "not_found_or_inaccessible", 401: "authentication_failed", 403: "access_denied"}.get(status, "redirect_requires_review" if 300 <= status < 400 else "http_failure")
                raise ApiError(status, relative, reason)
            self.wait((delay + random.randrange(200)) / 1000)
        raise ApiError(0, relative, "attempt_limit")

    def paginate(self, endpoint: str, field: str | None = None) -> list:
        next_page = endpoint + ("&" if "?" in endpoint else "?") + "per_page=100"
        visited = set()
        items = []
        total = None
        while next_page:
            if next_page in visited:
                raise ValueError("Pagination loop")
            visited.add(next_page)
            response = self.get(next_page, cache=False)
            page = response["body"].get(field) if field and isinstance(response["body"], dict) else response["body"]
            if not isinstance(page, list):
                raise ValueError("Unexpected paginated response")
            if field and type(response["body"].get("total_count")) is int:
                count = response["body"]["total_count"]
                if total is not None and total != count:
                    raise ValueError("Inventory changed during pagination; resume collection")
                total = count
            items.extend(page)
            match = re.search(r'<([^>]+)>;\s*rel="next"', response["headers"].get("link") or "")
            next_page = match[1] if match else None
        if total is not None and len(items) != total:
            raise ValueError("Paginated inventory count differs from total_count")
        return items