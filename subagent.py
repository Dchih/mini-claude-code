def spawn_subagent(task: str) -> str:
    """Spawn a sub-agent with all tools except subagent (no recursion)."""
    from agent import agent_loop
    from tools import TOOLS, TOOL_HANDLERS

    sub_tools = [t for t in TOOLS if t["name"] != "subagent"]
    sub_handlers = {k: v for k, v in TOOL_HANDLERS.items() if k != "subagent"}

    messages = [{"role": "user", "content": task}]

    print(f"\033[35m[subagent] Starting: {task[:80]}\033[0m")
    agent_loop(messages, tools=sub_tools, tool_handlers=sub_handlers)

    last = messages[-1]["content"]
    if isinstance(last, list):
        texts = [b.text for b in last if hasattr(b, "text")]
        return "\n".join(texts) if texts else "(subagent produced no text)"
    return str(last)
