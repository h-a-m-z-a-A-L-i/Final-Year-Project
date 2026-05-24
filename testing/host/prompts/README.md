# Chat prompts

Structured prompts for the notebook copilot (`testing/host/prompt_engineering.py`).

## Component schema (assembly order)

| Section | Technique | Source |
|---------|-----------|--------|
| **Role** | Role prompting | `base_notebook_assistant.txt` + `ask.txt` / `code.txt` |
| **Task** | Chain-of-thought (plan before answer) | Mode file |
| **Specifics** | Emphasis / stakes | Mode file |
| **Context** | Environment + tools + notebook evidence | Base + dynamic session block |
| **Examples** | Few-shot | Mode file + `tool_calling/*` (budget permitting) |
| **Notes** | Lost-in-the-middle — **always last** | Mode file + hard rules in code |

## UI modes

- **Ask** — explain, debug, review, placement questions.
- **Code** — generate cells with mandatory placement + run order.

There is **no Auto mode**; the user picks Ask or Code explicitly.

## Legacy files

`simple.txt`, `dependency.txt`, etc. are kept for reference; routing uses `ask.txt` / `code.txt` only.

## API alignment

- Static sections first (Role → Examples) for prompt caching; dynamic notebook context in **Context**.
- **Notes** at the end of the system message so critical rules are not lost in the middle.
