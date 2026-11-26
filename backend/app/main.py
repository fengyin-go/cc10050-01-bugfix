"""商品价格监控采集平台 后端服务入口。

启动：uvicorn app.main:app --host 127.0.0.1 --port 8020
健康检查：GET /api/health
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import ROUTERS
from app.store import store

app = FastAPI(title="商品价格监控采集平台", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/api/health")
def health() -> dict[str, object]:
    """健康检查：确认服务已经监听、示例数据已经就绪。"""
    return {"ok": True, "app": settings.app_name, "modules": len(store.module_names())}


@app.get("/api/overview")
def overview() -> dict[str, object]:
    """运营概览：把各业务模块的待处理量汇总成看板卡片。"""
    return store.overview()


@app.post("/api/reset")
@app.post("/api/test/reset")
def reset_state() -> dict[str, object]:
    """恢复种子数据并清空幂等台账，供验收/测试隔离重复执行。"""
    from app import rounds_core

    store.reset()
    rounds_core.reset_runtime()
    return {"ok": True, "message": "演示数据已重置", "modules": len(store.module_names())}


@app.get("/api/reset")
def reset_state_get() -> dict[str, object]:
    return reset_state()


# 资源路由含 /api/{module}，必须放在 health / overview 之后注册，
# 否则固定路径会被动态路径抢先匹配。
for module in ROUTERS:
    app.include_router(module.router)
