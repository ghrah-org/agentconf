# SPDX-License-Identifier: Apache-2.0
"""显式迁移至 schema 6：冻结源、映射凭据身份、事务转换和可恢复回执。"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Literal
from uuid import uuid4
from datetime import datetime, timezone
from pydantic import Field
from agentconf import _migrations_v5 as old
from agentconf.exceptions import MigrationError
from agentconf.models.secure import SafeModel
from agentconf.models.mcp_provider import MCPProviderCreate
from agentconf.storage.database import Database, MCP_PROVIDERS_SQL, MCP_SQL
from agentconf.storage.schema_v5 import Database as Schema5
from agentconf.storage.legacy import LegacyDatabase
from agentconf.storage.resources import ResourceRepository

canonical = old.canonical
checksum = old.checksum
database_digest = old.database_digest
write_private = old.write_private


class MigrationPlan(SafeModel):
    """计划仅包含身份、映射和源摘要，不包含 Key。"""

    plan_version: Literal[3] = 3
    target_version: Literal[6] = 6
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    migration_id: str = Field(
        default_factory=lambda: uuid4().hex, pattern=r"^[a-f0-9]{32}$"
    )
    db_path: str
    source_version: Literal[0, 2, 3, 4, 5]
    fingerprint: str
    counts: dict[str, int]
    origins: dict[str, int] = Field(default_factory=dict)
    api_formats: dict[str, str] = Field(default_factory=dict)
    agent_endpoints: dict[str, str] = Field(default_factory=dict)
    capability_relocations: dict[str, str] = Field(default_factory=dict)
    unresolved: list[str] = Field(default_factory=list)
    mcp_map: dict = Field(default_factory=dict)
    legacy_plan: dict | None = None

    def save(self, path):
        Path(path).write_text(self.model_dump_json(indent=2) + "\n", encoding="utf-8")

    @classmethod
    def load(cls, path):
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if raw.get("plan_version") != 3:
            raise MigrationError("旧迁移计划不能重新解释为 schema 6，请重新 prepare")
        return cls.model_validate(raw)

    def artifact(self, suffix):
        path = Path(self.db_path)
        return path.with_name(
            f"{path.name}.migration-{self.migration_id}.{suffix}.json"
        )


def _mapping(value):
    mapping = value or {}
    if not isinstance(mapping, dict) or set(mapping) - {"providers", "servers"}:
        raise MigrationError("MCP 映射只接受 providers 和 servers")
    providers = mapping.get("providers", [])
    servers = mapping.get("servers", {})
    if not isinstance(providers, list) or not isinstance(servers, dict):
        raise MigrationError("MCP 映射结构无效")
    for item in providers:
        if not isinstance(item, dict) or set(item) - {
            "id",
            "name",
            "vendor",
            "default_binding",
            "source_model_provider_id",
            "source_server_id",
            "source_field",
            "strip_prefix",
        }:
            raise MigrationError("Provider 映射只能引用源身份或字段，不接受密钥值")
        if bool(item.get("source_model_provider_id")) == bool(
            item.get("source_server_id")
        ):
            raise MigrationError("每个新 Provider 必须明确指定唯一凭据来源")
        if not item.get("id"):
            raise MigrationError("Provider 映射缺少稳定目标 ID")
    for item in servers.values():
        if (
            not isinstance(item, dict)
            or set(item) - {"auth", "remove_fields"}
            or "auth" not in item
        ):
            raise MigrationError("服务映射必须明确指定 auth，可选 remove_fields")
    return mapping


def _source_key(conn, item):
    if item.get("source_model_provider_id"):
        row = conn.execute(
            "SELECT api_key FROM providers WHERE id=?",
            (item["source_model_provider_id"],),
        ).fetchone()
        if row is None or not row[0]:
            raise MigrationError("明确指定的源 Provider 不存在或缺少密钥")
        return row[0]
    row = conn.execute(
        "SELECT settings_json FROM mcp_servers WHERE id=?", (item["source_server_id"],)
    ).fetchone()
    path = item.get("source_field")
    if (
        row is None
        or not isinstance(path, list)
        or len(path) != 2
        or path[0] not in {"headers", "env"}
    ):
        raise MigrationError("静态凭据来源必须是确定 Server 的 header 或 env 字段")
    value = json.loads(row[0]).get(path[0], {}).get(path[1])
    prefix = item.get("strip_prefix", "")
    if (
        not isinstance(value, str)
        or not isinstance(prefix, str)
        or not value.startswith(prefix)
        or not value[len(prefix) :]
    ):
        raise MigrationError("静态凭据源字段与显式前缀不匹配")
    return value[len(prefix) :]


def _convert(conn, plan, backup, *, preview=False):
    if plan.source_version < 5:
        legacy = old.MigrationPlan.model_validate(plan.legacy_plan)
        old._convert(conn, legacy, backup)
    rows = [dict(r) for r in conn.execute("SELECT * FROM mcp_servers ORDER BY id")]
    mapping = _mapping(plan.mcp_map)
    if set(mapping.get("servers", {})) - {r["id"] for r in rows}:
        raise MigrationError("映射引用不存在的 MCP Server")
    # 先固定所有源值，避免重建表后丢失静态凭据源。
    keys = {p["id"]: _source_key(conn, p) for p in mapping.get("providers", [])}
    conn.execute(MCP_PROVIDERS_SQL)
    conn.execute("ALTER TABLE mcp_servers RENAME TO mcp_servers_v5")
    conn.execute(MCP_SQL)
    provider_repo = ResourceRepository(conn, "mcp_providers")
    for item in mapping.get("providers", []):
        model = MCPProviderCreate.model_validate(
            {k: item[k] for k in ("name", "vendor", "default_binding") if k in item}
        )
        from agentconf.models.mcp_provider import MCPProvider

        record = MCPProvider.model_validate(
            {
                **model.model_dump(mode="json"),
                "id": item["id"],
                "api_key": None if preview else keys[item["id"]],
                "created_at": plan.created_at,
                "updated_at": plan.created_at,
            }
        )
        data = provider_repo.encode(record)
        conn.execute(
            f"INSERT INTO mcp_providers ({','.join(data)}) VALUES ({','.join('?' for _ in data)})",
            tuple(data.values()),
        )
    repo = ResourceRepository(conn, "mcp_servers")
    for row in rows:
        settings = json.loads(row.pop("settings_json"))
        reference = (
            settings.pop("credential_ref", None)
            if row["transport"] == "streamable_http"
            else None
        )
        choice = mapping.get("servers", {}).get(row["id"])
        if choice is None:
            if reference:
                raise MigrationError("MCP 引用需要显式映射，不能按名称或 URL 猜测")
            choice = {
                "auth": {
                    "mode": "inline"
                    if any(settings.get(k) for k in ("headers", "env"))
                    else "none"
                }
            }
        for path in choice.get("remove_fields", []):
            if (
                not isinstance(path, list)
                or len(path) != 2
                or path[0] not in {"headers", "env"}
            ):
                raise MigrationError("移除字段只能是确定 header 或 env 项")
            if path[1] not in settings.get(path[0], {}):
                raise MigrationError("待移除的源认证字段不存在")
            settings[path[0]].pop(path[1])
        from agentconf.models.mcp_server import MCPServer

        server = MCPServer.model_validate(
            {**row, "settings": settings, "auth": choice["auth"]}
        )
        repo.validate_mcp(server)
        data = repo.encode(server)
        conn.execute(
            f"INSERT INTO mcp_servers ({','.join(data)}) VALUES ({','.join('?' for _ in data)})",
            tuple(data.values()),
        )
    conn.execute("DROP TABLE mcp_servers_v5")
    conn.execute("PRAGMA user_version=6")
    db = Database(plan.db_path)
    db._connection = conn
    db._preflight()
    db._validate_rows()


def prepare_migration(
    path, *, api_formats=None, capability_relocations=None, mcp_map=None
):
    """只读冻结旧库，未解决的凭据关联不会被自动迁移。"""
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise MigrationError("迁移只用于已有数据库，新库请初始化")
    mapping = _mapping(mcp_map)
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version == 6:
            db = Database(path)
            db._connection = conn
            db._preflight()
            db._validate_rows()
            raise MigrationError("数据库已经是 schema 6，无需迁移")
        legacy = None
        if version < 5:
            legacy = old.prepare_migration(
                path,
                api_formats=api_formats,
                capability_relocations=capability_relocations,
            )
        else:
            db = Schema5(path)
            db._connection = conn
            db._preflight()
            db._validate_rows()
            if conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type IN ('view','trigger') OR (type='index' AND sql IS NOT NULL)"
            ).fetchone():
                raise MigrationError(
                    "schema 5 包含自定义索引、视图或触发器，请显式处理后迁移"
                )
        counts = {
            r[0]: conn.execute(f'SELECT count(*) FROM "{r[0]}"').fetchone()[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        unresolved = list(legacy.unresolved) if legacy else []
        if version == 5:
            for row in conn.execute(
                "SELECT id,transport,settings_json FROM mcp_servers"
            ):
                if (
                    row["transport"] == "streamable_http"
                    and json.loads(row["settings_json"]).get("credential_ref")
                    and row["id"] not in mapping.get("servers", {})
                ):
                    unresolved.append("MCP Server 需要显式认证映射: " + row["id"])
        plan = MigrationPlan(
            db_path=str(path),
            source_version=version,
            fingerprint=database_digest(conn),
            counts=counts,
            unresolved=unresolved,
            mcp_map=mapping,
            legacy_plan=legacy.model_dump(mode="json") if legacy else None,
            api_formats=legacy.api_formats if legacy else {},
            origins=legacy.origins if legacy else {},
            agent_endpoints=legacy.agent_endpoints if legacy else {},
            capability_relocations=legacy.capability_relocations if legacy else {},
        )
        if legacy:
            plan = plan.model_copy(
                update={
                    "legacy_plan": {
                        **plan.legacy_plan,
                        "migration_id": plan.migration_id,
                    }
                }
            )
        if not unresolved:
            with closing(sqlite3.connect(":memory:")) as scratch:
                scratch.row_factory = sqlite3.Row
                conn.backup(scratch)
                scratch.execute("PRAGMA foreign_keys=OFF")
                scratch.execute("BEGIN IMMEDIATE")
                _convert(scratch, plan, Path("unused-preview"), preview=True)
                scratch.rollback()
        return plan


def _verify_export(plan, receipt):
    if not plan.legacy_plan:
        return
    try:
        exported = json.loads(plan.artifact("agents").read_text())
        actual = exported.pop("checksum")
        if actual != receipt.get("export_checksum") or checksum(exported) != actual:
            raise ValueError
    except (OSError, ValueError, KeyError):
        raise MigrationError("Agent 导出缺失或校验失败") from None


def apply_migration(plan):
    """核对源快照、备份并原子应用；提交后的回执失败可用同一计划恢复。"""
    plan = MigrationPlan.model_validate_json(plan.model_dump_json())
    if plan.unresolved:
        raise MigrationError("存在未解决的映射，请重新 prepare")
    path = Path(plan.db_path).resolve()
    if str(path) != plan.db_path or not path.is_file():
        raise MigrationError("源数据库路径无效")
    receipt_path, done_path = plan.artifact("prepared"), plan.artifact("result")
    receipt = None
    if receipt_path.exists():
        try:
            receipt = json.loads(receipt_path.read_text())
            required = {
                "migration_id",
                "source_fingerprint",
                "target_fingerprint",
                "backup_path",
                "agent_export_path",
                "state",
                "export_checksum",
            }
            if (
                not isinstance(receipt, dict)
                or set(receipt) != required
                or receipt["state"] != "prepared"
            ):
                raise ValueError
        except (OSError, ValueError):
            raise MigrationError("迁移回执损坏，拒绝继续") from None
    if receipt and (receipt.get("migration_id"), receipt.get("source_fingerprint")) != (
        plan.migration_id,
        plan.fingerprint,
    ):
        raise MigrationError("迁移回执与计划不一致")
    with closing(sqlite3.connect(path.as_uri() + "?mode=rw", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("BEGIN IMMEDIATE")
        committed = False
        try:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            fingerprint = database_digest(conn)
            if version == 6:
                if not receipt or fingerprint != receipt.get("target_fingerprint"):
                    raise MigrationError("数据库已变化或不匹配本计划的迁移结果")
                _verify_export(plan, receipt)
                db = Database(path)
                db._connection = conn
                db._preflight()
                db._validate_rows()
                conn.rollback()
                write_private(done_path, {**receipt, "state": "complete"})
                return {**receipt, "schema_version": 6, "already_applied": True}
            if fingerprint != plan.fingerprint or version != plan.source_version:
                raise MigrationError("预览后数据库已变化，请重新 prepare")
            if receipt:
                backup = Path(receipt["backup_path"])
                if not backup.is_file():
                    raise MigrationError("原始备份缺失")
                with closing(
                    sqlite3.connect(backup.as_uri() + "?mode=ro", uri=True)
                ) as original:
                    if database_digest(original) != plan.fingerprint:
                        raise MigrationError("原始备份校验失败")
            else:
                backup_owner = LegacyDatabase(path)
                backup_owner._connection = conn
                backup_owner._backup()
                backup = backup_owner.backup_path
            exported = None
            if plan.legacy_plan:
                exported = old._export(
                    conn, old.MigrationPlan.model_validate(plan.legacy_plan)
                )
                write_private(plan.artifact("agents"), exported)
            _convert(conn, plan, backup)
            receipt = dict(
                migration_id=plan.migration_id,
                source_fingerprint=plan.fingerprint,
                target_fingerprint=database_digest(conn),
                backup_path=str(backup),
                agent_export_path=str(plan.artifact("agents")) if exported else None,
                state="prepared",
                export_checksum=exported["checksum"] if exported else None,
            )
            write_private(receipt_path, receipt)
            conn.commit()
            committed = True
            write_private(done_path, {**receipt, "state": "complete"})
            return {**receipt, "schema_version": 6, "already_applied": False}
        except BaseException as error:
            if conn.in_transaction:
                conn.rollback()
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            if committed:
                raise MigrationError(
                    "数据库已提交，完成记录写入失败；请用同一计划重试恢复"
                ) from None
            raise
        finally:
            conn.execute("PRAGMA foreign_keys=ON")
