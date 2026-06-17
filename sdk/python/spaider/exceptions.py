"""
Custom exceptions for the Spaider Python SDK.
"""

from __future__ import annotations

from typing import Optional


class SpaiderError(Exception):
    """
    Base exception for all Spaider SDK errors.

    Attributes:
        message: Human-readable error description.
        status_code: HTTP status code from the API response, if applicable.
        response_body: Raw response body from the API, if available.
    """

    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        response_body: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.response_body = response_body

    def __repr__(self) -> str:
        parts = [f"message={self.message!r}"]
        if self.status_code is not None:
            parts.append(f"status_code={self.status_code}")
        return f"{type(self).__name__}({', '.join(parts)})"


class AuthError(SpaiderError):
    """
    Raised when the API key is missing, invalid, or expired.

    HTTP 401 Unauthorized.
    """

    def __init__(
        self,
        message: str = "Invalid or missing API key.",
        status_code: int = 401,
        response_body: Optional[str] = None,
    ) -> None:
        super().__init__(message, status_code=status_code, response_body=response_body)


class RateLimitError(SpaiderError):
    """
    Raised when the API rate limit has been exceeded.

    HTTP 429 Too Many Requests. Callers should back off and retry.

    Attributes:
        retry_after: Seconds to wait before retrying, if provided by the API.
    """

    def __init__(
        self,
        message: str = "Rate limit exceeded. Please back off and retry.",
        status_code: int = 429,
        response_body: Optional[str] = None,
        retry_after: Optional[int] = None,
    ) -> None:
        super().__init__(message, status_code=status_code, response_body=response_body)
        self.retry_after = retry_after

    def __repr__(self) -> str:
        base = super().__repr__()
        if self.retry_after is not None:
            return base.rstrip(")") + f", retry_after={self.retry_after})"
        return base


class NotFoundError(SpaiderError):
    """
    Raised when a requested resource (node, agent, connection) does not exist.

    HTTP 404 Not Found.

    Attributes:
        resource_id: The ID of the resource that was not found, if known.
    """

    def __init__(
        self,
        message: str = "Resource not found.",
        status_code: int = 404,
        response_body: Optional[str] = None,
        resource_id: Optional[str] = None,
    ) -> None:
        super().__init__(message, status_code=status_code, response_body=response_body)
        self.resource_id = resource_id

    def __repr__(self) -> str:
        base = super().__repr__()
        if self.resource_id is not None:
            return base.rstrip(")") + f", resource_id={self.resource_id!r})"
        return base


class ValidationError(SpaiderError):
    """
    Raised when the API rejects the request due to invalid input.

    HTTP 422 Unprocessable Entity.
    """

    def __init__(
        self,
        message: str = "Request validation failed.",
        status_code: int = 422,
        response_body: Optional[str] = None,
    ) -> None:
        super().__init__(message, status_code=status_code, response_body=response_body)


class ServerError(SpaiderError):
    """
    Raised when the Spaider API returns a 5xx error.
    """

    def __init__(
        self,
        message: str = "Spaider API server error.",
        status_code: Optional[int] = 500,
        response_body: Optional[str] = None,
    ) -> None:
        super().__init__(message, status_code=status_code, response_body=response_body)
