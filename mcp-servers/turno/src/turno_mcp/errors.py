"""Map HTTP failures to clean, LLM-actionable errors."""

from __future__ import annotations

import httpx

_HINTS = {
    400: "Bad request — check the parameters/body.",
    401: "Unauthorized — check TURNO_API_TOKEN and TURNO_PARTNER_ID.",
    403: "Forbidden — your partner token may lack access to this resource.",
    404: "Not found — check the id.",
    409: "Conflict — the resource may already exist or be in a conflicting state.",
    422: "Validation failed — see details.",
    429: "Rate limited by Turno (retries exhausted).",
}


class TurnoAPIError(RuntimeError):
    """A Turno API call failed. The message is safe to surface to the model."""

    def __init__(self, status_code: int, message: str, details: object | None = None):
        self.status_code = status_code
        self.details = details
        super().__init__(message)


def _extract_message(response: httpx.Response) -> tuple[str, object | None]:
    try:
        body = response.json()
    except Exception:
        text = (response.text or "").strip()
        return (text or response.reason_phrase or "Unknown error"), None

    if isinstance(body, dict):
        msg = body.get("message") or body.get("error") or body.get("error_description")
        errors = body.get("errors")
        details = errors if errors is not None else body.get("error_details")
        if msg:
            return str(msg), details
        return str(body)[:500], details
    return str(body)[:500], None


def raise_for_status(response: httpx.Response) -> None:
    """Raise :class:`TurnoAPIError` with a friendly hint for non-2xx responses."""
    if response.is_success:
        return
    code = response.status_code
    message, details = _extract_message(response)
    full = f"Turno API {code}: {message}"
    hint = _HINTS.get(code)
    if hint:
        full += f" ({hint})"
    raise TurnoAPIError(code, full, details)
