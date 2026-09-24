import datetime
from contextvars import ContextVar

from langchain_core.tools import tool

from app.core.background_init import init_manager
from app.core.logger_handler import logger
from app.db.db_config import AsyncSessionLocal
from app.rag.knowledge_search import (
    format_for_model,
    get_document_detail,
    parse_source_id,
    search_unified,
)
from app.services.review_service import review_service

current_user_id_var: ContextVar[str] = ContextVar('current_user_id', default=None)

def set_current_user_id(user_id: str):
    """设置当前用户ID到上下文"""
    current_user_id_var.set(user_id)

def get_current_user_id_from_context() -> str:
    """从上下文获取当前用户ID"""
    return current_user_id_var.get()

@tool(description="用于获取当前年月日时分的工具")
async def what_time_is_now() -> str:
    """获取当前年月日时分的工具"""
    return f"当前时间是：{datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}"

@tool(description=(
    "在用户的「全部知识」中做语义检索，同时覆盖笔记与知识库文档，返回最相关的资料片段。"
    "当用户的问题需要依据其个人资料回答时使用（如「我之前记过什么」「帮我查一下」）。"
    "参数 query 为检索词，建议用关键词而非整句话；top_k 为返回条数（默认5）。"
    "返回结果带 [1][2] 编号，回答时请在关键结论后标注对应编号。"
))
async def search_knowledge_tool(query: str, top_k: int = 5) -> str:
    """
    统一知识检索工具：同时检索笔记与知识库

    检索无果时返回明确提示，让模型知道该换关键词重试或如实告知，
    而不是拿到空串后自由发挥。
    """
    user_id = get_current_user_id_from_context()
    if not user_id:
        return "错误: 无法确定用户身份"

    try:
        items = await search_unified(user_id, query, top_k)
    except Exception as e:
        logger.error(f"统一知识检索失败: {e}")
        return f"检索时出错: {str(e)}"

    if not items:
        return "未找到相关内容"

    # 返回文本内嵌 source_id，供调用方解析回结构化来源（ContextVar 跨不过 LangGraph 子任务边界）
    return format_for_model(items)


@tool(description=(
    "根据 source_id 取回某条资料的完整内容。"
    "当检索到的片段信息不足、需要看全文上下文时使用（如片段被截断、结论缺少依据）。"
    "参数 source_id 取自检索结果中标注的 source_id 值，形如 note:xxx 或 kb:xxx。"
))
async def get_document_detail_tool(source_id: str) -> str:
    """
    二级检索工具：按 source_id 取资料全文

    与 search_knowledge_tool 构成两级检索——一级便宜覆盖广，二级按需取全文。
    """
    user_id = get_current_user_id_from_context()
    if not user_id:
        return "错误: 无法确定用户身份"

    if parse_source_id(source_id) is None:
        return f"无效的 source_id: {source_id}"

    content = await get_document_detail(source_id, user_id)
    if not content:
        return "未找到该资料，可能已被删除"

    return content


@tool(description="获取用户的笔记统计信息，包括笔记总数、各分类（工作/学习/生活/项目）的笔记数量。")
async def get_note_stats_tool() -> str:
    """笔记统计工具"""
    user_id = get_current_user_id_from_context()
    if not user_id:
        return "错误: 无法确定用户身份"
    async with AsyncSessionLocal() as db:
        try:
            stats = await init_manager.note_service.get_category_stats(db, user_id)
            lines = ["📊 笔记统计\n"]
            lines.append(f"总笔记数: {stats['total']}\n")
            lines.append("各分类:")
            for cat in stats['categories']:
                emoji = {'work': '💼', 'study': '📖', 'life': '🏠', 'project': '🚀'}.get(cat['category'], '📄')
                lines.append(f"  {emoji} {cat['category']}: {cat['count']} 篇")
            if stats['uncategorized'] > 0:
                lines.append(f"  📄 未分类: {stats['uncategorized']} 篇")
            return "\n".join(lines)
        except Exception as e:
            logger.error(f"获取笔记统计失败: {e}")
            return f"获取笔记统计时出错: {str(e)}"

@tool(description="获取今日待回顾的笔记列表。返回每篇笔记的标题、内容预览和回顾次数，帮助用户进行间隔重复复习。")
async def get_today_reviews_tool() -> str:
    """获取今日回顾列表工具"""
    user_id = get_current_user_id_from_context()
    if not user_id:
        return "错误: 无法确定用户身份"
    async with AsyncSessionLocal() as db:
        try:
            reviews = await review_service.get_today_reviews(db, user_id)
            if not reviews:
                return "今日没有待回顾的笔记，继续保持！"
            lines = [f"📅 今日待回顾笔记（共 {len(reviews)} 篇）\n"]
            for i, rv in enumerate(reviews, 1):
                lines.append(f"{i}. **{rv['title']}**")
                lines.append(f"   回顾次数: 第 {rv['review_count'] + 1} 次")
                lines.append(f"   内容预览: {rv['content_preview'][:100]}...\n")
            return "\n".join(lines)
        except Exception as e:
            logger.error(f"获取今日回顾失败: {e}")
            return f"获取今日回顾时出错: {str(e)}"

@tool(description="标记一篇笔记为已回顾。参数 note_id 为笔记ID。调用成功后笔记的下次回顾时间会自动按艾宾浩斯遗忘曲线延后。")
async def mark_reviewed_tool(note_id: str) -> str:
    """标记回顾完成工具"""
    user_id = get_current_user_id_from_context()
    if not user_id:
        return "错误: 无法确定用户身份"
    async with AsyncSessionLocal() as db:
        try:
            result = await review_service.mark_reviewed(db, note_id, user_id)
            if result["success"]:
                return f"✅ 已标记回顾完成！第 {result['review_count']} 次回顾，下次回顾间隔 {result['interval_days']} 天。"
            else:
                return f"标记失败: {result['message']}"
        except Exception as e:
            logger.error(f"标记回顾失败: {e}")
            return f"标记回顾时出错: {str(e)}"

@tool(description=(
    "创建一篇新笔记。参数 title 为笔记标题，content 为笔记内容"
    "（支持Markdown格式，可选，不传则只创建标题）。"
    "创建后会自动生成向量索引和智能标签。"
))
async def create_note_tool(title: str, content: str = "") -> str:
    """创建笔记工具"""
    user_id = get_current_user_id_from_context()
    if not user_id:
        return "错误: 无法确定用户身份"
    from app.schemas.models import NoteCreate
    async with AsyncSessionLocal() as db:
        try:
            payload = NoteCreate(title=title, content=content)
            note = await init_manager.note_service.create_note(db, user_id, payload)
            return f"✅ 笔记创建成功！\n- 标题: {note.title}\n- ID: {note.id}\n- 标签和分类正在后台生成中..."
        except Exception as e:
            logger.error(f"创建笔记失败: {e}")
            return f"创建笔记时出错: {str(e)}"

@tool(description="获取某篇笔记的关联推荐，包括语义相似的笔记和知识库文档。参数 note_id 为笔记ID，top_k 为返回数量（默认3）。")
async def get_related_notes_tool(note_id: str, top_k: int = 3) -> str:
    """关联笔记推荐工具"""
    user_id = get_current_user_id_from_context()
    if not user_id:
        return "错误: 无法确定用户身份"
    async with AsyncSessionLocal() as db:
        try:
            related = await init_manager.note_service.get_related_notes(db, note_id, user_id, top_k=top_k)
            if not related:
                return "未找到关联笔记或知识库文档"
            lines = [f"🔗 关联推荐（共 {len(related)} 项）\n"]
            for i, item in enumerate(related, 1):
                source_label = "📝 笔记" if item['source'] == 'note' else "📚 知识库"
                lines.append(f"{i}. {source_label} — {item['title']}")
                lines.append(f"   相似度: {item['similarity']}")
                lines.append(f"   预览: {item['content_preview'][:100]}...\n")
            return "\n".join(lines)
        except Exception as e:
            logger.error(f"获取关联推荐失败: {e}")
            return f"获取关联推荐时出错: {str(e)}"
