from langchain.agents import AgentState
from langchain.agents.middleware import (
    ToolCallLimitMiddleware,
    after_agent,
    after_model,
    before_agent,
    before_model,
    wrap_model_call,
    wrap_tool_call,
)
from langgraph.runtime import Runtime

from app.core.logger_handler import logger

# 单轮对话内允许的工具调用次数上限。
# LangGraph 没有 AgentExecutor 的 max_iterations，用这个兜底，
# 防止检索持续无果时模型反复重试停不下来（正常路径远低于此值）。
TOOL_CALL_RUN_LIMIT = 10


@before_agent
def log_before_agent(status: AgentState, runtime: Runtime):
    """agent 运行前执行此函数"""
    logger.info(f"[before_agent] agent启动， 输入：{status['messages']}， 共{len(status['messages'])}条消息")


@after_agent
def log_after_agent(status: AgentState, runtime: Runtime):
    """agent 运行后执行此函数"""
    logger.info(f"[after_agent] agent运行结束， 输出：{status['messages']}， 共{len(status['messages'])}条消息")

@before_model
def log_before_model(status: AgentState, runtime: Runtime):
    """model 运行前执行此函数"""
    logger.info(f"[before_model] model启动， 输入：{status['messages']}， 共{len(status['messages'])}条消息")


@after_model
def log_after_model(status: AgentState, runtime: Runtime):
    """model 运行后执行此函数"""
    logger.info(f"[after_model] model运行结束， 输出：{status['messages']}， 共{len(status['messages'])}条消息")

@wrap_model_call
async def model_call_hook(request, handler):
    """
    model 调用前执行此函数

    必须为 async：整个服务在异步上下文（ainvoke / astream）中运行，
    只定义同步版本会抛 NotImplementedError: awrap_model_call is not available。
    """
    logger.info("模型调用了")
    return await handler(request)

@wrap_tool_call
async def tool_call_hook(request, handler):
    """
    tool 调用前执行此函数

    同 model_call_hook，必须提供异步实现，否则异步调用链路会中断。
    """
    logger.info(f"工具{request.tool_call['name']}调用了, 传入参数{request.tool_call['args']}")
    return await handler(request)


def get_middleware():
    """返回本模块的所有中间件"""
    return [
        log_before_agent,
        log_after_agent,
        log_before_model,
        log_after_model,
        model_call_hook,
        tool_call_hook,
        ToolCallLimitMiddleware(run_limit=TOOL_CALL_RUN_LIMIT),
    ]
