import asyncio
import json
import logging

from langchain_core.messages import AIMessage

import app.services as services_module
from app.agent.agent import agent_factory, get_agent_stream_response
from tests.test_agent_middleware import _FakeToolModel


class _FakeSessionManager:
    """假会话管理器，避免测试依赖数据库"""

    def __init__(self):
        self.saved: list[tuple] = []

    async def get_history(self, session_id, user_id):
        return []

    async def add_message(self, session_id, user_id, user_message, assistant_message):
        self.saved.append((session_id, user_message, assistant_message))


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
