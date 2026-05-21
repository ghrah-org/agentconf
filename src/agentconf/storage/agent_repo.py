# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""Agent 仓储层 - CRUD 操作"""

import json
import sqlite3
from datetime import datetime, timezone

from agentconf.exceptions import (
    AgentAlreadyExistsError,
    AgentNotFoundError,
    ModelNotFoundError,
)
from agentconf.models.agent import Agent


class AgentRepository:
    """Agent 数据访问对象"""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def create(
        self,
        id: str,
        name: str,
        model_instance_id: str,
        temperature: float | None = None,
        top_p: float | None = None,
        top_k: int | None = None,
        max_tokens: int | None = None,
        extra_params: dict | None = None,
    ) -> Agent:
        """创建 Agent"""
        # 验证 Model Instance 存在
        model_row = self.conn.execute(
            "SELECT id FROM model_instances WHERE id = ?", (model_instance_id,)
        ).fetchone()
        if model_row is None:
            raise ModelNotFoundError(f"Model Instance (id={model_instance_id}) 未找到")

        now = datetime.now(timezone.utc).isoformat()
        extra_params_json = json.dumps(extra_params) if extra_params else None

        try:
            self.conn.execute(
                """
                INSERT INTO agents (id, name, model_instance_id, temperature, top_p, top_k, max_tokens, extra_params, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (id, name, model_instance_id, temperature, top_p, top_k, max_tokens, extra_params_json, now, now),
            )
            self.conn.commit()
        except sqlite3.IntegrityError:
            raise AgentAlreadyExistsError(f"Agent '{name}' 已存在")

        return self.get_by_name(name)

    def get_by_name(self, name: str) -> Agent:
        """按名称查询 Agent"""
        row = self.conn.execute(
            "SELECT * FROM agents WHERE name = ?", (name,)
        ).fetchone()
        if row is None:
            raise AgentNotFoundError(f"Agent '{name}' 未找到")
        return self._row_to_model(row)

    def get_by_id(self, id: str) -> Agent:
        """按 ID 查询 Agent"""
        row = self.conn.execute(
            "SELECT * FROM agents WHERE id = ?", (id,)
        ).fetchone()
        if row is None:
            raise AgentNotFoundError(f"Agent (id={id}) 未找到")
        return self._row_to_model(row)

    def list_all(self) -> list[Agent]:
        """列出所有 Agent"""
        rows = self.conn.execute(
            "SELECT * FROM agents ORDER BY created_at"
        ).fetchall()
        return [self._row_to_model(row) for row in rows]

    def list_by_model(self, model_instance_id: str) -> list[Agent]:
        """列出指定 Model Instance 下的所有 Agent"""
        rows = self.conn.execute(
            "SELECT * FROM agents WHERE model_instance_id = ? ORDER BY created_at",
            (model_instance_id,),
        ).fetchall()
        return [self._row_to_model(row) for row in rows]

    def update(self, name: str, **kwargs) -> Agent:
        """更新 Agent 字段"""
        agent = self.get_by_name(name)

        allowed_fields = {
            "model_instance_id", "temperature", "top_p", "top_k",
            "max_tokens", "extra_params",
        }
        updates: dict = {}
        for k, v in kwargs.items():
            if k in allowed_fields:
                if k == "extra_params" and v is not None:
                    updates[k] = json.dumps(v)
                else:
                    updates[k] = v

        if not updates:
            return agent

        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [agent.id]

        self.conn.execute(
            f"UPDATE agents SET {set_clause} WHERE id = ?", values
        )
        self.conn.commit()
        return self.get_by_name(name)

    def delete(self, name: str) -> None:
        """删除 Agent"""
        agent = self.get_by_name(name)
        self.conn.execute("DELETE FROM agents WHERE id = ?", (agent.id,))
        self.conn.commit()

    @staticmethod
    def _row_to_model(row: sqlite3.Row) -> Agent:
        extra_params = None
        if row["extra_params"]:
            extra_params = json.loads(row["extra_params"])

        return Agent(
            id=row["id"],
            name=row["name"],
            model_instance_id=row["model_instance_id"],
            temperature=row["temperature"],
            top_p=row["top_p"],
            top_k=row["top_k"],
            max_tokens=row["max_tokens"],
            extra_params=extra_params,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
