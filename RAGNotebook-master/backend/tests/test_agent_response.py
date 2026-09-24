import asyncio
import logging

from langchain_core.messages import AIMessage

from app.agent.agent import agent_factory, get_agent_response
from tests.test_agent_middleware import _FakeToolModel


def _patch_model(monkeypatch):
    monkeypatch.setattr(
        agent_factory,
        "_create_chat_model",
        lambda *a, **k: _FakeToolModel(messages=iter([AIMessage(content="ok")])),
    )


def test_get_agent_response_returns_text(monkeypatch):
    """非流式调用必须返回模型最终回答"""
    _patch_model(monkeypatch)
    result = asyncio.run(get_agent_response("你好"))
    assert result["response"] == "ok"
    assert isinstance(result["steps"], list)


def test_get_agent_response_accepts_history(monkeypatch):
    """必须支持传入会话历史，多轮对话依赖它"""
    _patch_model(monkeypatch)
    result = asyncio.run(get_agent_response("继续", history=[("你好", "你好呀")]))
    assert result["response"] == "ok"


def test_get_agent_response_goes_through_langgraph_middleware(monkeypatch, caplog):
    """非流式路径也必须走 LangGraph，否则中间件仍是死代码"""
    _patch_model(monkeypatch)
    with caplog.at_level(logging.INFO):
        asyncio.run(get_agent_response("你好"))
    assert "before_agent" in caplog.text
