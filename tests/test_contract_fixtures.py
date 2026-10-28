"""供消费者使用的固定解析与导出样例必须保持可读取。"""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from agentconf import ResolvedModelEndpoint, ResolvedMCPServer
from agentconf.migrations import checksum

FIXTURES = Path(__file__).parent / "fixtures"


def test_resolved_contract_examples():
    data = json.loads((FIXTURES / "resolved_endpoints.json").read_text())
    results = {
        key: ResolvedModelEndpoint.model_validate(value) for key, value in data.items()
    }
    assert results["chat"].connection.api_format == "openai.chat_completions"
    assert results["responses"].connection.api_format == "openai.responses"
    assert results["deepseek-chat"].connection.provider_type == "deepseek"
    assert results["chat"].metadata.origin == "example"
    assert results["responses"].metadata is None
    for result in results.values():
        assert result.connection.api_key.get_secret_value() == "EXAMPLE_ONLY"
        assert "EXAMPLE_ONLY" not in result.model_dump_json()


def test_agent_export_preserves_shared_endpoint_and_distinct_parameters():
    data = json.loads((FIXTURES / "agent_export_v1.json").read_text())
    digest = data.pop("checksum")
    assert checksum(data) == digest
    assert data["export_version"] == 1
    first, second = data["agents"]
    assert first["endpoint_id"] == second["endpoint_id"]
    assert first["agent_id"] != second["agent_id"]
    assert first["request_params"]["temperature"] == 0
    assert first["request_params"]["extra_params"] == {"stream": False, "stop": None}
    assert first["request_params"] != second["request_params"]


def test_known_resolved_transport_rejects_generic_connection():
    with pytest.raises(ValidationError):
        ResolvedMCPServer.model_validate(
            dict(
                server_id="example",
                name="example",
                revision=1,
                transport="stdio",
                connection={"options": {"command": "python"}},
            )
        )
