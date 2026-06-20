from __future__ import annotations


class FeishuError(RuntimeError):
    pass


class FeishuConfigurationError(FeishuError):
    pass


class FeishuAuthenticationError(FeishuError):
    pass


class FeishuPermanentError(FeishuError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class FeishuTransientError(FeishuError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retry_after: int | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after
