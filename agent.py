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
contexts = [{"role": "system", "content": SystemPropmt}]

def main_loop(contexts):
  while True:
    UserPrompt = input()
    contexts.append({"role": "user", "content": UserPrompt})

    try: 
      response = client.chat.completions.create(
        model=MODEL,
        messages=contexts,
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

    contexts.append({"role": "assistant", "content": message.content})

    if message.tool_calls:
      print("需要调用工具")

    elif message.content:
      print(message.content)

    else:
      handle_empty(message)


def handle_empty(message):
  print(f"error: {message}")

if __name__ == "__main__":                                                                                                                                                                                         
      main_loop(contexts) 



