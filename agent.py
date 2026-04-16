import os
import sys
import time
import threading
import itertools
from pathlib import Path
from openai import OpenAI
from dotenv import load_dotenv
import json
from prompt_toolkit import prompt as pt_prompt
from tool_use import TOOL_HANDLES, TOOL_DEFINITIONS, WORKDIR

load_dotenv()

ANTHROPIC_KEY = os.getenv("ANTHROPIC_KEY")

client = OpenAI(
  api_key=ANTHROPIC_KEY,
  base_url="https://open.bigmodel.cn/api/paas/v4"
)

def spinner_context(label="思考中"):
  """上下文管理器：在 with 块执行期间显示旋转动画"""
  stop = threading.Event()

  def _spin():
    frames = itertools.cycle("⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏")
    while not stop.is_set():
      sys.stdout.write(f"\r{next(frames)} {label}...")
      sys.stdout.flush()
      time.sleep(0.08)
    sys.stdout.write("\r" + " " * (len(label) + 6) + "\r")
    sys.stdout.flush()

  class _Ctx:
    def __enter__(self):
      threading.Thread(target=_spin, daemon=True).start()
      return self
    def __exit__(self, *_):
      stop.set()
      time.sleep(0.1)  # 等动画线程写完最后一帧

  return _Ctx()


SystemPrompt = (
  f"你是一个专业的代码助手, 当前工作路径是: {WORKDIR} "
  f"你没有任何关于文件内容的先验知识。"
  f"读取文件前必须调用 read_file 工具，禁止猜测文件内容。"
)
MODEL = "GLM-5.1"

# ──────────────────────────────────────────────
# 消息规范化层
# ──────────────────────────────────────────────

# 协议允许的角色
VALID_ROLES = {"system", "user", "assistant", "tool"}

# 每种角色允许的字段（其余字段在发送前会被清洗掉）
ALLOWED_FIELDS = {
  "system":    {"role", "content"},
  "user":      {"role", "content"},
  "assistant": {"role", "content", "tool_calls", "refusal"},
  "tool":      {"role", "content", "tool_call_id"},
}


def sanitize_message(msg: dict) -> dict:
  """清洗单条消息，只保留协议定义的字段，防止内部元数据泄漏导致 400"""
  role = msg.get("role", "")
  if role not in VALID_ROLES:
    raise ValueError(f"无效的消息角色: {role!r}")

  allowed = ALLOWED_FIELDS.get(role, {"role", "content"})
  return {k: v for k, v in msg.items() if k in allowed}


def validate_message_sequence(messages: list[dict]) -> None:
  """
  校验整个消息序列的合法性：
  1. user / assistant 必须严格交替（system 只能在开头）
  2. 每个 tool 消息前必须有带 tool_calls 的 assistant 消息
  3. assistant 的每个 tool_call 都必须有对应的 tool 消息（通过 tool_call_id 关联）
  """
  if not messages:
    return

  # --- 检查 system 只在开头 ---
  for i, msg in enumerate(messages):
    if msg["role"] == "system" and i != 0:
      raise ValueError(f"system 消息只能出现在开头，但在索引 {i} 处发现")

  # --- 收集 assistant 的 tool_call_id 集合，以及 tool 消息引用的 id ---
  assistant_tool_call_ids = []   # 所有 assistant 发出的 tool_call.id
  tool_result_ids = []           # 所有 tool 消息引用的 tool_call_id

  for i, msg in enumerate(messages):
    role = msg["role"]

    if role == "assistant" and msg.get("tool_calls"):
      for tc in msg["tool_calls"]:
        assistant_tool_call_ids.append(tc["id"])

    if role == "tool":
      tc_id = msg.get("tool_call_id")
      if not tc_id:
        raise ValueError(f"索引 {i} 的 tool 消息缺少 tool_call_id")
      tool_result_ids.append(tc_id)

      # tool 消息前面必须能追溯到带 tool_calls 的 assistant（中间可以有其他 tool 消息）
      preceding_assistant = next(
        (messages[j] for j in range(i - 1, -1, -1) if messages[j]["role"] == "assistant"),
        None,
      )
      if not preceding_assistant or not preceding_assistant.get("tool_calls"):
        raise ValueError(
          f"索引 {i} 的 tool 消息前必须有带 tool_calls 的 assistant 消息"
        )

  # --- 每个 tool_use 必须有匹配的 tool_result ---
  for tc_id in assistant_tool_call_ids:
    if tc_id not in tool_result_ids:
      raise ValueError(f"assistant 的 tool_call (id={tc_id}) 没有对应的 tool_result")

  # 每个 tool_result 也必须对应一个 assistant 的 tool_call
  for tr_id in tool_result_ids:
    if tr_id not in assistant_tool_call_ids:
      raise ValueError(f"tool 消息引用了不存在的 tool_call_id: {tr_id}")

  # --- 不允许连续 user 消息（assistant 因 tool use 可以连续出现）---
  conversation_roles = [m["role"] for m in messages if m["role"] in ("user", "assistant")]
  for i in range(1, len(conversation_roles)):
    if conversation_roles[i] == "user" and conversation_roles[i - 1] == "user":
      raise ValueError(
        f"user / assistant 消息必须严格交替，"
        f"但索引 {i} 处出现连续的 user 角色"
      )


def normalize_messages(messages: list[dict]) -> list[dict]:
  """
  完整的消息规范化流水线：
  1. 清洗每条消息（去除非协议字段）
  2. 校验序列合法性
  3. 返回清洗后的消息列表
  """
  sanitized = [sanitize_message(m) for m in messages]
  validate_message_sequence(sanitized)
  return sanitized


# ──────────────────────────────────────────────
# 主循环
# ──────────────────────────────────────────────

state = {
  "messages": [{"role": "system", "content": SystemPrompt}],
  "pending_tool_calls": False,  # 标记上一轮是否有未完成的 tool_calls
}


def main_loop(state):
  while True:
    # 如果上一轮是 tool_result，不需要用户输入，直接继续
    if not state["pending_tool_calls"]:
      try:
        user_input = pt_prompt("\n> ")
      except (EOFError, KeyboardInterrupt):
        print("\n再见！")
        break
      if user_input.strip().lower() in ("exit", "quit", "q"):
        print("再见！")
        break
      state["messages"].append({"role": "user", "content": user_input})

    # 发送前规范化消息
    try:
      clean_messages = normalize_messages(state["messages"])
    except ValueError as e:
      print(f"[消息规范化错误] {e}")
      # 尝试修复：回退到最近一次合法状态
      _repair_messages(state)
      continue

    try:
      with spinner_context("思考中"):
        response = client.chat.completions.create(
          model=MODEL,
          messages=clean_messages,
          tools=TOOL_DEFINITIONS,
          temperature=0.7,
          max_tokens=4096,
          stream=False,
        )
    except Exception as e:
      print(f"请求失败: {e}")
      # 如果是 400 错误，尝试修复消息历史
      if "400" in str(e):
        print("[尝试修复消息历史...]")
        _repair_messages(state)
      break

    if not response.choices or len(response.choices) == 0:
      print("出错：无响应")
      break

    message = response.choices[0].message

    if message.tool_calls:
      # ✅ 关键修复：必须先将带 tool_calls 的 assistant 消息追加到历史
      assistant_msg = {
        "role": "assistant",
        "content": message.content or "",
        "tool_calls": [
          {
            "id": tc.id,
            "type": "function",
            "function": {
              "name": tc.function.name,
              "arguments": tc.function.arguments,
            },
          }
          for tc in message.tool_calls
        ],
      }
      state["messages"].append(assistant_msg)

      # 执行每个 tool_call 并追加 tool_result
      for tc in message.tool_calls:
        handler = TOOL_HANDLES.get(tc.function.name)
        try:
          args = json.loads(tc.function.arguments)
        except json.JSONDecodeError:
          args = {}

        if handler:
          try:
            output = handler(**args)
          except Exception as e:
            output = f"error: tool execution failed: {e}"
        else:
          output = f"error: unknown tool: {tc.function.name}"

        # ✅ 每个 tool_use 都必须有匹配的 tool_result（通过 tool_call_id 关联）
        state["messages"].append({
          "role": "tool",
          "tool_call_id": tc.id,
          "content": str(output),
        })

      state["pending_tool_calls"] = True

    elif message.content:
      print(message.content)
      state["messages"].append({
        "role": "assistant",
        "content": message.content,
      })
      state["pending_tool_calls"] = False

    else:
      # 空响应处理
      print("[警告] 模型返回了空响应")
      state["messages"].append({
        "role": "assistant",
        "content": "",
      })
      state["pending_tool_calls"] = False


def _repair_messages(state):
  """
  紧急修复：当消息序列不合法时，尝试回退到安全状态。
  保留 system + 最后一条 user 消息，丢弃损坏的历史。
  """
  messages = state["messages"]
  state["messages"] = [messages[0]]  # 只保留 system，下一轮重新输入
  state["pending_tool_calls"] = False
  print("[已修复消息历史，请重新输入]")


if __name__ == "__main__":
  main_loop(state)
