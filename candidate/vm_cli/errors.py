from __future__ import annotations


class CliError(Exception):
    pass


_UNIFIED_CODES = {
    "authentication",
    "permission",
    "not_found",
    "invalid_request",
    "capacity",
    "state_conflict",
    "unsupported",
    "network",
    "timeout",
    "configuration",
    "internal",
}


class ProviderError(CliError):
    def __init__(
        self,
        provider: str,
        message: str,
        *,
        code: str | None = None,
        status: int | None = None,
        suggestion: str | None = None,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.message = message
        self.provider_code = code
        self.code = normalize_error_code(code=code, status=status)
        self.status = status
        self.suggestion = suggestion

    def __str__(self) -> str:
        parts = [f"Error [{self.code}] ({self.provider}): {self.message}"]
        if self.suggestion:
            parts.append(f"Hint: {self.suggestion}")
        return "\n".join(parts)


class UnsupportedOperationError(CliError):
    def __init__(self, provider: str, action: str) -> None:
        super().__init__(f"Error [unsupported] ({provider}): Operation '{action}' is not supported by this provider.")
        self.provider = provider
        self.action = action


def normalize_error_code(*, code: str | None, status: int | None = None) -> str:
    if code in _UNIFIED_CODES:
        return code

    if code:
        normalized = _normalize_provider_code(code)
        if normalized:
            return normalized

    if status is not None:
        if status in {400, 422}:
            return "invalid_request"
        if status == 401:
            return "authentication"
        if status == 403:
            return "permission"
        if status == 404:
            return "not_found"
        if status in {409, 412}:
            return "state_conflict"
        if status in {429, 507}:
            return "capacity"
        if 500 <= status <= 599:
            return "internal"

    return "internal"


def _normalize_provider_code(code: str) -> str | None:
    upper_code = code.upper()

    direct_map = {
        "UNAUTHENTICATED": "authentication",
        "PERMISSION_DENIED": "permission",
        "NOT_FOUND": "not_found",
        "INVALID_ARGUMENT": "invalid_request",
        "RESOURCE_EXHAUSTED": "capacity",
        "FAILED_PRECONDITION": "state_conflict",
        "DEADLINE_EXCEEDED": "timeout",
        "UNAVAILABLE": "network",
        "CANCELLED": "internal",
        "UNKNOWN": "internal",
        "IN_PROGRESS": "timeout",
        "SUCCEEDED": "internal",
    }
    if upper_code in direct_map:
        return direct_map[upper_code]

    lowered = code.lower()
    if "invalid-api-key" in lowered or "unauthenticated" in lowered or "unauthorized" in lowered:
        return "authentication"
    if "insufficient-capacity" in lowered or "resource_exhausted" in lowered:
        return "capacity"
    if "object-does-not-exist" in lowered or "not-found" in lowered:
        return "not_found"
    if "invalid" in lowered:
        return "invalid_request"
    if "reserved-instance" in lowered or "failed_precondition" in lowered:
        return "state_conflict"
    if "permission" in lowered or "forbidden" in lowered:
        return "permission"

    return None
