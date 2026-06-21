from __future__ import annotations


class AIProviderError(RuntimeError):
    error_code = "ai_provider_error"
    retryable = False

    def __init__(self, message: str = "AI provider request failed") -> None:
        super().__init__(message)


class AIAuthenticationError(AIProviderError):
    error_code = "ai_authentication_failed"


class AIRateLimitedError(AIProviderError):
    error_code = "ai_rate_limited"
    retryable = True

    def __init__(self, retry_after_seconds: int | None = None) -> None:
        super().__init__("AI provider rate limited the request")
        self.retry_after_seconds = retry_after_seconds


class AITransientError(AIProviderError):
    error_code = "ai_transient_error"
    retryable = True


class AITimeoutError(AITransientError):
    error_code = "ai_timeout"


class AIJSONValidationError(AIProviderError):
    error_code = "ai_json_validation_failed"


class AIBudgetExceededError(AIProviderError):
    error_code = "ai_budget_exceeded"


class AICircuitOpenError(AIProviderError):
    error_code = "ai_circuit_open"
    retryable = True
