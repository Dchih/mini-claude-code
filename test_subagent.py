"""
子代理模块单元测试

覆盖核心路径和边界情况：
- SubagentContext 数据结构
- 受限工具集筛选
- 摘要生成
- 消息裁剪
- run_subagent 端到端（mock LLM）
"""

import json
import pytest
from unittest.mock import MagicMock, patch

from subagent import (
  SubagentContext,
  SUBAGENT_SYSTEM_PROMPT,
  ALLOWED_TOOL_NAMES,
  FORBIDDEN_TOOLS,
  DEFAULT_SUBAGENT_TOOLS,
  _summarize_args,
  _build_summary_from_context,
  _trim_subagent_messages,
  _normalize_messages,
  run_subagent,
  run_subagent_tool,
)


# ──────────────────────────────────────────────
# SubagentContext 测试
# ──────────────────────────────────────────────

class TestSubagentContext:
  def test_default_init(self):
    ctx = SubagentContext(
      messages=[{"role": "system", "content": "test"}],
      tools=[],
      handlers={},
    )
    assert ctx.turn_count == 0
    assert ctx.max_turns == 10
    assert not ctx.is_exhausted
    assert ctx._tool_call_log == []

  def test_custom_max_turns(self):
    ctx = SubagentContext(
      messages=[],
      tools=[],
      handlers={},
      max_turns=3,
    )
    assert ctx.max_turns == 3
    assert not ctx.is_exhausted

  def test_increment_and_exhaustion(self):
    ctx = SubagentContext(
      messages=[], tools=[], handlers={}, max_turns=2,
    )
    ctx.increment_turn()
    assert ctx.turn_count == 1
    assert not ctx.is_exhausted

    ctx.increment_turn()
    assert ctx.turn_count == 2
    assert ctx.is_exhausted

  def test_record_tool_call(self):
    ctx = SubagentContext(messages=[], tools=[], handlers={})
    ctx.record_tool_call("bash", {"command": "ls"}, "file1\nfile2")
    ctx.record_tool_call("read_file", {"path": "a.py"}, "content...")

    assert len(ctx._tool_call_log) == 2
    assert ctx._tool_call_log[0]["tool"] == "bash"
    assert ctx._tool_call_log[0]["args_summary"] == "ls"
    assert ctx._tool_call_log[1]["tool"] == "read_file"


# ──────────────────────────────────────────────
# 受限工具集测试
# ──────────────────────────────────────────────

class TestToolFiltering:
  def test_forbidden_tools_excluded(self):
    """确保禁止的工具不在允许列表中"""
    for t in FORBIDDEN_TOOLS:
      assert t not in ALLOWED_TOOL_NAMES

  def test_default_tools_are_allowed(self):
    """默认工具集必须全部在允许列表中"""
    for t in DEFAULT_SUBAGENT_TOOLS:
      assert t in ALLOWED_TOOL_NAMES

  def test_write_edit_forbidden(self):
    """write_file 和 edit_file 不能被子代理使用"""
    assert "write_file" not in ALLOWED_TOOL_NAMES
    assert "edit_file" not in ALLOWED_TOOL_NAMES

  def test_subagent_not_in_allowed(self):
    """subagent 自身不能递归调用"""
    assert "subagent" not in ALLOWED_TOOL_NAMES

  def test_tool_definition_filtering_in_run(self):
    """run_subagent 内部会筛选工具定义，排除禁止工具"""
    full_defs = [
      {"type": "function", "function": {"name": "bash", "parameters": {}}},
      {"type": "function", "function": {"name": "read_file", "parameters": {}}},
      {"type": "function", "function": {"name": "write_file", "parameters": {}}},
      {"type": "function", "function": {"name": "subagent", "parameters": {}}},
    ]
    all_handlers = {
      "bash": lambda **kw: "",
      "read_file": lambda **kw: "",
    }
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _make_mock_response("ok")

    run_subagent(
      task="test",
      client=mock_client,
      model="test",
      full_tool_defs=full_defs,
      all_handlers=all_handlers,
    )

    call_args = mock_client.chat.completions.create.call_args
    tools_arg = call_args.kwargs.get("tools") or call_args[1].get("tools", [])
    names = {t["function"]["name"] for t in tools_arg}
    assert "bash" in names
    assert "read_file" in names
    assert "write_file" not in names
    assert "subagent" not in names


# ──────────────────────────────────────────────
# 参数摘要测试
# ──────────────────────────────────────────────

class TestSummarizeArgs:
  def test_bash(self):
    result = _summarize_args("bash", {"command": "find . -name '*.py'"})
    assert result == "find . -name '*.py'"

  def test_bash_truncation(self):
    result = _summarize_args("bash", {"command": "x" * 200})
    assert len(result) <= 80

  def test_read_file(self):
    result = _summarize_args("read_file", {"path": "src/main.py"})
    assert result == "src/main.py"

  def test_read_file_with_limit(self):
    result = _summarize_args("read_file", {"path": "a.py", "limit": 10})
    assert "a.py" in result
    assert "limit=10" in result

  def test_git(self):
    result = _summarize_args("git", {"command": "log --oneline"})
    assert result == "log --oneline"

  def test_unknown_tool(self):
    result = _summarize_args("custom", {"x": 1})
    assert "custom" in result or "x" in result


# ──────────────────────────────────────────────
# 摘要生成测试
# ──────────────────────────────────────────────

class TestBuildSummary:
  def test_basic_summary(self):
    ctx = SubagentContext(messages=[], tools=[], handlers={}, max_turns=10)
    ctx._turn_count = 3
    ctx.record_tool_call("bash", {"command": "ls"}, "file1.py\nfile2.py")
    ctx.record_tool_call("read_file", {"path": "file1.py"}, "# content")

    summary = _build_summary_from_context(ctx, "找到了两个 Python 文件")
    assert "子代理执行摘要" in summary
    assert "找到了两个 Python 文件" in summary
    assert "工具调用 (2 次)" in summary
    assert "bash" in summary
    assert "read_file" in summary
    assert "3/10" in summary

  def test_exhausted_summary(self):
    ctx = SubagentContext(messages=[], tools=[], handlers={}, max_turns=2)
    ctx._turn_count = 2

    summary = _build_summary_from_context(ctx, "部分完成")
    assert "达到最大轮次限制" in summary

  def test_no_tool_calls(self):
    ctx = SubagentContext(messages=[], tools=[], handlers={})
    summary = _build_summary_from_context(ctx, "简单回答")
    assert "简单回答" in summary
    assert "工具调用" not in summary

  def test_empty_final_text(self):
    ctx = SubagentContext(messages=[], tools=[], handlers={})
    summary = _build_summary_from_context(ctx, "")
    assert "子代理执行摘要" in summary


# ──────────────────────────────────────────────
# 消息裁剪测试
# ──────────────────────────────────────────────

class TestTrimMessages:
  def test_short_messages_unchanged(self):
    msgs = [
      {"role": "system", "content": "sys"},
      {"role": "user", "content": "hello"},
      {"role": "assistant", "content": "hi"},
    ]
    result = _trim_subagent_messages(msgs, keep_turns=4)
    assert len(result) >= 3  # 至少保留原始消息

  def test_long_messages_trimmed(self):
    msgs = [
      {"role": "system", "content": "sys"},
    ]
    # 创建 10 轮对话
    for i in range(10):
      msgs.append({"role": "user", "content": f"turn {i}"})
      msgs.append({"role": "assistant", "content": f"reply {i}"})

    result = _trim_subagent_messages(msgs, keep_turns=3)
    # system + 裁剪提示(2条) + 最近3轮(6条) = 9
    assert result[0]["role"] == "system"
    assert len(result) < len(msgs)
    # 应该包含裁剪提示
    has_trim_notice = any(
      "已裁剪" in m.get("content", "") for m in result
    )
    assert has_trim_notice

  def test_system_always_preserved(self):
    msgs = [
      {"role": "system", "content": "important system prompt"},
    ]
    for i in range(20):
      msgs.append({"role": "user", "content": f"u{i}"})
      msgs.append({"role": "assistant", "content": f"a{i}"})

    result = _trim_subagent_messages(msgs, keep_turns=2)
    assert result[0] == {"role": "system", "content": "important system prompt"}


# ──────────────────────────────────────────────
# run_subagent 端到端测试（mock LLM）
# ──────────────────────────────────────────────

def _make_mock_response(content="", tool_calls=None):
  """构造模拟的 LLM 响应"""
  msg = MagicMock()
  msg.content = content
  msg.tool_calls = tool_calls

  choice = MagicMock()
  choice.message = msg

  resp = MagicMock()
  resp.choices = [choice]
  return resp


def _make_mock_tool_call(name, arguments, call_id=None):
  """构造模拟的 tool_call"""
  tc = MagicMock()
  tc.id = call_id or f"call_{name}"
  tc.function.name = name
  tc.function.arguments = json.dumps(arguments)
  return tc


class TestRunSubagent:
  def _make_handlers(self, *names):
    """创建 mock handler 映射"""
    return {name: MagicMock(return_value="mock output") for name in names}

  def test_simple_text_response(self):
    """子代理直接返回文本，无需工具调用"""
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _make_mock_response(
      content="项目包含 3 个 Python 文件"
    )

    result = run_subagent(
      task="列出项目中的 Python 文件",
      client=mock_client,
      model="test-model",
      full_tool_defs=[
        {"type": "function", "function": {"name": "bash", "parameters": {}}},
      ],
      all_handlers=self._make_handlers("bash"),
    )

    assert "项目包含 3 个 Python 文件" in result
    assert "子代理执行摘要" in result

  def test_tool_call_then_text(self):
    """子代理先调用工具，再返回文本"""
    tc = _make_mock_tool_call("bash", {"command": "ls *.py"})
    mock_handler = MagicMock(return_value="main.py\nutils.py")

    # 第一次调用：返回工具调用
    # 第二次调用：返回文本
    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = [
      _make_mock_response(content="", tool_calls=[tc]),
      _make_mock_response(content="找到了 main.py 和 utils.py"),
    ]

    result = run_subagent(
      task="查找 Python 文件",
      client=mock_client,
      model="test-model",
      full_tool_defs=[
        {"type": "function", "function": {"name": "bash", "parameters": {}}},
      ],
      all_handlers={"bash": mock_handler},
    )

    assert "找到了 main.py 和 utils.py" in result
    assert "工具调用 (1 次)" in result

  def test_max_turns_enforcement(self):
    """子代理达到最大轮次后停止"""
    tc = _make_mock_tool_call("bash", {"command": "ls"})
    mock_handler = MagicMock(return_value="output")

    # 每次都返回工具调用，模拟失控
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _make_mock_response(
      content="思考中...", tool_calls=[tc]
    )

    result = run_subagent(
      task="无限循环测试",
      client=mock_client,
      model="test-model",
      max_turns=3,
      full_tool_defs=[
        {"type": "function", "function": {"name": "bash", "parameters": {}}},
      ],
      all_handlers={"bash": mock_handler},
    )

    assert "达到最大轮次限制" in result
    assert "3/3" in result

  def test_forbidden_tools_excluded(self):
    """即使请求禁止的工具，也会被排除"""
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _make_mock_response(
      content="完成"
    )

    result = run_subagent(
      task="测试",
      client=mock_client,
      model="test-model",
      tools=["bash", "write_file", "subagent"],  # 请求包含禁止工具
      full_tool_defs=[
        {"type": "function", "function": {"name": "bash", "parameters": {}}},
        {"type": "function", "function": {"name": "write_file", "parameters": {}}},
        {"type": "function", "function": {"name": "subagent", "parameters": {}}},
      ],
      all_handlers={"bash": MagicMock(return_value="")},
    )

    # 验证传给 LLM 的工具定义不包含禁止工具
    call_args = mock_client.chat.completions.create.call_args
    tools_arg = call_args.kwargs.get("tools") or call_args[1].get("tools", [])
    tool_names = {t["function"]["name"] for t in tools_arg}
    assert "write_file" not in tool_names
    assert "subagent" not in tool_names
    assert "bash" in tool_names

  def test_no_tools_available(self):
    """所有工具都被禁止时返回错误"""
    result = run_subagent(
      task="测试",
      client=MagicMock(),
      model="test-model",
      tools=["write_file", "subagent"],  # 全部被禁止
    )
    assert "error" in result.lower() or "没有可用" in result

  def test_llm_failure(self):
    """LLM 请求失败时返回错误摘要"""
    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = Exception("API Error")

    result = run_subagent(
      task="测试",
      client=mock_client,
      model="test-model",
      all_handlers={"bash": MagicMock(return_value="")},
    )
    assert "失败" in result or "error" in result.lower()


# ──────────────────────────────────────────────
# run_subagent_tool 入口测试
# ──────────────────────────────────────────────

class TestRunSubagentTool:
  def test_tool_entry_with_string_tools(self):
    """逗号分隔的工具字符串正确解析"""
    mock_client = MagicMock()
    mock_handlers = {"bash": MagicMock(return_value="")}

    with patch("subagent.run_subagent") as mock_run:
      mock_run.return_value = "摘要"
      result = run_subagent_tool(
        task="测试",
        tools="bash, read_file",
        max_turns=5,
        client=mock_client,
        model="test",
        full_tool_defs=[],
        all_handlers=mock_handlers,
      )
      mock_run.assert_called_once_with(
        task="测试",
        client=mock_client,
        model="test",
        tools=["bash", "read_file"],
        max_turns=5,
        full_tool_defs=[],
        all_handlers=mock_handlers,
      )

  def test_tool_entry_default_tools(self):
    """不指定 tools 时使用默认值"""
    mock_client = MagicMock()
    with patch("subagent.run_subagent") as mock_run:
      mock_run.return_value = "摘要"
      run_subagent_tool(
        task="测试",
        client=mock_client,
        model="test",
        full_tool_defs=[],
      )
      call_kwargs = mock_run.call_args
      assert call_kwargs.kwargs.get("tools") is None or call_kwargs[1].get("tools") is None


if __name__ == "__main__":
  pytest.main([__file__, "-v"])
