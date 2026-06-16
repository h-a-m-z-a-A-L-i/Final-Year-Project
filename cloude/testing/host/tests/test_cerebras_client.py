"""Tests for Cerebras primary/secondary key router."""

import os
import sys
from unittest.mock import MagicMock, patch

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from testing.host.cerebras_client import (
    CerebrasClientRouter,
    _is_quota_error,
    reset_cerebras_key_state_for_tests,
)


def test_is_quota_error_detects_token_quota():
    assert _is_quota_error(Exception("Error 429: token_quota_exceeded"))
    assert _is_quota_error(
        Exception(
            "Error code: 429 - {'code': 'queue_exceeded', "
            "'type': 'too_many_requests_error'}"
        )
    )
    assert not _is_quota_error(Exception("connection timeout"))


def test_secondary_profile_used_when_configured():
    reset_cerebras_key_state_for_tests()
    primary = MagicMock()
    secondary = MagicMock()
    primary.chat.completions.create.side_effect = Exception("token_quota_exceeded")
    secondary.chat.completions.create.return_value = {"ok": True}

    with patch("testing.host.cerebras_client.Cerebras") as mock_cls:
        mock_cls.side_effect = lambda api_key: primary if "primary" in api_key else secondary
        router = CerebrasClientRouter(
            primary_key="primary-key",
            secondary_key="secondary-key",
            profile="auto",
        )
        result = router.create_completion(model="m", messages=[])

    assert result == {"ok": True}
    secondary.chat.completions.create.assert_called_once()
    assert router.active_profile == "secondary"


def test_forced_secondary_skips_primary():
    reset_cerebras_key_state_for_tests()
    primary = MagicMock()
    secondary = MagicMock()
    secondary.chat.completions.create.return_value = {"from": "secondary"}

    with patch("testing.host.cerebras_client.Cerebras") as mock_cls:
        mock_cls.side_effect = lambda api_key: primary if "pk" in api_key else secondary
        router = CerebrasClientRouter(
            primary_key="pk-1",
            secondary_key="sk-2",
            profile="secondary",
        )
        result = router.create_completion(model="m", messages=[])

    assert result == {"from": "secondary"}
    primary.chat.completions.create.assert_not_called()
    secondary.chat.completions.create.assert_called_once()


def test_forced_secondary_does_not_failover_to_primary():
    reset_cerebras_key_state_for_tests()
    primary = MagicMock()
    secondary = MagicMock()
    secondary.chat.completions.create.side_effect = Exception("token_quota_exceeded")

    with patch("testing.host.cerebras_client.Cerebras") as mock_cls:
        mock_cls.side_effect = lambda api_key: primary if "pk" in api_key else secondary
        router = CerebrasClientRouter(
            primary_key="pk-1",
            secondary_key="sk-2",
            profile="secondary",
        )
        try:
            router.create_completion(model="m", messages=[])
            assert False, "expected quota error"
        except Exception as exc:
            assert "token_quota_exceeded" in str(exc)

    primary.chat.completions.create.assert_not_called()
    secondary.chat.completions.create.assert_called_once()
