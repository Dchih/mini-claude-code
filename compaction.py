"""
上下文压缩模块

两阶段流水线：
  1. 整体 context 偏大 → 裁剪旧消息（优先裁 tool output）
  2. 整体 context 仍然过大 → LLM 压缩成结构化摘要
"""

import json
from pathlib import Path

# ── 阈值 ──────────────────────────────────────────
TOOL_OUTPUT_TRIM  = 400      # 旧 tool output 裁剪到此长度
CONTEXT_TRIM      = 80_000   # 整体超过此字符数 → 裁剪旧消息
CONTEXT_COMPACT   = 150_000  # 裁剪后仍超过 → LLM 压缩

KEEP_RECENT_TURNS = 6        # 裁剪时保留最近几个完整轮次


# ── 工具函数 ──────────────────────────────────────

def _char_count(messages: list[dict]) -> int:
    return sum(len(json.dumps(m, ensure_ascii=False)) for m in messages)


def _count_turns(messages: list[dict]) -> list[int]:
    """每条消息所属的轮次（system=-1，每遇 user 轮次+1）。"""
    turns = []
    turn = -1
    for msg in messages:
        if msg["role"] == "system":
            turns.append(-1)
        else:
            if msg["role"] == "user":
                turn += 1
            turns.append(turn)
    return turns


# ── Phase 1：裁剪旧消息 ───────────────────────────

def trim_old_messages(messages: list[dict]) -> list[dict]:
    """
    对超出最近 KEEP_RECENT_TURNS 轮的消息进行裁剪：
    - tool output  → 截断到 TOOL_OUTPUT_TRIM 字符
    - 其他长消息   → 替换为单行占位
    system 和短消息原样保留。
    """
    turns = _count_turns(messages)
    max_turn = max((t for t in turns if t >= 0), default=0)
    cutoff = max_turn - KEEP_RECENT_TURNS

    result = []
    for msg, turn in zip(messages, turns):
        if turn >= 0 and turn < cutoff:
            content = msg.get("content", "")
            if msg["role"] == "tool" and len(content) > TOOL_OUTPUT_TRIM:
                trimmed = content[:TOOL_OUTPUT_TRIM] + f"\n... [已裁剪，原 {len(content)} 字符]"
                msg = {**msg, "content": trimmed}
            elif msg["role"] != "tool" and len(content) > 200:
                msg = {**msg, "content": f"[{msg['role']} 已压缩，原 {len(content)} 字符]"}
        result.append(msg)
    return result


# ── Phase 2：LLM 压缩 ────────────────────────────

_COMPACT_SYSTEM = "你是一个对话历史摘要助手，输出简洁、准确的中文结构化摘要。"

_COMPACT_PROMPT = """\
请将下面的对话历史压缩为结构化摘要，必须保留以下五点：

## 当前任务目标
（用户本次会话想完成什么）

## 已完成的关键动作
（列举执行过的重要操作，每条一行）

## 已修改或重点查看的文件
（文件路径 + 一句话说明）

## 关键决定与约束
（影响后续工作的决策、规范、限制）

## 下一步应做什么
（当前进度下，最应该执行的下一步）

---
对话历史：
{history}
"""


def _format_history(messages: list[dict]) -> str:
    lines = []
    for msg in messages:
        role = msg["role"]
        content = msg.get("content", "")
        tool_calls = msg.get("tool_calls")

        if role == "system":
            continue
        if role == "assistant" and tool_calls:
            names = ", ".join(tc["function"]["name"] for tc in tool_calls)
            lines.append(f"[assistant → 调用工具: {names}]")
            if content:
                lines.append(f"[assistant]: {content[:300]}")
        elif role == "tool":
            lines.append(f"[tool 结果]: {content[:400]}")
        else:
            lines.append(f"[{role}]: {content[:600]}")
    return "\n".join(lines)


def compact_history(messages: list[dict], client, model: str) -> list[dict]:
    """调用 LLM 将消息历史压缩为摘要，失败时退化为激进裁剪。"""
    system = messages[0]
    history_text = _format_history(messages[1:])
    prompt = _COMPACT_PROMPT.format(history=history_text)

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _COMPACT_SYSTEM},
                {"role": "user",   "content": prompt},
            ],
            temperature=0.2,
            max_tokens=2048,
            stream=False,
        )
        summary = resp.choices[0].message.content
    except Exception as e:
        summary = f"[压缩失败: {e}]\n保留最近对话内容。"

    return [
        system,
        {
            "role": "user",
            "content": f"[以下是之前对话的压缩摘要，请据此继续工作]\n\n{summary}",
        },
        {
            "role": "assistant",
            "content": "已了解历史摘要，将继续完成任务。",
        },
    ]


# ── 统一入口 ──────────────────────────────────────

def maybe_compact(
    messages: list[dict],
    client,
    model: str,
) -> tuple[list[dict], str]:
    """
    按需执行压缩流水线，返回 (新 messages, 触发阶段描述)。
    阶段描述为空字符串表示未触发任何压缩。
    """
    size = _char_count(messages)
    if size < CONTEXT_TRIM:
        return messages, ""

    # Phase 1：裁剪旧消息
    messages = trim_old_messages(messages)
    size = _char_count(messages)
    if size < CONTEXT_COMPACT:
        return messages, "trim"

    # Phase 2：LLM 压缩
    messages = compact_history(messages, client, model)
    return messages, "compact"
