import asyncio

from app.agent.agent_tools import (
    get_document_detail_tool,
    search_knowledge_tool,
    set_current_user_id,
)
from app.rag.knowledge_search import parse_sources_from_text

_FAKE_ITEMS = [
    {"source_id": "note:n1", "source_type": "note", "title": "MySQL 索引", "snippet": "索引失效", "score": 1.0},
    {"source_id": "kb:c1", "source_type": "knowledge_base", "title": "redis.pdf", "snippet": "缓存淘汰", "score": 0.8},
]


def _patch_search(monkeypatch, items=None):
    async def fake_search(user_id, query, top_k=5):
        return list(_FAKE_ITEMS if items is None else items)

    monkeypatch.setattr("app.agent.agent_tools.search_unified", fake_search)


async def _invoke(query: str):
    return await search_knowledge_tool.ainvoke({"query": query})


def test_tool_returns_numbered_text(monkeypatch):
    """工具返回必须带 [n] 编号，模型才能引用"""
    _patch_search(monkeypatch)
    set_current_user_id("u1")
    result = asyncio.run(_invoke("索引"))
    assert "[1]" in result
    assert "[2]" in result


def test_tool_output_is_parseable_for_citation(monkeypatch):
    """工具返回必须能被解析出来源，否则后端无法把回答里的 [1] 反查成 source_id"""
    _patch_search(monkeypatch)
    set_current_user_id("u1")
    result = asyncio.run(_invoke("索引"))
    parsed = parse_sources_from_text(result)
    assert [item["source_id"] for item in parsed] == ["note:n1", "kb:c1"]
    assert parsed[0]["title"] == "MySQL 索引"


def test_tool_reports_no_result(monkeypatch):
    """检索无果必须如实返回未找到，不能返回空串让模型自由发挥"""
    _patch_search(monkeypatch, items=[])
    set_current_user_id("u1")
    result = asyncio.run(_invoke("不存在的东西"))
    assert "未找到" in result


def test_tool_requires_user_identity(monkeypatch):
    """缺少用户身份必须拒绝，多租户隔离不能让检索跨用户"""
    _patch_search(monkeypatch)
    set_current_user_id(None)
    result = asyncio.run(_invoke("索引"))
    assert "用户身份" in result


async def _invoke_detail(source_id: str):
    return await get_document_detail_tool.ainvoke({"source_id": source_id})


def test_detail_tool_returns_content(monkeypatch):
    """二级检索必须能取回资料全文，供模型在片段不足时深挖"""

    async def fake_detail(source_id, user_id):
        return "索引失效的完整说明"

    monkeypatch.setattr("app.agent.agent_tools.get_document_detail", fake_detail)
    set_current_user_id("u1")
    result = asyncio.run(_invoke_detail("note:n1"))
    assert "索引失效的完整说明" in result


def test_detail_tool_rejects_invalid_source_id():
    """非法 source_id 必须给出明确错误，不能让模型拿到空值乱猜"""
    set_current_user_id("u1")
    result = asyncio.run(_invoke_detail("nocolon"))
    assert "无效" in result


def test_detail_tool_reports_missing_document(monkeypatch):
    """资料已删除时必须如实告知，不能假装取到内容"""

    async def fake_detail(source_id, user_id):
        return None

    monkeypatch.setattr("app.agent.agent_tools.get_document_detail", fake_detail)
    set_current_user_id("u1")
    result = asyncio.run(_invoke_detail("note:gone"))
    assert "未找到" in result
