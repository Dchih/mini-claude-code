import os
import httpx
from anthropic import Anthropic
from dotenv import load_dotenv
from tools import TOOLS, TOOL_HANDLERS

load_dotenv()

client = Anthropic(base_url=os.getenv("ANTHROPIC_API_URL"), api_key=os.getenv("ANTHROPIC_API_KEY"))
MODEL = os.environ["MODEL_ID"]

SYSTEM = """\
You are a autonomous coding agent operating in a command-line environment.

# Core principles
- Act, don't explain. Execute tools immediately instead of describing what you would do.
- Read before writing. Always read a file before modifying it.
- Verify after changing. After edits, re-read or run tests to confirm correctness.

# Tool usage
- Prefer read_file/write_file/edit_file over bash for file operations — they are safer and sandboxed.
- Use bash for: running programs, git commands, installing packages, and other shell tasks.
- Use edit_file for surgical changes. Use write_file only for new files or full rewrites.
- Use subagent to delegate isolated, self-contained subtasks (e.g. "write unit tests for X", "refactor module Y"). The sub-agent cannot spawn further sub-agents.

# Task management
- Use the todo tool to track progress on multi-step tasks.
- Break complex work into clear steps, update status as you go.

# Safety
- Never execute destructive commands (rm -rf /, sudo, etc.).
- Do not write files outside the workspace directory.
- When unsure, read first, ask the user, then act.

# Style
- Be concise in text output. Lead with actions, not reasoning.
- When reporting results, show what changed, not what you thought about.
"""

MAX_HISTORY = 20  # max message pairs to keep

def _estimate_len(content) -> int:
    """Rough char-length estimate for a message's content."""
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        total = 0
        for block in content:
            if isinstance(block, dict):
                total += len(str(block.get("content", ""))) + len(str(block.get("text", "")))
            else:
                total += len(getattr(block, "text", ""))
        return total
    return 0

def trim_history(messages: list):
    """Keep the first user message and the most recent MAX_HISTORY messages."""
    if len(messages) <= MAX_HISTORY:
        return
    # Always preserve the first user message for context
    first = messages[0]
    recent = messages[-MAX_HISTORY + 1:]
    # Make sure we start with a user message
    while recent and recent[0].get("role") != "user":
        recent.pop(0)
    messages.clear()
    messages.append(first)
    messages.extend(recent)

def agent_loop(messages: list, tools=None, tool_handlers=None):
    tools = tools or TOOLS
    tool_handlers = tool_handlers or TOOL_HANDLERS
    rounds_slice_todo = 0
    while True:
        trim_history(messages)
        response = client.messages.create(
            model=MODEL,
            system=SYSTEM,
            max_tokens=20000,
            tools=tools,
            messages=messages,
            timeout=httpx.Timeout(timeout=600.0, connect=5.0)
        )
        messages.append({
            "role": "assistant",
            "content": response.content
        })
        if response.stop_reason != "tool_use":
            break

        results = []
        used_todo = False

        for block in response.content:
            if block.type == "tool_use":
                handler = tool_handlers.get(block.name)
                if not handler:
                    output = f"Unknown tool: {block.name}"
                else:
                    print(f"\033[33m[{block.name}] {block.input}\033[0m")
                    try:
                        output = handler(**block.input)
                    except Exception as e:
                        output = f"Error: {e}"
                    print(output[:200])
                if len(output) > 10000:
                    output = output[:10000] + f"\n... (truncated, {len(output)} chars total)"
                results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": output
                })

                if block.name == "todo":
                    used_todo = True

        rounds_slice_todo = 0 if used_todo else rounds_slice_todo + 1

        if rounds_slice_todo >= 3:
            results.insert(0, {
                "type": "text",
                "text": "<reminder>Update your todos.</reminder>"
            })

        messages.append({
            "role": "user",
            "content": results
        })

if __name__ == "__main__":
    history = []
    print("Mini Claude Code Agent (输入 q 退出)")
    print("=" * 40)
    while True:
        try:
            query = input("\033[36m>>> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break
        if query.strip().lower() in ("q", "exit", ""):
            break
        history.append({
            "role": "user",
            "content": query
        })
        agent_loop(history)
        last = history[-1]["content"]
        if isinstance(last, list):
            for block in last:
                if hasattr(block, "text"):
                    print(block.text)
        print()
