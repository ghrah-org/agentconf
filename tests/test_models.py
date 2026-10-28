# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""测试 Pydantic 数据模型"""

import pytest

from agentconf.models.base import Modality, ProviderType
from agentconf.models.capabilities import (
    EmbeddingCapabilities,
    GenericCapabilities,
    ImageGenCapabilities,
    LLMCapabilities,
    RerankCapabilities,
    STTCapabilities,
    TTSCapabilities,
    get_capabilities_class,
)
from agentconf.models.provider import Provider, ProviderCreate


# ─── 基础类型测试 ─────────────────────────────────────


class TestProviderType:
    def test_valid_types(self):
        assert ProviderType.OPENAI == "openai"
        assert ProviderType.ANTHROPIC == "anthropic"
        assert ProviderType.CUSTOM == "custom"

    def test_from_string(self):
        assert ProviderType("openai") == ProviderType.OPENAI

    def test_invalid_type(self):
        with pytest.raises(ValueError):
            ProviderType("invalid")


class TestModality:
    def test_modality_values(self):
        assert Modality.TEXT == "text"
        assert Modality.IMAGE == "image"
        assert Modality.AUDIO == "audio"
        assert Modality.VIDEO == "video"
        assert Modality.DOCUMENT_PDF == "document_pdf"
        assert Modality.EMBEDDING == "embedding"
        assert Modality.SCORE == "score"

    def test_modality_from_string(self):
        assert Modality("text") == Modality.TEXT
        assert Modality("image") == Modality.IMAGE


# ─── Provider 测试 ────────────────────────────────────


class TestProviderCreate:
    def test_valid_create(self):
        pc = ProviderCreate(
            name="test",
            provider_type=ProviderType.OPENAI,
            base_url="https://api.openai.com/v1",
            api_key="sk-test",
        )
        assert pc.name == "test"
        assert pc.api_key.get_secret_value() == "sk-test"

    def test_empty_name(self):
        with pytest.raises(ValueError):
            ProviderCreate(
                name="",
                provider_type="openai",
                base_url="https://api.openai.com/v1",
                api_key="sk-test",
            )


class TestProvider:
    def test_valid_provider(self):
        p = Provider(
            name="test",
            provider_type="openai",
            base_url="https://api.openai.com/v1",
            api_key="sk-test-key-value",
        )
        assert p.name == "test"
        assert p.id is not None  # auto-generated UUID

    def test_auto_uuid(self):
        p1 = Provider(
            name="p1",
            provider_type="openai",
            base_url="https://api.openai.com/v1",
            api_key="sk-key-1",
        )
        p2 = Provider(
            name="p2",
            provider_type="anthropic",
            base_url="https://api.anthropic.com",
            api_key="sk-key-2",
        )
        assert p1.id != p2.id


# ─── Capabilities 测试 ────────────────────────────────


class TestLLMCapabilities:
    def test_defaults(self):
        caps = LLMCapabilities()
        assert caps.input_modalities == [Modality.TEXT]
        assert caps.output_modalities == [Modality.TEXT]
        assert caps.supports_streaming is True
        assert caps.supports_function_calling is True
        assert caps.supports_reasoning is True
        assert caps.supports_vision is False
        assert caps.supports_json_mode is False
        assert caps.max_context_length == 1_000_000
        assert caps.max_output_length is None

    def test_custom_values(self):
        caps = LLMCapabilities(
            input_modalities=[Modality.TEXT, Modality.IMAGE],
            output_modalities=[Modality.TEXT],
            supports_vision=True,
            supports_function_calling=True,
            max_context_length=128000,
        )
        assert Modality.IMAGE in caps.input_modalities
        assert caps.supports_vision is True
        assert caps.max_context_length == 128000

    def test_from_dict(self):
        caps = LLMCapabilities.model_validate(
            {
                "supports_vision": True,
                "max_context_length": 128000,
            }
        )
        assert caps.supports_vision is True
        assert caps.max_context_length == 128000

    def test_serialization_roundtrip(self):
        caps = LLMCapabilities(
            input_modalities=[Modality.TEXT, Modality.IMAGE],
            supports_vision=True,
            max_context_length=128000,
        )
        data = caps.model_dump(mode="json")
        restored = LLMCapabilities.model_validate(data)
        assert restored.supports_vision is True
        assert restored.max_context_length == 128000
        assert Modality.IMAGE in restored.input_modalities


class TestEmbeddingCapabilities:
    def test_defaults(self):
        caps = EmbeddingCapabilities()
        assert caps.output_modalities == [Modality.EMBEDDING]
        assert caps.dimensions is None
        assert caps.max_input_length is None

    def test_custom(self):
        caps = EmbeddingCapabilities(dimensions=1536, max_input_length=8192)
        assert caps.dimensions == 1536


class TestRerankCapabilities:
    def test_defaults(self):
        caps = RerankCapabilities()
        assert caps.output_modalities == [Modality.SCORE]

    def test_custom(self):
        caps = RerankCapabilities(top_n=5, max_input_length=4096)
        assert caps.top_n == 5


class TestTTSCapabilities:
    def test_defaults(self):
        caps = TTSCapabilities()
        assert caps.output_modalities == [Modality.AUDIO]
        assert caps.languages is None
        assert caps.voices is None

    def test_custom(self):
        caps = TTSCapabilities(languages=["zh", "en"], voices=["alloy", "echo"])
        assert "zh" in caps.languages


class TestSTTCapabilities:
    def test_defaults(self):
        caps = STTCapabilities()
        assert caps.input_modalities == [Modality.AUDIO]
        assert caps.output_modalities == [Modality.TEXT]

    def test_custom(self):
        caps = STTCapabilities(languages=["zh", "en", "ja"])
        assert len(caps.languages) == 3


class TestImageGenCapabilities:
    def test_defaults(self):
        caps = ImageGenCapabilities()
        assert caps.output_modalities == [Modality.IMAGE]
        assert caps.supported_sizes is None

    def test_custom(self):
        caps = ImageGenCapabilities(supported_sizes=["1024x1024", "512x512"])
        assert "1024x1024" in caps.supported_sizes


class TestGenericCapabilities:
    def test_defaults(self):
        caps = GenericCapabilities()
        assert caps.output_modalities == [Modality.TEXT]


class TestGetCapabilitiesClass:
    def test_known_types(self):
        assert get_capabilities_class("llm") is LLMCapabilities
        assert get_capabilities_class("embedding") is EmbeddingCapabilities
        assert get_capabilities_class("rerank") is RerankCapabilities
        assert get_capabilities_class("tts") is TTSCapabilities
        assert get_capabilities_class("stt") is STTCapabilities
        assert get_capabilities_class("asr") is STTCapabilities
        assert get_capabilities_class("image_gen") is ImageGenCapabilities

    def test_unknown_type_returns_generic(self):
        assert get_capabilities_class("unknown_type") is GenericCapabilities
        assert get_capabilities_class("video_gen") is GenericCapabilities
