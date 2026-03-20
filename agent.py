import httpx
from config import client, MODEL, SYSTEM
from tools import TOOLS, TOOL_HANDLERS
from compact import auto_compact, micro_compact, THRESHOLD, estimate_token

MAX_HISTORY = 20  # max message pairs to keep

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
        micro_compact(messages)

        token_est = estimate_token(messages)
        if token_est > THRESHOLD:
            print(f"\033[91m  [auto_compact triggered! ~{token_est} tokens > {THRESHOLD}]\033[0m")
            messages[:] = auto_compact(messages)

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
        manual_compact = False

        for block in response.content:
            if block.type == "tool_use":
                handler = tool_handlers.get(block.name)
                if block.name == "compact":
                    manual_compact = True
                    output = "Compressing conversation...."
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
                
                if block.name == "load_skill":
                    print(f"\033[35m> load_skill({block.input['name']}): loaded {len(str(output))} chars\033[0m")
                else:
                    print(f"\033[33m> {block.name}: {str(output)[:200]}\033[0m")

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

        if manual_compact:
            print(f"\033[91m  [manual compact triggered]\033[0m")
            messages[:] = auto_compact(messages)

if __name__ == "__main__":
    history = []
    print("Mini Claude Code Agent (输入 q 退出)")
    print("=" * 40)
    while True:
        try:
            tokens = estimate_token(history)
            query = input(f"\033[36m[~{tokens} tok] compact >> \033[0m")
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
