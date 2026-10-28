# SPDX-License-Identifier: Apache-2.0
"""只读迁移预览与冻结计划；普通客户端不自动升级已有数据库。"""

from __future__ import annotations

import hashlib
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from agentconf.exceptions import MigrationError
from agentconf.storage.database import Database, SCHEMA_VERSION
from agentconf.storage.catalog_repo import read_profile


def database_digest(conn: sqlite3.Connection) -> str:
    """覆盖 schema、版本与全部数据；只输出摘要，不输出原始秘密。"""
    digest = hashlib.sha256()
    digest.update(str(conn.execute("PRAGMA user_version").fetchone()[0]).encode())
    for line in conn.iterdump():
        digest.update(line.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


class MigrationPlan(BaseModel):
    """schema 4 开发阶段的冻结迁移计划，无原始配置/凭据值。"""

    model_config = ConfigDict(extra="forbid")
    plan_version: Literal[1] = 1
    target_version: Literal[4] = 4
    db_path: str
    source_version: Literal[0, 2, 3]
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    counts: dict[str, int]
    origins: dict[str, int]

    def save(self, path: str | Path) -> None:
        Path(path).write_text(self.model_dump_json(indent=2) + "\n", encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> MigrationPlan:
        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))


def prepare_migration(path: str | Path) -> MigrationPlan:
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise MigrationError("迁移只用于已有数据库，新库请先初始化")
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN")
        db = Database(path)
        db._connection = conn
        if not db._preflight():
            raise MigrationError(f"数据库已经是 schema {SCHEMA_VERSION}，无需迁移")
        if conn.execute("PRAGMA foreign_key_check").fetchall():
            raise MigrationError("旧库存在无效外键引用")
        counts = {
            table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in sorted(db._tables())
        }
        origins: dict[str, int] = {}
        if "model_capabilities" in counts:
            for row in conn.execute("SELECT * FROM model_capabilities"):
                profile = read_profile(row)
                origins[profile.origin] = origins.get(profile.origin, 0) + 1
        return MigrationPlan(
            db_path=str(path),
            source_version=conn.execute("PRAGMA user_version").fetchone()[0],
            fingerprint=database_digest(conn),
            counts=counts,
            origins=origins,
        )


def apply_migration(plan: MigrationPlan) -> dict:
    plan = MigrationPlan.model_validate_json(plan.model_dump_json())
    path = Path(plan.db_path).expanduser().resolve()
    if not path.is_file():
        raise MigrationError("原数据库不存在")
    db = Database(path)
    with closing(sqlite3.connect(path.as_uri() + "?mode=rw", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        db._connection = conn
        if database_digest(conn) != plan.fingerprint:
            raise MigrationError("预览后数据库已变化，请重新 prepare")
        db._init_schema(allow_upgrade=True, expected_digest=plan.fingerprint)
        return {
            "schema_version": SCHEMA_VERSION,
            "backup_path": str(db.backup_path) if db.backup_path else None,
        }
