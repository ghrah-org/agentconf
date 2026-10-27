# SPDX-License-Identifier: Apache-2.0
"""纯模型绑定的数据访问；连接与能力由外键指定。"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from agentconf.exceptions import (
    CapabilityTypeMismatchError,
    ModelAlreadyExistsError,
    ModelNotFoundError,
    ProviderNotFoundError,
)
from agentconf.models.model_instance import ModelInstance
from agentconf.storage.catalog_repo import CatalogRepository


class ModelRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def _validate(self, model: ModelInstance) -> None:
        if (
            self.conn.execute(
                "SELECT id FROM providers WHERE id=?", (model.provider_id,)
            ).fetchone()
            is None
        ):
            raise ProviderNotFoundError(f"Provider 未找到: {model.provider_id}")
        if model.capabilities_key is not None:
            profile = CatalogRepository(self.conn).get(model.capabilities_key)
            if profile.model_type != model.model_type:
                raise CapabilityTypeMismatchError(
                    f"绑定 {model.model_type} 与档案 {profile.model_type} 类型不同"
                )

    def create(self, **values) -> ModelInstance:
        model = ModelInstance.model_validate(values)
        with self.conn:
            self._validate(model)
            data = model.model_dump(mode="json")
            try:
                self.conn.execute(
                    f"INSERT INTO model_instances ({', '.join(data)}) VALUES ({', '.join('?' for _ in data)})",
                    list(data.values()),
                )
            except sqlite3.IntegrityError as exc:
                raise ModelAlreadyExistsError(
                    f"模型绑定名称必须全局唯一: {model.name}"
                ) from exc
        return self.get_by_name(model.name)

    def get_by_name(self, name: str) -> ModelInstance:
        row = self.conn.execute(
            "SELECT * FROM model_instances WHERE name=?", (name,)
        ).fetchone()
        if row is None:
            raise ModelNotFoundError(f"模型绑定未找到: {name}")
        return ModelInstance.model_validate(dict(row))

    def get_by_id(self, id: str) -> ModelInstance:
        row = self.conn.execute(
            "SELECT * FROM model_instances WHERE id=?", (id,)
        ).fetchone()
        if row is None:
            raise ModelNotFoundError(f"模型绑定未找到: {id}")
        return ModelInstance.model_validate(dict(row))

    def list_all(self) -> list[ModelInstance]:
        return [
            ModelInstance.model_validate(dict(r))
            for r in self.conn.execute(
                "SELECT * FROM model_instances ORDER BY created_at, name"
            )
        ]

    def list_by_provider(self, provider_id: str) -> list[ModelInstance]:
        return [m for m in self.list_all() if m.provider_id == provider_id]

    def update(self, name: str, **values) -> ModelInstance:
        if set(values) - {"model_name", "model_type", "capabilities_key"}:
            raise TypeError("只可修改 model_name、model_type、capabilities_key")
        with self.conn:
            old = self.get_by_name(name)
            data = old.model_dump()
            data.update(values, updated_at=datetime.now(timezone.utc))
            model = ModelInstance.model_validate(data)
            self._validate(model)
            if values:
                data = model.model_dump(mode="json")
                self.conn.execute(
                    "UPDATE model_instances SET model_name=?, model_type=?, capabilities_key=?, updated_at=? WHERE id=?",
                    (
                        model.model_name,
                        model.model_type,
                        model.capabilities_key,
                        data["updated_at"],
                        model.id,
                    ),
                )
        return self.get_by_name(name)

    def delete(self, name: str) -> None:
        with self.conn:
            model = self.get_by_name(name)
            self.conn.execute("DELETE FROM model_instances WHERE id=?", (model.id,))
