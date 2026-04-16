import os
import sys
import time
import select
import tty
import termios
import threading
import itertools
from pathlib import Path
from openai import OpenAI
from dotenv import load_dotenv
import json
from prompt_toolkit import PromptSession
from prompt_toolkit.key_binding import KeyBindings
from tool_use import TOOL_HANDLES, TOOL_DEFINITIONS, WORKDIR, set_global_store, TodoStore as _TodoStore
from permissions import (
  RiskLevel, PermissionStore,
  assess_risk, confirm_permission, get_risk_key,
)
from compaction import maybe_compact
from todo import TodoStore

load_dotenv()

ANTHROPIC_KEY = os.getenv("ANTHROPIC_KEY")

client = OpenAI(
  api_key=ANTHROPIC_KEY,
  base_url="https://open.bigmodel.cn/api/paas/v4"
)

def _display_width(s: str) -> int:
  """计算字符串的终端显示宽度（CJK 字符占 2 列）"""
  width = 0
  for ch in s:
    cp = ord(ch)
    if (0x1100 <= cp <= 0x115F or 0x2E80 <= cp <= 0x303E or
        0x3040 <= cp <= 0xA4CF or 0xAC00 <= cp <= 0xD7A3 or
        0xF900 <= cp <= 0xFAFF or 0xFE10 <= cp <= 0xFE1F or
        0xFE30 <= cp <= 0xFE4F or 0xFF00 <= cp <= 0xFF60 or
        0xFFE0 <= cp <= 0xFFE6 or 0x1F300 <= cp <= 0x1F9FF):
      width += 2
    else:
      width += 1
  return width


def _call_with_spinner(fn, label="思考中"):
  """
  在后台线程执行 fn()，主线程显示 spinner 并监听 ESC。
  ESC 或 Ctrl-C 时抛出 KeyboardInterrupt，fn() 的结果正常返回。
  """
  result = [None]
  error: list[Exception | None] = [None]
  done = threading.Event()

  def _worker():
    try:
      result[0] = fn()
    except Exception as e:
      error[0] = e
    finally:
      done.set()

  threading.Thread(target=_worker, daemon=True).start()

  hint = " Esc 中断"
  full_text = f"⠋ {label}...{hint}"
  clear_width = _display_width(full_text) + 2
  frames = itertools.cycle("⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏")
  cancelled = False

  if sys.stdin.isatty():
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
      tty.setcbreak(fd)
      while not done.is_set():
        sys.stdout.write(f"\r{next(frames)} {label}...{hint}")
        sys.stdout.flush()
        r, _, _ = select.select([sys.stdin], [], [], 0.08)
        if r:
          ch = sys.stdin.read(1)
          if ord(ch) == 27:  # ESC
            cancelled = True
            break
    except KeyboardInterrupt:
      cancelled = True
    finally:
      termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
      sys.stdout.write("\r" + " " * clear_width + "\r")
      sys.stdout.flush()
  else:
    # 非 tty 环境（管道 / 测试）：静默等待
    done.wait()

  if cancelled:
    raise KeyboardInterrupt

  done.wait()
  if error[0] is not None:
    raise error[0]
  return result[0]


_input_kb = KeyBindings()

@_input_kb.add("escape", eager=True)
def _(event):
  event.app.exit(exception=KeyboardInterrupt())

_session = PromptSession(key_bindings=_input_kb)


SystemPrompt = (
  f"你是一个专业的代码助手, 当前工作路径是: {WORKDIR} "
  f"你没有任何关于文件内容的先验知识。"
  f"读取文件前必须调用 read_file 工具，禁止猜测文件内容。\n\n"
  f"## 记忆规范\n"
  f"**会话开始时（第一轮必做）：**\n"
  f"1. project_memory(action='load', scope='project')\n"
  f"   - 'no_summary' → 阅览项目后立即 save project\n"
  f"   - 有内容 → 直接使用，无需重复阅览已知文件\n"
  f"2. project_memory(action='load', scope='session')\n"
  f"   - 'no_summary' → 新会话，从用户输入开始\n"
  f"   - 有内容 → 告知用户上次进度，询问是否继续\n\n"
  f"**会话过程中：**\n"
  f"- 完成每个重要步骤后：save session（更新进度）\n"
  f"- 新增模块/重构/修改接口后：save project（更新项目知识）\n\n"
  f"## 待办任务规范\n"
  f"当用户提出多步骤任务时，你必须：\n"
  f"1. 先用 todo 工具创建待办列表，将任务拆解为有序步骤\n"
  f"2. 开始执行每一步前，用 todo update 将状态设为 in_progress（并提供 activeForm 描述当前动作）\n"
  f"3. 完成每一步后，用 todo update 将状态设为 completed\n"
  f"4. 如果某一步失败，保持 in_progress 状态并告知用户\n"
  f"5. 单步骤简单任务无需创建待办\n"
  f"待办 ID 使用递增数字：'1', '2', '3'..."
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
  assistant_tool_call_ids = set()   # 所有 assistant 发出的 tool_call.id
  tool_result_ids = set()           # 所有 tool 消息引用的 tool_call_id

  for i, msg in enumerate(messages):
    role = msg["role"]

    if role == "assistant" and msg.get("tool_calls"):
      for tc in msg["tool_calls"]:
        assistant_tool_call_ids.add(tc["id"])

    if role == "tool":
      tc_id = msg.get("tool_call_id")
      if not tc_id:
        raise ValueError(f"索引 {i} 的 tool 消息缺少 tool_call_id")
      tool_result_ids.add(tc_id)

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
# 过程输出辅助函数
# ──────────────────────────────────────────────

# 工具名到图标/颜色的映射
TOOL_STYLE = {
  "bash":       ("⚙️ ",  "\033[33m"),   # 黄色
  "read_file":  ("📖",  "\033[36m"),   # 青色
  "write_file": ("✏️ ",  "\033[32m"),   # 绿色
  "edit_file":  ("🔧",  "\033[35m"),   # 紫色
  "git":        ("🔀",  "\033[34m"),   # 蓝色
  "todo":       ("📋",  "\033[36m"),   # 青色
}
RESET = "\033[0m"
DIM   = "\033[2m"


def _short_preview(text: str, max_len: int = 120) -> str:
  """截断长文本为单行预览"""
  one_line = text.replace("\n", "↵ ").strip()
  if len(one_line) > max_len:
    return one_line[:max_len] + "…"
  return one_line


def _print_tool_call(name: str, args: dict, idx: int, total: int):
  """打印工具调用的过程信息"""
  icon, color = TOOL_STYLE.get(name, ("👉", "\033[37m"))
  tag = f"{icon}{name}"

  # 根据工具类型提取关键参数做摘要
  summary = ""
  if name == "bash":
    summary = _short_preview(args.get("command", ""), 80)
  elif name == "read_file":
    path = args.get("path", "")
    limit = args.get("limit")
    summary = path + (f" (limit={limit})" if limit else "")
  elif name == "write_file":
    path = args.get("path", "")
    content_len = len(args.get("content", ""))
    summary = f"{path} ({content_len} chars)"
  elif name == "edit_file":
    path = args.get("path", "")
    old_preview = _short_preview(args.get("old_text", ""), 40)
    summary = f"{path}  ← {old_preview}"
  elif name == "git":
    summary = _short_preview(args.get("command", ""), 80)
  elif name == "todo":
    action = args.get("action", "")
    tid = args.get("id", "")
    content = _short_preview(args.get("content", ""), 60)
    status = args.get("status", "")
    parts = [action]
    if tid:
      parts.append(f"[{tid}]")
    if content:
      parts.append(content)
    if status:
      parts.append(f"→ {status}")
    summary = " ".join(parts)

  counter = f"[{idx}/{total}] " if total > 1 else ""
  print(f"\n{DIM}{counter}{color}{tag}{RESET}{DIM} {summary}{RESET}")


def _print_tool_result(name: str, output: str):
  """打印工具执行结果的摘要"""
  icon, _ = TOOL_STYLE.get(name, ("👉", ""))
  # 截取前几行作为预览
  lines = output.strip().splitlines()
  preview_lines = lines[:5]
  preview = "\n".join(preview_lines)
  if len(lines) > 5:
    preview += f"\n  ... ({len(lines) - 5} more lines)"

  # 如果输出很短就直接显示，否则折叠
  if len(output) < 200:
    print(f"{DIM}  ↳ {preview}{RESET}")
  else:
    print(f"{DIM}  ↳ {preview}{RESET}")
    print(f"{DIM}  ({len(output)} chars total){RESET}")


# ──────────────────────────────────────────────
# 主循环
# ──────────────────────────────────────────────

state = {
  "messages": [{"role": "system", "content": SystemPrompt}],
  "pending_tool_calls": False,  # 标记上一轮是否有未完成的 tool_calls
  "permissions": PermissionStore(),  # 会话级权限存储
  "todo_store": TodoStore(),  # 待办任务管理
}

# 将 todo store 注入到 tool_use 模块
set_global_store(state["todo_store"])


def _print_todo_status(todo_store):
  """当待办列表有变更时，渲染当前状态"""
  if not todo_store.is_empty and todo_store.has_changed:
    rendered = todo_store.render()
    if rendered:
      print(f"\n{rendered}")
    todo_store.mark_displayed()


def main_loop(state):
  while True:
    # 如果上一轮是 tool_result，不需要用户输入，直接继续
    if not state["pending_tool_calls"]:
      # 展示待办进度摘要（在用户输入前）
      todo_store = state["todo_store"]
      if not todo_store.is_empty:
        done, total = todo_store.progress
        pct = done * 100 // total
        print(f"\n{DIM}📋 待办进度: {done}/{total} ({pct}%){RESET}")

      try:
        user_input = _session.prompt("\n> ")
      except (EOFError, KeyboardInterrupt):
        print("\n再见！")
        break
      if user_input.strip().lower() in ("exit", "quit", "q"):
        print("再见！")
        break
      state["messages"].append({"role": "user", "content": user_input})

    # 发送前压缩 + 规范化消息
    state["messages"], compacted = maybe_compact(state["messages"], client, MODEL)
    if compacted == "trim":
      print(f"{DIM}[已裁剪旧消息]{RESET}")
    elif compacted == "compact":
      print(f"{DIM}[已压缩对话历史]{RESET}")

    try:
      clean_messages = normalize_messages(state["messages"])
    except ValueError as e:
      print(f"[消息规范化错误] {e}")
      # 尝试修复：回退到最近一次合法状态
      _repair_messages(state)
      continue

    try:
      response = _call_with_spinner(
        lambda: client.chat.completions.create(
          model=MODEL,
          messages=clean_messages,
          tools=TOOL_DEFINITIONS,
          temperature=0.7,
          max_tokens=4096,
          stream=False,
        ),
        "思考中",
      )
    except Exception as e:
      print(f"请求失败: {e}")
      # 如果是 400 错误，尝试修复消息历史
      if hasattr(e, "status_code") and e.status_code == 400:
        print("[尝试修复消息历史...]")
        _repair_messages(state)
        continue
      break

    if not response.choices or len(response.choices) == 0:
      print("出错：无响应")
      break

    message = response.choices[0].message

    # ── 如果 assistant 有文本内容，先展示 ──
    if message.content:
      print(f"\n{message.content}")

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
      for idx, tc in enumerate(message.tool_calls, 1):
        handler = TOOL_HANDLES.get(tc.function.name)
        try:
          args = json.loads(tc.function.arguments)
        except json.JSONDecodeError:
          args = {}

        # ── 过程输出：工具调用开始 ──
        _print_tool_call(tc.function.name, args, idx, len(message.tool_calls))

        # ── 权限检查 ──
        risk = assess_risk(tc.function.name, args)
        if risk == RiskLevel.DANGEROUS:
          allowed = confirm_permission(
            tc.function.name, args, state["permissions"]
          )
          if not allowed:
            output = "error: permission denied by user"
            _print_tool_result(tc.function.name, output)
            state["messages"].append({
              "role": "tool",
              "tool_call_id": tc.id,
              "content": output,
            })
            continue

        if handler:
          try:
            output = _call_with_spinner(lambda: handler(**args), "执行中")
          except Exception as e:
            output = f"error: tool execution failed: {e}"
        else:
          output = f"error: unknown tool: {tc.function.name}"

        # ── 过程输出：工具调用结果 ──
        _print_tool_result(tc.function.name, output)

        # ✅ 每个 tool_use 都必须有匹配的 tool_result（通过 tool_call_id 关联）
        state["messages"].append({
          "role": "tool",
          "tool_call_id": tc.id,
          "content": str(output),
        })

      state["pending_tool_calls"] = True

      # ── 待办状态展示 ──
      _print_todo_status(state["todo_store"])

    elif message.content:
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
