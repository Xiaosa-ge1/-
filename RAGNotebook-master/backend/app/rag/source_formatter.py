"""
溯源来源格式化模块

将检索到的文档转换为「结构化来源条目」，供 Agent 工具返回、SSE 推送与前端渲染共用。

设计要点：
1. source_id 必须稳定 —— 只用 doc_id 构造，不用内容 hash，
   笔记内容修订后重切 chunk 也不会失效。
2. 笔记与知识库的 ID 空间可能冲突，必须带类型前缀区分。
3. 笔记是整篇存储（不切块），知识库才切块，因此两者取 ID 的方式不同。
"""

SNIPPET_MAX_LENGTH = 200

NOTE_PREFIX = "note"
KB_PREFIX = "kb"


def normalize_distances(distances: list[float]) -> list[float]:
    """
    把「距离」归一化为「相似度」

    向量检索返回的是距离（越小越相似），BM25 返回的是分数（越大越好），
    两者量纲不同，直接合并排序没有意义。这里统一转成 0-1 的相似度。

    :param distances: 距离列表（越小越相似）
    :return: 相似度列表（越大越相似，落在 0-1）
    """
    if not distances:
        return []
    if len(distances) == 1:
        return [1.0]

    d_min, d_max = min(distances), max(distances)
    # 全部相等时无法归一化，统一给满分，避免除零
    if d_max == d_min:
        return [1.0] * len(distances)

    return [1.0 - (d - d_min) / (d_max - d_min) for d in distances]


def merge_sources(note_items: list[dict], kb_items: list[dict]) -> list[dict]:
    """
    合并笔记与知识库两源结果

    两源的原始分数量纲不同，必须各自归一化后再合并排序，
    否则笔记的距离和知识库的 BM25 分数直接比大小是错的。

    :param note_items: 笔记结果（含 score，语义为距离）
    :param kb_items: 知识库结果（含 score，语义为距离）
    :return: 合并后按相似度降序的条目列表
    """
    note_scores = normalize_distances([it.get("score", 0.0) for it in note_items])
    kb_scores = normalize_distances([it.get("score", 0.0) for it in kb_items])

    merged: list[dict] = []
    for item, score in zip(note_items, note_scores):
        merged.append({**item, "score": score})
    for item, score in zip(kb_items, kb_scores):
        merged.append({**item, "score": score})

    return sorted(merged, key=lambda x: x["score"], reverse=True)


def build_source_id(metadata: dict, chroma_id: str = "") -> str:
    """
    构造稳定的来源 ID

    :param metadata: 文档 metadata
    :param chroma_id: ChromaDB 中的文档 ID（知识库切块时使用）
    :return: 形如 "note:n_001" / "kb:chunk_abc" 的稳定 ID
    """
    if not metadata:
        metadata = {}

    # 笔记：优先用 note_id（写入时即有，且整篇存储不切块）
    note_id = metadata.get("note_id")
    if note_id:
        return f"{NOTE_PREFIX}:{note_id}"

    # 知识库：用 Chroma 的 chunk id 兜底回退到 source
    doc_id = chroma_id or metadata.get("source") or "unknown"
    return f"{KB_PREFIX}:{doc_id}"


def build_source_item(page_content: str, metadata: dict, chroma_id: str = "") -> dict:
    """
    构造结构化来源条目

    :param page_content: 文档正文
    :param metadata: 文档 metadata
    :param chroma_id: ChromaDB 中的文档 ID
    :return: 含 source_id / source_type / title / snippet 的字典
    """
    if not metadata:
        metadata = {}

    source_type = metadata.get("source_type")
    if not source_type:
        source_type = "note" if metadata.get("note_id") else "knowledge_base"

    if source_type == "note":
        title = metadata.get("title") or "无标题"
    else:
        title = metadata.get("original_filename") or metadata.get("source") or "知识库文档"

    content = page_content or ""
    snippet = content[:SNIPPET_MAX_LENGTH]
    if len(content) > SNIPPET_MAX_LENGTH:
        snippet += "..."

    return {
        "source_id": build_source_id(metadata, chroma_id),
        "source_type": source_type,
        "title": title,
        "snippet": snippet,
    }
