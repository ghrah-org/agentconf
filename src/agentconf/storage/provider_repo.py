# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""Provider 仓储层 - CRUD 操作"""

import sqlite3
from datetime import datetime, timezone

from pydantic import SecretStr

from agentconf.exceptions import (
    ProviderAlreadyExistsError,
    ProviderNotFoundError,
)
from agentconf.models.provider import Provider


class ProviderRepository:
    """Provider 数据访问对象"""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def create(
        self,
        id: str,
        name: str,
        provider_type: str,
        base_url: str,
        api_key: str,
    ) -> Provider:
        """创建 Provider"""
        now = datetime.now(timezone.utc).isoformat()
        try:
            self.conn.execute(
                """
                INSERT INTO providers (id, name, provider_type, base_url, api_key, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (id, name, provider_type, base_url, api_key, now, now),
            )
            self.conn.commit()
        except sqlite3.IntegrityError:
            self.conn.rollback()
            raise ProviderAlreadyExistsError(f"Provider '{name}' 已存在")

        return self.get_by_name(name)

    def get_by_name(self, name: str) -> Provider:
        """按名称查询 Provider"""
        row = self.conn.execute(
            "SELECT * FROM providers WHERE name = ?", (name,)
        ).fetchone()
        if row is None:
            raise ProviderNotFoundError(f"Provider '{name}' 未找到")
        return self._row_to_model(row)

    def get_by_id(self, id: str) -> Provider:
        """按 ID 查询 Provider"""
        row = self.conn.execute(
            "SELECT * FROM providers WHERE id = ?", (id,)
        ).fetchone()
        if row is None:
            raise ProviderNotFoundError(f"Provider (id={id}) 未找到")
        return self._row_to_model(row)

    def list_all(self) -> list[Provider]:
        """列出所有 Provider"""
        rows = self.conn.execute(
            "SELECT * FROM providers ORDER BY created_at"
        ).fetchall()
        return [self._row_to_model(row) for row in rows]

    def update(self, name: str, **kwargs) -> Provider:
        """更新 Provider 字段"""
        provider = self.get_by_name(name)

        allowed_fields = {"provider_type", "base_url", "api_key"}
        updates = {k: v for k, v in kwargs.items() if k in allowed_fields and v is not None}
        if not updates:
            return provider

        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [provider.id]

        self.conn.execute(
            f"UPDATE providers SET {set_clause} WHERE id = ?", values
        )
        self.conn.commit()
        return self.get_by_name(name)

    def delete(self, name: str) -> None:
        """删除 Provider"""
        provider = self.get_by_name(name)
        self.conn.execute("DELETE FROM providers WHERE id = ?", (provider.id,))
        self.conn.commit()

    @staticmethod
    def _row_to_model(row: sqlite3.Row) -> Provider:
        return Provider(
            id=row["id"],
            name=row["name"],
            provider_type=row["provider_type"],
            base_url=row["base_url"],
            api_key=row["api_key"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
    @staticmethod
    def _row_to_model_sensitive_secret(row: sqlite3.Row) -> Provider:
        return Provider(
            id=row["id"],
            name=row["name"],
            provider_type=row["provider_type"],
            base_url=row["base_url"],
            api_key=SecretStr(row["api_key"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
