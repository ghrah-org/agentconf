# SPDX-License-Identifier: Apache-2.0
"""规范目录下载、语义指纹与可保存的导入/更新预览。网络仅在显式调用时使用。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Protocol
from urllib.request import Request, urlopen

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agentconf.exceptions import CatalogImportError
from agentconf.models.capability_profile import (
    CAPABILITY_FIELDS,
    KNOWN_ANNOTATION_FIELDS,
    LIST_CAPABILITY_FIELDS,
    CapabilityProfile,
)

NORMALIZER_VERSION = 3
MODELS_DEV_URL = "https://models.dev/models.json"
CONTENT_FIELDS = tuple(
    k
    for k in CapabilityProfile.model_fields
    if k
    not in {
        "created_at",
        "updated_at",
        "source_fetched_at",
        "revision",
        "base_fingerprint",
    }
)


def _canonical_value(value: Any) -> Any:
    """递归排序 JSON 对象键，保留任意扩展数组的顺序。"""
    if isinstance(value, dict):
        return {key: _canonical_value(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, list):
        return [_canonical_value(item) for item in value]
    return value


def semantic_extra(profile: CapabilityProfile) -> dict[str, Any]:
    """去掉说明字段后的扩展能力；说明变化不影响能力指纹。"""
    return {
        key: value
        for key, value in (profile.extra or {}).items()
        if key not in KNOWN_ANNOTATION_FIELDS
    }


def _semantic_declaration(profile: CapabilityProfile) -> dict[str, Any]:
    """只对已知集合能力排序去重，说明与未声明字段不进入语义比较。"""
    payload: dict[str, Any] = {"model_type": profile.model_type}
    for key in CAPABILITY_FIELDS:
        value = getattr(profile, key)
        if value is not None and key != "extra":
            payload[key] = (
                sorted(set(value))
                if key in LIST_CAPABILITY_FIELDS
                else _canonical_value(value)
            )
    extra = semantic_extra(profile)
    if extra:
        payload["extra"] = _canonical_value(extra)
    return payload


def fingerprint(profile: CapabilityProfile) -> str:
    """能力语义指纹：类型、显式能力与语义性 extra；不含展示名/说明/时间/谱系。"""
    encoded = json.dumps(
        _semantic_declaration(profile),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def content(profile: CapabilityProfile) -> dict[str, Any]:
    """用于比较声明的内容；抓取时间与修订号不构成能力差异。"""
    values = profile.model_dump(mode="json")
    return {key: values[key] for key in CONTENT_FIELDS}


def revision(profile: CapabilityProfile) -> int:
    """返回并发校验用的整行修订号。"""
    return profile.revision


def row_hash(profile: CapabilityProfile) -> str:
    """整行快照校验，与 revision 一起检测未递增版本的外部修改。"""
    return hashlib.sha256(profile.model_dump_json().encode()).hexdigest()


def declaration_changes(
    base: CapabilityProfile, other: CapabilityProfile
) -> dict[str, dict[str, Any]]:
    """比较两份档案的语义能力，供 derived diff 使用。"""
    before = _semantic_declaration(base)
    after = _semantic_declaration(other)
    return {
        field: {"base": before.get(field), "derived": after.get(field)}
        for field in sorted(set(before) | set(after))
        if _canonical_value(before.get(field)) != _canonical_value(after.get(field))
    }


class CatalogSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str
    source_url: str
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    normalizer_version: int = NORMALIZER_VERSION
    profiles: list[CapabilityProfile]

    @model_validator(mode="after")
    def validate_snapshot(self) -> CatalogSnapshot:
        if self.normalizer_version != NORMALIZER_VERSION:
            raise ValueError("不支持的规范化版本")
        keys = [p.model_key for p in self.profiles]
        if not keys or len(keys) != len(set(keys)):
            raise ValueError("目录为空或存在重复模型键")
        if any(
            p.origin != "upstream"
            or p.source != self.source
            or p.derived_from is not None
            or p.base_fingerprint is not None
            for p in self.profiles
        ):
            raise ValueError("外部快照仅允许同一来源的 upstream 档案")
        return self


class Relocation(BaseModel):
    """人工键搬迁的冻结声明与引用快照。"""

    model_config = ConfigDict(extra="forbid")
    profile: CapabilityProfile
    new_key: str = Field(min_length=1, pattern=r"^local/.+")
    references: list[str]
    reference_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_local(self) -> Relocation:
        if self.profile.origin != "local" or self.new_key == self.profile.model_key:
            raise ValueError("只能把 local 档案搬到不同的 local/ 键")
        return self


class ImportPlan(BaseModel):
    """预览和应用共用一份已规范化快照；应用不再次下载。"""

    model_config = ConfigDict(extra="forbid")
    operation: Literal["import"] = "import"
    snapshot: CatalogSnapshot
    added: list[str]
    changed: list[str]
    unchanged: list[str]
    missing: list[str]
    conflicts: dict[str, str] = Field(default_factory=dict)
    differences: dict[str, dict[str, Any]] = Field(default_factory=dict)
    relocations: dict[str, Relocation] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_partition(self) -> ImportPlan:
        groups = self.added + self.changed + self.unchanged + list(self.conflicts)
        snapshot_keys = {p.model_key for p in self.snapshot.profiles}
        if len(groups) != len(set(groups)) or set(groups) != snapshot_keys:
            raise ValueError("预览分类与快照不一致")
        if not set(self.differences) <= set(self.changed):
            raise ValueError("差异项必须属于 changed")
        if not set(self.relocations) <= set(self.added):
            raise ValueError("搬迁后必须导入对应上游键")
        if any(k != r.profile.model_key for k, r in self.relocations.items()):
            raise ValueError("搬迁键与原档案不一致")
        return self

    def save(self, path: str | Path) -> None:
        Path(path).write_text(self.model_dump_json(indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> ImportPlan:
        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))


def prepare_import(
    snapshot: CatalogSnapshot,
    existing: list[CapabilityProfile],
    *,
    relocations: dict[str, Relocation] | None = None,
) -> ImportPlan:
    """只新增不覆盖；已有同键档案一律进入冲突或差异分类。"""
    relocations = relocations or {}
    local = {p.model_key: p for p in existing if p.model_key not in relocations}
    incoming = {p.model_key: p for p in snapshot.profiles}
    added, changed, unchanged, conflicts, differences = [], [], [], {}, {}
    for key, profile in sorted(incoming.items()):
        old = local.get(key)
        if old is None:
            added.append(key)
        elif old.origin != "upstream":
            conflicts[key] = (
                f"{old.origin} 档案占用上游规范键，需显式 relocation 后才能导入"
            )
        elif old.source != snapshot.source:
            conflicts[key] = f"来源 {old.source} 与快照 {snapshot.source} 不同"
        elif content(old) == content(profile):
            unchanged.append(key)
        else:
            changed.append(key)
            before, after = content(old), content(profile)
            differences[key] = {
                field: {"old": before[field], "new": after[field]}
                for field in before
                if before[field] != after[field]
            }
    return ImportPlan(
        snapshot=snapshot,
        added=added,
        changed=changed,
        unchanged=unchanged,
        missing=sorted(
            k
            for k, p in local.items()
            if p.origin == "upstream"
            and p.source == snapshot.source
            and k not in incoming
        ),
        conflicts=conflicts,
        differences=differences,
        relocations=relocations,
    )


class UpdatePlan(ImportPlan):
    """仅同来源 upstream 记录可更新，人工/派生/示例记录受保护。"""

    operation: Literal["update"] = "update"
    updatable: list[str]
    protected: list[str]
    expected_revisions: dict[str, int]
    expected_hashes: dict[str, str]
    references: dict[str, list[str]] = Field(default_factory=dict)
    reference_fingerprints: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_update(self) -> UpdatePlan:
        if (
            len(self.updatable) != len(set(self.updatable))
            or not set(self.updatable) <= set(self.changed)
            or set(self.updatable) != set(self.expected_revisions)
            or set(self.updatable) != set(self.expected_hashes)
            or set(self.updatable) & (set(self.protected) | set(self.conflicts))
        ):
            raise ValueError("更新分类或修订号不一致")
        snapshot_keys = {p.model_key for p in self.snapshot.profiles}
        if not set(self.protected) <= snapshot_keys:
            raise ValueError("受保护项必须出现在快照中")
        return self


def prepare_update(
    snapshot: CatalogSnapshot,
    existing: list[CapabilityProfile],
    *,
    relocations: dict[str, Relocation] | None = None,
) -> UpdatePlan:
    """新增可接受；已有记录仅在 origin/source/类型一致时成为更新候选。"""
    base = prepare_import(snapshot, existing, relocations=relocations)
    local = {p.model_key: p for p in existing if p.model_key not in (relocations or {})}
    incoming = {p.model_key: p for p in snapshot.profiles}
    protected, updatable, revisions = [], [], {}
    conflicts = dict(base.conflicts)
    for key in sorted(set(local) & set(incoming)):
        old, new = local[key], incoming[key]
        if old.origin != "upstream" or old.source != snapshot.source:
            protected.append(key)
            conflicts.setdefault(
                key,
                f"{old.origin} 档案由用户维护，不能由来源自动更新",
            )
        elif old.model_type != new.model_type:
            conflicts[key] = (
                f"模型类型冲突: {old.model_type} → {new.model_type}，请新建档案并人工重绑"
            )
        elif key in base.changed:
            updatable.append(key)
            revisions[key] = old.revision
    values = base.model_dump()
    values["conflicts"] = conflicts
    values["changed"] = [key for key in base.changed if key not in conflicts]
    values["differences"] = {
        key: val for key, val in base.differences.items() if key not in conflicts
    }
    values["operation"] = "update"
    return UpdatePlan(
        **values,
        updatable=updatable,
        protected=protected,
        expected_revisions=revisions,
        expected_hashes={key: row_hash(local[key]) for key in updatable},
    )


class CatalogSource(Protocol):
    def fetch(self) -> CatalogSnapshot: ...


class ModelsDevSource:
    """只读取独立 models.json，不从供应商目录推断身份或能力。"""

    def __init__(self, timeout: float = 10, max_bytes: int = 32 * 1024 * 1024) -> None:
        if timeout <= 0 or max_bytes <= 0:
            raise ValueError("下载超时与大小限制必须为正数")
        self.timeout, self.max_bytes = timeout, max_bytes

    def fetch(self) -> CatalogSnapshot:
        try:
            request = Request(MODELS_DEV_URL, headers={"User-Agent": "agentconf/0.3.0"})
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read(self.max_bytes + 1)
            if len(raw) > self.max_bytes:
                raise ValueError("目录超过下载大小限制")
            return self.parse(raw)
        except Exception as exc:
            raise CatalogImportError(f"models.dev 导入失败: {exc}") from exc

    @staticmethod
    def parse(raw: bytes) -> CatalogSnapshot:
        try:
            data = json.loads(raw)
            if not isinstance(data, dict) or not data:
                raise ValueError("models.json 必须为非空模型字典")
            now = datetime.now(timezone.utc)
            profiles = []
            types = {
                "chat": "llm",
                "llm": "llm",
                "embedding": "embedding",
                "reranking": "rerank",
                "speech": "tts",
                "transcription": "stt",
                "image": "image_gen",
                "video": "video",
                "realtime": "realtime",
            }
            for key, row in sorted(data.items()):
                if not isinstance(row, dict) or row.get("id") != key:
                    raise ValueError(f"模型规范 ID 不一致: {key}")
                model_type = types.get(row.get("type"), row.get("type") or "llm")
                limits = row.get("limit", {})
                modalities = row.get("modalities", {})
                caps: dict[str, Any] = {}
                for direction in ("input", "output"):
                    if direction in modalities:
                        caps[f"{direction}_modalities"] = [
                            "document_pdf" if m == "pdf" else m
                            for m in modalities[direction]
                        ]
                if model_type == "llm":
                    for upstream, local in (
                        ("context", "max_context_length"),
                        ("output", "max_output_length"),
                    ):
                        if limits.get(upstream) is not None:
                            caps[local] = limits[upstream]
                    for upstream, local in (
                        ("reasoning", "supports_reasoning"),
                        ("tool_call", "supports_function_calling"),
                    ):
                        if upstream in row:
                            caps[local] = row[upstream]
                    if "input" in modalities:
                        caps["supports_vision"] = "image" in modalities["input"]
                elif model_type in {"embedding", "rerank"}:
                    if limits.get("input", limits.get("context")) is not None:
                        caps["max_input_length"] = limits.get(
                            "input", limits.get("context")
                        )
                    caps["output_modalities"] = [
                        "embedding" if model_type == "embedding" else "score"
                    ]
                if model_type == "tts":
                    caps["output_modalities"] = ["audio"]
                elif model_type == "stt":
                    caps["input_modalities"] = ["audio"]
                    caps["output_modalities"] = ["text"]
                elif model_type == "image_gen":
                    caps["output_modalities"] = ["image"]
                # 保留非 LLM 的上游限制与标记，避免将 LLM 字段投射到不适用的能力类。
                extra = {
                    k: row[k]
                    for k in ("description", "release_date", "last_updated")
                    if k in row
                }
                if model_type != "llm":
                    extra.update(
                        {
                            k: row[k]
                            for k in ("limit", "reasoning", "tool_call")
                            if k in row
                        }
                    )
                if extra:
                    caps["extra"] = extra
                profiles.append(
                    CapabilityProfile(
                        model_key=key,
                        origin="upstream",
                        display_name=row.get("name", key),
                        family=row.get("family"),
                        model_type=model_type,
                        source="models.dev",
                        source_ref=key,
                        source_fetched_at=now,
                        created_at=now,
                        updated_at=now,
                        **caps,
                    )
                )
            return CatalogSnapshot(
                source="models.dev",
                source_url=MODELS_DEV_URL,
                source_sha256=hashlib.sha256(raw).hexdigest(),
                profiles=profiles,
            )
        except Exception as exc:
            raise CatalogImportError(f"models.json 格式无效: {exc}") from exc
