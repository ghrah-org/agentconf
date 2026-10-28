# SPDX-License-Identifier: Apache-2.0
"""能力档案仓储：来源写权限、派生谱系、显式升级与引用保护。"""

from __future__ import annotations

import json
import hashlib
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from agentconf.catalog import (
    ImportPlan,
    UpdatePlan,
    Relocation,
    content,
    fingerprint,
    revision,
    row_hash,
)
from agentconf.exceptions import (
    CapabilityInUseError,
    CapabilityNotFoundError,
    CatalogError,
    CatalogImportError,
    CatalogConflictError,
    CatalogReadOnlyError,
    MigrationError,
)
from agentconf.models.capability_profile import (
    CapabilityProfile,
    JSON_FIELDS,
    SCALAR_FIELDS,
    Origin,
    normalize_model_type,
)

PROFILE_COLUMNS = tuple(CapabilityProfile.model_fields)
JSON_COLUMNS = JSON_FIELDS + ("annotations",)
BOOL_FIELDS = tuple(k for k in SCALAR_FIELDS if k.startswith("supports_"))
ORIGINS = frozenset({"upstream", "derived", "local", "example"})


def insert_profile(
    conn: sqlite3.Connection,
    profile: CapabilityProfile,
    *,
    table: str = "model_capabilities",
) -> None:
    values = profile.model_dump(mode="json")
    for key in JSON_COLUMNS:
        if values[key] is not None:
            values[key] = json.dumps(values[key], ensure_ascii=False, sort_keys=True)
    conn.execute(
        f"INSERT INTO {table} ({', '.join(PROFILE_COLUMNS)}) VALUES ({', '.join('?' for _ in PROFILE_COLUMNS)})",
        tuple(values[k] for k in PROFILE_COLUMNS),
    )


def read_profile(row: sqlite3.Row) -> CapabilityProfile:
    values = dict(row)
    # 只读预览可能打开 schema 2/3；在不迁移的前提下按保守规则投影。
    if "origin" not in values:
        if values.get("managed_by") is not None and (
            values["managed_by"] != values["source"]
            or values["managed_by"] in {"manual", "example"}
        ):
            raise MigrationError("旧档案维护来源不一致，拒绝推定 origin")
        if values.get("entry_kind") == "example":
            values["origin"] = "example"
        elif values.get("managed_by"):
            values["origin"] = "upstream"
        else:
            values["origin"] = "local"
    values.pop("entry_kind", None)
    values.pop("managed_by", None)
    values.setdefault("revision", 1)
    for key in JSON_COLUMNS:
        if values.get(key) is not None:
            values[key] = json.loads(values[key])
    for key in BOOL_FIELDS:
        if values[key] is not None:
            if values[key] not in (0, 1):
                raise MigrationError(f"非法布尔能力声明: {key}")
            values[key] = bool(values[key])
    return CapabilityProfile.model_validate(values)


class CatalogRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    # ── 读取 ──

    def get(self, key: str) -> CapabilityProfile:
        row = self.conn.execute(
            "SELECT * FROM model_capabilities WHERE model_key=?", (key,)
        ).fetchone()
        if row is None:
            raise CapabilityNotFoundError(f"能力档案未找到: {key}")
        return read_profile(row)

    def list(
        self, *, model_type: str | None = None, origin: Origin | None = None
    ) -> list[CapabilityProfile]:
        if origin is not None and origin not in ORIGINS:
            raise ValueError("origin 必须为 upstream/derived/local/example")
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
        if origin:
            profiles = [p for p in profiles if p.origin == origin]
        return profiles

    @staticmethod
    def read_only(path: str | Path) -> list[CapabilityProfile]:
        path = Path(path).expanduser().resolve()
        if not path.exists():
            return []
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as conn:
            conn.row_factory = sqlite3.Row
            if conn.execute("PRAGMA user_version").fetchone()[0] not in {2, 3, 4}:
                raise MigrationError(
                    "目录预览不迁移旧库；请使用 migrate prepare/apply 显式升级"
                )
            return CatalogRepository(conn).list()

    def references(self, key: str) -> list[str]:
        self.get(key)
        bindings = [
            row[0]
            for row in self.conn.execute(
                "SELECT name FROM model_instances WHERE capabilities_key=? ORDER BY name",
                (key,),
            )
        ]
        if "derived_from" in {
            r[1] for r in self.conn.execute("PRAGMA table_info(model_capabilities)")
        }:
            bindings += [
                f"derived:{r[0]}"
                for r in self.conn.execute(
                    "SELECT model_key FROM model_capabilities WHERE derived_from=? ORDER BY model_key",
                    (key,),
                )
            ]
        return bindings

    def variants(self, key: str) -> list[CapabilityProfile]:
        profile = self.get(key)
        if profile.origin != "upstream":
            raise CatalogError("只有 upstream 档案可以有派生变体")
        return [
            read_profile(row)
            for row in self.conn.execute(
                "SELECT * FROM model_capabilities WHERE derived_from=? ORDER BY model_key",
                (key,),
            )
        ]

    def reference_fingerprint(self, key: str) -> str:
        """记录引用实体身份，避免同名绑定删除重建后绕过预览校验。"""
        models = [
            tuple(row)
            for row in self.conn.execute(
                "SELECT id,name,model_type,capabilities_key FROM model_instances WHERE capabilities_key=? ORDER BY id",
                (key,),
            )
        ]
        derived = []
        if "derived_from" in {
            r[1] for r in self.conn.execute("PRAGMA table_info(model_capabilities)")
        }:
            derived = [
                tuple(row)
                for row in self.conn.execute(
                    "SELECT model_key,derived_from FROM model_capabilities WHERE derived_from=? ORDER BY model_key",
                    (key,),
                )
            ]
        return hashlib.sha256(
            json.dumps(
                [models, derived], ensure_ascii=False, separators=(",", ":")
            ).encode()
        ).hexdigest()

    def stale(self, key: str) -> bool:
        profile = self.get(key)
        if profile.origin != "derived":
            raise CatalogError("只有 derived 档案需要检查 stale")
        if not profile.derived_from:
            raise CatalogError("derived 档案缺少父档案")
        parent = self.get(profile.derived_from)
        return profile.base_fingerprint != fingerprint(parent)

    def diff(self, key: str) -> dict:
        profile = self.get(key)
        if profile.origin != "derived":
            raise CatalogError("只有 derived 档案支持 diff")
        if not profile.derived_from:
            raise CatalogError("derived 档案缺少父档案")
        parent = self.get(profile.derived_from)
        from agentconf.catalog import declaration_changes

        return {
            "key": key,
            "derived_from": parent.model_key,
            "stale": profile.base_fingerprint != fingerprint(parent),
            "base_fingerprint": profile.base_fingerprint,
            "parent_fingerprint": fingerprint(parent),
            "changes": declaration_changes(parent, profile),
        }

    # ── 人工维护（local）与派生（derived）──

    def create(self, profile: CapabilityProfile) -> CapabilityProfile:
        """SDK/CLI 人工新建一律为 local，不接受调用方伪造 origin。"""
        now = datetime.now(timezone.utc)
        values = profile.model_dump()
        values.update(
            origin="local",
            derived_from=None,
            base_fingerprint=None,
            revision=1,
            source="manual" if values["source"] == "example" else values["source"],
            created_at=now,
            updated_at=now,
        )
        profile = CapabilityProfile.model_validate(values)
        with self.conn:
            try:
                insert_profile(self.conn, profile)
            except sqlite3.IntegrityError as exc:
                raise CatalogError(
                    f"能力档案已存在或不符合约束: {profile.model_key}"
                ) from exc
        return self.get(profile.model_key)

    def edit(
        self, profile: CapabilityProfile, *, expected_revision: int
    ) -> CapabilityProfile:
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            old = self.get(profile.model_key)
            if revision(old) != expected_revision:
                raise CatalogConflictError("档案已被修改，请重新查看差异")
            if old.origin not in {"local", "derived"}:
                raise CatalogReadOnlyError(
                    f"{old.origin} 档案只读，请 derive/clone 后编辑"
                )
            if profile.model_type != old.model_type:
                raise CatalogError("档案类型不可变，请新建档案")
            immutable = (
                "origin",
                "source",
                "source_ref",
                "source_fetched_at",
                "derived_from",
                "base_fingerprint",
                "created_at",
                "revision",
            )
            if any(
                getattr(profile, field) != getattr(old, field) for field in immutable
            ):
                raise CatalogError("身份、来源与派生基线不可通过普通编辑修改")
            values = profile.model_dump()
            values.update(
                origin=old.origin,
                source=old.source,
                source_ref=old.source_ref,
                source_fetched_at=old.source_fetched_at,
                derived_from=old.derived_from,
                base_fingerprint=old.base_fingerprint,
                revision=old.revision,
                created_at=old.created_at,
            )
            candidate = CapabilityProfile.model_validate(values)
            if content(old) == content(candidate):
                self.conn.rollback()
                return old
            values.update(
                revision=old.revision + 1, updated_at=datetime.now(timezone.utc)
            )
            saved = CapabilityProfile.model_validate(values)
            self._write(saved)
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return self.get(profile.model_key)

    def delete(self, key: str, *, expected_revision: int) -> None:
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            profile = self.get(key)
            if profile.revision != expected_revision:
                raise CatalogConflictError("档案已被修改，请重新预览")
            refs = self.references(key)
            if refs:
                raise CapabilityInUseError(f"能力档案被引用: {', '.join(refs)}")
            if profile.origin in {"upstream", "example"}:
                raise CatalogReadOnlyError(
                    f"{profile.origin} 档案不允许通过普通 CRUD 删除"
                )
            try:
                self.conn.execute(
                    "DELETE FROM model_capabilities WHERE model_key=?", (key,)
                )
            except sqlite3.IntegrityError as exc:
                raise CapabilityInUseError(f"能力档案被引用: {key}") from exc
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def derive(
        self,
        parent_key: str,
        key: str,
        *,
        display_name: str | None = None,
        expected_revision: int | None = None,
    ) -> CapabilityProfile:
        return self._copy(
            parent_key,
            key,
            derived=True,
            display_name=display_name,
            expected_revision=expected_revision,
        )

    def clone(
        self,
        source_key: str,
        key: str,
        *,
        display_name: str | None = None,
        expected_revision: int | None = None,
    ) -> CapabilityProfile:
        return self._copy(
            source_key,
            key,
            derived=False,
            display_name=display_name,
            expected_revision=expected_revision,
        )

    def _copy(
        self,
        source_key: str,
        key: str,
        *,
        derived: bool,
        display_name: str | None,
        expected_revision: int | None,
    ) -> CapabilityProfile:
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            source = self.get(source_key)
            if expected_revision is not None and source.revision != expected_revision:
                raise CatalogConflictError("预览后原件已变，请重新查看")
            if source.origin != ("upstream" if derived else "example"):
                raise CatalogReadOnlyError(
                    "只能从 upstream 派生，或从 example 复制为 local"
                )
            if self._exists(key):
                raise CatalogError(f"能力档案已存在: {key}")
            now = datetime.now(timezone.utc)
            values = source.model_dump()
            values.update(
                model_key=key,
                display_name=display_name or source.display_name,
                origin="derived" if derived else "local",
                revision=1,
                derived_from=source.model_key if derived else None,
                base_fingerprint=fingerprint(source) if derived else None,
                created_at=now,
                updated_at=now,
            )
            if not derived:
                values.update(source="manual", source_ref=None, source_fetched_at=None)
            profile = CapabilityProfile.model_validate(values)
            insert_profile(self.conn, profile)
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return self.get(key)

    def rebuild(
        self,
        key: str,
        *,
        expected_revision: int,
        expected_parent_fingerprint: str | None = None,
    ) -> CapabilityProfile:
        """显式重建派生基线；只更新 base_fingerprint，不合并上游字段。"""
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            profile = self.get(key)
            if profile.origin != "derived":
                raise CatalogError("只有 derived 档案可以重建基线")
            if revision(profile) != expected_revision:
                raise CatalogConflictError("档案已被修改，请重新预览")
            if not profile.derived_from:
                raise CatalogError("derived 档案缺少父档案")
            parent = self.get(profile.derived_from)
            if (
                expected_parent_fingerprint is not None
                and fingerprint(parent) != expected_parent_fingerprint
            ):
                raise CatalogConflictError("预览后上游已变化，请重新查看")
            values = profile.model_dump()
            values.update(
                base_fingerprint=fingerprint(parent),
                revision=profile.revision + 1,
                updated_at=datetime.now(timezone.utc),
            )
            self._write(CapabilityProfile.model_validate(values))
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return self.get(key)

    def relocate(
        self,
        key: str,
        new_key: str,
        *,
        expected_revision: int,
        expected_references: list[str] | None = None,
    ) -> CapabilityProfile:
        """把人工占用的规范键搬到 local/...，同事务更新全部端点引用。"""
        if not new_key.startswith("local/"):
            raise CatalogError("人工档案只能搬迁到 local/ 命名空间")
        if new_key == key:
            raise CatalogError("新旧键相同")
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            profile = self.get(key)
            if profile.origin != "local":
                raise CatalogError("只有 local 档案可以搬迁")
            if revision(profile) != expected_revision:
                raise CatalogError("档案已被修改，请重新预览")
            if (
                expected_references is not None
                and self.references(key) != expected_references
            ):
                raise CatalogConflictError("预览后引用已变化，请重新查看")
            if self._exists(new_key):
                raise CatalogError(f"能力档案已存在: {new_key}")
            values = profile.model_dump()
            values.update(
                model_key=new_key,
                revision=profile.revision + 1,
                updated_at=datetime.now(timezone.utc),
            )
            moved = CapabilityProfile.model_validate(values)
            insert_profile(self.conn, moved)
            self.conn.execute(
                "UPDATE model_instances SET capabilities_key=? WHERE capabilities_key=?",
                (new_key, key),
            )
            self.conn.execute(
                "DELETE FROM model_capabilities WHERE model_key=?", (key,)
            )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return self.get(new_key)

    # ── 导入与同来源升级 ──

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
            "conflicts": plan.conflicts,
        }
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            self._apply_relocations(plan, selected)
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

    def apply_update(
        self,
        plan: UpdatePlan,
        keys: list[str] | None = None,
        *,
        types: list[str] | None = None,
    ) -> dict:
        """接受同一预览的新增与 upstream 变更；并发冲突使整批回滚。"""
        plan = UpdatePlan.model_validate_json(plan.model_dump_json())
        eligible = set(plan.added) | set(plan.updatable)
        selected = eligible if keys is None else set(keys)
        if not selected <= eligible:
            raise CatalogImportError(
                "仅可接受新增或可更新项；人工保护与类型冲突项不可覆盖"
            )
        incoming = {p.model_key: p for p in plan.snapshot.profiles}
        if types is not None:
            wanted = {normalize_model_type(t) for t in types}
            selected = {key for key in selected if incoming[key].model_type in wanted}
        result = {
            "added": [],
            "updated": [],
            "skipped": [],
            "protected": plan.protected,
            "conflicts": plan.conflicts,
            "missing_retained": plan.missing,
        }
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            self._apply_relocations(plan, selected)
            for key in sorted(selected):
                new = incoming[key]
                try:
                    old = self.get(key)
                except CapabilityNotFoundError:
                    if key not in plan.added:
                        raise CatalogImportError(
                            f"预览后档案已被删除: {key}，整批回滚"
                        ) from None
                    insert_profile(self.conn, new)
                    result["added"].append(key)
                    continue
                if old.origin != "upstream" or old.source != plan.snapshot.source:
                    raise CatalogImportError(f"预览后档案的来源已变化: {key}，整批回滚")
                if content(old) == content(new):
                    result["skipped"].append(key)
                    continue
                if (
                    key not in plan.updatable
                    or revision(old) != plan.expected_revisions[key]
                    or row_hash(old) != plan.expected_hashes[key]
                ):
                    raise CatalogImportError(f"预览后档案已变化: {key}，整批回滚")
                if old.model_type != new.model_type:
                    raise CatalogImportError(f"模型类型冲突: {key}，整批回滚")
                if self.references(key) != plan.references.get(
                    key, []
                ) or self.reference_fingerprint(key) != plan.reference_fingerprints.get(
                    key
                ):
                    raise CatalogImportError(f"预览后引用绑定已变化: {key}，整批回滚")
                values = new.model_dump()
                values.update(
                    created_at=old.created_at,
                    updated_at=datetime.now(timezone.utc),
                    revision=old.revision + 1,
                )
                self._write(CapabilityProfile.model_validate(values))
                result["updated"].append(key)
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return result

    def set_management(self, *args, **kwargs):
        """0.2 维护权交接已由 origin/derive 取代。"""
        raise CatalogError(
            "catalog_set_management 已移除；请使用 catalog_derive/clone 或重新导入 upstream"
        )

    # ── 内部 ──

    def prepare_relocations(
        self, mapping: dict[str, str] | None
    ) -> dict[str, Relocation]:
        plans = {}
        for key, new_key in (mapping or {}).items():
            if self._exists(new_key) or new_key in {r.new_key for r in plans.values()}:
                raise CatalogError(f"搬迁目标已存在或重复: {new_key}")
            plans[key] = Relocation(
                profile=self.get(key),
                new_key=new_key,
                references=self.references(key),
                reference_fingerprint=self.reference_fingerprint(key),
            )
        return plans

    def _apply_relocations(self, plan: ImportPlan, selected: set[str]) -> None:
        for key, relocation in plan.relocations.items():
            if key not in selected:
                continue
            # 已完成的相同 apply 可以跳过；人工目标若有改变则仍应停止。
            if (
                self._exists(key)
                and self.get(key).origin == "upstream"
                and self._exists(relocation.new_key)
            ):
                values = relocation.profile.model_dump()
                values.update(
                    model_key=relocation.new_key,
                    revision=relocation.profile.revision + 1,
                )
                current = self.get(relocation.new_key)
                expected = CapabilityProfile.model_validate(values)
                if (
                    content(current) == content(expected)
                    and current.revision == expected.revision
                ):
                    continue
            if (
                not self._exists(key)
                or row_hash(self.get(key)) != row_hash(relocation.profile)
                or self.references(key) != relocation.references
                or self.reference_fingerprint(key) != relocation.reference_fingerprint
                or self._exists(relocation.new_key)
            ):
                raise CatalogImportError(f"搬迁预览后数据或引用已变化: {key}，整批回滚")
            values = relocation.profile.model_dump()
            values.update(
                model_key=relocation.new_key,
                revision=relocation.profile.revision + 1,
                updated_at=datetime.now(timezone.utc),
            )
            insert_profile(self.conn, CapabilityProfile.model_validate(values))
            self.conn.execute(
                "UPDATE model_instances SET capabilities_key=? WHERE capabilities_key=?",
                (relocation.new_key, key),
            )
            self.conn.execute(
                "DELETE FROM model_capabilities WHERE model_key=?", (key,)
            )

    def _exists(self, key: str) -> bool:
        return (
            self.conn.execute(
                "SELECT 1 FROM model_capabilities WHERE model_key=?", (key,)
            ).fetchone()
            is not None
        )

    def _write(self, profile: CapabilityProfile) -> None:
        """原位写入，不删除被绑定的档案。调用者负责事务。"""
        encoded = profile.model_dump(mode="json")
        for key in JSON_COLUMNS:
            if encoded[key] is not None:
                encoded[key] = json.dumps(
                    encoded[key], ensure_ascii=False, sort_keys=True
                )
        columns = [k for k in PROFILE_COLUMNS if k != "model_key"]
        self.conn.execute(
            f"UPDATE model_capabilities SET {', '.join(k + '=?' for k in columns)} WHERE model_key=?",
            [encoded[k] for k in columns] + [profile.model_key],
        )
