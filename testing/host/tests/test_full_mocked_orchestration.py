import sys
sys.path.insert(0, r'd:/FYP/normal-chrome')

import testing.host.streaming as streaming
from testing.host.tool_registry import registry


class FakeCompletions:
    def __init__(self, responses):
        self._responses = responses
        self._i = 0

    def create(self, **kwargs):
        # Return next response in sequence; if stream=True return an iterator
        if kwargs.get('stream'):
            # stream response: return an iterable that yields nothing
            return []
        resp = None
        if self._i < len(self._responses):
            resp = self._responses[self._i]
            self._i += 1
        else:
            resp = self._responses[-1]
        return resp


class FakeClient:
    def __init__(self, responses):
        self.chat = type('C', (), {})()
        self.chat.completions = FakeCompletions(responses)


def test_full_orchestration_calls_tool_and_gets_final():
    # Set up a dummy tool that marks called
    called = {"flag": False}

    def dummy_tool(args):
        called['flag'] = True
        return {"ok": True, "result": {"summary": "ok"}}

    reg = registry()
    reg.register("notebook_graph_query", {"type": "object", "properties": {"url": {"type": "string"}}}, "test", dummy_tool)

    # Build fake model responses:
    # 1) stream call -> empty iterable
    # 2) structured detection -> function_call
    structured = {"choices": [{"message": {"function_call": {"name": "notebook_graph_query", "arguments": '{"url": "https://example.com"}'}}}]}
    # 3) final assistant response after tool
    final = {"choices": [{"message": {"content": "Final assistant text after tool."}}]}

    fake = FakeClient([structured, final])
    # Patch streaming client
    streaming._CEREBRAS_CLIENT = fake

    # Call the streaming function (it will run synchronously)
    try:
        streaming._run_streaming_chat(url="https://example.com", prompt="Summarize", tab_id=1, session_id="s", history=[], context="", mode="ask")
    except Exception as e:
        raise

    assert called['flag'] is True


if __name__ == "__main__":
    test_full_orchestration_calls_tool_and_gets_final()
    print('ok')
