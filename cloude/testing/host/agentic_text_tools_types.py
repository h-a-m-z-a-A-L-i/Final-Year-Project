"""Parse-result types and helpers for text-format tool batches."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TextToolParseResult:
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    unknown_tools: list[str] = field(default_factory=list)
    batch_count: int = 0
    multiple_batches: bool = False
    parse_errors: list[str] = field(default_factory=list)
    recovery_used: bool = False
    recovery_methods: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.tool_calls)

    def to_feedback_dict(self) -> dict[str, Any]:
        return {
            "unknown_tools": list(self.unknown_tools),
            "batch_count": self.batch_count,
            "multiple_batches_merged": self.multiple_batches,
            "parse_errors": list(self.parse_errors),
            "parsed_tool_count": len(self.tool_calls),
            "recovery_used": self.recovery_used,
            "recovery_methods": list(self.recovery_methods),
        }


def build_unknown_tools_nudge(result: TextToolParseResult) -> str:
    if not result.unknown_tools and not result.parse_errors:
        return ""
    lines = ["TOOL PARSE FEEDBACK — fix your next <agent_tool_batch>:"]
    if result.unknown_tools:
        lines.append(f"Unknown tools (not in schema): {result.unknown_tools}")
        lines.append("Use only registered browser/local notebook tools from the system prompt.")
    if result.multiple_batches:
        lines.append(
            f"Note: {result.batch_count} <agent_tool_batch> blocks were merged into one batch. "
            "Emit a single batch per turn."
        )
    if result.parse_errors:
        lines.append("Parse issues: " + "; ".join(result.parse_errors[:3]))
    lines.append("Emit one valid <agent_tool_batch>[...]</agent_tool_batch> JSON array.")
    return "\n".join(lines)
