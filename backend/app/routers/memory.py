"""用户可查看、清空、回填与检索自己的长期记忆。"""

from fastapi import APIRouter, HTTPException

from app.core.config import settings
from app.core.deps import CurrentUser
from app.core.response import success
from app.schemas.memory import MemorySearch
from app.services import memory_service

router = APIRouter(prefix="/api/v1/memory", tags=["长期记忆"])


@router.get("/cards")
async def cards(current: CurrentUser):
    items = await memory_service.list_cards(current.id)
    return success({"items": items, "total": len(items), "enabled": settings.MEMORY_ENABLED})


@router.delete("")
async def clear(current: CurrentUser):
    await memory_service.clear_memory(current.id)
    return success(message="长期记忆已清空；原始聊天与最近对话上下文仍保留")


@router.post("/backfill")
async def backfill(current: CurrentUser):
    if not settings.MEMORY_ENABLED:
        raise HTTPException(status_code=409, detail="长期记忆已关闭")
    return success(await memory_service.index_history(current.id))


@router.post("/search")
async def search(body: MemorySearch, current: CurrentUser):
    if not settings.MEMORY_ENABLED:
        raise HTTPException(status_code=409, detail="长期记忆已关闭")
    if not body.query.strip():
        raise HTTPException(status_code=422, detail="查询不能为空")
    items = await memory_service.search_history(current.id, body.query, body.top_k)
    return success({"items": items, "total": len(items)})
