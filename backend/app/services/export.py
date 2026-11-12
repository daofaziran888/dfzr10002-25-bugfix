"""数据导出配置业务规则：状态流转、字段校验、筛选口径与导出快照缓存。

导出快照按“当前列表筛选条件 + 字段选择 + 导出格式 + 导出上限”整体签名做缓存：
- 并发生成时同一签名只会产出一次快照（in-flight 去重，复用线程等待同一份结果）；
- 不同筛选条件的缓存互不串数据，缓存键始终绑定完整筛选契约；
- 筛选结果为空也会生成一条快照，文件里只保留表头。
"""
from __future__ import annotations

import threading
from datetime import datetime
from typing import Any

from app.store import store
from app.xlsx import build_csv, build_xlsx

MODULE = "export"
REQUIRED_FIELDS = ["模板编号", "模板名称", "数据源绑定"]
# 清单导出允许选择的字段（列表展示列），id/status 等内部字段不对外导出
EXPORTABLE_FIELDS = [
    "模板编号",
    "模板名称",
    "数据源绑定",
    "导出格式",
    "字段选择",
    "筛选参数",
    "导出上限",
    "是否启用",
]
SUPPORTED_FORMATS = ["XLSX", "CSV"]
DEFAULT_FORMAT = "XLSX"
DEFAULT_FIELDS = ["模板编号", "模板名称"]
DEFAULT_LIMIT = 10000
MAX_LIMIT = 10000
STATUS_ORDER = ["草稿", "待校验", "已启用", "已停用"]
ACTION_RULES = {"提交校验": "待校验", "启用配置": "已启用", "停用配置": "已停用"}
NEGATIVE_ACTIONS = ["停用配置"]


class ExportError(ValueError):
    """导出参数契约错误：由路由层转成 400 结构化响应。"""


class ExportService:
    def __init__(self) -> None:
        # 快照缓存：filter_signature -> 快照记录；锁同时保护缓存与生成中的 future
        self._snapshots: list[dict[str, Any]] = []
        self._cache: dict[str, dict[str, Any]] = {}
        self._inflight: dict[str, threading.Event] = {}
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ 列表
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = self._filtered_rows(keyword=keyword, status=status)
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"导出模板 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于数据导出配置可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"导出模板已{action}"

    # ------------------------------------------------------------- 筛选契约
    def _filtered_rows(self, *, keyword: str | None, status: str | None) -> list[dict[str, Any]]:
        """与列表完全同口径的筛选，保证文件内容与页面总计一致。"""
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("模板编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        return rows

    @staticmethod
    def _normalize_fields(fields: list[str]) -> list[str]:
        chosen = [field.strip() for field in fields if field and field.strip()]
        if not chosen:
            chosen = list(DEFAULT_FIELDS)
        unknown = [field for field in chosen if field not in EXPORTABLE_FIELDS]
        if unknown:
            raise ExportError(
                f"字段 {('、'.join(unknown))} 不在可导出字段范围内，"
                f"可选：{'、'.join(EXPORTABLE_FIELDS)}"
            )
        # 去重但保持用户给定的顺序
        return list(dict.fromkeys(chosen))

    @staticmethod
    def _normalize_limit(raw: int | None) -> int:
        if raw is None:
            return DEFAULT_LIMIT
        if raw <= 0:
            raise ExportError("导出上限必须为正整数")
        return min(raw, MAX_LIMIT)

    @staticmethod
    def build_filter_signature(
        *,
        keyword: str | None,
        status: str | None,
        file_format: str,
        fields: list[str],
        limit: int,
    ) -> str:
        """完整筛选契约的稳定签名：任一条件变化都会得到不同缓存键。"""
        return "|".join([
            keyword or "",
            status or "",
            file_format,
            ",".join(fields),
            str(limit),
        ])

    # --------------------------------------------------------------- 导出快照
    def create_snapshot(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        file_format: str = DEFAULT_FORMAT,
        fields: list[str] | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """按当前列表条件生成（或复用）导出快照。

        同一筛选签名并发调用时只生成一次：后来的请求等待首个生成完成并复用
        同一份快照与文件，绝不混入其他筛选条件下的数据。
        """
        fmt = (file_format or DEFAULT_FORMAT).upper()
        if fmt not in SUPPORTED_FORMATS:
            raise ExportError(
                f"导出格式 {file_format} 暂不支持，可选：{'、'.join(SUPPORTED_FORMATS)}"
            )
        chosen_fields = self._normalize_fields(fields or DEFAULT_FIELDS)
        capped_limit = self._normalize_limit(limit)
        signature = self.build_filter_signature(
            keyword=keyword,
            status=status,
            file_format=fmt,
            fields=chosen_fields,
            limit=capped_limit,
        )

        with self._lock:
            cached = self._cache.get(signature)
            if cached is not None:
                cached["hit_count"] = cached.get("hit_count", 1) + 1
                return cached
            event = self._inflight.get(signature)
            if event is None:
                event = threading.Event()
                self._inflight[signature] = event
                leader = True
            else:
                leader = False

        if not leader:
            # 等待首个并发生成完成；缓存一定已经写入，筛选签名完全一致所以可安全复用
            event.wait()
            with self._lock:
                cached = self._cache.get(signature)
                if cached is not None:
                    cached["hit_count"] = cached.get("hit_count", 1) + 1
                    return cached
                # 极端情况下首发生成失败，降级为当前线程重新生成
                return self.create_snapshot(
                    keyword=keyword,
                    status=status,
                    file_format=fmt,
                    fields=chosen_fields,
                    limit=capped_limit,
                )

        try:
            snapshot = self._generate_snapshot(
                keyword=keyword,
                status=status,
                file_format=fmt,
                fields=chosen_fields,
                limit=capped_limit,
                signature=signature,
            )
            with self._lock:
                # 编号与文件名在锁内分配，避免不同筛选契约并发生成时撞号
                snapshot["snapshot_id"] = len(self._snapshots) + 1
                snapshot["filename"] = (
                    f"export-templates-{snapshot['snapshot_id']}.{snapshot['file_format'].lower()}"
                )
                self._snapshots.append(snapshot)
                self._cache[signature] = snapshot
            return snapshot
        finally:
            with self._lock:
                self._inflight.pop(signature, None)
                event.set()

    def _generate_snapshot(
        self,
        *,
        keyword: str | None,
        status: str | None,
        file_format: str,
        fields: list[str],
        limit: int,
        signature: str,
    ) -> dict[str, Any]:
        matched = self._filtered_rows(keyword=keyword, status=status)
        total = len(matched)
        limited_rows = matched[:limit]
        # 被导出上限截掉的模板编号必须可追溯，解释“页面 6 条、文件 5 条”的差异
        omitted_codes = [str(row.get("模板编号", "")) for row in matched[limit:]]
        data_rows = [[row.get(field) for field in fields] for row in limited_rows]

        if file_format == "XLSX":
            content = build_xlsx(fields, data_rows)
            mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ext = "xlsx"
        else:
            content = build_csv(fields, data_rows)
            mime = "text/csv; charset=utf-8"
            ext = "csv"

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # snapshot_id / filename 由 create_snapshot 在锁内统一分配，防止并发撞号
        return {
            "snapshot_id": 0,
            "module": MODULE,
            "filename": f"export-templates-pending.{ext}",
            "media_type": mime,
            "file_format": file_format,
            "filters": {
                "keyword": keyword or "",
                "status": status or "",
                "fields": fields,
                "limit": limit,
            },
            "filter_signature": signature,
            "total": total,
            "row_count": len(limited_rows),
            "matched_codes": [str(row.get("模板编号", "")) for row in matched],
            "omitted_codes": omitted_codes,
            "content": content,
            "created_at": now,
            "hit_count": 1,
        }

    def list_snapshots(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        """任务清单：按当前列表筛选条件过滤快照（不含文件字节）。

        命中规则与列表口径一致——快照生成时的 keyword/status 必须落在当前
        筛选条件内，保证“最近快照与列表筛选一致”。
        """
        with self._lock:
            snapshots = [dict(item) for item in self._snapshots]
        result = []
        for snapshot in snapshots:
            filters = snapshot.get("filters", {})
            if keyword and keyword not in str(filters.get("keyword", "")):
                continue
            if status and filters.get("status") != status:
                continue
            snapshot.pop("content", None)
            result.append(snapshot)
        return result

    def get_snapshot(self, snapshot_id: int) -> dict[str, Any] | None:
        with self._lock:
            for snapshot in self._snapshots:
                if snapshot.get("snapshot_id") == snapshot_id:
                    return dict(snapshot)
        return None
