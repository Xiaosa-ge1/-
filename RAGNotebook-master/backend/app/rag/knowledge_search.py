"""
统一知识检索模块

把「笔记」与「知识库文档」两个来源的检索结果合并为统一的结构化条目。
用户心里只有一个「我的知识」，不该感知到系统内部有两个仓库。

对外提供：
- search_unified：双源检索 + 合并排序，返回结构化条目（含 source_id）
- format_for_model：把条目渲染成带 [n] 编号的文本，供模型引用
"""

import asyncio
import re

from langchain_core.messages import ToolMessage

from app.core.logger_handler import logger
from app.rag.source_formatter import build_source_item, merge_sources

SOURCE_TYPE_LABELS = {
    "note": "笔记",
    "knowledge_base": "知识库",
}

# 沉淀建议的触发门槛：回答太短通常是寒暄或简单问答，不值得存成笔记
SUGGESTION_MIN_RESPONSE_LENGTH = 120
SUGGESTION_TITLE_MAX_LENGTH = 40
SUGGESTION_PREVIEW_MAX_LENGTH = 200

# 解析工具返回文本中的来源行。
# 标题用贪婪匹配 + 尾部 (source_id: ...) 锚定，才能兼容标题里含《》的情况。
SOURCE_LINE_PATTERN = re.compile(
    r"^\[(\d+)\]\s+(?:笔记|知识库|资料)《(.+)》\s*\(source_id:\s*([^)]+)\)",
    re.MULTILINE,
)


def extract_sources_from_messages(messages: list) -> list[dict]:
    """
    从消息序列中提取本次回答实际参考到的来源

    只认工具消息（ToolMessage）：模型自己写在回答里的 [1] 属于幻觉，
    若一并采信，溯源会指向不存在的资料。

    多次检索可能命中同一条，按 source_id 去重并保留首次出现的顺序。

    :param messages: LangGraph 完整消息序列
    :return: 去重后的结构化来源列表
    """
    sources: list[dict] = []
    seen: set[str] = set()

    for message in messages or []:
        if not isinstance(message, ToolMessage):
            continue
        for item in parse_sources_from_text(message.content):
            source_id = item["source_id"]
            if source_id in seen:
                continue
            seen.add(source_id)
            sources.append(item)

    return sources


def build_sources_event(messages: list, session_id: str) -> dict | None:
    """
    构造 sources 的 SSE 事件

    无来源时返回 None（前端不必渲染空卡片）。

    :param messages: LangGraph 完整消息序列
    :param session_id: 会话 ID
    :return: SSE 事件字典；无来源返回 None
    """
    sources = extract_sources_from_messages(messages)
    if not sources:
        return None

    return {
        "type": "sources",
        "items": sources,
        "session_id": session_id,
    }


def has_created_note(messages: list) -> bool:
    """
    判断本轮对话是否已经创建过笔记

    用于避免重复弹出「存为笔记」建议——已经沉淀过的内容不该再问一次。

    :param messages: LangGraph 完整消息序列
    :return: 是否调用过 create_note_tool
    """
    for message in messages or []:
        tool_calls = getattr(message, "tool_calls", None)
        if not tool_calls:
            continue
        for call in tool_calls:
            if call.get("name") == "create_note_tool":
                return True
    return False


def build_suggestion_event(
        query: str,
        response_text: str,
        note_created: bool,
        session_id: str,
) -> dict | None:
    """
    构造「把这次对话存成笔记」的建议事件

    克制触发，避免变成骚扰：
    - 本轮已经创建过笔记 → 不再建议（已经沉淀过了）
    - 回答过短（寒暄、一两句问答）→ 不值得沉淀

    :param query: 用户本轮提问，用作建议标题
    :param response_text: 助手回答，用于判断是否值得沉淀
    :param note_created: 本轮是否已经创建过笔记
    :param session_id: 会话 ID
    :return: SSE 事件字典；不该建议时返回 None
    """
    if note_created:
        return None
    if len(response_text or "") < SUGGESTION_MIN_RESPONSE_LENGTH:
        return None

    title = (query or "新的对话")[:SUGGESTION_TITLE_MAX_LENGTH]
    return {
        "type": "suggestion",
        "action": "create_note",
        "title": title,
        "content_preview": response_text[:SUGGESTION_PREVIEW_MAX_LENGTH],
        "session_id": session_id,
    }


def parse_source_id(source_id: str) -> tuple[str, str] | None:
    """
    拆解 source_id 为（来源类型, 文档 ID）

    只按第一个冒号切分，因为文档 ID 自身可能含冒号。

    :param source_id: 形如 "note:n_001" / "kb:chunk_abc"
    :return: (source_type, doc_id)；非法输入返回 None
    """
    if not source_id or ":" not in source_id:
        return None

    prefix, doc_id = source_id.split(":", 1)
    if prefix not in ("note", "kb") or not doc_id:
        return None

    return prefix, doc_id


def format_for_model(items: list[dict]) -> str:
    """
    把结构化条目渲染成模型可读的文本

    带 [n] 编号是为了让模型在回答里标注引用（如「索引失效[1]」），
    编号从 1 开始，与 items 顺序一一对应，便于后端反查来源。

    :param items: build_source_item 产出的结构化条目
    :return: 带编号的文本；空列表返回空串
    """
    if not items:
        return ""

    blocks = []
    for index, item in enumerate(items, 1):
        label = SOURCE_TYPE_LABELS.get(item.get("source_type", ""), "资料")
        title = item.get("title") or "无标题"
        snippet = item.get("snippet") or ""
        source_id = item.get("source_id") or ""
        # 内嵌 source_id：模型读它无碍，后端据此把回答里的 [n] 反查回具体来源。
        # 不用 ContextVar 传递，因为它无法跨越 LangGraph 的子任务边界。
        blocks.append(f"[{index}] {label}《{title}》 (source_id: {source_id})\n{snippet}")

    return "\n\n".join(blocks)


def parse_sources_from_text(text: str) -> list[dict]:
    """
    从工具返回文本中解析出结构化来源

    与 format_for_model 互为逆操作，保证渲染与解析的契约一致。

    :param text: 工具返回的文本（或完整消息内容）
    :return: [{index, source_id, source_type, title}, ...]
    """
    if not text:
        return []

    results = []
    for match in SOURCE_LINE_PATTERN.finditer(text):
        index, title, source_id = match.groups()
        source_id = source_id.strip()
        results.append({
            "index": int(index),
            "source_id": source_id,
            # 类型从 ID 前缀推断，比依赖标题文案可靠
            "source_type": "note" if source_id.startswith("note:") else "knowledge_base",
            "title": title,
        })
    return results


async def _search_notes(user_id: str, query: str, top_k: int) -> list[dict]:
    """检索笔记库，返回结构化条目（笔记整篇存储，不切块）"""
    from app.core.background_init import init_manager

    notes_store = init_manager.note_service.notes_store

    docs = await asyncio.to_thread(
        notes_store.similarity_search_with_score,
        query,
        k=top_k,
        filter={"$and": [{"user_id": user_id}, {"doc_type": "note"}]},
    )
    return [
        build_source_item(doc.page_content, doc.metadata or {}, chroma_id="")
        | {"score": score}
        for doc, score in docs
    ]


async def _search_knowledge_base(user_id: str, query: str, top_k: int) -> list[dict]:
    """检索知识库集合，返回结构化条目"""
    from app.rag.vector_store import VectorStoreService

    vector_store = VectorStoreService()
    docs = await asyncio.to_thread(
        vector_store.vectors_store.similarity_search_with_score,
        query,
        k=top_k,
        filter={"user_id": user_id},
    )
    return [
        build_source_item(doc.page_content, doc.metadata or {}, chroma_id=doc.metadata.get("chunk_id", ""))
        | {"score": score}
        for doc, score in docs
    ]


async def _get_note_content(note_id: str, user_id: str) -> str | None:
    """取笔记全文（笔记整篇存储，无需拼接切片）"""
    from app.core.background_init import init_manager
    from app.db.db_config import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        note = await init_manager.note_service.get_note(db, note_id, user_id)
    if not note:
        return None
    return f"笔记《{note.title}》\n\n{note.content}"


async def _get_chunk_content(chunk_id: str) -> str | None:
    """取知识库切片的完整内容"""
    from app.rag.vector_store import VectorStoreService

    vector_store = VectorStoreService()
    result = await asyncio.to_thread(vector_store.vectors_store.get, ids=[chunk_id])
    documents = result.get("documents") or []
    if not documents:
        return None
    return documents[0]


async def get_document_detail(source_id: str, user_id: str) -> str | None:
    """
    按 source_id 取回资料全文（两级检索的第二级）

    一级检索只返回片段，命中但需要更多上下文时，模型可用此接口深挖全文。

    :param source_id: 形如 "note:n_001" / "kb:chunk_abc"
    :param user_id: 用户 ID（笔记查询需要做归属校验）
    :return: 资料正文；不存在或无权访问返回 None
    """
    parsed = parse_source_id(source_id)
    if not parsed:
        return None

    source_type, doc_id = parsed
    try:
        if source_type == "note":
            return await _get_note_content(doc_id, user_id)
        return await _get_chunk_content(doc_id)
    except Exception as e:
        logger.error(f"【二级检索】取全文失败 source_id={source_id}: {e}")
        return None


async def search_unified(user_id: str, query: str, top_k: int = 5) -> list[dict]:
    """
    统一检索笔记与知识库

    两源分别检索后各自归一化再合并，避免不同量纲的分数直接比较。
    任一源检索失败不影响另一源，保证部分可用。

    :param user_id: 用户 ID（多租户隔离）
    :param query: 检索词
    :param top_k: 返回条数
    :return: 按相关度降序的结构化条目列表
    """
    note_items: list[dict] = []
    kb_items: list[dict] = []

    try:
        note_items = await _search_notes(user_id, query, top_k)
    except Exception as e:
        logger.error(f"【统一检索】笔记库检索失败: {e}")

    try:
        kb_items = await _search_knowledge_base(user_id, query, top_k)
    except Exception as e:
        logger.error(f"【统一检索】知识库检索失败: {e}")

    merged = merge_sources(note_items, kb_items)
    logger.info(
        f"【统一检索】查询「{query}」命中 笔记 {len(note_items)} 条 / 知识库 {len(kb_items)} 条，"
        f"合并后取前 {min(top_k, len(merged))} 条"
    )
    return merged[:top_k]
