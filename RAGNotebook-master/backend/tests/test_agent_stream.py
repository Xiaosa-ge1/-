import asyncio
import json
import logging

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage

import app.services as services_module
from app.agent.agent import agent_factory, get_agent_stream_response
from tests.test_agent_middleware import _FakeToolModel


class _FakeSessionManager:
    """假会话管理器，避免测试依赖数据库"""

    def __init__(self):
        self.saved: list[tuple] = []

    async def get_history(self, session_id, user_id):
        return []

    async def add_message(self, session_id, user_id, user_message, assistant_message, sources=None):
        self.saved.append((session_id, user_message, assistant_message, sources))


class _FakeAgentWithSources:
    """假 Agent：模拟「先调工具拿到资料，再产出回答」的消息序列"""

    async def astream(self, inputs, stream_mode=None):
        tool_message = ToolMessage(
            content="[1] 笔记《MySQL 索引》 (source_id: note:n1)\n索引失效",
            tool_call_id="c1",
        )
        yield ("values", {"messages": [HumanMessage(content="hi")]})
        yield ("messages", (AIMessageChunk(content="ok"), {"langgraph_node": "model"}))
        # values 是累积语义，最后一次才是完整序列
        yield (
            "values",
            {"messages": [HumanMessage(content="hi"), tool_message, AIMessage(content="ok")]},
        )


def _patch(monkeypatch) -> _FakeSessionManager:
    monkeypatch.setattr(
        agent_factory,
        "_create_chat_model",
        lambda *a, **k: _FakeToolModel(messages=iter([AIMessage(content="ok")])),
    )
    fake_manager = _FakeSessionManager()
    # session_manager 是只读代理，实际返回 services 模块下的全局实例，故替换该模块级全局名
    monkeypatch.setattr(services_module, "database_session_manager", fake_manager)
    return fake_manager


def _collect(generator):
    async def run():
        return [chunk async for chunk in generator]

    return asyncio.run(run())


def _events(chunks) -> list[dict]:
    result = []
    for chunk in chunks:
        line = chunk.strip()
        if line.startswith("data: "):
            result.append(json.loads(line[6:]))
    return result


def test_stream_emits_response_then_done(monkeypatch):
    """SSE 契约：response 事件累积出完整回答，最后必须以 done 结束"""
    _patch(monkeypatch)
    events = _events(_collect(get_agent_stream_response("你好", "s1", "u1")))
    types = [event["type"] for event in events]
    assert "response" in types
    assert types[-1] == "done"
    text = "".join(event.get("content", "") for event in events if event["type"] == "response")
    assert text == "ok"


def test_stream_persists_history(monkeypatch):
    """回答必须写回会话历史，否则刷新页面就丢了"""
    fake_manager = _patch(monkeypatch)
    _collect(get_agent_stream_response("你好", "s1", "u1"))
    assert fake_manager.saved
    assert fake_manager.saved[0][2] == "ok"


def test_stream_goes_through_langgraph_middleware(monkeypatch, caplog):
    """流式是主链路，必须走 LangGraph，中间件才会生效"""
    _patch(monkeypatch)
    with caplog.at_level(logging.INFO):
        _collect(get_agent_stream_response("你好", "s1", "u1"))
    assert "before_agent" in caplog.text


def _patch_with_sources(monkeypatch) -> _FakeSessionManager:
    fake_manager = _FakeSessionManager()
    monkeypatch.setattr(services_module, "database_session_manager", fake_manager)
    monkeypatch.setattr(agent_factory, "create_agent", lambda **kwargs: _FakeAgentWithSources())
    return fake_manager


def test_stream_emits_sources_event_before_done(monkeypatch):
    """来源必须以独立事件在 done 之前推送，前端才能渲染来源卡片"""
    _patch_with_sources(monkeypatch)
    events = _events(_collect(get_agent_stream_response("你好", "s1", "u1")))
    types = [event["type"] for event in events]
    assert "sources" in types
    assert types.index("sources") < types.index("done")


def test_stream_sources_event_carries_structured_items(monkeypatch):
    """sources 事件必须带结构化条目（source_id/title），前端才能做成可点卡片"""
    _patch_with_sources(monkeypatch)
    events = _events(_collect(get_agent_stream_response("你好", "s1", "u1")))
    sources_event = next(event for event in events if event["type"] == "sources")
    assert sources_event["items"][0]["source_id"] == "note:n1"
    assert sources_event["items"][0]["title"] == "MySQL 索引"


def test_stream_persists_sources_with_message(monkeypatch):
    """来源必须随消息一起落库，否则刷新页面后来源卡片就没了"""
    fake_manager = _patch_with_sources(monkeypatch)
    _collect(get_agent_stream_response("你好", "s1", "u1"))
    saved_sources = fake_manager.saved[0][3]
    assert saved_sources
    assert saved_sources[0]["source_id"] == "note:n1"


class _FakeAgentLongAnswer:
    """假 Agent：产出一段足够长的回答，用于验证沉淀建议的触发门槛"""

    async def astream(self, inputs, stream_mode=None):
        text = "内容" * 60
        yield ("values", {"messages": [HumanMessage(content="hi")]})
        yield ("messages", (AIMessageChunk(content=text), {"langgraph_node": "model"}))
        yield ("values", {"messages": [HumanMessage(content="hi"), AIMessage(content=text)]})


def test_stream_offers_suggestion_for_substantive_answer(monkeypatch):
    """有实质内容的回答应给出「存为笔记」建议，聊完的知识要能沉淀下来"""
    monkeypatch.setattr(services_module, "database_session_manager", _FakeSessionManager())
    monkeypatch.setattr(agent_factory, "create_agent", lambda **kwargs: _FakeAgentLongAnswer())
    events = _events(_collect(get_agent_stream_response("怎么设计索引", "s1", "u1")))
    types = [event["type"] for event in events]
    assert "suggestion" in types
    assert types.index("suggestion") < types.index("done")


def test_stream_skips_suggestion_for_short_answer(monkeypatch):
    """寒暄式短回答不该弹存笔记建议，否则处处是骚扰"""
    fake_manager = _FakeSessionManager()
    monkeypatch.setattr(services_module, "database_session_manager", fake_manager)
    monkeypatch.setattr(agent_factory, "_create_chat_model", lambda *a, **k: _FakeToolModel(messages=iter([AIMessage(content="ok")])))
    events = _events(_collect(get_agent_stream_response("你好", "s1", "u1")))
    assert "suggestion" not in [event["type"] for event in events]


def test_stream_skips_sources_event_when_no_retrieval(monkeypatch):
    """没检索到资料时不应发 sources 事件，避免前端渲染空卡片"""
    fake_manager = _FakeSessionManager()
    monkeypatch.setattr(services_module, "database_session_manager", fake_manager)
    monkeypatch.setattr(agent_factory, "_create_chat_model", lambda *a, **k: _FakeToolModel(messages=iter([AIMessage(content="ok")])))
    events = _events(_collect(get_agent_stream_response("你好", "s1", "u1")))
    assert "sources" not in [event["type"] for event in events]
    assert fake_manager.saved[0][3] is None
