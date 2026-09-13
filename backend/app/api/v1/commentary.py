"""Commentary endpoints — AI macro analysis text.

GET  /commentary        → latest batch (or generating/empty status)
POST /commentary/regenerate → 后台异步生成，立即返回当前态（last-good + generating 标记）；
    前端经轮询收敛。旧版同步阻塞（blocking=True）要等整轮模型调用（kimi-k3 推理模型
    可达 2-4 分钟），前端 30s 超时前毫无反馈。
    Token-guarded (F4): it spends money on paid LLM calls, and any page the
    user browsed could otherwise fire it with
    ``fetch(…, {method:'POST', mode:'no-cors'})`` — a billing attack that needs
    no response body, so CORS never blocked it.
GET  /commentary/history → batch index (desc); ?ts=… single-batch detail
"""

from fastapi import APIRouter, Depends, HTTPException

from backend.app.core import commentary
from backend.app.core.auth import require_token
from backend.app.schemas.commentary import Commentary

router = APIRouter(prefix="/commentary", tags=["commentary"])


@router.get("", response_model=Commentary)
def get_commentary():
    """Latest AI commentary (or status=generating/empty)."""
    return Commentary(**commentary.get_current())


@router.post("/regenerate", response_model=Commentary,
             dependencies=[Depends(require_token)])
def regenerate():
    """后台重生成，立即返回 last-good + generating 标记（前端轮询收敛）。

    generate(blocking=False) 在调用方线程持锁后才返回（见 commentary.generate 注释），
    因此这里的 get_current() 必然读到 generating 态，无竞态。
    """
    commentary.generate(blocking=False)
    return Commentary(**commentary.get_current())


@router.get("/history")
def history(ts: str | None = None):
    """批次索引（ts 倒序）；带 ?ts=… 返回该批完整详情，未知 ts → 404。"""
    if ts is None:
        return {"items": commentary.history_index()}
    batch = commentary.get_batch(ts)
    if batch is None:
        raise HTTPException(status_code=404, detail=f"批次不存在：{ts}")
    return Commentary(**batch)
