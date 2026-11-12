"""数据导出配置接口：维护导出模板，并提供与列表同口径的清单下载与快照任务清单。

路由顺序约定：静态路径（/export、/snapshots）必须注册在 /{entry_id} 之前，
否则导出入口会被详情入口抢先匹配，把 "export" 当成 entry_id 解析而报
“entry_id 不是整数”。详情入口 /{entry_id} 保留，只接受整数编号。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse, Response

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.export import (
    DEFAULT_FORMAT,
    EXPORTABLE_FIELDS,
    SUPPORTED_FORMATS,
    ExportError,
    ExportService,
)

router = APIRouter(prefix="/api/export", tags=["数据导出配置"])

service = ExportService()

LIST_FIELDS = ["模板编号", "模板名称", "数据源绑定", "导出格式", "字段选择", "筛选参数", "导出上限", "是否启用"]
STATUSES = ["草稿", "待校验", "已启用", "已停用"]


def _contract_error(message: str) -> JSONResponse:
    """统一的导出契约错误返回：结构化 JSON，便于前端区分参数错误与服务异常。"""
    return JSONResponse(
        status_code=400,
        content={"ok": False, "code": "EXPORT_INVALID_CONTRACT", "message": message},
    )


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按模板编号检索"),
    status: str | None = Query(default=None, description="草稿、待校验、已启用、已停用"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按模板编号与状态过滤数据导出配置列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


# -------------------------------------------------------------- 导出下载入口
# 注意：必须排在 /{entry_id} 前面，/api/export/export 只能走清单下载，不进详情解析。
@router.get("/export")
def export_entries(
    keyword: str | None = Query(default=None, description="与列表一致：按模板编号检索"),
    status: str | None = Query(default=None, description="与列表一致：状态过滤"),
    format: str = Query(default=DEFAULT_FORMAT, description="导出格式：XLSX / CSV"),
    fields: str | None = Query(
        default=None,
        description="字段选择，逗号分隔；默认模板编号、模板名称",
    ),
    limit: str | None = Query(default=None, description="导出上限，正整数，最大 10000"),
) -> Response:
    """按当前列表筛选条件下载导出清单，并在任务清单留下一条快照记录。

    空筛选结果不报错：返回仅带表头的文件，同时生成一条 0 行快照。
    同一筛选契约（keyword/status/format/fields/limit）并发生成只产出一次快照，
    缓存复用时不会混入其他条件的数据。
    """
    chosen_fields = [field.strip() for field in fields.split(",")] if fields else []
    try:
        limit_value = int(limit) if limit not in (None, "") else None
    except (TypeError, ValueError):
        return _contract_error(f"导出上限「{limit}」不是正整数")
    try:
        snapshot = service.create_snapshot(
            keyword=keyword,
            status=status,
            file_format=format,
            fields=chosen_fields,
            limit=limit_value,
        )
    except ExportError as exc:
        return _contract_error(str(exc))

    quoted_name = snapshot["filename"].replace('"', "")
    return Response(
        content=snapshot["content"],
        media_type=snapshot["media_type"],
        headers={
            "Content-Disposition": (
                f'attachment; filename="{quoted_name}"; '
                f"filename*=UTF-8''{quoted_name}"
            ),
            "X-Export-Snapshot-Id": str(snapshot["snapshot_id"]),
            "X-Export-Total": str(snapshot["total"]),
            "X-Export-Row-Count": str(snapshot["row_count"]),
        },
    )


@router.get("/snapshots")
def list_snapshots(
    keyword: str | None = Query(default=None, description="与列表一致：按模板编号检索"),
    status: str | None = Query(default=None, description="与列表一致：状态过滤"),
) -> dict[str, Any]:
    """导出任务清单：返回当前列表筛选条件下的快照记录（不含文件字节）。"""
    items = service.list_snapshots(keyword=keyword, status=status)
    return {"module": "export", "total": len(items), "items": items}


@router.get("/snapshots/{snapshot_id}/download")
def download_snapshot(snapshot_id: int) -> Response:
    """按快照编号重新下载已生成的缓存文件，复用同一筛选契约下的快照。"""
    snapshot = service.get_snapshot(snapshot_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail=f"导出快照 {snapshot_id} 不存在或已清理")
    quoted_name = snapshot["filename"].replace('"', "")
    return Response(
        content=snapshot["content"],
        media_type=snapshot["media_type"],
        headers={"Content-Disposition": f'attachment; filename="{quoted_name}"'},
    )


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: str) -> dict:
    """读取单条导出模板明细；编号必须是整数，不存在时给出可读的错误说明。"""
    if not str(entry_id).isdigit():
        # 结构化错误，避免 FastAPI 默认 422 把“路由被抢匹配”的问题暴露给页面
        return JSONResponse(
            status_code=400,
            content={
                "ok": False,
                "code": "EXPORT_ENTRY_ID_INVALID",
                "message": f"entry_id 不是整数：{entry_id}；导出清单请走 /api/export/export",
            },
        )
    entry = service.get_entry(int(entry_id))
    if entry is None:
        raise HTTPException(status_code=404, detail=f"导出模板 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条导出模板，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="导出模板已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条导出模板执行提交校验、启用配置、停用配置；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


__all__ = ["router", "LIST_FIELDS", "STATUSES", "EXPORTABLE_FIELDS", "SUPPORTED_FORMATS"]
