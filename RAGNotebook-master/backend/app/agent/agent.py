import asyncio
import json
import os
from collections.abc import AsyncGenerator

from langchain.agents import create_agent as create_langgraph_agent
from langchain_community.chat_models import ChatTongyi
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool
from langchain_ollama import ChatOllama

from app.agent.agent_middleware import get_middleware
from app.agent.agent_tools import (
    create_note_tool,
    get_document_detail_tool,
    get_note_stats_tool,
    get_related_notes_tool,
    get_today_reviews_tool,
    mark_reviewed_tool,
    search_knowledge_tool,
    set_current_user_id,
    what_time_is_now,
)
from app.core.logger_handler import logger
from app.services import session_manager as sm
from app.utils.prompt_loader import load_prompt


class _NormalizedTongyi(ChatTongyi):
    """
    ChatTongyi wrapper：修复 Qwen3 流式 tool_calls arguments 格式问题。

    根因：Tongyi SDK 的 subtract_client_response 用字符串替换计算 arguments
    增量（如 '{"title": "量子"}'.replace('{"title":', '') → ': "量子"}'），
    产生非法 JSON 片段，check_response 报错。
    解决：重写 subtract_client_response，对 arguments 使用完整替换而非增量减法。
    """

    class Config:
        arbitrary_types_allowed = True

    def subtract_client_response(self, resp, prev_resp):
        """重写 delta 计算：arguments 使用完整值而非增量减法。"""
        import json as _json

        resp_copy = _json.loads(_json.dumps(resp))
        choice = resp_copy["output"]["choices"][0]
        message = choice["message"]

        prev_resp_copy = _json.loads(_json.dumps(prev_resp))
        prev_choice = prev_resp_copy["output"]["choices"][0]
        prev_message = prev_choice["message"]

        message["content"] = message["content"].replace(prev_message["content"], "")

        if message.get("tool_calls"):
            for index, tool_call in enumerate(message["tool_calls"]):
                function = tool_call["function"]
                if prev_message.get("tool_calls") and index < len(prev_message["tool_calls"]):
                    prev_function = prev_message["tool_calls"][index]["function"]
                    if "name" in function:
                        function["name"] = function["name"].replace(
                            prev_function["name"], ""
                        )
                    # 关键修复：arguments 不做增量减法，保留完整值
                    # 原始逻辑会产生非法 JSON 片段

        return resp_copy


class AgentFactory:
    """
    生产 Agent 工厂类
    支持：
    - 每次调用创建全新的 AgentExecutor 实例
    - 动态注入工具、提示词、模型配置
    - 支持异步流式调用
    """

    def __init__(
            self,
            model: str = "qwen3-max",
            api_key: str | None = None,
            default_tools: list[BaseTool] | None = None,
            default_middleware: list | None = None,
            default_system_prompt: str | None = None,
    ):
        """
        初始化工厂配置（仅配置，不创建实例）
        :param model: 默认模型名称
        :param api_key: 默认 API Key（不传则从env读取）
        :param default_tools: 默认工具列表
        :param default_system_prompt: 默认系统提示词
        """
        self.model = model
        self.api_key = api_key or os.getenv("CHAT_API_KEY")
        self.default_tools = default_tools or self._get_default_tools()
        self.default_middleware = default_middleware or self._get_default_middleware()
        self.default_system_prompt = default_system_prompt or self._get_default_system_prompt()

    @staticmethod
    def _get_default_tools() -> list[BaseTool]:
        """获取默认工具列表"""
        return [
            what_time_is_now,
            # 统一检索：同时覆盖笔记与知识库，取代原先仅搜笔记的 search_notes_tool
            search_knowledge_tool,
            # 两级检索的第二级：片段不够时按 source_id 取全文
            get_document_detail_tool,
            get_note_stats_tool,
            get_today_reviews_tool,
            mark_reviewed_tool,
            create_note_tool,
            get_related_notes_tool,
        ]

    def _get_default_middleware(self) -> list:
        """获取默认中间件列表"""
        return get_middleware()

    @staticmethod
    def _get_default_system_prompt() -> str:
        """获取默认系统提示词"""
        return load_prompt('main_prompt')

    def _create_chat_model(self, custom_model: str | None = None):
        """内部方法：根据LLM_TYPE创建聊天模型实例"""
        llm_type = os.getenv("LLM_TYPE", "ALIYUN").upper()

        if llm_type == "OLLAMA":
            model_name = custom_model or os.getenv("OLLAMA_MODEL_NAME", self.model)
            base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

            logger.info(f"🤖 Agent使用Ollama模型: {model_name}")

            return ChatOllama(
                model=model_name,
                base_url=base_url,
                streaming=True,
                top_p=0.7,
            )

        elif llm_type == "ALIYUN":
            api_key = os.getenv("ALIYUN_ACCESS_KEY_SECRET")
            base_url = os.getenv("ALIYUN_BASE_URL")
            model_name = custom_model or os.getenv("ALIYUN_MODEL_NAME", self.model)

            logger.info(f"🤖 Agent使用阿里云百炼模型: {model_name}")

            return _NormalizedTongyi(
                model=model_name,
                api_key=api_key,
                base_url=base_url,
                streaming=True,
                top_p=0.7,
            )

        else:
            raise ValueError(f"不支持的LLM_TYPE: {llm_type}，可选值: ALIYUN, OLLAMA")

    def create_agent(
            self,
            custom_tools: list[BaseTool] | None = None,
            custom_model: str | None = None,
            custom_system_prompt: str | None = None,
            custom_middleware: list | None = None,
    ):
        """
        核心工厂方法：创建全新的 LangGraph Agent 实例

        相比 AgentExecutor，LangGraph 的 create_agent 会真正消费 middleware，
        日志、工具调用追踪等钩子才会生效（此前挂在 AgentExecutor 上属于死代码）。

        :param custom_tools: 自定义工具列表（覆盖默认）
        :param custom_model: 自定义模型（覆盖默认）
        :param custom_system_prompt: 自定义系统提示词（覆盖默认）
        :param custom_middleware: 自定义中间件列表（覆盖默认）
        :return: 编译后的 LangGraph 图
        """
        chat_model = self._create_chat_model(custom_model)
        tools = custom_tools or self.default_tools
        middleware = custom_middleware if custom_middleware is not None else self.default_middleware
        system_prompt = custom_system_prompt or self.default_system_prompt

        return create_langgraph_agent(
            chat_model,
            tools=tools,
            system_prompt=system_prompt,
            middleware=middleware,
        )


# 初始化全局工厂配置
agent_factory = AgentFactory()


def build_messages(history: list[tuple] | None, query: str) -> list[BaseMessage]:
    """
    构造 LangGraph 输入消息序列

    :param history: 会话历史 [(user_msg, assistant_msg), ...]
    :param query: 本次用户输入
    :return: 消息列表
    """
    messages: list[BaseMessage] = []
    if history:
        for user_msg, assistant_msg in history:
            messages.append(HumanMessage(content=user_msg))
            messages.append(AIMessage(content=assistant_msg))
    messages.append(HumanMessage(content=query))
    return messages


def extract_final_text(result: dict) -> str:
    """
    从 LangGraph 返回结果中取出最终回答

    末尾可能带 tool_calls 的消息是中间步骤，必须跳过，
    取最后一条没有工具调用的 AI 消息才是真正回答。

    :param result: agent.ainvoke 的返回值
    :return: 最终回答文本
    """
    messages = result.get("messages", []) if isinstance(result, dict) else []
    for message in reversed(messages):
        if not isinstance(message, AIMessage):
            continue
        if getattr(message, "tool_calls", None):
            continue
        content = message.content
        if isinstance(content, str) and content:
            return content
        # 多模态返回时 content 是分块列表，拼接文本部分
        if isinstance(content, list):
            texts = [part.get("text", "") for part in content if isinstance(part, dict)]
            joined = "".join(texts)
            if joined:
                return joined
    return "抱歉，我无法理解您的请求。"


def extract_tool_steps(messages: list[BaseMessage]) -> list[dict]:
    """
    从消息序列中提取工具调用步骤，用于前端展示思考过程

    :param messages: LangGraph 完整消息序列
    :return: [{thought, tool, tool_input, tool_output}, ...]
    """
    steps: list[dict] = []
    for message in messages:
        tool_calls = getattr(message, "tool_calls", None)
        if tool_calls:
            for call in tool_calls:
                steps.append({
                    "thought": "",
                    "tool": call.get("name"),
                    "tool_input": call.get("args"),
                    "tool_output": "",
                })
        if isinstance(message, ToolMessage) and steps and not steps[-1]["tool_output"]:
            steps[-1]["tool_output"] = message.content
    return steps


async def get_agent_response(
        query: str,
        history: list[tuple] | None = None,
        user_id: str | None = None,
        custom_tools: list[BaseTool] | None = None,
        **kwargs
):
    """
    获取 Agent 响应（使用工厂创建实例）
    :param query: 用户查询
    :param history: 会话历史 [(user_msg, assistant_msg), ...]
    :param user_id: 用户ID
    :param custom_tools: 自定义工具（可选，用于动态切换工具）
    :param kwargs: 其他工厂参数
    :return: 响应结果
    """
    if user_id:
        set_current_user_id(user_id)

    try:
        # 1. 从工厂获取全新的 LangGraph Agent
        agent = agent_factory.create_agent(custom_tools=custom_tools, **kwargs)

        # 2. 构建消息序列并执行
        messages = build_messages(history, query)
        result = await agent.ainvoke({"messages": messages})

        # 3. 提取回答与工具调用步骤
        all_messages = result.get("messages", []) if isinstance(result, dict) else []
        steps = extract_tool_steps(all_messages)
        for step in steps:
            logger.info(f"🛠️ [调用工具] {step['tool']}")
            logger.info(f"📥 [工具输入] {step['tool_input']}")
            logger.info(f"📤 [工具结果] {step['tool_output']}")

        return {
            "response": extract_final_text(result),
            "steps": steps
        }

    except Exception as e:
        logger.error(f"Agent 执行错误: {str(e)}", exc_info=True)
        return {
            "response": f"抱歉，处理您的请求时出现了错误: {str(e)}",
            "steps": []
        }

async def get_agent_stream_response(
        query: str,
        session_id: str,
        user_id: str,
        custom_tools: list[BaseTool] | None = None,
        **kwargs
) -> AsyncGenerator[str, None]:
    """
    获取 Agent 流式响应（真流式，模型产出 token 即推送）

    不再接收路由层预检索的上下文：检索已下沉为 Agent 自己的工具，
    由模型决定何时查、查什么、要不要取全文。

    :param query: 用户查询
    :param session_id: 会话 ID
    :param user_id: 用户 ID
    :param custom_tools: 自定义工具（可选）
    :param kwargs: 其他参数
    :return: 流式响应生成器
    """

    thinking_queue = asyncio.Queue()
    agent_result_holder = {"response": None, "error": None}
    agent_done = asyncio.Event()

    async def run_agent():
        """在独立任务中执行 Agent，模型产出 token 即入队，实现真流式"""
        try:
            set_current_user_id(user_id)

            history = await sm.session_manager.get_history(session_id, user_id)
            logger.info(f"【Agent流式响应】获取会话历史成功，历史记录数: {len(history)}")

            messages = build_messages(history, query)

            agent = agent_factory.create_agent(custom_tools=custom_tools, **kwargs)

            full_response = []

            async for message_chunk, metadata in agent.astream(
                {"messages": messages}, stream_mode="messages"
            ):
                # 只取「模型节点」产出的文本；工具节点的内容是调用参数，不能当回答
                if metadata.get("langgraph_node") != "model":
                    continue
                # 带工具调用的分片属于中间步骤，跳过
                if getattr(message_chunk, "tool_call_chunks", None):
                    continue
                content = getattr(message_chunk, "content", "")
                if not content or not isinstance(content, str):
                    continue
                full_response.append(content)
                await thinking_queue.put(
                    {"type": "response", "content": content, "session_id": session_id}
                )

            agent_result_holder["response"] = "".join(full_response) if full_response else "抱歉，我无法理解您的请求。"
        except Exception as e:
            logger.error(f"【Agent流式响应】Agent执行失败: {e}", exc_info=True)
            agent_result_holder["error"] = str(e)
        finally:
            agent_done.set()

    # 启动 Agent 执行任务
    agent_task = asyncio.create_task(run_agent())

    try:
        logger.info(f"【Agent流式响应】开始处理请求，用户ID: {user_id}, 会话ID: {session_id}, 查询: {query}")

        # 先发送初始响应
        yield f"data: {json.dumps({'type': 'response', 'content': '', 'session_id': session_id}, ensure_ascii=False)}\n\n"

        # 持续监听队列并实时推送思考事件，同时等待 Agent 完成
        while not agent_done.is_set():
            try:
                # 使用短超时轮询队列，实现实时推送
                event = await asyncio.wait_for(thinking_queue.get(), timeout=0.1)
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                thinking_queue.task_done()
            except TimeoutError:
                # 超时是正常的，继续等待
                continue

        # Agent 已完成，推送队列中剩余的所有思考事件
        while not thinking_queue.empty():
            try:
                event = thinking_queue.get_nowait()
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                thinking_queue.task_done()
            except asyncio.QueueEmpty:
                break

        # 等待 agent_task 完全结束
        await agent_task

        if agent_result_holder["error"]:
            error_message = f"错误: {agent_result_holder['error']}"
            yield f"data: {json.dumps({'type': 'error', 'content': error_message, 'session_id': session_id}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
            return

        response = agent_result_holder["response"]

        # 添加到会话历史
        await sm.session_manager.add_message(session_id, user_id, query, response)
        logger.info("【Agent流式响应】添加到会话历史成功")

        # 正文已在生成过程中实时推送，这里只发结束标记
        yield f"data: {json.dumps({'type': 'done', 'session_id': session_id}, ensure_ascii=False)}\n\n"
        logger.info(f"【Agent流式响应】处理完成，会话ID: {session_id}")

    except Exception as e:
        logger.error(f"【Agent流式响应】处理请求失败: {e}", exc_info=True)

        # 取消 agent 任务
        agent_task.cancel()
        try:
            await agent_task
        except asyncio.CancelledError:
            pass

        error_message = f"错误: {str(e)}"
        yield f"data: {json.dumps({'type': 'error', 'content': error_message, 'session_id': session_id}, ensure_ascii=False)}\n\n"
        yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
