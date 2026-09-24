import asyncio
import logging

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage

from app.agent.agent import agent_factory


class _FakeToolModel(GenericFakeChatModel):
    """支持 bind_tools 的假模型：create_agent 会绑定工具，原生 fake 未实现该方法"""

    def bind_tools(self, tools, **kwargs):
        return self


def _fake_model():
    """构造假模型，避免测试依赖真实 LLM 调用"""
    return _FakeToolModel(messages=iter([AIMessage(content="ok")]))


def test_agent_is_langgraph_graph(monkeypatch):
    """Agent 必须是 LangGraph 图，AgentExecutor 不消费中间件"""
    monkeypatch.setattr(agent_factory, "_create_chat_model", lambda *a, **k: _fake_model())
    agent = agent_factory.create_agent()
    assert type(agent).__name__ == "CompiledStateGraph"


def test_middleware_hooks_are_actually_invoked(monkeypatch, caplog):
    """中间件钩子必须真实触发，证明从死代码变为生效（生产链路是异步的）"""
    monkeypatch.setattr(agent_factory, "_create_chat_model", lambda *a, **k: _fake_model())
    agent = agent_factory.create_agent()
    with caplog.at_level(logging.INFO):
        asyncio.run(agent.ainvoke({"messages": [HumanMessage("hi")]}))
    assert "before_agent" in caplog.text
    assert "after_agent" in caplog.text


def test_agent_accepts_custom_system_prompt(monkeypatch):
    """system_prompt 必须支持自定义，RAG 上下文注入依赖它"""
    monkeypatch.setattr(agent_factory, "_create_chat_model", lambda *a, **k: _fake_model())
    agent = agent_factory.create_agent(custom_system_prompt="只回答资料里的内容")
    result = asyncio.run(agent.ainvoke({"messages": [HumanMessage("hi")]}))
    assert result["messages"]


def test_agent_returns_messages_with_final_answer(monkeypatch):
    """调用后 messages 末尾必须是模型的最终回答"""
    monkeypatch.setattr(agent_factory, "_create_chat_model", lambda *a, **k: _fake_model())
    agent = agent_factory.create_agent()
    result = asyncio.run(agent.ainvoke({"messages": [HumanMessage("hi")]}))
    assert result["messages"][-1].content == "ok"
