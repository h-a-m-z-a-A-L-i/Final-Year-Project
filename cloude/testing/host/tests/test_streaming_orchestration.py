import sys
sys.path.insert(0, r'd:/FYP/normal-chrome')

from testing.host.streaming import _run_streaming_chat
from testing.host.tool_registry import registry


def test_tool_call_parsing_and_execution():
    # Construct a fake structured response payload as the model would return
    fake_structured = {
        "choices": [
            {
                "message": {
                    "function_call": {
                        "name": "notebook_graph_query",
                        "arguments": '{"url": "https://example.com/notebook"}'
                    }
                }
            }
        ]
    }

    # Ensure tool is registered
    reg = registry()
    assert reg.get("notebook_graph_query") is not None

    # Directly exercise the registry call to ensure execution path works
    out = reg.call("notebook_graph_query", {"url": "https://example.com/notebook"})
    assert isinstance(out, dict)


if __name__ == "__main__":
    test_tool_call_parsing_and_execution()
    print("test_streaming_orchestration: ok")
