# SPDX-License-Identifier: Apache-2.0
"""三个连接资源的事务 CRUD；领域校验与并发检查共用。"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from agentconf.exceptions import (
    ProviderNotFoundError,
    EndpointNotFoundError,
    MCPServerNotFoundError,
    ResourceAlreadyExistsError,
    ResourceInUseError,
    RevisionConflictError,
    CapabilityTypeMismatchError,
)
from agentconf.models.provider import Provider, ProviderCreate
from agentconf.models.model_endpoint import ModelEndpoint, ModelEndpointCreate
from agentconf.models.mcp_server import MCPServer, MCPServerCreate, GenericMCPConnection
from agentconf.models.secure import reveal, MASK, merge_masked


@contextmanager
def transaction(conn):
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


SPEC = {
    "providers": (
        Provider,
        ProviderCreate,
        ProviderNotFoundError,
        {"connection_options"},
    ),
    "model_endpoints": (
        ModelEndpoint,
        ModelEndpointCreate,
        EndpointNotFoundError,
        set(),
    ),
    "mcp_servers": (MCPServer, MCPServerCreate, MCPServerNotFoundError, {"settings"}),
}


def resource_data(model):
    """用于受控持久化的资源形状；自定义 MCP 设置仍是原始对象。"""
    data = reveal(model)
    if isinstance(model, (MCPServer, MCPServerCreate)) and isinstance(
        model.settings, GenericMCPConnection
    ):
        data["settings"] = reveal(model.settings.options)
    return data


def editable_data(model, *, provider_name=None, show_secrets=False):
    """只给出可编辑字段，默认以掩码保留敏感值。"""
    if isinstance(model, Provider):
        data = resource_data(model) if show_secrets else model.model_dump(mode="json")
        allowed = ProviderCreate.model_fields
    elif isinstance(model, ModelEndpoint):
        data = resource_data(model)
        data["provider_name"] = provider_name
        allowed = ModelEndpointCreate.model_fields
    else:
        data = resource_data(model)
        if not show_secrets:
            if isinstance(model.settings, GenericMCPConnection):
                data["settings"] = MASK
            else:
                data["settings"] = model.settings.model_dump(mode="json")
        allowed = MCPServerCreate.model_fields
    return {key: value for key, value in data.items() if key in allowed}


class ResourceRepository:
    def __init__(self, conn, table):
        self.conn, self.table = conn, table
        self.model, self.create_model, self.missing, self.json_fields = SPEC[table]

    def decode(self, row):
        data = dict(row)
        if self.table == "mcp_servers":
            data["settings"] = data.pop("settings_json")
        for field in self.json_fields:
            data[field] = json.loads(data[field])
        return self.model.model_validate(data)

    def get(self, name):
        return self._get("name", name)

    def get_by_id(self, value):
        return self._get("id", value)

    def _get(self, field, value):
        row = self.conn.execute(
            f"SELECT * FROM {self.table} WHERE {field}=?", (value,)
        ).fetchone()
        if row is None:
            raise self.missing(f"{self.table} 资源未找到")
        return self.decode(row)

    def list(self):
        return [
            self.decode(row)
            for row in self.conn.execute(f"SELECT * FROM {self.table} ORDER BY name")
        ]

    def provider_references(self, provider_id):
        return [
            r[0]
            for r in self.conn.execute(
                "SELECT name FROM model_endpoints WHERE provider_id=? ORDER BY name",
                (provider_id,),
            )
        ]

    def bind(self, data):
        if self.table != "model_endpoints":
            return data
        provider = self.conn.execute(
            "SELECT id FROM providers WHERE name=?", (data.pop("provider_name"),)
        ).fetchone()
        if provider is None:
            raise ProviderNotFoundError("指定的 Provider 不存在")
        data["provider_id"] = provider[0]
        key = data.get("capabilities_key")
        if key is not None:
            row = self.conn.execute(
                "SELECT model_type FROM model_capabilities WHERE model_key=?", (key,)
            ).fetchone()
            if row is None:
                from agentconf.exceptions import CapabilityNotFoundError

                raise CapabilityNotFoundError("能力档案不存在")
            if row[0] != data["model_type"]:
                raise CapabilityTypeMismatchError("模型端点与能力档案用途不一致")
        return data

    def create(self, payload):
        payload = merge_masked(payload, {}, {})
        validated = self.create_model.model_validate(payload)
        with transaction(self.conn):
            model = self.model.model_validate(self.bind(resource_data(validated)))
            data = self.encode(model)
            try:
                self.conn.execute(
                    f"INSERT INTO {self.table} ({','.join(data)}) VALUES ({','.join('?' for _ in data)})",
                    tuple(data.values()),
                )
            except sqlite3.IntegrityError:
                raise ResourceAlreadyExistsError("资源已存在或违反引用约束") from None
            return model

    def encode(self, model):
        data = resource_data(model)
        for field in self.json_fields:
            data[field] = json.dumps(
                data[field], ensure_ascii=False, sort_keys=True, allow_nan=False
            )
        if self.table == "mcp_servers":
            data["settings_json"] = data.pop("settings")
        return data

    def update(self, name, patch, expected_revision, expected_id=None):
        with transaction(self.conn):
            old = self.get(name)
            if old.revision != expected_revision or (
                expected_id is not None and old.id != expected_id
            ):
                raise RevisionConflictError("资源已变化，请重新预览")
            provider_name = None
            if self.table == "model_endpoints":
                provider_name = self.conn.execute(
                    "SELECT name FROM providers WHERE id=?", (old.provider_id,)
                ).fetchone()[0]
            values = editable_data(old, provider_name=provider_name, show_secrets=True)
            patch = merge_masked(
                patch, values, editable_data(old, provider_name=provider_name)
            )
            values.update(patch)
            validated = self.create_model.model_validate(values)
            candidate = self.model.model_validate(
                {
                    **self.bind(resource_data(validated)),
                    "id": old.id,
                    "revision": old.revision + 1,
                    "created_at": old.created_at,
                    "updated_at": datetime.now(timezone.utc),
                }
            )
            data = self.encode(candidate)
            columns = [key for key in data if key != "id"]
            try:
                self.conn.execute(
                    f"UPDATE {self.table} SET {','.join(k + '=?' for k in columns)} WHERE id=?",
                    [data[k] for k in columns] + [old.id],
                )
            except sqlite3.IntegrityError:
                raise ResourceAlreadyExistsError("资源名称重复或引用不合法") from None
            return candidate

    def delete(self, name, expected_revision, expected_id=None):
        with transaction(self.conn):
            old = self.get(name)
            if old.revision != expected_revision or (
                expected_id is not None and old.id != expected_id
            ):
                raise RevisionConflictError("资源已变化，请重新预览")
            if self.table == "providers" and self.provider_references(old.id):
                raise ResourceInUseError("Provider 仍被模型端点引用")
            self.conn.execute(f"DELETE FROM {self.table} WHERE id=?", (old.id,))
