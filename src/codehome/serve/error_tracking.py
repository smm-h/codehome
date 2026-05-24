"""Sentry error tracking integration.

Initializes Sentry if a DSN is configured in the server config
(~/.codehome/config.json, key: "sentry_dsn"). If no DSN is present,
initialization is silently skipped.

Uses the generic ASGI integration (no FastAPI-specific extras needed)
since the middleware stack is now wesktop-based.
"""

import logging

logger = logging.getLogger(__name__)

try:
    import sentry_sdk

    _HAS_SENTRY = True
except ModuleNotFoundError:
    _HAS_SENTRY = False


def _before_send(event: dict[str, object], hint: dict[str, object]) -> dict[str, object] | None:
    """Filter out expected errors (4xx responses, auth failures).

    Sentry should only capture genuine server errors, not routine
    client-side mistakes like bad credentials or missing resources.
    """
    if "exc_info" in hint:
        exc_info = hint["exc_info"]
        assert isinstance(exc_info, tuple)
        _exc_type, exc_value, _tb = exc_info

        # wesktop HTTPError with 4xx status codes are expected.
        from wesktop.asgi import HTTPError

        if isinstance(exc_value, HTTPError) and 400 <= exc_value.status_code < 500:
            return None

    return event


def init_sentry(config: object) -> bool:
    """Initialize Sentry SDK using the DSN from server config.

    Args:
        config: ServerConfig instance (or any object with a sentry_dsn attribute).

    Returns:
        True if Sentry was initialized, False if skipped.

    """
    dsn = getattr(config, "sentry_dsn", None)
    if not dsn:
        logger.debug("No sentry_dsn configured; Sentry error tracking disabled")
        return False

    if not _HAS_SENTRY:
        logger.warning("sentry_sdk not installed; Sentry error tracking disabled")
        return False

    sentry_sdk.init(
        dsn=dsn,
        before_send=_before_send,  # type: ignore[arg-type]
        # Capture 100% of errors; adjust if volume becomes a concern.
        sample_rate=1.0,
        # Do not send PII by default; user context is added explicitly below.
        send_default_pii=False,
    )
    logger.info("Sentry error tracking initialized")
    return True


def set_user_context(username: str) -> None:
    """Attach the authenticated user to the current Sentry scope.

    Call this after authentication so that error reports include the
    username for debugging context.
    """
    if _HAS_SENTRY:
        sentry_sdk.set_user({"username": username})
