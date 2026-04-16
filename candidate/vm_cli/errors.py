from __future__ import annotations


class CliError(Exception):
    pass


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
        self.code = code
        self.status = status
        self.suggestion = suggestion

    def __str__(self) -> str:
        parts = [f"[{self.provider}] {self.message}"]
        if self.code:
            parts.append(f"code={self.code}")
        if self.status is not None:
            parts.append(f"status={self.status}")
        if self.suggestion:
            parts.append(f"suggestion={self.suggestion}")
        return " | ".join(parts)


class UnsupportedOperationError(CliError):
    def __init__(self, provider: str, action: str) -> None:
        super().__init__(f"[{provider}] '{action}' is not supported by this provider")
        self.provider = provider
        self.action = action
