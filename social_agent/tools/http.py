"""Small HTTP helper for the outside services (one place for timeouts and errors)."""
import requests

TIMEOUT = 120


class ServiceError(Exception):
    """An outside service failed."""

    def __init__(self, message: str, status: int | None = None, body: str = ""):
        super().__init__(message)
        self.status = status
        self.body = body


def request(method: str, url: str, *, service: str, **kwargs) -> requests.Response:
    kwargs.setdefault("timeout", TIMEOUT)
    try:
        resp = requests.request(method, url, **kwargs)
    except requests.RequestException as exc:
        raise ServiceError(f"{service}: network error: {exc}") from exc
    if resp.status_code >= 400:
        raise ServiceError(f"{service}: HTTP {resp.status_code}: {resp.text[:300]}",
                           status=resp.status_code, body=resp.text)
    return resp
