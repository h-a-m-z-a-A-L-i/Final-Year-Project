from testing.host.agentic_action_guard import (
    MAX_ERROR_RECOVERY_ROUNDS,
    build_error_recovery_nudge,
    queue_error_active,
)


def test_queue_error_active():
    assert queue_error_active({"tool_queue_status": "error"}) is True
    assert queue_error_active({"tool_queue_complete": True}) is False
    assert queue_error_active({"needs_fix": True, "execution_error": {"cell_index": 3}}) is True


def test_build_error_recovery_nudge_mentions_task_and_pending():
    verification = {
        "execution_error": {
            "cell_index": 25,
            "error_type": "NameError",
            "error_summary": "name 'x' is not defined",
        },
        "pending_run_cells": [26, 27],
        "tool_queue": {"run_completed": [23, 24, 25]},
        "user_response_gate": "fix it",
    }
    nudge = build_error_recovery_nudge(
        "run cells 23 to 27",
        verification,
        use_text_tools=True,
    )
    assert "run cells 23 to 27" in nudge
    assert "25" in nudge
    assert "[26, 27]" in nudge
    assert "agent_tool_batch" in nudge
    assert MAX_ERROR_RECOVERY_ROUNDS >= 3
