from pathlib import Path
from openai import OpenAI


client = OpenAI(
  api_key=ANTHROPIC_KEY,
  base_url="https://open.bigmodel.cn/api/paas/v4"
)

systemPropmt = ""

while True: {

  response = client.chat.completions.create({
    model="GLM-5.1",

  })
}