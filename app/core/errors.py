from __future__ import annotations


class Web3NewsIntelError(Exception):
    """Base application exception."""


class FetchError(Web3NewsIntelError):
    def __init__(
        self, message: str, *, status_code: int | None = None, error_code: str = "fetch_error"
    ):
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code


class AccessDeniedError(FetchError):
    def __init__(self, message: str = "access denied", *, status_code: int | None = None):
        super().__init__(message, status_code=status_code, error_code="access_denied")


class RobotsDisallowedError(FetchError):
    def __init__(self, url: str):
        super().__init__(f"robots.txt disallows fetching {url}", error_code="robots_disallowed")
        self.url = url


class ResponseTooLargeError(FetchError):
    def __init__(self, max_bytes: int):
        super().__init__(
            f"response exceeded configured maximum of {max_bytes} bytes",
            error_code="response_too_large",
        )
        self.max_bytes = max_bytes


class InvalidContentTypeError(FetchError):
    def __init__(self, content_type: str | None):
        super().__init__(
            f"invalid content type: {content_type or '<missing>'}",
            error_code="invalid_content_type",
        )
        self.content_type = content_type
