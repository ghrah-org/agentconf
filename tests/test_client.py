# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""测试存储层和客户端集成"""

import pytest

from agentconf.client import AgentsConfig
from agentconf.exceptions import (
    AgentAlreadyExistsError,
    AgentNotFoundError,
    ModelAlreadyExistsError,
    ModelNotFoundError,
    ProviderAlreadyExistsError,
    ProviderNotFoundError,
)


@pytest.fixture
def db_path(tmp_path):
    """临时数据库路径"""
    return str(tmp_path / "test_config.db")


@pytest.fixture
def config(db_path):
    """创建测试用 AgentsConfig 实例"""
    cfg = AgentsConfig(db_path=db_path)
    yield cfg
    cfg.close()


@pytest.fixture
def config_with_provider(config):
    """预创建 Provider 的配置"""
    config.create_provider(
        name="openai",
        provider_type="openai",
        base_url="https://api.openai.com/v1",
        api_key="sk-test-key-123",
    )
    return config


@pytest.fixture
def config_with_model(config_with_provider):
    """预创建 Model Instance 的配置"""
    config_with_provider.create_model(
        provider_name="openai",
        name="gpt4o",
        model_name="gpt-4o",
    )
    return config_with_provider


@pytest.fixture
def config_with_agent(config_with_model):
    """预创建 Agent 的配置"""
    config_with_model.create_agent(
        name="reviewer",
        model_name="gpt4o",
        temperature=0.3,
        top_p=0.9,
    )
    return config_with_model


# ─── Provider 测试 ───────────────────────────────────


class TestProviderCRUD:
    def test_create_provider(self, config):
        provider = config.create_provider(
            name="anthropic",
            provider_type="anthropic",
            base_url="https://api.anthropic.com",
            api_key="sk-ant-test",
        )
        assert provider.name == "anthropic"
        assert provider.provider_type == "anthropic"
        assert provider.base_url == "https://api.anthropic.com"

    def test_create_duplicate_provider(self, config_with_provider):
        with pytest.raises(ProviderAlreadyExistsError):
            config_with_provider.create_provider(
                name="openai",
                provider_type="openai",
                base_url="https://api.openai.com/v1",
                api_key="sk-another",
            )

    def test_get_provider(self, config_with_provider):
        provider = config_with_provider.get_provider("openai")
        assert provider.name == "openai"

    def test_get_nonexistent_provider(self, config):
        with pytest.raises(ProviderNotFoundError):
            config.get_provider("nonexistent")

    def test_list_providers(self, config_with_provider):
        config_with_provider.create_provider(
            name="anthropic",
            provider_type="anthropic",
            base_url="https://api.anthropic.com",
            api_key="sk-ant-test",
        )
        providers = config_with_provider.list_providers()
        assert len(providers) == 2
        names = {p.name for p in providers}
        assert names == {"openai", "anthropic"}

    def test_update_provider(self, config_with_provider):
        updated = config_with_provider.update_provider(
            "openai", base_url="https://new-api.openai.com/v1"
        )
        assert updated.base_url == "https://new-api.openai.com/v1"

    def test_update_provider_api_key(self, config_with_provider):
        _updated = config_with_provider.update_provider("openai", api_key="sk-new-key")
        # 验证通过解析获取新的 key
        config_with_provider.create_model(
            provider_name="openai", name="test-model", model_name="gpt-4o"
        )
        resolved = config_with_provider.resolve_model("test-model")
        assert resolved.api_key == "sk-new-key"

    def test_delete_provider(self, config_with_provider):
        config_with_provider.delete_provider("openai")
        with pytest.raises(ProviderNotFoundError):
            config_with_provider.get_provider("openai")

    def test_delete_provider_cascades(self, config_with_model):
        """删除 Provider 应级联删除 Model 和 Agent"""
        config_with_model.create_agent(name="test-agent", model_name="gpt4o")
        config_with_model.delete_provider("openai")
        with pytest.raises(ModelNotFoundError):
            config_with_model.get_model("gpt4o")
        with pytest.raises(AgentNotFoundError):
            config_with_model.get_agent("test-agent")


# ─── Model Instance 测试 ───────────────────────────────


class TestModelCRUD:
    def test_create_model(self, config_with_provider):
        model = config_with_provider.create_model(
            provider_name="openai",
            name="gpt4o",
            model_name="gpt-4o",
        )
        assert model.name == "gpt4o"
        assert model.model_name == "gpt-4o"
        assert model.model_type == "llm"

    def test_create_model_with_type(self, config_with_provider):
        model = config_with_provider.create_model(
            provider_name="openai",
            name="text-embedding",
            model_name="text-embedding-3-large",
            model_type="embedding",
        )
        assert model.model_type == "embedding"

    def test_create_duplicate_model(self, config_with_model):
        with pytest.raises(ModelAlreadyExistsError):
            config_with_model.create_model(
                provider_name="openai",
                name="gpt4o",
                model_name="gpt-4o",
            )

    def test_get_model(self, config_with_model):
        model = config_with_model.get_model("gpt4o")
        assert model.name == "gpt4o"

    def test_list_models(self, config_with_model):
        config_with_model.create_model(
            provider_name="openai",
            name="gpt35",
            model_name="gpt-3.5-turbo",
        )
        models = config_with_model.list_models()
        assert len(models) == 2

    def test_list_models_by_provider(self, config_with_provider):
        config_with_provider.create_model(
            provider_name="openai", name="gpt4o", model_name="gpt-4o"
        )
        config_with_provider.create_provider(
            name="anthropic",
            provider_type="anthropic",
            base_url="https://api.anthropic.com",
            api_key="sk-ant-test",
        )
        config_with_provider.create_model(
            provider_name="anthropic", name="claude35", model_name="claude-3.5-sonnet"
        )

        openai_models = config_with_provider.list_models(provider_name="openai")
        assert len(openai_models) == 1
        assert openai_models[0].name == "gpt4o"

        all_models = config_with_provider.list_models()
        assert len(all_models) == 2

    def test_list_models_by_type(self, config_with_provider):
        config_with_provider.create_model(
            provider_name="openai", name="gpt4o", model_name="gpt-4o", model_type="llm"
        )
        config_with_provider.create_model(
            provider_name="openai",
            name="embed-3",
            model_name="text-embedding-3-large",
            model_type="embedding",
        )
        llm_models = config_with_provider.list_models(model_type="llm")
        assert len(llm_models) == 1
        assert llm_models[0].name == "gpt4o"

        embed_models = config_with_provider.list_models(model_type="embedding")
        assert len(embed_models) == 1
        assert embed_models[0].name == "embed-3"

    def test_update_model(self, config_with_model):
        updated = config_with_model.update_model("gpt4o", model_name="gpt-4o-mini")
        assert updated.model_name == "gpt-4o-mini"

    def test_delete_model(self, config_with_model):
        config_with_model.delete_model("gpt4o")
        with pytest.raises(ModelNotFoundError):
            config_with_model.get_model("gpt4o")


# ─── 能力过滤测试 ──────────────────────────────────────


# ─── LLM 默认能力测试 ──────────────────────────────────


# ─── 多模型类型测试 ────────────────────────────────────


# ─── Agent 测试 ──────────────────────────────────────


class TestAgentCRUD:
    def test_create_agent(self, config_with_model):
        agent = config_with_model.create_agent(
            name="reviewer",
            model_name="gpt4o",
            temperature=0.3,
            top_p=0.9,
        )
        assert agent.name == "reviewer"
        assert agent.temperature == 0.3
        assert agent.top_p == 0.9
        assert agent.top_k is None

    def test_create_agent_with_extra_params(self, config_with_model):
        agent = config_with_model.create_agent(
            name="custom-agent",
            model_name="gpt4o",
            extra_params={"stop": ["\n"], "presence_penalty": 0.6},
        )
        assert agent.extra_params == {"stop": ["\n"], "presence_penalty": 0.6}

    def test_create_duplicate_agent(self, config_with_agent):
        with pytest.raises(AgentAlreadyExistsError):
            config_with_agent.create_agent(
                name="reviewer",
                model_name="gpt4o",
            )

    def test_get_agent(self, config_with_agent):
        agent = config_with_agent.get_agent("reviewer")
        assert agent.name == "reviewer"

    def test_list_agents(self, config_with_model):
        config_with_model.create_agent(name="agent1", model_name="gpt4o")
        config_with_model.create_agent(name="agent2", model_name="gpt4o")
        agents = config_with_model.list_agents()
        assert len(agents) == 2

    def test_update_agent(self, config_with_agent):
        updated = config_with_agent.update_agent("reviewer", temperature=0.8, top_k=50)
        assert updated.temperature == 0.8
        assert updated.top_k == 50

    def test_update_agent_model(self, config_with_model):
        config_with_model.create_model(
            provider_name="openai", name="gpt35", model_name="gpt-3.5-turbo"
        )
        config_with_model.create_agent(name="test-agent", model_name="gpt4o")
        updated = config_with_model.update_agent("test-agent", model_name="gpt35")
        # 应该更新了 model_instance_id
        assert updated.model_instance_id == config_with_model.get_model("gpt35").id

    def test_delete_agent(self, config_with_agent):
        config_with_agent.delete_agent("reviewer")
        with pytest.raises(AgentNotFoundError):
            config_with_agent.get_agent("reviewer")


# ─── 配置解析测试 ────────────────────────────────────


class TestResolution:
    def test_resolve_model_basic(self, config_with_model):
        resolved = config_with_model.resolve_model("gpt4o")
        assert resolved.provider_name == "openai"
        assert resolved.provider_type == "openai"
        assert resolved.base_url == "https://api.openai.com/v1"
        assert resolved.api_key == "sk-test-key-123"
        assert resolved.model_name == "gpt-4o"
        assert resolved.model_type == "llm"

    def test_resolve_agent_full(self, config_with_agent):
        resolved = config_with_agent.resolve_agent("reviewer")
        assert resolved.agent_name == "reviewer"
        assert resolved.model.provider_name == "openai"
        assert resolved.model.api_key == "sk-test-key-123"
        assert resolved.model.model_name == "gpt-4o"
        assert resolved.temperature == 0.3
        assert resolved.top_p == 0.9
        assert resolved.top_k is None

    def test_resolve_agent_to_dict(self, config_with_agent):
        resolved = config_with_agent.resolve_agent("reviewer")
        d = resolved.model_dump()
        assert d["agent_name"] == "reviewer"
        assert d["model"]["model_name"] == "gpt-4o"
        assert d["temperature"] == 0.3


# ─── Agent 操作测试（model_name 参数） ──────────────────


class TestAgentModelName:
    def test_agent_create_with_model_name(self, config_with_model):
        """Agent 使用 model_name 参数创建"""
        agent = config_with_model.create_agent(
            name="test-agent",
            model_name="gpt4o",
        )
        assert agent.name == "test-agent"

    def test_agent_update_with_model_name(self, config_with_model):
        config_with_model.create_model(
            provider_name="openai", name="gpt35", model_name="gpt-3.5-turbo"
        )
        config_with_model.create_agent(name="test-agent", model_name="gpt4o")
        updated = config_with_model.update_agent("test-agent", model_name="gpt35")
        assert updated.model_instance_id == config_with_model.get_model("gpt35").id

    def test_delete_provider_cascades(self, config_with_model):
        """删除 Provider 级联删除关联 Model 和 Agent"""
        config_with_model.create_agent(name="test-agent", model_name="gpt4o")
        config_with_model.delete_provider("openai")
        with pytest.raises(ModelNotFoundError):
            config_with_model.get_model("gpt4o")


# ─── Context Manager 测试 ────────────────────────────


class TestContextManager:
    def test_context_manager(self, db_path):
        with AgentsConfig(db_path=db_path) as config:
            config.create_provider(
                name="test",
                provider_type="openai",
                base_url="https://api.openai.com/v1",
                api_key="sk-test",
            )
            assert len(config.list_providers()) == 1
