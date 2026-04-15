import os
from pathlib import Path
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_KEY=os.getenv("ANTHROPIC_KEY")


client = OpenAI(
  api_key=ANTHROPIC_KEY,
  base_url="https://open.bigmodel.cn/api/paas/v4"
)

SystemPropmt = "你是一个专业的代码助手"
MODEL = "GLM-5.1"
UserPrompt=""
state = {
  "messages": [{"role": "system", "content": SystemPropmt}],
  "tool_count": 1,
  "transition_reason": None
}

def main_loop(state):
  while True:
    UserPrompt = input()
    state.messages.append({"role": "user", "content": UserPrompt})

    try: 
      response = client.chat.completions.create(
        model=MODEL,
        messages=state.messages,
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

    if message.tool_calls:
      print("需要调用工具")

    elif message.content:
      print(message.content)

    else:
      handle_empty(message)

    state.messages.append({"role": "assistant", "content": message.content})
    state["turn_count"] += 1
    state["trasition_reason"] = "tool_result"

def handle_empty(message):
  print(f"error: {message}")

if __name__ == "__main__":                                                                                                                                                                                         
      main_loop(state) 



