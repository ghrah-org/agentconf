# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""模型能力标记 — 按 model_type 差异化的能力描述"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Discriminator, Field, Tag

from agentconf.models.base import Modality


def _get_capabilities_type(v: object) -> str:
    """判别器：从对象或 dict 中提取 capabilities_type 字段"""
    if isinstance(v, dict):
        return v.get("capabilities_type", "generic")
    return getattr(v, "capabilities_type", "generic")


# ---- 通用基类 ----

class BaseCapabilities(BaseModel):
    """所有 capabilities 的基类"""
    capabilities_type: str = Field(default="generic", description="能力类型判别器")
    input_modalities: list[Modality] = Field(
        default_factory=lambda: [Modality.TEXT],
        description="支持的输入模态",
    )
    extra: dict | None = Field(None, description="扩展能力标记")


# ---- LLM ----

class LLMCapabilities(BaseCapabilities):
    """大语言模型能力"""
    capabilities_type: Literal["llm"] = "llm"
    output_modalities: list[Modality] = Field(
        default_factory=lambda: [Modality.TEXT],
        description="支持的输出模态",
    )
    supports_streaming: bool = Field(default=True, description="是否支持流式输出")
    supports_function_calling: bool = Field(default=True, description="是否支持函数调用")
    supports_reasoning: bool = Field(default=True, description="是否支持推理")
    supports_vision: bool = Field(default=False, description="是否支持视觉理解")
    supports_json_mode: bool = Field(default=False, description="是否支持 JSON 模式")
    max_context_length: int | None = Field(1_000_000, description="最大上下文长度（tokens）")
    max_output_length: int | None = Field(None, description="最大输出长度（tokens）")


# ---- Embedding ----

class EmbeddingCapabilities(BaseCapabilities):
    """向量嵌入模型能力"""
    capabilities_type: Literal["embedding"] = "embedding"
    output_modalities: list[Modality] = Field(
        default_factory=lambda: [Modality.EMBEDDING],
        description="支持的输出模态",
    )
    dimensions: int | None = Field(None, description="向量维度")
    max_input_length: int | None = Field(None, description="最大输入长度（tokens）")


# ---- Rerank ----

class RerankCapabilities(BaseCapabilities):
    """重排序模型能力"""
    capabilities_type: Literal["rerank"] = "rerank"
    output_modalities: list[Modality] = Field(
        default_factory=lambda: [Modality.SCORE],
        description="支持的输出模态",
    )
    max_input_length: int | None = Field(None, description="最大输入长度（tokens）")
    top_n: int | None = Field(None, description="最大返回文档数")


# ---- TTS ----

class TTSCapabilities(BaseCapabilities):
    """文本转语音模型能力"""
    capabilities_type: Literal["tts"] = "tts"
    output_modalities: list[Modality] = Field(
        default_factory=lambda: [Modality.AUDIO],
        description="支持的输出模态",
    )
    languages: list[str] | None = Field(None, description="支持的语言列表")
    voices: list[str] | None = Field(None, description="支持的音色列表")


# ---- STT / ASR ----

class STTCapabilities(BaseCapabilities):
    """语音转文本模型能力"""
    capabilities_type: Literal["stt"] = "stt"
    input_modalities: list[Modality] = Field(
        default_factory=lambda: [Modality.AUDIO],
        description="支持的输入模态",
    )
    output_modalities: list[Modality] = Field(
        default_factory=lambda: [Modality.TEXT],
        description="支持的输出模态",
    )
    languages: list[str] | None = Field(None, description="支持的语言列表")


# ---- Image Generation ----

class ImageGenCapabilities(BaseCapabilities):
    """图像生成模型能力"""
    capabilities_type: Literal["image_gen"] = "image_gen"
    output_modalities: list[Modality] = Field(
        default_factory=lambda: [Modality.IMAGE],
        description="支持的输出模态",
    )
    supported_sizes: list[str] | None = Field(None, description="支持的图像尺寸列表")


# ---- 通用兜底 ----

class GenericCapabilities(BaseCapabilities):
    """通用能力标记（未知模型类型的兜底）"""
    capabilities_type: Literal["generic"] = "generic"
    output_modalities: list[Modality] = Field(
        default_factory=lambda: [Modality.TEXT],
        description="支持的输出模态",
    )


# ---- 判别联合类型 ----

Capabilities = Annotated[
    Union[
        Annotated[LLMCapabilities, Tag("llm")],
        Annotated[EmbeddingCapabilities, Tag("embedding")],
        Annotated[RerankCapabilities, Tag("rerank")],
        Annotated[TTSCapabilities, Tag("tts")],
        Annotated[STTCapabilities, Tag("stt")],
        Annotated[ImageGenCapabilities, Tag("image_gen")],
        Annotated[GenericCapabilities, Tag("generic")],
    ],
    Discriminator(_get_capabilities_type),
]

# capability_type 字符串 → 对应类的映射
_CAPABILITIES_TYPE_CLASS_MAP: dict[str, type[BaseCapabilities]] = {
    "llm": LLMCapabilities,
    "embedding": EmbeddingCapabilities,
    "rerank": RerankCapabilities,
    "tts": TTSCapabilities,
    "stt": STTCapabilities,
    "image_gen": ImageGenCapabilities,
    "generic": GenericCapabilities,
}

# model_type → capabilities_type 的映射（用于根据 model_type 推断默认 capabilities 类）
MODEL_TYPE_CAPABILITIES_MAP: dict[str, type[BaseCapabilities]] = {
    "llm": LLMCapabilities,
    "embedding": EmbeddingCapabilities,
    "rerank": RerankCapabilities,
    "tts": TTSCapabilities,
    "stt": STTCapabilities,
    "asr": STTCapabilities,
    "image_gen": ImageGenCapabilities,
}


def get_capabilities_class(model_type: str) -> type[BaseCapabilities]:
    """根据 model_type 获取对应的 Capabilities 类"""
    return MODEL_TYPE_CAPABILITIES_MAP.get(model_type, GenericCapabilities)
