from app.rag.knowledge_search import (
    format_for_model,
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
