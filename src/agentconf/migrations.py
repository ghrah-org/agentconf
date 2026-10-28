# SPDX-License-Identifier: Apache-2.0
"""只读冻结迁移、Agent 私密导出及跨文件中断恢复。"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import Field, field_validator
from agentconf.exceptions import MigrationError
from agentconf.models.secure import SafeModel, reveal
from agentconf.models.provider import ProviderCreate
from agentconf.models.model_endpoint import ModelEndpoint
from agentconf.models.capability_profile import CapabilityProfile, normalize_model_type
from agentconf.storage.database import Database, PROVIDERS_SQL, MODELS_SQL, MCP_SQL
from agentconf.storage.legacy import LegacyDatabase, LegacyAgent
from agentconf.storage.catalog_repo import read_profile, insert_profile


def canonical(value) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def checksum(value) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def database_digest(conn: sqlite3.Connection) -> str:
    digest = hashlib.sha256()
    digest.update(str(conn.execute("PRAGMA user_version").fetchone()[0]).encode())
    for line in conn.iterdump():
        digest.update(line.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


class MigrationPlan(SafeModel):
    """预览只含映射和摘要，不包含 Agent 参数值或凭据。"""

    plan_version: Literal[2] = 2
    target_version: Literal[5] = 5
    normalizer_version: Literal[4] = 4
    migration_id: str = Field(
        default_factory=lambda: uuid4().hex, pattern=r"^[a-f0-9]{32}$"
    )
    db_path: str
    source_version: Literal[0, 2, 3, 4]
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    counts: dict[str, int]
    origins: dict[str, int]
    api_formats: dict[str, str]
    unresolved: list[str]
    agent_endpoints: dict[str, str]
    capability_relocations: dict[str, str] = Field(default_factory=dict)

    @field_validator("capability_relocations")
    @classmethod
    def relocation_names(cls, value):
        if len(set(value.values())) != len(value) or any(
            not target.startswith("local/") or len(target) <= 6 or key == target
            for key, target in value.items()
        ):
            raise ValueError("能力键搬迁目标必须是不同且唯一的 local/ 键")
        return value

    def save(self, path: str | Path) -> None:
        Path(path).write_text(self.model_dump_json(indent=2) + "\n", encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path):
        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))

    def artifact(self, suffix):
        path = Path(self.db_path)
        return path.with_name(
            f"{path.name}.migration-{self.migration_id}.{suffix}.json"
        )


def _agents(conn):
    result = []
    for row in conn.execute(
        "SELECT a.*,m.name AS endpoint_name,m.model_name AS request_model FROM agents a JOIN model_instances m ON a.model_instance_id=m.id ORDER BY a.name"
    ):
        data = dict(row)
        endpoint, request = data.pop("endpoint_name"), data.pop("request_model")
        if data["extra_params"] is not None:
            data["extra_params"] = json.loads(data["extra_params"])
        parsed = LegacyAgent.model_validate(data)
        raw = parsed.model_dump(mode="json")
        result.append(
            dict(
                agent_id=raw["id"],
                agent_name=raw["name"],
                endpoint_id=raw["model_instance_id"],
                endpoint_name=endpoint,
                model_name=request,
                created_at=raw["created_at"],
                updated_at=raw["updated_at"],
                request_params={
                    key: raw[key]
                    for key in (
                        "temperature",
                        "top_p",
                        "top_k",
                        "max_tokens",
                        "extra_params",
                    )
                },
            )
        )
    if len(result) != conn.execute("SELECT count(*) FROM agents").fetchone()[0]:
        raise MigrationError("旧 Agent 引用无效，拒绝不完整导出")
    return result


def _formats(conn, supplied):
    names = {r[0] for r in conn.execute("SELECT name FROM model_instances")}
    if set(supplied) - names:
        raise MigrationError("接口映射含有不存在的模型绑定")
    result, unresolved = {}, []
    defaults = {
        "openai": "openai.chat_completions",
        "anthropic": "anthropic.messages",
        "deepseek": "openai.chat_completions",
    }
    for row in conn.execute(
        "SELECT m.name,m.model_type,p.provider_type FROM model_instances m JOIN providers p ON m.provider_id=p.id"
    ):
        name, model_type, provider_type = tuple(row)
        value = supplied.get(name)
        if value is None and normalize_model_type(model_type) == "llm":
            value = defaults.get(provider_type)
        if not isinstance(value, str) or not value.strip():
            unresolved.append(name)
        else:
            result[name] = value
    return result, sorted(unresolved)


def prepare_migration(
    path: str | Path,
    *,
    api_formats: dict[str, str] | None = None,
    capability_relocations: dict[str, str] | None = None,
) -> MigrationPlan:
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise MigrationError("迁移只用于已有数据库，新库请初始化")
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version == 5:
            current = Database(path)
            current._connection = conn
            current._preflight()
            current._validate_rows()
            raise MigrationError("数据库已经是 schema 5，无需迁移")
        db = LegacyDatabase(path)
        db._connection = conn
        db._preflight()
        if not db._tables():
            raise MigrationError("空库请使用 init，不需要迁移")
        if conn.execute("PRAGMA foreign_key_check").fetchall():
            raise MigrationError("旧库存在无效外键引用")
        for row in conn.execute("SELECT * FROM providers"):
            ProviderCreate.model_validate(
                {
                    key: row[key]
                    for key in ("name", "provider_type", "base_url", "api_key")
                }
            )
        counts = {
            table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in sorted(db._tables())
        }
        profiles, origins = {}, {}
        if "model_capabilities" in counts:
            for row in conn.execute("SELECT * FROM model_capabilities"):
                profile = read_profile(row)
                profiles[profile.model_key] = profile
                origins[profile.origin] = origins.get(profile.origin, 0) + 1
        relocations = capability_relocations or {}
        targets = set()
        for key, target in relocations.items():
            if (
                key not in profiles
                or profiles[key].origin != "local"
                or not isinstance(target, str)
                or not target.startswith("local/")
                or target in profiles
                or target in targets
                or len(target) <= 6
            ):
                raise MigrationError(
                    "能力键搬迁需要 local 原件和不存在的唯一 local/ 目标"
                )
            targets.add(target)
        formats, unresolved = _formats(conn, api_formats or {})
        agents = _agents(conn)
        return MigrationPlan(
            db_path=str(path),
            source_version=version,
            fingerprint=database_digest(conn),
            counts=counts,
            origins=origins,
            api_formats=formats,
            unresolved=unresolved,
            agent_endpoints={a["agent_name"]: a["endpoint_name"] for a in agents},
            capability_relocations=relocations,
        )


def _sync_directory(path):
    # Windows 不提供目录 fsync；文件本身仍先完成 fsync 再原子替换。
    if os.name != "nt":
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def write_private(path: Path, value):
    """文件只创建一次；同内容可重用，不覆盖其它或损坏的导出。"""
    if path.is_symlink():
        raise MigrationError("迁移文件不能是符号链接")
    if path.exists():
        if os.name != "nt" and path.stat().st_mode & 0o077:
            raise MigrationError("已有迁移文件权限过宽")
        try:
            same = json.loads(path.read_text(encoding="utf-8")) == value
        except (ValueError, OSError):
            same = False
        if not same:
            raise MigrationError("迁移文件已存在且内容不同，拒绝覆盖")
        return
    temp = path.with_name(path.name + ".tmp-" + uuid4().hex)
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(canonical(value) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
        _sync_directory(path.parent)
    finally:
        temp.unlink(missing_ok=True)


def _export(conn, plan):
    payload = dict(
        export_version=1,
        migration_id=plan.migration_id,
        source_version=plan.source_version,
        source_fingerprint=plan.fingerprint,
        target_version=5,
        agents=_agents(conn),
    )
    return {**payload, "checksum": checksum(payload)}


def _verify_export(plan, expected_checksum):
    path = plan.artifact("agents")
    try:
        export = json.loads(path.read_text(encoding="utf-8"))
        actual = export.pop("checksum")
        if (
            actual != expected_checksum
            or checksum(export) != actual
            or export["migration_id"] != plan.migration_id
            or export["source_fingerprint"] != plan.fingerprint
        ):
            raise ValueError
    except (OSError, ValueError, KeyError):
        raise MigrationError("Agent 导出缺失或校验失败，不执行迁移/重试") from None


def _convert(conn, plan, backup_path):
    legacy = LegacyDatabase(plan.db_path)
    legacy._connection = conn
    if plan.source_version < 4:
        # 外部已经持有写事务及原库备份；这里绝不提交中间版本。
        legacy.backup_path = backup_path
        legacy._init_schema(
            allow_upgrade=True,
            expected_digest=plan.fingerprint,
            manage_transaction=False,
        )
    for key, new_key in plan.capability_relocations.items():
        row = conn.execute(
            "SELECT * FROM model_capabilities WHERE model_key=?", (key,)
        ).fetchone()
        if row is None:
            raise MigrationError("能力搬迁原件不存在")
        profile = read_profile(row)
        if (
            profile.origin != "local"
            or conn.execute(
                "SELECT 1 FROM model_capabilities WHERE model_key=?", (new_key,)
            ).fetchone()
        ):
            raise MigrationError("能力搬迁发生冲突")
        data = profile.model_dump()
        data.update(
            model_key=new_key, revision=profile.revision + 1, updated_at=plan.created_at
        )
        insert_profile(conn, CapabilityProfile.model_validate(data))
        conn.execute(
            "UPDATE model_instances SET capabilities_key=? WHERE capabilities_key=?",
            (new_key, key),
        )
        conn.execute("DELETE FROM model_capabilities WHERE model_key=?", (key,))
    conn.execute(
        PROVIDERS_SQL.replace("CREATE TABLE providers", "CREATE TABLE providers_new", 1)
    )
    conn.execute(
        "INSERT INTO providers_new (id,name,provider_type,base_url,api_key,created_at,updated_at) SELECT id,name,provider_type,base_url,api_key,created_at,updated_at FROM providers"
    )
    conn.execute(MODELS_SQL)
    for row in conn.execute("SELECT * FROM model_instances ORDER BY name").fetchall():
        values = dict(row)
        values.update(api_format=plan.api_formats[values["name"]], revision=1)
        endpoint = ModelEndpoint.model_validate(values)
        data = reveal(endpoint)
        conn.execute(
            f"INSERT INTO model_endpoints ({','.join(data)}) VALUES ({','.join('?' for _ in data)})",
            tuple(data.values()),
        )
    conn.execute(MCP_SQL)
    conn.execute("DROP TABLE agents")
    conn.execute("DROP TABLE model_instances")
    conn.execute("DROP TABLE providers")
    conn.execute("ALTER TABLE providers_new RENAME TO providers")
    conn.execute("PRAGMA user_version=5")
    db = Database(plan.db_path)
    db._connection = conn
    db._preflight()
    db._validate_rows()
    expected = {
        "providers": plan.counts["providers"],
        "model_endpoints": plan.counts["model_instances"],
        "model_capabilities": plan.counts.get("model_capabilities", 0),
        "mcp_servers": 0,
    }
    if any(
        conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] != count
        for table, count in expected.items()
    ):
        raise MigrationError("迁移行数校验失败")


def apply_migration(plan: MigrationPlan) -> dict:
    plan = MigrationPlan.model_validate_json(plan.model_dump_json())
    path = Path(plan.db_path).expanduser().resolve()
    if str(path) != plan.db_path or not path.is_file():
        raise MigrationError("源数据库路径无效")
    if plan.unresolved:
        raise MigrationError("存在未指定 api_format 的绑定，请补全映射后重新 prepare")
    receipt_path, done_path = plan.artifact("prepared"), plan.artifact("result")
    receipt = None
    if receipt_path.exists():
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            required = {
                "migration_id",
                "source_fingerprint",
                "target_fingerprint",
                "export_checksum",
                "backup_path",
                "state",
            }
            if (
                not isinstance(receipt, dict)
                or set(receipt) != required
                or receipt["state"] != "prepared"
            ):
                raise ValueError
        except (ValueError, OSError):
            raise MigrationError("迁移结果记录损坏") from None
        if (
            receipt.get("migration_id") != plan.migration_id
            or receipt.get("source_fingerprint") != plan.fingerprint
        ):
            raise MigrationError("迁移结果记录与计划不一致")
    with closing(sqlite3.connect(path.as_uri() + "?mode=rw", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=OFF")
        committed = False
        try:
            conn.execute("BEGIN IMMEDIATE")
            current = database_digest(conn)
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version == 5:
                if not receipt or current != receipt.get("target_fingerprint"):
                    raise MigrationError("数据库已变化且不匹配本计划的迁移结果")
                _verify_export(plan, receipt["export_checksum"])
                db = Database(path)
                db._connection = conn
                db._preflight()
                db._validate_rows()
                conn.rollback()
                write_private(done_path, {**receipt, "state": "complete"})
                return {
                    "schema_version": 5,
                    "already_applied": True,
                    "backup_path": receipt["backup_path"],
                    "agent_export_path": str(plan.artifact("agents")),
                }
            if current != plan.fingerprint or version != plan.source_version:
                raise MigrationError("预览后数据库已变化，请重新 prepare")
            legacy = LegacyDatabase(path)
            legacy._connection = conn
            legacy._preflight()
            formats, unresolved = _formats(conn, plan.api_formats)
            if unresolved or formats != plan.api_formats:
                raise MigrationError("接口格式映射不完整")
            if {
                a["agent_name"]: a["endpoint_name"] for a in _agents(conn)
            } != plan.agent_endpoints:
                raise MigrationError("Agent 映射与冻结计划不一致")
            if receipt:
                backup = Path(receipt["backup_path"])
                if not backup.is_file():
                    raise MigrationError("原始备份缺失")
                with closing(
                    sqlite3.connect(backup.as_uri() + "?mode=ro", uri=True)
                ) as old:
                    if database_digest(old) != plan.fingerprint:
                        raise MigrationError("原始备份校验失败")
            else:
                legacy._backup()
                backup = legacy.backup_path
            exported = _export(conn, plan)
            write_private(plan.artifact("agents"), exported)
            _convert(conn, plan, backup)
            prepared = dict(
                migration_id=plan.migration_id,
                source_fingerprint=plan.fingerprint,
                target_fingerprint=database_digest(conn),
                export_checksum=exported["checksum"],
                backup_path=str(backup),
                state="prepared",
            )
            write_private(receipt_path, prepared)
            conn.commit()
            committed = True
            write_private(done_path, {**prepared, "state": "complete"})
            return {
                "schema_version": 5,
                "already_applied": False,
                "backup_path": str(backup),
                "agent_export_path": str(plan.artifact("agents")),
            }
        except BaseException as error:
            if conn.in_transaction:
                conn.rollback()
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            observed_commit = committed or (
                "prepared" in locals()
                and conn.execute("PRAGMA user_version").fetchone()[0] == 5
                and database_digest(conn) == prepared["target_fingerprint"]
            )
            if observed_commit:
                raise MigrationError(
                    "数据库已提交；完成记录未写入，请使用同一计划重试恢复"
                ) from None
            raise
        finally:
            conn.execute("PRAGMA foreign_keys=ON")
