import os
from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()

client = Anthropic(base_url=os.getenv("ANTHROPIC_API_URL"), api_key=os.getenv("ANTHROPIC_API_KEY"))
MODEL = os.environ["MODEL_ID"]

TOOLS = [
  {
    "name" : "bash",
    "description" : "Run a shell command and return its output.",
    "input_schema" : {
      "type" : "object",
      "properties" : {
        "command" : {
          "type" : "string",
          "description" : "The shell command to run."
        }
      },
      "required" : ["command"]
    }
  }
]

SYSTEM = "You are a coding agent. Use bash to solve tasks. Act, don't explain."

response = client.messages.create(
    model=MODEL,
    system=SYSTEM,
    max_tokens=1000,
    tools=TOOLS,
    messages=[
        {
            "role": "user",
            "content": "列出当前目录的所有文件"
        }
    ]
)

print(f"停止原因: {response.stop_reason}")
print()

for block in response.content: 
    print(f"类型：{block.type}")
    if block.type == "text": 
        print(f"内容：{block.text}")
    elif block.type == "tool_use":
        print(f"工具名称：{block.name}")
        print(f"参数：{block.input}")
        print(f"工具调用ID：{block.id}")