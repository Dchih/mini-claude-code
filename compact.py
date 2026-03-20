import json
import time
from pathlib import Path
from config import client, SYSTEM, MODEL

THRESHOLD = 50000
KEEP_RECENT = 3
TRANSCRIPT_DIR = Path.cwd() / ".transcript"


def estimate_token(messages: list) -> int:
  """4 chars = 1 token"""
  return len(str(messages)) // 4

def micro_compact(messages: list) -> str:
  """
  每轮静默执行：把旧的 tool_result 内容替换成短占位符。
  只保留最近 KEEP_RECENT 个 tool_result 的完整内容。

  比如第 1 轮的 bash 输出了 2000 字的 ls 结果，
  到了第 5 轮时，那个 2000 字就变成 "[Previous: used bash]"
  """
  tool_results = []
  for msg_idx, msg in enumerate(messages):
    if msg["role"] == "user" and isinstance(msg.get("content"), list):
      for part_idx, part in enumerate(msg["content"]):
        if isinstance(part, dict) and part.get("type") == "tool_result":
          tool_results.append(msg_idx, part_idx, part)

  if len(tool_results) <= KEEP_RECENT:
    return
  
  tool_name_map = {}
  for msg in messages:
    if msg["role"] == "assistant":
      content = msg.get("content", [])
      if isinstance(content, list):
        for block in content:
          if hasattr(block, "type") and block.type == "tool_use":
            tool_name_map[block.id] = block.name

  to_clear = tool_results[:-KEEP_RECENT]
  clear_count = 0
  for _, _, result in to_clear:
    content = result.get("content", "")
    if isinstance(content, str) and len(content) > 100:
      tool_id = result.get("tool_use_id", "")
      tool_name = tool_name_map.get(tool_id, "unknown")
      result["content"] = f"[Previous: used {tool_name}]"
      cleared_count += 1
  
  if clear_count > 0:
    print(f"\033[90m  [micro_compact] cleared {cleared_count} old tool results\033[0m")

def auto_compact(messages: list) -> list:
  """
  token 超阈值时触发：
  1. 保存完整对话到磁盘（以防万一需要回溯）
  2. 让 LLM 总结对话
  3. 用摘要替换所有历史消息
  """
  TRANSCRIPT_DIR.mkdir(exist_ok=True)
  transcript_path = TRANSCRIPT_DIR / f"transcript_{int(time.time())}.jsonl"
  with open(transcript_path, "w", encoding="utf-8") as f:
    for msg in messages:
      f.write(json.dumps(msg, default=str, ensure_ascii=False) + "\n")
    print(f"\033[90m  [transcript saved: {transcript_path}]\033[0m")

  conversation_text = json.dumps(messages, default=str, ensure_ascii=False)[:80000]
  response = client.messages.create(
    model = MODEL,
    messages=[{
      "role": "user",
      "content": (
              "Summarize this conversation for continuity. Include: "
              "1) What was accomplished, "
              "2) Current state of files/code, "
              "3) Key decisions made. "
              "Be concise but preserve critical details.\n\n"
              + conversation_text
      )
    }],
    max_tokens=2000
  )
  summary = response.content[0].text
  print(f"\033[90m  [compressed to {len(summary)} chars summary]\033[0m")

  return [
      {
          "role": "user",
          "content": f"[Conversation compressed. Transcript: {transcript_path}]\n\n{summary}",
      },
      {
          "role": "assistant",
          "content": "Understood. I have the context from the summary. Continuing.",
      },
  ]