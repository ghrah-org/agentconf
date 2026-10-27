# SPDX-License-Identifier: Apache-2.0
"""能力档案仓储；导入仅新增，纠错通过显式编辑完成。"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from agentconf.catalog import ImportPlan, content, revision
from agentconf.exceptions import (
    CapabilityInUseError,
    CapabilityNotFoundError,
    CatalogError,
    CatalogImportError,
    MigrationError,
)
from agentconf.models.capability_profile import (
    CapabilityProfile,
    JSON_FIELDS,
    SCALAR_FIELDS,
    normalize_model_type,
)

PROFILE_COLUMNS = tuple(CapabilityProfile.model_fields)
BOOL_FIELDS = tuple(k for k in SCALAR_FIELDS if k.startswith("supports_"))


def insert_profile(conn: sqlite3.Connection, profile: CapabilityProfile) -> None:
    values = profile.model_dump(mode="json")
    for key in JSON_FIELDS:
        if values[key] is not None:
            values[key] = json.dumps(values[key], ensure_ascii=False, sort_keys=True)
    conn.execute(
        f"INSERT INTO model_capabilities ({', '.join(PROFILE_COLUMNS)}) VALUES ({', '.join('?' for _ in PROFILE_COLUMNS)})",
        tuple(values[k] for k in PROFILE_COLUMNS),
    )


def read_profile(row: sqlite3.Row) -> CapabilityProfile:
    values = dict(row)
    for key in JSON_FIELDS:
        if values[key] is not None:
            values[key] = json.loads(values[key])
    for key in BOOL_FIELDS:
        if values[key] is not None:
            values[key] = bool(values[key])
    return CapabilityProfile.model_validate(values)


class CatalogRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def get(self, key: str) -> CapabilityProfile:
        row = self.conn.execute(
            "SELECT * FROM model_capabilities WHERE model_key=?", (key,)
        ).fetchone()
        if row is None:
            raise CapabilityNotFoundError(f"能力档案未找到: {key}")
        return read_profile(row)

    def list(
        self, *, model_type: str | None = None, entry_kind: str | None = None
    ) -> list[CapabilityProfile]:
        if entry_kind not in {None, "model", "example"}:
            raise ValueError("entry_kind 必须为 model 或 example")
        profiles = [
            read_profile(r)
            for r in self.conn.execute(
                "SELECT * FROM model_capabilities ORDER BY model_key"
            )
        ]
        if model_type:
            profiles = [
                p for p in profiles if p.model_type == normalize_model_type(model_type)
            ]
        if entry_kind:
            profiles = [p for p in profiles if p.entry_kind == entry_kind]
        return profiles

    @staticmethod
    def read_only(path: str | Path) -> list[CapabilityProfile]:
        path = Path(path).expanduser().resolve()
        if not path.exists():
            return []
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as conn:
            conn.row_factory = sqlite3.Row
            if conn.execute("PRAGMA user_version").fetchone()[0] != 2:
                raise MigrationError("目录预览不迁移旧库；请先显式打开配置库完成升级")
            return CatalogRepository(conn).list()

    def references(self, key: str) -> list[str]:
        self.get(key)
        return [
            row[0]
            for row in self.conn.execute(
                "SELECT name FROM model_instances WHERE capabilities_key=? ORDER BY name",
                (key,),
            )
        ]

    def create(self, profile: CapabilityProfile) -> CapabilityProfile:
        with self.conn:
            try:
                insert_profile(self.conn, profile)
            except sqlite3.IntegrityError as exc:
                raise CatalogError(
                    f"能力档案已存在或不符合约束: {profile.model_key}"
                ) from exc
        return self.get(profile.model_key)

    def edit(
        self, profile: CapabilityProfile, *, expected_revision: str
    ) -> CapabilityProfile:
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            old = self.get(profile.model_key)
            if revision(old) != expected_revision:
                raise CatalogError("档案已被修改，请重新查看差异")
            if (old.model_type, old.entry_kind) != (
                profile.model_type,
                profile.entry_kind,
            ):
                raise CatalogError("档案类型和种类不可变，请新建档案")
            if content(old) == content(profile):
                self.conn.rollback()
                return old
            values = profile.model_dump()
            values.update(
                created_at=old.created_at, updated_at=datetime.now(timezone.utc)
            )
            profile = CapabilityProfile.model_validate(values)
            # 更新原行而非删除重插，保持 RESTRICT 外键有效。
            encoded = profile.model_dump(mode="json")
            for key in JSON_FIELDS:
                if encoded[key] is not None:
                    encoded[key] = json.dumps(
                        encoded[key], ensure_ascii=False, sort_keys=True
                    )
            columns = [k for k in PROFILE_COLUMNS if k != "model_key"]
            self.conn.execute(
                f"UPDATE model_capabilities SET {', '.join(k + '=?' for k in columns)} WHERE model_key=?",
                [encoded[k] for k in columns] + [profile.model_key],
            )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return self.get(profile.model_key)

    def delete(self, key: str) -> None:
        with self.conn:
            refs = self.references(key)
            if refs:
                raise CapabilityInUseError(f"能力档案被引用: {', '.join(refs)}")
            try:
                self.conn.execute(
                    "DELETE FROM model_capabilities WHERE model_key=?", (key,)
                )
            except sqlite3.IntegrityError as exc:
                raise CapabilityInUseError(f"能力档案被引用: {key}") from exc

    def apply(
        self,
        plan: ImportPlan,
        keys: list[str] | None = None,
        *,
        types: list[str] | None = None,
    ) -> dict:
        plan = ImportPlan.model_validate_json(plan.model_dump_json())
        selected = set(plan.added if keys is None else keys)
        if not selected <= set(plan.added):
            raise CatalogImportError("只能接受预览中的新增档案，已有差异必须人工编辑")
        incoming = {p.model_key: p for p in plan.snapshot.profiles}
        if types is not None:
            wanted = {normalize_model_type(t) for t in types}
            selected = {k for k in selected if incoming[k].model_type in wanted}
        result: dict = {
            "added": [],
            "skipped": [],
            "changed_retained": plan.changed,
            "missing_retained": plan.missing,
        }
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            for key in sorted(selected):
                try:
                    old = self.get(key)
                except CapabilityNotFoundError:
                    insert_profile(self.conn, incoming[key])
                    result["added"].append(key)
                else:
                    if content(old) != content(incoming[key]):
                        raise CatalogImportError(
                            f"预览后档案已被创建且内容不同: {key}，整批回滚"
                        )
                    result["skipped"].append(key)
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return result
