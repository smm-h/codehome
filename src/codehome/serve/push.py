"""Push notification manager with VAPID key management and per-user subscriptions.

Stores VAPID keys in .codehome/vapid_keys.json and push subscriptions in
.codehome/push_subscriptions.json.  Both files are created on first use.
"""

import asyncio
import base64
import json
import logging
from collections.abc import Callable
from pathlib import Path

from codehome.paths import resolve_global, codehome_home

logger = logging.getLogger(__name__)

try:
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    from py_vapid import Vapid
    from pywebpush import WebPushException, webpush

    _HAS_PUSH_DEPS = True
except ModuleNotFoundError:
    _HAS_PUSH_DEPS = False

# Default notification preferences -- all enabled.
# Grouped by domain for UI display; the keys are the push categories
# referenced in events._PUSH_EVENT_MAP.
DEFAULT_PREFERENCES: dict[str, bool] = {
    # Deployments
    "pipeline_failure": True,
    "pipeline_success": True,
    # AI
    "agent_question": True,
    "agent_completion": True,
    "conductor_message": True,
    "conductor_completion": True,
    # Services
    "service_state": True,
    # Git
    "branch_activity": True,
    "review_activity": True,
    "rebase_result": True,
    # Quality
    "test_results": True,
}


class PushManager:
    """Manages VAPID keys, push subscriptions, and notification delivery."""

    def __init__(self, legacy_dir: Path | None = None) -> None:
        # When an explicit dir is passed (tests), use it for both reads
        # and writes.  Otherwise use resolve_global() for reads (dual-path
        # fallback) and codehome_home() for writes (new canonical location).
        self._read_path: Callable[[str], Path]
        if legacy_dir:
            self._read_path = lambda rel: legacy_dir / rel
            self._write_dir = legacy_dir
        else:
            self._read_path = resolve_global
            self._write_dir = codehome_home()
        self._vapid: dict[str, str] = {}
        # Subscription store: maps user_id to list of subscription info dicts.
        self._subscriptions: dict[str, list[dict[str, object]]] = {}
        # Preferences store: maps user_id to per-event-type boolean prefs.
        self._preferences: dict[str, dict[str, bool]] = {}
        self._init_keys()
        self._load_subscriptions()
        self._load_preferences()

    # ---- VAPID key management ----

    def _init_keys(self) -> None:
        """Load or generate VAPID keypair."""
        if not _HAS_PUSH_DEPS:
            logger.debug("Push dependencies not installed; push notifications disabled")
            return

        keys_read = self._read_path("vapid_keys.json")
        if keys_read.exists():
            self._vapid = json.loads(keys_read.read_text())
            return

        # Generate new VAPID keypair -- write to canonical home dir.
        self._write_dir.mkdir(parents=True, exist_ok=True)
        vapid = Vapid()
        vapid.generate_keys()
        # Encode public key as URL-safe base64 (uncompressed EC point format).
        raw_pub = vapid.public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
        pub_b64 = base64.urlsafe_b64encode(raw_pub).decode().rstrip("=")
        self._vapid = {
            "private_key": vapid.private_pem().decode(),
            "public_key": pub_b64,
        }
        keys_write = self._write_dir / "vapid_keys.json"
        keys_write.write_text(json.dumps(self._vapid, indent=2))
        logger.info("Generated new VAPID keypair at %s", keys_write)

    @property
    def public_key(self) -> str:
        """Return the URL-safe base64-encoded public VAPID key."""
        return self._vapid.get("public_key", "")

    @property
    def _private_key_pem(self) -> str:
        return self._vapid.get("private_key", "")

    # ---- Subscription management ----

    def _load_subscriptions(self) -> None:
        path = self._read_path("push_subscriptions.json")
        if path.exists():
            self._subscriptions = json.loads(path.read_text())

    def _save_subscriptions(self) -> None:
        self._write_dir.mkdir(parents=True, exist_ok=True)
        (self._write_dir / "push_subscriptions.json").write_text(
            json.dumps(self._subscriptions, indent=2)
        )

    def subscribe(self, user_id: str, subscription_info: dict[str, object]) -> None:
        """Add a push subscription for a user."""
        if user_id not in self._subscriptions:
            self._subscriptions[user_id] = []

        # Avoid duplicate subscriptions (same endpoint).
        endpoint = subscription_info.get("endpoint", "")
        existing = [s for s in self._subscriptions[user_id] if s.get("endpoint") == endpoint]
        if existing:
            # Update the existing subscription (keys may have changed).
            idx = self._subscriptions[user_id].index(existing[0])
            self._subscriptions[user_id][idx] = subscription_info
        else:
            self._subscriptions[user_id].append(subscription_info)

        self._save_subscriptions()

    def unsubscribe(self, user_id: str, endpoint: str) -> None:
        """Remove a push subscription by endpoint."""
        if user_id not in self._subscriptions:
            return
        self._subscriptions[user_id] = [s for s in self._subscriptions[user_id] if s.get("endpoint") != endpoint]
        if not self._subscriptions[user_id]:
            del self._subscriptions[user_id]
        self._save_subscriptions()

    def get_subscriptions(self, user_id: str) -> list[dict[str, object]]:
        """Return all subscriptions for a user."""
        return self._subscriptions.get(user_id, [])

    # ---- Preference management ----

    def _load_preferences(self) -> None:
        path = self._read_path("push_preferences.json")
        if path.exists():
            self._preferences = json.loads(path.read_text())

    def _save_preferences(self) -> None:
        self._write_dir.mkdir(parents=True, exist_ok=True)
        (self._write_dir / "push_preferences.json").write_text(
            json.dumps(self._preferences, indent=2)
        )

    def get_preferences(self, user_id: str) -> dict[str, bool]:
        """Return notification preferences for a user, with defaults."""
        user_prefs = self._preferences.get(user_id, {})
        return {**DEFAULT_PREFERENCES, **user_prefs}

    def set_preferences(self, user_id: str, prefs: dict[str, bool]) -> None:
        """Update notification preferences for a user."""
        current = self.get_preferences(user_id)
        # Only accept known preference keys.
        for key in DEFAULT_PREFERENCES:
            if key in prefs:
                current[key] = prefs[key]
        self._preferences[user_id] = current
        self._save_preferences()

    # ---- Notification delivery ----

    def send_notification(
        self,
        user_id: str,
        title: str,
        body: str,
        url: str = "/",
        tag: str = "",
        event_type: str = "",
    ) -> None:
        """Send a push notification to all of a user's subscribed devices.

        If event_type is provided, the notification is only sent if the user
        has that event type enabled in their preferences.
        """
        if event_type:
            prefs = self.get_preferences(user_id)
            if not prefs.get(event_type, True):
                return

        if not _HAS_PUSH_DEPS:
            return

        subs = self.get_subscriptions(user_id)
        if not subs:
            return

        payload = json.dumps({"title": title, "body": body, "url": url, "tag": tag})
        stale_endpoints: list[str] = []

        for sub in subs:
            try:
                webpush(
                    subscription_info=sub,
                    data=payload,
                    vapid_private_key=self._private_key_pem,
                    vapid_claims={"sub": "mailto:dev@codehome.local"},
                )
            except WebPushException as e:
                # 410 Gone or 404 means the subscription is no longer valid.
                if hasattr(e, "response") and e.response is not None:
                    status = getattr(e.response, "status_code", 0)
                    if status in (404, 410):
                        stale_endpoints.append(str(sub.get("endpoint", "")))
                        continue
                logger.warning("Push notification failed for %s: %s", user_id, e)
            except Exception:
                logger.exception("Unexpected error sending push to %s", user_id)

        # Clean up stale subscriptions.
        for endpoint in stale_endpoints:
            self.unsubscribe(user_id, endpoint)

    def broadcast(
        self,
        title: str,
        body: str,
        url: str = "/",
        tag: str = "",
        event_type: str = "",
    ) -> None:
        """Send a push notification to all subscribed users."""
        for user_id in list(self._subscriptions):
            self.send_notification(user_id, title, body, url, tag, event_type)

    async def broadcast_async(
        self,
        title: str,
        body: str,
        url: str = "/",
        tag: str = "",
        event_type: str = "",
    ) -> None:
        """Async wrapper: runs the synchronous broadcast in a thread.

        This avoids blocking the event loop with the synchronous HTTP
        POST calls that webpush() makes for each subscription.
        """
        await asyncio.to_thread(self.broadcast, title, body, url=url, tag=tag, event_type=event_type)


# Module-level singleton, initialized at import time.
push_manager = PushManager()
