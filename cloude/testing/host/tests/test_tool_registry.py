import json
from testing.host.tool_registry import registry


def test_register_and_call_dummy_tool():
    reg = registry()

    def dummy(args):
        return {"ok": True, "echo": args}

    reg.register("dummy_tool", {"type": "object"}, "A dummy tool for tests", dummy)

    out = reg.call("dummy_tool", {"x": 1, "y": "z"})
    assert out.get("ok") is True
    assert out.get("echo") == {"x": 1, "y": "z"}


if __name__ == "__main__":
    test_register_and_call_dummy_tool()
    print("test_tool_registry: ok")
