from langchain_core.messages import AIMessage, ToolMessage

from app.rag.knowledge_search import (
    build_sources_event,
    build_suggestion_event,
    extract_sources_from_messages,
    format_for_model,
    has_created_note,
    parse_source_id,
    parse_sources_from_text,
)


def test_format_for_model_numbers_entries():
    """必须给每条编上 [n] 号，模型才能引用并在回答里标注 [1]"""
    items = [
        {"source_id": "note:n1", "source_type": "note", "title": "MySQL 索引", "snippet": "索引失效"},
        {"source_id": "kb:c1", "source_type": "knowledge_base", "title": "redis.pdf", "snippet": "缓存淘汰"},
    ]
    text = format_for_model(items)
    assert "[1]" in text
    assert "[2]" in text


def test_format_for_model_labels_source_type():
    """必须区分笔记与知识库，用户要能分辨结论出自哪里"""
    items = [
        {"source_id": "note:n1", "source_type": "note", "title": "MySQL 索引", "snippet": "索引失效"},
        {"source_id": "kb:c1", "source_type": "knowledge_base", "title": "redis.pdf", "snippet": "缓存淘汰"},
    ]
    text = format_for_model(items)
    assert "笔记" in text
    assert "知识库" in text


def test_format_for_model_includes_snippet():
    """必须带上内容片段，否则模型无法据以作答"""
    items = [{"source_id": "note:n1", "source_type": "note", "title": "T", "snippet": "索引失效常见于隐式类型转换"}]
    text = format_for_model(items)
    assert "索引失效常见于隐式类型转换" in text


def test_format_for_model_empty_returns_empty_string():
    """空结果返回空串，由调用方决定如何提示，纯函数不做兜底文案"""
    assert format_for_model([]) == ""


def test_parse_sources_roundtrip():
    """format_for_model 的输出必须能被 parse_sources_from_text 原样解析回来（契约一致）"""
    items = [
        {"source_id": "note:n1", "source_type": "note", "title": "MySQL 索引", "snippet": "索引失效"},
        {"source_id": "kb:c1", "source_type": "knowledge_base", "title": "redis.pdf", "snippet": "缓存淘汰"},
    ]
    parsed = parse_sources_from_text(format_for_model(items))
    assert [item["source_id"] for item in parsed] == ["note:n1", "kb:c1"]
    assert [item["index"] for item in parsed] == [1, 2]


def test_parse_sources_infers_type_from_id_prefix():
    """类型从 source_id 前缀推断，不依赖标题文案"""
    parsed = parse_sources_from_text(format_for_model([
        {"source_id": "kb:c9", "source_type": "knowledge_base", "title": "x.pdf", "snippet": "s"},
    ]))
    assert parsed[0]["source_type"] == "knowledge_base"


def test_parse_sources_ignores_plain_text():
    """普通回答文本不应被误解析出来源"""
    assert parse_sources_from_text("这是普通回答，没有任何来源标注") == []
    assert parse_sources_from_text("") == []


def test_parse_sources_handles_title_with_brackets():
    """标题里含书名号或括号时仍要能正确解析，不能被截断"""
    parsed = parse_sources_from_text(format_for_model([
        {"source_id": "note:n7", "source_type": "note", "title": "读《人类简史》有感", "snippet": "s"},
    ]))
    assert parsed[0]["title"] == "读《人类简史》有感"
    assert parsed[0]["source_id"] == "note:n7"


def test_parse_source_id_splits_type_and_id():
    """source_id 必须能拆出来源类型与文档 ID，二级检索依赖它"""
    assert parse_source_id("note:n_001") == ("note", "n_001")
    assert parse_source_id("kb:chunk_abc") == ("kb", "chunk_abc")


def test_parse_source_id_handles_id_with_colon():
    """文档 ID 自身含冒号时不能截断，只按第一个冒号切分"""
    assert parse_source_id("kb:a:b") == ("kb", "a:b")


def test_parse_source_id_rejects_invalid():
    """非法输入必须返回 None，让调用方走错误分支而不是崩溃"""
    assert parse_source_id("") is None
    assert parse_source_id("nocolon") is None
    assert parse_source_id("unknown:x") is None


def _tool_message(content: str, call_id: str = "c1") -> ToolMessage:
    return ToolMessage(content=content, tool_call_id=call_id)


def test_extract_sources_from_tool_messages():
    """来源必须从工具消息里提取，这是溯源链路的数据源"""
    messages = [
        _tool_message("[1] 笔记《MySQL 索引》 (source_id: note:n1)\n索引失效"),
        _tool_message("[1] 知识库《redis.pdf》 (source_id: kb:c1)\n缓存淘汰", call_id="c2"),
    ]
    sources = extract_sources_from_messages(messages)
    assert [item["source_id"] for item in sources] == ["note:n1", "kb:c1"]


def test_extract_sources_dedupes():
    """多次检索可能命中同一条资料，来源列表必须去重"""
    same = "[1] 笔记《MySQL 索引》 (source_id: note:n1)\n索引失效"
    sources = extract_sources_from_messages([
        _tool_message(same, "c1"),
        _tool_message(same, "c2"),
    ])
    assert len(sources) == 1


def test_extract_sources_ignores_model_written_text():
    """模型自己编的 [1] 不能被当成来源，否则溯源会指向不存在的东西"""
    messages = [AIMessage(content="[1] 笔记《我编的》 (source_id: note:fake)")]
    assert extract_sources_from_messages(messages) == []


def test_extract_sources_handles_empty_messages():
    assert extract_sources_from_messages([]) == []


def test_build_sources_event_returns_none_without_sources():
    """没有来源时不应发 sources 事件，避免前端渲染空卡片"""
    assert build_sources_event([AIMessage(content="普通回答")], "s1") is None


def test_build_sources_event_carries_items_and_session():
    """sources 事件必须带会话 ID 与结构化条目，前端据此渲染来源卡片"""
    messages = [_tool_message("[1] 笔记《MySQL 索引》 (source_id: note:n1)\n索引失效")]
    event = build_sources_event(messages, "s1")
    assert event["type"] == "sources"
    assert event["session_id"] == "s1"
    assert event["items"][0]["source_id"] == "note:n1"
    assert event["items"][0]["title"] == "MySQL 索引"


def test_suggestion_offered_for_substantive_answer():
    """有实质内容的回答才建议沉淀，让对话能变成知识"""
    event = build_suggestion_event("怎么设计索引", "内容" * 100, False, "s1")
    assert event["type"] == "suggestion"
    assert event["action"] == "create_note"
    assert event["title"] == "怎么设计索引"


def test_suggestion_skipped_when_note_already_created():
    """本轮已经存过笔记就别再建议，重复打扰比不提示更糟"""
    assert build_suggestion_event("问题", "内容" * 100, True, "s1") is None


def test_suggestion_skipped_for_short_answer():
    """寒暄、短问答不该弹「存为笔记」，否则处处是骚扰"""
    assert build_suggestion_event("你好", "你好呀", False, "s1") is None


def test_suggestion_truncates_long_title():
    """标题过长要截断，前端卡片放不下"""
    event = build_suggestion_event("问" * 80, "内容" * 100, False, "s1")
    assert len(event["title"]) <= 40


def _ai_message_with_tool_call(name: str):
    from langchain_core.messages import AIMessage

    message = AIMessage(content="")
    message.tool_calls = [{"name": name, "args": {}, "id": "c1"}]
    return message


def test_detects_note_creation():
    """本轮创建过笔记就应识别出来，避免重复建议沉淀"""
    assert has_created_note([_ai_message_with_tool_call("create_note_tool")]) is True


def test_no_note_creation_for_other_tools():
    """调用别的工具不该被当成已沉淀"""
    assert has_created_note([_ai_message_with_tool_call("search_knowledge_tool")]) is False
    assert has_created_note([]) is False
