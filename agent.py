import os
from pathlib import Path
from openai import OpenAI
from dotenv import load_dotenv
import json
from tool_use import TOOL_HANDLES, TOOL_DEFINITIONS, WORKDIR

load_dotenv()

ANTHROPIC_KEY=os.getenv("ANTHROPIC_KEY")


client = OpenAI(
  api_key=ANTHROPIC_KEY,
  base_url="https://open.bigmodel.cn/api/paas/v4"
)

SystemPropmt = f"你是一个专业的代码助手, 当前工作路径是: {WORKDIR}\
  你没有任何关于文件内容的先验知识。\
  读取文件前必须调用 read_file 工具，禁止猜测文件内容。"
MODEL = "GLM-5.1"
UserPrompt=""
state = {
  "messages": [{"role": "system", "content": SystemPropmt}],
  "tool_count": 1,
  "transition_reason": None
}

def main_loop(state):
  while True:
    if state["transition_reason"] != "tool_result":
      UserPrompt = input()
      state["messages"].append({"role": "user", "content": UserPrompt})

    try: 
      response = client.chat.completions.create(
        model=MODEL,
        messages=state["messages"],
        tools=TOOL_DEFINITIONS,
        temperature=0.7,
        max_tokens=4096,
        stream=False,
      )
    except Exception as e: 
      print(f"请求失败: {e}")
      break

    if not response.choices or len(response.choices) == 0: 
      print("出错")
      return
    
    message = response.choices[0].message
    
    results = []
    if message.tool_calls:
      
      for block in message.tool_calls:
        handler = TOOL_HANDLES.get(block.function.name)
        args = json.loads(block.function.arguments)
        output = handler(**args) if handler \
          else f"Unknown tool: {block.name}"
        state["messages"].append({
          "role": "tool",
          "tool_call_id": block.id,
          "content": str(output)
        })
      state["transition_reason"] = "tool_result"
    elif message.content:
      print(message.content)
      state["messages"].append({"role": "assistant", "content": message.content})
      state["transition_reason"] = None

    else:
      handle_empty(message)
    
    state["tool_count"] += 1
    

def handle_empty(message):
  print(f"error: {message}")

if __name__ == "__main__":                                                                                                                                                                                         
      main_loop(state) 



