# SPDX-License-Identifier: Apache-2.0
"""接口格式的静态提示，不导入客户端 SDK，不执行请求。"""

from agentconf.models.secure import SafeModel


class SDKHint(SafeModel):
    distribution: str
    import_name: str


_HINTS = {
    "openai.chat_completions": ("openai", "openai"),
    "openai.responses": ("openai", "openai"),
    "openai.embeddings": ("openai", "openai"),
    "openai.audio.transcriptions": ("openai", "openai"),
    "openai.audio.speech": ("openai", "openai"),
    "anthropic.messages": ("anthropic", "anthropic"),
    "google.generate_content": ("google-genai", "google.genai"),
    "ollama.chat": ("ollama", "ollama"),
}


def sdk_hint(api_format: str) -> SDKHint | None:
    """仅返回建议包名；未知接口格式原样交给下游。"""
    hint = _HINTS.get(api_format)
    return SDKHint(distribution=hint[0], import_name=hint[1]) if hint else None
