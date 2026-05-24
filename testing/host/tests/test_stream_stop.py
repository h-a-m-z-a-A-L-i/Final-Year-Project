import os
import sys

import pytest

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from testing.host.streaming import (
    _ACTIVE_STREAMS,
    _ACTIVE_STREAMS_LOCK,
    _signal_remote_stop,
    begin_active_stream,
    is_stream_stopped,
    clear_active_stream,
)


@pytest.fixture(autouse=True)
def _clear_streams():
    with _ACTIVE_STREAMS_LOCK:
        _ACTIVE_STREAMS.clear()
    yield
    with _ACTIVE_STREAMS_LOCK:
        _ACTIVE_STREAMS.clear()


def test_begin_and_stop_by_session():
    begin_active_stream("42", "sess-a", "https://example.com/notebook")
    assert not is_stream_stopped("42", "sess-a")
    _signal_remote_stop("sess-a")
    assert is_stream_stopped("42", "sess-a")


def test_stop_does_not_affect_other_session_same_tab():
    begin_active_stream("7", "sess-one", "https://a.com/n")
    with _ACTIVE_STREAMS_LOCK:
        _ACTIVE_STREAMS["7"]["sessionId"] = "sess-one"
    _signal_remote_stop("sess-two")
    assert not is_stream_stopped("7", "sess-one")


def test_clear_active_stream_only_matching_session():
    begin_active_stream("9", "keep", "https://b.com/n")
    clear_active_stream("9", "other")
    with _ACTIVE_STREAMS_LOCK:
        assert "9" in _ACTIVE_STREAMS
    clear_active_stream("9", "keep")
    with _ACTIVE_STREAMS_LOCK:
        assert "9" not in _ACTIVE_STREAMS
