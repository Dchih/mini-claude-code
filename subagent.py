"""
子代理模块 —— 局部任务的隔离执行单元

设计原则：
  - 父 agent 把局部任务丢给一个带独立 messages 的子 agent 去做
  - 子 agent 用受限工具完成后，只返回摘要，不回灌完整过程
  - 子代理内部有独立的对话历史，与父代理完全隔离
  - 受 max_turns 约束，防止子代理失控

数据流：
  父 agent → subagent(task=..., tools=[...]) → SubagentContext → 独立 LLM 循环 → 摘要 → 返回父 agent

注意：本模块不 import tool_use，避免循环依赖。
所有工具 handler 和定义通过参数注入。
"""

import json
from dataclasses import dataclass, field
from typing import Optional, Callable
from pathlib import Path


# ──────────────────────────────────────────────
# 常量
# ──────────────────────────────────────────────

WORKDIR = Path.cwd()

# 子代理默认可用的工具名（只读 + 安全操作）
DEFAULT_SUBAGENT_TOOLS = ["bash", "read_file", "git"]

# 子代理绝对不能使用的工具（防止递归/越权）
FORBIDDEN_TOOLS = {"subagent", "write_file", "edit_file", "project_memory"}

# 所有允许子代理使用的工具名
ALLOWED_TOOL_NAMES = {"bash", "read_file", "git"}


# ──────────────────────────────────────────────
# SubagentContext 数据结构
# ──────────────────────────────────────────────

@dataclass
class SubagentContext:
  """
  子代理上下文 —— 完全隔离的执行环境。

  Attributes:
    messages:  独立的对话历史（system + task + 内部轮次）
    tools:     子代理可用的工具定义列表
    handlers:  工具名 → handler 函数的映射
    max_turns: 最大工具调用轮次（防止失控）
  """
  messages: list[dict]
  tools: list[dict]
  handlers: dict[str, Callable]
  max_turns: int = 10

  # 运行时状态（不由外部设置）
  _turn_count: int = field(default=0, init=False, repr=False)
  _tool_call_log: list[dict] = field(default_factory=list, init=False, repr=False)

  @property
  def turn_count(self) -> int:
    return self._turn_count

  @property
  def is_exhausted(self) -> bool:
    return self._turn_count >= self.max_turns

  def record_tool_call(self, name: str, args: dict, output: str):
    """记录一次工具调用（用于最终摘要生成）"""
    self._tool_call_log.append({
      "tool": name,
      "args_summary": _summarize_args(name, args),
      "output_preview": output[:200] if output else "",
    })

  def increment_turn(self):
    self._turn_count += 1


# ──────────────────────────────────────────────
# 参数摘要辅助
# ──────────────────────────────────────────────

def _summarize_args(name: str, args: dict) -> str:
  """为摘要生成提取工具参数的关键信息"""
  if name == "bash":
    return args.get("command", "")[:80]
  elif name == "read_file":
    path = args.get("path", "")
    limit = args.get("limit")
    return path + (f" (limit={limit})" if limit else "")
  elif name == "git":
    return args.get("command", "")[:80]
  return json.dumps(args, ensure_ascii=False)[:100]


# ──────────────────────────────────────────────
# 子代理 System Prompt
# ──────────────────────────────────────────────

SUBAGENT_SYSTEM_PROMPT = (
  f"你是一个子代理，负责完成父代理委派的局部任务。\n"
  f"当前工作路径: {WORKDIR}\n\n"
  f"规则：\n"
  f"1. 你只能使用提供的受限工具集\n"
  f"2. 完成任务后，用简洁的文字总结你的发现和结果\n"
  f"3. 不要做超出任务范围的额外操作\n"
  f"4. 如果无法完成任务，说明原因\n"
  f"5. 读取文件前必须调用 read_file，禁止猜测文件内容\n"
)


# ──────────────────────────────────────────────
# 消息规范化（子代理内嵌版本，避免循环依赖）
# ──────────────────────────────────────────────

VALID_ROLES = {"system", "user", "assistant", "tool"}

ALLOWED_FIELDS = {
  "system":    {"role", "content"},
  "user":      {"role", "content"},
  "assistant": {"role", "content", "tool_calls", "refusal"},
  "tool":      {"role", "content", "tool_call_id"},
}


def _sanitize_message(msg: dict) -> dict:
  """清洗单条消息，只保留协议定义的字段"""
  role = msg.get("role", "")
  if role not in VALID_ROLES:
    raise ValueError(f"无效的消息角色: {role!r}")
  allowed = ALLOWED_FIELDS.get(role, {"role", "content"})
  return {k: v for k, v in msg.items() if k in allowed}


def _validate_message_sequence(messages: list[dict]) -> None:
  """校验消息序列合法性（轻量版，只做必要检查）"""
  if not messages:
    return

  assistant_tool_call_ids = set()
  tool_result_ids = set()

  for i, msg in enumerate(messages):
    role = msg["role"]

    if role == "system" and i != 0:
      raise ValueError(f"system 消息只能出现在开头")

    if role == "assistant" and msg.get("tool_calls"):
      for tc in msg["tool_calls"]:
        assistant_tool_call_ids.add(tc["id"])

    if role == "tool":
      tc_id = msg.get("tool_call_id")
      if not tc_id:
        raise ValueError(f"tool 消息缺少 tool_call_id")
      tool_result_ids.add(tc_id)

  for tc_id in assistant_tool_call_ids:
    if tc_id not in tool_result_ids:
      raise ValueError(f"tool_call (id={tc_id}) 没有对应的 tool_result")

  for tr_id in tool_result_ids:
    if tr_id not in assistant_tool_call_ids:
      raise ValueError(f"tool 消息引用了不存在的 tool_call_id: {tr_id}")


def _normalize_messages(messages: list[dict]) -> list[dict]:
  """子代理内部的消息规范化"""
  sanitized = [_sanitize_message(m) for m in messages]
  _validate_message_sequence(sanitized)
  return sanitized


# ──────────────────────────────────────────────
# 摘要生成
# ──────────────────────────────────────────────

def _build_summary_from_context(ctx: SubagentContext, final_text: str) -> str:
  """
  从子代理上下文生成结构化摘要。

  不调用 LLM，纯规则生成，确保快速且无额外开销。
  """
  lines = ["## 子代理执行摘要", ""]

  # 任务结果
  if final_text:
    lines.append("### 结果")
    lines.append(final_text.strip())
    lines.append("")

  # 工具调用统计
  if ctx._tool_call_log:
    lines.append(f"### 工具调用 ({len(ctx._tool_call_log)} 次)")
    for i, log in enumerate(ctx._tool_call_log, 1):
      lines.append(f"  {i}. {log['tool']}: {log['args_summary']}")
    lines.append("")

  # 轮次信息
  lines.append(f"### 执行统计")
  lines.append(f"- 使用轮次: {ctx.turn_count}/{ctx.max_turns}")
  if ctx.is_exhausted:
    lines.append(f"- ⚠️ 达到最大轮次限制，任务可能未完成")
  lines.append("")

  return "\n".join(lines)


# ──────────────────────────────────────────────
# 消息裁剪（子代理专用，轻量版）
# ──────────────────────────────────────────────

def _char_count(messages: list[dict]) -> int:
  return sum(len(json.dumps(m, ensure_ascii=False)) for m in messages)


def _trim_subagent_messages(messages: list[dict], keep_turns: int = 4) -> list[dict]:
  """
  裁剪子代理的旧消息，保留 system + 最近 N 轮。

  与父代理的 compaction 不同，子代理不需要 LLM 压缩，
  直接裁剪即可（子代理的对话通常较短）。
  """
  if len(messages) <= 2:
    return messages

  system = messages[0]
  rest = messages[1:]

  # 按 user 消息分轮
  turns = []
  current_turn = []
  for msg in rest:
    if msg["role"] == "user" and current_turn:
      turns.append(current_turn)
      current_turn = [msg]
    else:
      current_turn.append(msg)
  if current_turn:
    turns.append(current_turn)

  # 只保留最近 keep_turns 轮
  kept = turns[-keep_turns:]

  # 添加裁剪提示
  trimmed_count = len(turns) - keep_turns
  result = [system]
  if trimmed_count > 0:
    result.append({
      "role": "user",
      "content": f"[之前 {trimmed_count} 轮对话已裁剪]",
    })
    result.append({
      "role": "assistant",
      "content": "了解，继续执行任务。",
    })

  for turn in kept:
    result.extend(turn)

  return result


# ──────────────────────────────────────────────
# 核心：子代理运行循环
# ──────────────────────────────────────────────

def run_subagent(
  task: str,
  client,
  model: str,
  tools: Optional[list[str]] = None,
  max_turns: int = 10,
  full_tool_defs: Optional[list[dict]] = None,
  all_handlers: Optional[dict[str, Callable]] = None,
) -> str:
  """
  启动子代理执行局部任务，返回结构化摘要。

  Args:
    task:           任务描述（由父 agent 提供）
    client:         OpenAI 客户端实例
    model:          模型名称
    tools:          允许的工具名列表（默认 DEFAULT_SUBAGENT_TOOLS）
    max_turns:      最大工具调用轮次
    full_tool_defs: 完整工具定义列表（用于筛选子代理可用工具）
    all_handlers:   完整工具 handler 映射（用于筛选子代理可用 handler）

  Returns:
    结构化摘要字符串，作为 subagent 工具的返回值注入父 agent 的对话
  """
  # ── 1. 构建受限工具集 ──
  tool_names = set(tools or DEFAULT_SUBAGENT_TOOLS)
  # 强制排除禁止的工具
  tool_names -= FORBIDDEN_TOOLS
  # 只保留已知允许的工具
  tool_names &= ALLOWED_TOOL_NAMES

  if not tool_names:
    return "error: 子代理没有可用的工具"

  # 筛选工具定义
  if full_tool_defs:
    sub_tool_defs = [td for td in full_tool_defs if td["function"]["name"] in tool_names]
  else:
    sub_tool_defs = []

  # 筛选 handler
  if all_handlers:
    sub_handlers = {name: all_handlers[name] for name in tool_names if name in all_handlers}
  else:
    sub_handlers = {}

  if not sub_handlers:
    return "error: 子代理没有可用的工具 handler"

  # ── 2. 构建 SubagentContext ──
  ctx = SubagentContext(
    messages=[
      {"role": "system", "content": SUBAGENT_SYSTEM_PROMPT},
      {"role": "user",   "content": task},
    ],
    tools=sub_tool_defs,
    handlers=sub_handlers,
    max_turns=max_turns,
  )

  # ── 3. 独立 LLM 循环 ──
  final_text = ""

  while not ctx.is_exhausted:
    try:
      clean = _normalize_messages(ctx.messages)
    except ValueError as e:
      final_text = f"[子代理消息规范化错误: {e}]"
      break

    # 上下文过大时裁剪
    if _char_count(ctx.messages) > 60_000:
      ctx.messages = _trim_subagent_messages(ctx.messages, keep_turns=4)

    try:
      response = client.chat.completions.create(
        model=model,
        messages=clean,
        tools=sub_tool_defs if sub_tool_defs else None,
        temperature=0.5,
        max_tokens=2048,
        stream=False,
      )
    except Exception as e:
      final_text = f"[子代理 LLM 请求失败: {e}]"
      break

    if not response.choices:
      final_text = "[子代理收到空响应]"
      break

    message = response.choices[0].message
    final_text = message.content or ""

    # ── 无工具调用 → 子代理自行结束 ──
    if not message.tool_calls:
      ctx.messages.append({
        "role": "assistant",
        "content": final_text,
      })
      break

    # ── 有工具调用 → 执行并继续 ──
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
    ctx.messages.append(assistant_msg)
    ctx.increment_turn()

    for tc in message.tool_calls:
      handler = ctx.handlers.get(tc.function.name)
      try:
        args = json.loads(tc.function.arguments)
      except json.JSONDecodeError:
        args = {}

      if handler:
        try:
          output = handler(**args)
        except Exception as e:
          output = f"error: {e}"
      else:
        output = f"error: unknown tool: {tc.function.name}"

      ctx.record_tool_call(tc.function.name, args, str(output))

      ctx.messages.append({
        "role": "tool",
        "tool_call_id": tc.id,
        "content": str(output),
      })

  # ── 4. 生成摘要 ──
  summary = _build_summary_from_context(ctx, final_text)
  return summary


# ──────────────────────────────────────────────
# 工具函数：供 tool_use.py 调用
# ──────────────────────────────────────────────

def run_subagent_tool(
  task: str,
  tools: Optional[str] = None,
  max_turns: int = 10,
  client=None,
  model: str = "",
  full_tool_defs: Optional[list[dict]] = None,
  all_handlers: Optional[dict[str, Callable]] = None,
) -> str:
  """
  subagent 工具的入口函数。

  Args:
    task:         任务描述
    tools:        逗号分隔的工具名列表（可选，默认 "bash,read_file,git"）
    max_turns:    最大工具调用轮次
    client:       OpenAI 客户端（由调用方注入）
    model:        模型名称
    full_tool_defs: 完整工具定义列表
    all_handlers:  完整工具 handler 映射

  Returns:
    结构化摘要
  """
  tool_list = None
  if tools:
    tool_list = [t.strip() for t in tools.split(",") if t.strip()]

  return run_subagent(
    task=task,
    client=client,
    model=model,
    tools=tool_list,
    max_turns=max_turns,
    full_tool_defs=full_tool_defs,
    all_handlers=all_handlers,
  )
