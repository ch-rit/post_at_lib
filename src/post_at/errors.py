from __future__ import annotations

from typing import Any


class PostAtError(Exception):
    """Base class for all errors raised by this library."""


class AuthenticationError(PostAtError):
    """Login, token exchange, or token refresh failed."""


class NotAuthenticatedError(AuthenticationError):
    """An authenticated endpoint was called without a logged-in session."""


class InteractionRequiredError(AuthenticationError):
    """Azure AD B2C asked for a step the headless login cannot answer (MFA, terms, ...).

    Use :meth:`post_at.PostAtClient.begin_browser_login` instead.
    """


class ApiError(PostAtError):
    """The BFF answered with a non-success HTTP status."""

    def __init__(self, status_code: int, body: Any, operation_id: str | None) -> None:
        self.status_code = status_code
        self.body = body
        self.operation_id = operation_id
        super().__init__(f"HTTP {status_code} (operation {operation_id}): {body!r}")


class GraphQLError(PostAtError):
    """The GraphQL endpoint answered with an ``errors`` array."""

    def __init__(self, errors: list[Any], data: Any) -> None:
        self.errors = errors
        self.data = data
        super().__init__(f"GraphQL errors: {errors!r}")
