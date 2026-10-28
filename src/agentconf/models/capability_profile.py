# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""能力档案的原始声明、来源谱系与只读有效投影。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    field_validator,
    model_validator,
)

from agentconf.models.base import Modality
from agentconf.models.capabilities import BaseCapabilities, get_capabilities_class

SCALAR_FIELDS = (
    "max_context_length",
    "max_output_length",
    "max_input_length",
    "dimensions",
    "top_n",
    "supports_streaming",
    "supports_function_calling",
    "supports_reasoning",
    "supports_vision",
    "supports_json_mode",
)
JSON_FIELDS = (
    "input_modalities",
    "output_modalities",
    "languages",
    "voices",
    "supported_sizes",
    "extra",
)
CAPABILITY_FIELDS = SCALAR_FIELDS + JSON_FIELDS
LIST_CAPABILITY_FIELDS = (
    "input_modalities",
    "output_modalities",
    "languages",
    "voices",
    "supported_sizes",
)
KNOWN_ANNOTATION_FIELDS = ("description", "release_date", "last_updated")

Origin = Literal["upstream", "derived", "local", "example"]
"""能力档案来源类型。

- upstream: 随包 seed 或同来源导入器维护，普通 CRUD 只读；
- derived: 用户从某个 upstream 完整复制出的派生档案，只有一层；
- local: 用户自建或由 example 复制，普通 CRUD 可维护；
- example: 随包虚构示例，只读。
"""


def normalize_model_type(value: str) -> str:
    """统一历史类型别名；未知类型继续使用 GenericCapabilities。"""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("model_type 必须是非空字符串")
    value = value.strip()
    return "stt" if value == "asr" else value


def _canonical_json_value(value: Any) -> Any:
    """递归排序 JSON 对象键，保证指纹与落库形状稳定。"""
    if isinstance(value, dict):
        return {
            key: _canonical_json_value(value[key]) for key in sorted(value, key=str)
        }
    if isinstance(value, list):
        return [_canonical_json_value(item) for item in value]
    return value


class CapabilityProfile(BaseModel):
    """完整能力声明；None 为未声明，默认值只在投影时补齐。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    model_key: str = Field(min_length=1)
    origin: Origin = "local"
    display_name: str = Field(min_length=1)
    family: str | None = None
    model_type: str = "llm"
    max_context_length: StrictInt | None = Field(None, gt=0)
    max_output_length: StrictInt | None = Field(None, gt=0)
    max_input_length: StrictInt | None = Field(None, gt=0)
    dimensions: StrictInt | None = Field(None, gt=0)
    top_n: StrictInt | None = Field(None, gt=0)
    supports_streaming: StrictBool | None = None
    supports_function_calling: StrictBool | None = None
    supports_reasoning: StrictBool | None = None
    supports_vision: StrictBool | None = None
    supports_json_mode: StrictBool | None = None
    input_modalities: list[Modality] | None = None
    output_modalities: list[Modality] | None = None
    languages: list[str] | None = None
    voices: list[str] | None = None
    supported_sizes: list[str] | None = None
    extra: dict[str, Any] | None = None
    annotations: dict[str, Any] | None = None
    source: str = Field(default="manual", min_length=1)
    source_ref: str | None = None
    source_fetched_at: datetime | None = None
    derived_from: str | None = Field(None, min_length=1)
    base_fingerprint: str | None = Field(None, pattern=r"^[a-f0-9]{64}$")
    revision: StrictInt = Field(1, gt=0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    _normalize_type = field_validator("model_type", mode="before")(normalize_model_type)

    @model_validator(mode="before")
    @classmethod
    def canonicalize_input(cls, data: Any) -> Any:
        """把说明字段移入 annotations，并规范化集合与 JSON 键序。"""
        if not isinstance(data, dict):
            return data
        values = dict(data)
        annotations = dict(values.get("annotations") or {})
        if isinstance(values.get("extra"), dict):
            extra = dict(values["extra"])
            explicit_empty = not extra
            for key in KNOWN_ANNOTATION_FIELDS:
                if key in extra:
                    if key in annotations and annotations[key] != extra[key]:
                        raise ValueError(f"说明字段重复且不一致: {key}")
                    annotations.setdefault(key, extra.pop(key))
            extra = _canonical_json_value(extra)
            values["extra"] = extra if extra or explicit_empty else None
        if annotations:
            values["annotations"] = _canonical_json_value(annotations)
        elif "annotations" in values:
            values["annotations"] = None
        return values

    @model_validator(mode="after")
    def validate_declaration(self) -> CapabilityProfile:
        """隔离示例命名空间，校验来源谱系与能力字段归属。"""
        is_example = self.origin == "example"
        if is_example != self.model_key.startswith("example-"):
            raise ValueError("example-* 为示例保留键，必须与 origin=example 一致")
        if is_example != (self.source == "example"):
            raise ValueError("示例来源必须为 example，真实模型不能使用此来源")
        if not is_example and self.source == "example":
            raise ValueError("只有 example 档案可以使用 example 来源")
        if self.origin == "derived":
            if not self.derived_from or not self.base_fingerprint:
                raise ValueError("derived 档案必须记录父档案与基线指纹")
            if self.derived_from == self.model_key:
                raise ValueError("派生档案不能以自身为父档案")
            if self.source in {"manual", "example"}:
                raise ValueError("派生档案必须保留上游来源")
        elif self.derived_from is not None or self.base_fingerprint is not None:
            raise ValueError("只有 derived 档案可以记录父档案与基线指纹")
        if self.origin == "upstream" and self.source in {"manual", "example"}:
            raise ValueError("upstream 档案必须来自外部来源")
        if self.annotations and set(self.annotations) & set(CAPABILITY_FIELDS):
            raise ValueError("annotations 不得重复声明能力字段")
        allowed = get_capabilities_class(self.model_type).model_fields
        invalid = [
            key
            for key in CAPABILITY_FIELDS
            if getattr(self, key) is not None and key not in allowed
        ]
        if invalid:
            raise ValueError(
                f"{self.model_type} 不支持这些能力字段: {', '.join(invalid)}"
            )
        if self.extra and set(self.extra) & set(CAPABILITY_FIELDS):
            raise ValueError("extra 不得重复声明已定义的能力字段")
        return self

    def declaration(self) -> dict[str, Any]:
        """只返回已声明能力，不物化默认值。"""
        values = self.model_dump(mode="json")
        return {
            key: values[key] for key in CAPABILITY_FIELDS if values[key] is not None
        }

    def to_capabilities(self) -> tuple[BaseCapabilities, dict[str, str]]:
        """生成有效能力和逐字段来源，原档案不变。"""
        values = self.declaration()
        cls = get_capabilities_class(self.model_type)
        caps = cls.model_validate(values)
        label = f"derived:{self.model_key}" if self.origin == "derived" else self.source
        return caps, {
            key: label if key in values else "default"
            for key in cls.model_fields
            if key != "capabilities_type"
        }

    @classmethod
    def from_capabilities(
        cls,
        *,
        model_key: str,
        display_name: str,
        model_type: str,
        capabilities: BaseCapabilities | dict[str, Any] | None = None,
        **metadata: Any,
    ) -> CapabilityProfile:
        """只读取显式能力字段，避免把对象的默认值固化为声明。"""
        if isinstance(capabilities, BaseCapabilities):
            values = capabilities.model_dump(mode="json", exclude_unset=True)
            discriminator = values.pop(
                "capabilities_type", capabilities.capabilities_type
            )
            if discriminator != get_capabilities_class(model_type)().capabilities_type:
                raise ValueError("能力对象与 model_type 不一致")
        else:
            values = dict(capabilities or {})
        return cls(
            model_key=model_key,
            display_name=display_name,
            model_type=model_type,
            **values,
            **metadata,
        )


def build_capabilities(
    model_type: str,
    profile: CapabilityProfile | None,
) -> tuple[BaseCapabilities, dict[str, str]]:
    """生成任意类型有效能力；无档案时仅使用类型默认值。"""
    model_type = normalize_model_type(model_type)
    if profile is not None:
        if profile.model_type != model_type:
            raise ValueError("能力档案与绑定类型不一致")
        return profile.to_capabilities()
    caps = get_capabilities_class(model_type)()
    return caps, {
        key: "default" for key in type(caps).model_fields if key != "capabilities_type"
    }


def validate_capability_filter(expression: str) -> None:
    """验证能力筛选表达式，未知条件明确报错。"""
    if expression in {key for key in SCALAR_FIELDS if key.startswith("supports_")}:
        return
    parts = expression.split(":")
    if len(parts) == 2 and parts[0] in {"input_modality", "output_modality"}:
        Modality(parts[1])
        return
    raise ValueError(f"未知能力筛选条件: {expression}")


def matches_capability(caps: BaseCapabilities, expression: str) -> bool:
    """按有效能力筛选，类型专属字段缺失时视为不支持。"""
    validate_capability_filter(expression)
    if ":" in expression:
        direction, modality = expression.split(":")
        values = getattr(caps, direction.replace("_modality", "_modalities"), [])
        return Modality(modality) in values
    return getattr(caps, expression, False) is True
