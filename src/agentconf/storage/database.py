# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""SQLite 数据库连接管理和 Schema 初始化"""

import sqlite3
from pathlib import Path

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS providers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    provider_type TEXT NOT NULL CHECK(provider_type IN ('openai', 'anthropic', 'custom', 'deepseek')),
    base_url TEXT NOT NULL,
    api_key TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS model_instances (
    id TEXT PRIMARY KEY,
    provider_id TEXT NOT NULL REFERENCES providers(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    model_name TEXT NOT NULL,
    model_type TEXT NOT NULL DEFAULT 'llm',
    base_url TEXT,
    api_key TEXT,
    capabilities TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(provider_id, name)
);

CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    model_instance_id TEXT NOT NULL REFERENCES model_instances(id) ON DELETE CASCADE,
    temperature REAL,
    top_p REAL,
    top_k INTEGER,
    max_tokens INTEGER,
    extra_params TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

MIGRATION_ADD_DEEPSEEK_PROVIDER_TYPE = """
CREATE TABLE IF NOT EXISTS providers_new (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    provider_type TEXT NOT NULL CHECK(provider_type IN ('openai', 'anthropic', 'custom', 'deepseek')),
    base_url TEXT NOT NULL,
    api_key TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
INSERT OR IGNORE INTO providers_new SELECT * FROM providers;
DROP TABLE IF EXISTS providers;
ALTER TABLE providers_new RENAME TO providers;
"""


class Database:
    """SQLite 数据库管理器"""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path).expanduser().resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection: sqlite3.Connection | None = None

    def connect(self) -> sqlite3.Connection:
        """获取数据库连接（懒初始化，带 WAL 模式和外键支持）"""
        if self._connection is None:
            self._connection = sqlite3.connect(
                str(self.db_path),
                check_same_thread=False,
            )
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("PRAGMA foreign_keys=ON")
            self._connection.row_factory = sqlite3.Row
            self._init_schema()
        return self._connection

    def _init_schema(self) -> None:
        """初始化数据库 schema"""
        assert self._connection is not None
        self._connection.executescript(SCHEMA_SQL)
        self._migrate_add_deepseek_provider_type()
        self._connection.commit()

    def _migrate_add_deepseek_provider_type(self) -> None:
        """迁移：为 providers 表的 provider_type CHECK 约束添加 'deepseek'"""
        assert self._connection is not None
        try:
            current_check = self._connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='providers'"
            ).fetchone()
            if current_check and "'deepseek'" not in current_check[0]:
                self._connection.executescript(MIGRATION_ADD_DEEPSEEK_PROVIDER_TYPE)
        except Exception:
            pass

    def close(self) -> None:
        """关闭数据库连接"""
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    @property
    def conn(self) -> sqlite3.Connection:
        """快捷获取连接"""
        return self.connect()
