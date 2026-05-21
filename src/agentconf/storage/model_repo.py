# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""ModelInstance 仓储层 - CRUD 操作"""

from __future__ import annotations

import json
import sqlite3
import warnings
from datetime import datetime, timezone

from pydantic import SecretStr, ValidationError

from agentconf.exceptions import (
    ModelAlreadyExistsError,
    ModelNotFoundError,
    ProviderNotFoundError,
)
from agentconf.models.capabilities import BaseCapabilities, get_capabilities_class
from agentconf.models.model_instance import ModelInstance


class ModelRepository:
    """ModelInstance 数据访问对象"""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def create(
        self,
        id: str,
        provider_id: str,
        name: str,
        model_name: str,
        model_type: str = "llm",
        base_url: str | None = None,
        api_key: str | None = None,
        capabilities: BaseCapabilities | None = None,
    ) -> ModelInstance:
        """创建 ModelInstance"""
        # 验证 Provider 存在
        provider_row = self.conn.execute(
            "SELECT id FROM providers WHERE id = ?", (provider_id,)
        ).fetchone()
        if provider_row is None:
            raise ProviderNotFoundError(f"Provider (id={provider_id}) 未找到")

        capabilities_json = capabilities.model_dump_json() if capabilities else None
        now = datetime.now(timezone.utc).isoformat()
        try:
            self.conn.execute(
                """
                INSERT INTO model_instances
                    (id, provider_id, name, model_name, model_type, base_url, api_key, capabilities, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (id, provider_id, name, model_name, model_type, base_url, api_key, capabilities_json, now, now),
            )
            self.conn.commit()
        except sqlite3.IntegrityError:
            raise ModelAlreadyExistsError(
                f"Model Instance '{name}' (provider_id={provider_id}) 已存在"
            )

        return self.get_by_name(name)

    def get_by_name(self, name: str) -> ModelInstance:
        """按名称查询 ModelInstance"""
        row = self.conn.execute(
            "SELECT * FROM model_instances WHERE name = ?", (name,)
        ).fetchone()
        if row is None:
            raise ModelNotFoundError(f"Model Instance '{name}' 未找到")
        return self._row_to_model(row)

    def get_by_id(self, id: str) -> ModelInstance:
        """按 ID 查询 ModelInstance"""
        row = self.conn.execute(
            "SELECT * FROM model_instances WHERE id = ?", (id,)
        ).fetchone()
        if row is None:
            raise ModelNotFoundError(f"Model Instance (id={id}) 未找到")
        return self._row_to_model(row)

    def list_by_provider(self, provider_id: str) -> list[ModelInstance]:
        """列出指定 Provider 下的所有 ModelInstance"""
        rows = self.conn.execute(
            "SELECT * FROM model_instances WHERE provider_id = ? ORDER BY created_at",
            (provider_id,),
        ).fetchall()
        return [self._row_to_model(row) for row in rows]

    def list_all(self) -> list[ModelInstance]:
        """列出所有 ModelInstance"""
        rows = self.conn.execute(
            "SELECT * FROM model_instances ORDER BY created_at"
        ).fetchall()
        return [self._row_to_model(row) for row in rows]

    def list_by_type(self, model_type: str) -> list[ModelInstance]:
        """按模型类型筛选 ModelInstance"""
        rows = self.conn.execute(
            "SELECT * FROM model_instances WHERE model_type = ? ORDER BY created_at",
            (model_type,),
        ).fetchall()
        return [self._row_to_model(row) for row in rows]

    def list_by_capability(self, capability_filter: str) -> list[ModelInstance]:
        """按能力筛选 ModelInstance（业务层筛选）

        Args:
            capability_filter: 能力筛选条件，如 'supports_vision'、'input_modality:image'
        """
        all_models = self.list_all()
        result = []

        for model in all_models:
            if model.capabilities is None:
                continue

            caps = model.capabilities
            if capability_filter == "supports_vision":
                if hasattr(caps, "supports_vision") and getattr(caps, "supports_vision"):
                    result.append(model)
            elif capability_filter == "supports_function_calling":
                if hasattr(caps, "supports_function_calling") and getattr(caps, "supports_function_calling"):
                    result.append(model)
            elif capability_filter == "supports_streaming":
                if hasattr(caps, "supports_streaming") and getattr(caps, "supports_streaming"):
                    result.append(model)
            elif capability_filter.startswith("input_modality:"):
                modality = capability_filter.split(":", 1)[1]
                if any(m.value == modality for m in caps.input_modalities):
                    result.append(model)
            elif capability_filter.startswith("output_modality:"):
                modality = capability_filter.split(":", 1)[1]
                if hasattr(caps, "output_modalities") and any(
                    m.value == modality for m in caps.output_modalities
                ):
                    result.append(model)
            else:
                result.append(model)

        return result

    def update(self, name: str, **kwargs) -> ModelInstance:
        """更新 ModelInstance 字段"""
        model = self.get_by_name(name)

        allowed_fields = {"model_name", "model_type", "base_url", "api_key", "capabilities"}
        updates = {}
        for k, v in kwargs.items():
            if k in allowed_fields and v is not None:
                if k == "capabilities" and isinstance(v, BaseCapabilities):
                    updates[k] = v.model_dump_json()
                else:
                    updates[k] = v

        if not updates:
            return model

        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [model.id]

        self.conn.execute(
            f"UPDATE model_instances SET {set_clause} WHERE id = ?", values
        )
        self.conn.commit()
        return self.get_by_name(name)

    def delete(self, name: str) -> None:
        """删除 ModelInstance"""
        model = self.get_by_name(name)
        self.conn.execute("DELETE FROM model_instances WHERE id = ?", (model.id,))
        self.conn.commit()

    @staticmethod
    def _deserialize_capabilities(
        raw: str | None, model_type: str = "llm"
    ) -> BaseCapabilities | None:
        """反序列化 capabilities JSON"""
        if raw is None:
            return None
        try:
            caps_cls = get_capabilities_class(model_type)
            return caps_cls.model_validate_json(raw)
        except (ValidationError, json.JSONDecodeError) as e:
            warnings.warn(
                f"Failed to deserialize capabilities for model_type={model_type!r}: {e}",
                RuntimeWarning,
                stacklevel=2,
            )
            return None

    @staticmethod
    def _row_to_model(row: sqlite3.Row) -> ModelInstance:
        """将数据库行转换为 ModelInstance 模型"""
        raw_caps = row["capabilities"] if "capabilities" in row.keys() else None
        model_type = row["model_type"] if "model_type" in row.keys() else "llm"
        capabilities = ModelRepository._deserialize_capabilities(raw_caps, model_type)

        return ModelInstance(
            id=row["id"],
            provider_id=row["provider_id"],
            name=row["name"],
            model_name=row["model_name"],
            model_type=model_type,
            base_url=row["base_url"],
            api_key=SecretStr(row["api_key"]) if row["api_key"] else None,
            capabilities=capabilities,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
