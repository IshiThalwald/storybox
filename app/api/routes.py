from fastapi import APIRouter, Depends, Request, HTTPException, status
from fastapi.responses import StreamingResponse, JSONResponse
from app.models.schemas import ChatCompletionRequest, ChatCompletionResponse, ModelList
from app.utils.auth import custom_verify_password
from app.utils.logging import log
from app.vertex.routes import chat_api, models_api
from app.vertex.models import OpenAIRequest, OpenAIMessage
import app.config.settings as settings

router = APIRouter()

# 获取当前 API Key（Vertex 模式使用）
current_api_key = None

async def verify_user_agent(request: Request):
    """验证 User-Agent（可选）"""
    if not settings.WHITELIST_USER_AGENT:
        return
    if request.headers.get("User-Agent") not in settings.WHITELIST_USER_AGENT:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, 
            detail="Not allowed client"
        )

@router.get("/v1/models", response_model=ModelList)
@router.get("/models", response_model=ModelList)
async def list_models(
    request: Request, 
    _=Depends(custom_verify_password), 
    _2=Depends(verify_user_agent)
):
    """获取可用模型列表（Vertex 模式）"""
    # 从 app state 获取凭证管理器
    credential_manager = request.app.state.credential_manager
    
    if settings.ENABLE_VERTEX:
        return await models_api.list_models(request, credential_manager)
    
    # 如果 Vertex 未启用，返回空列表或默认模型
    return ModelList(data=[])

@router.post("/v1/chat/completions", response_model=ChatCompletionResponse)
@router.post("/chat/completions", response_model=ChatCompletionResponse)
async def chat_completions(
    request: ChatCompletionRequest,
    http_request: Request,
    _dp=Depends(custom_verify_password),
    _du=Depends(verify_user_agent)
):
    """处理聊天请求 - 仅 Vertex 模式"""
    
    # 转换请求格式
    openai_messages = []
    for message in request.messages:
        openai_messages.append(
            OpenAIMessage(
                role=message.get("role", ""), 
                content=message.get("content", "")
            )
        )

    # 构建 Vertex 请求
    vertex_request = OpenAIRequest(
        model=request.model,
        messages=openai_messages,
        temperature=request.temperature,
        max_tokens=request.max_tokens,
        top_p=request.top_p,
        top_k=request.top_k,
        stream=request.stream,
        stop=request.stop,
        presence_penalty=request.presence_penalty,
        frequency_penalty=request.frequency_penalty,
        seed=getattr(request, "seed", None),
        n=request.n,
    )

    # 获取凭证管理器
    credential_manager = http_request.app.state.credential_manager
    
    # 调用 Vertex API（支持流式和非流式）
    return await chat_api.chat_completions(
        http_request, 
        vertex_request, 
        credential_manager
    )

@router.get("/health")
async def health_check():
    """健康检查"""
    return {"status": "healthy", "mode": "vertex_only"}
