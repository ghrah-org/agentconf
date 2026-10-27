# SPDX-License-Identifier: Apache-2.0
"""规范目录下载、类型转换和可保存的导入预览。网络仅在显式调用时使用。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol
from urllib.request import Request, urlopen

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agentconf.exceptions import CatalogImportError
from agentconf.models.capability_profile import CapabilityProfile

NORMALIZER_VERSION = 1
MODELS_DEV_URL = "https://models.dev/models.json"
CONTENT_FIELDS = tuple(
    k
    for k in CapabilityProfile.model_fields
    if k
    not in {
        "created_at",
        "updated_at",
        "source_fetched_at",
    }
)


def content(profile: CapabilityProfile) -> dict[str, Any]:
    """用于比较声明的内容；抓取时间不构成能力差异。"""
    values = profile.model_dump(mode="json")
    return {key: values[key] for key in CONTENT_FIELDS}


def revision(profile: CapabilityProfile) -> str:
    """编辑时使用整行指纹，防止覆盖别人的修订。"""
    return hashlib.sha256(profile.model_dump_json().encode()).hexdigest()


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
            p.entry_kind != "model" or p.source != self.source for p in self.profiles
        ):
            raise ValueError("外部快照仅允许同一来源的真实模型档案")
        return self


class ImportPlan(BaseModel):
    """预览和应用共用一份已规范化快照；应用不再次下载。"""

    model_config = ConfigDict(extra="forbid")
    snapshot: CatalogSnapshot
    added: list[str]
    changed: list[str]
    unchanged: list[str]
    missing: list[str]
    differences: dict[str, dict[str, Any]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_partition(self) -> ImportPlan:
        groups = self.added + self.changed + self.unchanged
        if len(groups) != len(set(groups)) or set(groups) != {
            p.model_key for p in self.snapshot.profiles
        }:
            raise ValueError("预览分类与快照不一致")
        return self

    def save(self, path: str | Path) -> None:
        Path(path).write_text(self.model_dump_json(indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> ImportPlan:
        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))


def prepare_import(
    snapshot: CatalogSnapshot, existing: list[CapabilityProfile]
) -> ImportPlan:
    local = {p.model_key: p for p in existing}
    incoming = {p.model_key: p for p in snapshot.profiles}
    added, changed, unchanged, differences = [], [], [], {}
    for key, profile in sorted(incoming.items()):
        old = local.get(key)
        if old is None:
            added.append(key)
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
            if p.source == snapshot.source
            and p.entry_kind == "model"
            and k not in incoming
        ),
        differences=differences,
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
            request = Request(MODELS_DEV_URL, headers={"User-Agent": "agentconf/0.2.0"})
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
