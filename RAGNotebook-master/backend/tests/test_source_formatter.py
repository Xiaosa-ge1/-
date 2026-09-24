from app.rag.source_formatter import build_source_id, build_source_item


def test_source_id_stable_across_content_change():
    """笔记内容修订后，只要 note_id 不变，source_id 必须保持不变（不依赖内容 hash）"""
    before = {"source_type": "note", "note_id": "n_001", "title": "红黑树"}
    after = {"source_type": "note", "note_id": "n_001", "title": "红黑树（修订版）"}
    assert build_source_id(before) == build_source_id(after)


def test_source_id_distinguishes_note_and_kb():
    """笔记与知识库文档 ID 空间可能冲突，必须带类型前缀"""
    note_meta = {"source_type": "note", "note_id": "same_id"}
    kb_meta = {"source_type": "knowledge_base", "source": "doc.pdf"}
    assert build_source_id(note_meta) != build_source_id(kb_meta, chroma_id="same_id")


def test_build_source_item_has_required_fields():
    """结构化条目必须含溯源所需的全部字段"""
    item = build_source_item(
        page_content="索引失效的常见原因",
        metadata={"source_type": "note", "note_id": "n_001", "title": "MySQL 索引"},
        chroma_id="n_001",
    )
    assert {"source_id", "source_type", "title", "snippet"} <= set(item)
    assert item["source_id"] == "note:n_001"
    assert item["source_type"] == "note"
