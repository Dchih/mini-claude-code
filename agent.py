import os
from anthropic import Anthropic
from dotenv import load_dotenv
from tools import TOOLS, TOOL_HANDLERS

load_dotenv()

client = Anthropic(base_url=os.getenv("ANTHROPIC_API_URL"), api_key=os.getenv("ANTHROPIC_API_KEY"))
MODEL = os.environ["MODEL_ID"]

SYSTEM = "You are a coding agent. Use the provided tools to solve tasks. Act, don't explain. Prefer read_file/write_file/edit_file over bash for file operations."

def agent_loop(messages: list):
    while True:
        response = client.messages.create(
            model=MODEL,
            system=SYSTEM,
            max_tokens=8000,
            tools=TOOLS,
            messages=messages
        )
        messages.append({
            "role": "assistant",
            "content": response.content
        })
        if response.stop_reason != "tool_use":
            break
        results = []
        for block in response.content:
            if block.type == "tool_use":
                handler = TOOL_HANDLERS.get(block.name)
                if not handler:
                    output = f"Unknown tool: {block.name}"
                else:
                    print(f"\033[33m[{block.name}] {block.input}\033[0m")
                    output = handler(**block.input)
                    print(output[:200])
                results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": output
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
