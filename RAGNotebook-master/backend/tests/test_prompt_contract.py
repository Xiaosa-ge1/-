from app.utils.prompt_loader import load_prompt


def test_prompt_requires_numbered_citation():
    """必须要求模型用 [编号] 标注来源，否则前端无法解析出来源卡片"""
    prompt = load_prompt("main_prompt")
    assert "[1]" in prompt


def test_prompt_allows_retry_with_new_keyword():
    """必须允许首次检索无果时换关键词重试，否则 Agent 无法自我纠正"""
    prompt = load_prompt("main_prompt")
    assert "换个关键词" in prompt or "换关键词" in prompt


def test_prompt_forbids_fabricating_when_no_result():
    """必须明确禁止在资料不足时编造，这是知识库产品的底线"""
    prompt = load_prompt("main_prompt")
    assert "不得编造" in prompt or "禁止编造" in prompt


def test_prompt_points_to_unified_search_tool():
    """必须指向统一检索工具，否则模型不知道该用哪个工具查资料"""
    prompt = load_prompt("main_prompt")
    assert "search_knowledge_tool" in prompt
    assert "search_notes_tool" not in prompt


def test_prompt_mentions_two_stage_retrieval():
    """必须告诉模型片段不足时能取全文，否则两级检索的第二级永远不会被用上"""
    prompt = load_prompt("main_prompt")
    assert "get_document_detail_tool" in prompt
