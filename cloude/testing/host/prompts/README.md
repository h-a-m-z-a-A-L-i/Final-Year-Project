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

## Local read tools (LLM)

Implemented in `local_notebook_tools.py` (not in `tools/` — browser tools stay there for later).

| Tool | Purpose |
|------|---------|
| `notebook_snapshot_status` | Check live/persistent JSON exists |
| `notebook_list_cells` | Compact index of all cells |
| `notebook_graph_query` | Dependency graph with upstream/downstream |
| `notebook_get_cell` | Full source/output for one cell |
| `notebook_get_cells` | Batch up to 10 cells |
| `notebook_find_symbol` | Where a variable is defined |
| `notebook_search` | Text/regex search in inputs/outputs |
| `notebook_cell_neighbors` | Run-order hints for one cell |

`build_cerebras_tools(local_only=True)` exposes **only** these to the model (default).

## API alignment

- Static sections first (Role → Examples) for prompt caching; dynamic notebook context in **Context**.
- **Notes** at the end of the system message so critical rules are not lost in the middle.
