import asyncio
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

import app.config.settings as settings
from app.api.routes import router
from app.api.dashboard import dashboard_router, init_dashboard_router
from app.vertex.credentials_manager import CredentialManager
from app.vertex.vertex_ai_init import init_vertex_ai
from app.utils.logging import log

# 全局凭证管理器
credential_manager = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    global credential_manager
    
    log("info", "正在初始化 Vertex AI...")
    
    # 初始化凭证管理器
    credential_manager = CredentialManager()
    
    # 从环境变量加载凭证
    if settings.GOOGLE_CREDENTIALS_JSON:
        import json
        try:
            creds = json.loads(settings.GOOGLE_CREDENTIALS_JSON)
            credential_manager.add_credential_from_json(creds)
            log("info", "已从环境变量加载 Google Credentials")
        except Exception as e:
            log("error", f"加载 Google Credentials 失败: {e}")
    
    # 初始化 Vertex AI
    success = await init_vertex_ai(credential_manager=credential_manager)
    if success:
        log("info", "Vertex AI 初始化成功")
    else:
        log("warning", "Vertex AI 初始化失败，部分功能可能不可用")
    
    # 保存到 app state 供路由使用
    app.state.credential_manager = credential_manager
    
    # 初始化 Dashboard（简化版，不传 key_manager 等轮询组件）
    init_dashboard_router(None, None, None, credential_manager)
    
    yield
    
    # 关闭时清理
    log("info", "应用关闭")

# 创建 FastAPI 应用
app = FastAPI(
    title="Vertex AI 中转服务",
    description="纯净的 Vertex AI 中转，无轮询",
    version="1.0.0",
    lifespan=lifespan
)

# CORS 中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(router)
app.include_router(dashboard_router)

@app.get("/")
async def root():
    return {"message": "Vertex AI 中转服务运行中", "mode": "vertex_only"}

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=7860,
        reload=False
    )
