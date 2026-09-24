from langchain.agents.middleware import ToolCallLimitMiddleware

from app.agent.agent import AgentFactory, agent_factory


def test_dead_tool_not_registered():
    """get_user_info_tools 是死工具：模型手里没有 JWT，永远调不通，不应注册"""
    names = {tool.name for tool in AgentFactory._get_default_tools()}
    assert "get_user_info_tools" not in names


def test_required_tools_registered():
    """核心能力工具必须全部注册，避免改造时误删"""
    names = {tool.name for tool in AgentFactory._get_default_tools()}
    required = {
        "what_time_is_now",
        "search_knowledge_tool",
        "get_document_detail_tool",
        "get_note_stats_tool",
        "get_today_reviews_tool",
        "mark_reviewed_tool",
        "create_note_tool",
        "get_related_notes_tool",
    }
    assert required <= names


def test_legacy_note_only_search_removed():
    """search_notes_tool 已被 search_knowledge_tool 取代：两个相似工具并存会让模型选错"""
    names = {tool.name for tool in AgentFactory._get_default_tools()}
    assert "search_notes_tool" not in names


def test_agent_has_tool_call_limit_fallback():
    """LangGraph 无 max_iterations，必须挂载工具调用上限，防止检索无果时无限重试"""
    middleware = agent_factory._get_default_middleware()
    assert any(isinstance(item, ToolCallLimitMiddleware) for item in middleware)
