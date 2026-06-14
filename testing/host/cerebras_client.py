"""Cerebras client router — primary + optional secondary API keys with quota failover."""

from __future__ import annotations

import os
import threading
from typing import Any

from cerebras.cloud.sdk import Cerebras

_KEY_LOCK = threading.Lock()
_EXHAUSTED: set[str] = set()
_ACTIVE_PROFILE = "primary"


def _is_quota_error(exc: Exception) -> bool:
    err = str(exc or "").lower()
    needles = (
        "token_quota_exceeded",
        "too_many_tokens_error",
        "tokens per day limit exceeded",
        "daily token",
        "quota exceeded",
    )
    return any(n in err for n in needles) or (
        "429" in err and ("quota" in err or "token" in err)
    )


class _CompletionsProxy:
    def __init__(self, router: "CerebrasClientRouter"):
        self._router = router

    def create(self, **kwargs):
        return self._router.create_completion(**kwargs)


class _ChatProxy:
    def __init__(self, router: "CerebrasClientRouter"):
        self.completions = _CompletionsProxy(router)


class CerebrasClientRouter:
    """Drop-in wrapper: .chat.completions.create(...) with key failover."""

    def __init__(
        self,
        *,
        primary_key: str,
        secondary_key: str = "",
        profile: str = "auto",
    ):
        self._profiles: dict[str, str] = {}
        if primary_key:
            self._profiles["primary"] = primary_key
        if secondary_key:
            self._profiles["secondary"] = secondary_key
        self._profile_pref = str(profile or "auto").strip().lower()
        if self._profile_pref not in ("auto", "primary", "secondary"):
            self._profile_pref = "auto"
        self._clients: dict[str, Cerebras] = {
            name: Cerebras(api_key=key) for name, key in self._profiles.items()
        }
        self.chat = _ChatProxy(self)
        with _KEY_LOCK:
            global _ACTIVE_PROFILE
            if self._profile_pref == "secondary" and "secondary" in self._clients:
                _ACTIVE_PROFILE = "secondary"
            elif self._profile_pref == "primary" and "primary" in self._clients:
                _ACTIVE_PROFILE = "primary"
            elif "primary" in self._clients:
                _ACTIVE_PROFILE = "primary"
            elif "secondary" in self._clients:
                _ACTIVE_PROFILE = "secondary"

    @property
    def active_profile(self) -> str:
        with _KEY_LOCK:
            return _ACTIVE_PROFILE if _ACTIVE_PROFILE in self._clients else next(iter(self._clients), "primary")

    def available_profiles(self) -> list[str]:
        return list(self._clients.keys())

    def _ordered_profiles(self) -> list[str]:
        with _KEY_LOCK:
            if self._profile_pref == "secondary":
                order = ["secondary", "primary"]
            elif self._profile_pref == "primary":
                order = ["primary", "secondary"]
            else:
                global _ACTIVE_PROFILE
                if _ACTIVE_PROFILE in self._clients and _ACTIVE_PROFILE not in _EXHAUSTED:
                    first = _ACTIVE_PROFILE
                elif "primary" in self._clients and "primary" not in _EXHAUSTED:
                    first = "primary"
                elif "secondary" in self._clients:
                    first = "secondary"
                else:
                    first = next(iter(self._clients), "primary")
                rest = [p for p in ("primary", "secondary") if p in self._clients and p != first]
                order = [first] + rest
            return [p for p in order if p in self._clients and p not in _EXHAUSTED]

    def _mark_exhausted(self, profile: str) -> None:
        global _ACTIVE_PROFILE
        with _KEY_LOCK:
            _EXHAUSTED.add(profile)
            if profile == _ACTIVE_PROFILE:
                for candidate in ("secondary", "primary"):
                    if candidate in self._clients and candidate not in _EXHAUSTED:
                        _ACTIVE_PROFILE = candidate
                        break

    def _set_active(self, profile: str) -> None:
        global _ACTIVE_PROFILE
        with _KEY_LOCK:
            _ACTIVE_PROFILE = profile

    def create_completion(self, **kwargs):
        profiles = self._ordered_profiles()
        if not profiles:
            raise RuntimeError("No Cerebras API key configured")

        last_exc: Exception | None = None
        for idx, profile in enumerate(profiles):
            client = self._clients[profile]
            try:
                result = client.chat.completions.create(**kwargs)
                self._set_active(profile)
                return result
            except Exception as exc:
                last_exc = exc
                if _is_quota_error(exc) and idx < len(profiles) - 1:
                    self._mark_exhausted(profile)
                    try:
                        from .config import log
                    except Exception:
                        from config import log
                    log(
                        f"Cerebras key profile '{profile}' quota exceeded — "
                        f"failover to '{profiles[idx + 1]}'"
                    )
                    continue
                raise
        if last_exc:
            raise last_exc
        raise RuntimeError("Cerebras completion failed")


def build_cerebras_router() -> CerebrasClientRouter | None:
    primary = os.environ.get("CEREBRAS_API_KEY", "").strip()
    secondary = os.environ.get("CEREBRAS_SECONDARY_API_KEY", "").strip()
    profile = os.environ.get("CEREBRAS_KEY_PROFILE", "auto").strip().lower()
    if not primary and not secondary:
        return None
    return CerebrasClientRouter(
        primary_key=primary,
        secondary_key=secondary,
        profile=profile,
    )


def reset_cerebras_key_state_for_tests() -> None:
    global _ACTIVE_PROFILE
    with _KEY_LOCK:
        _EXHAUSTED.clear()
        _ACTIVE_PROFILE = "primary"
