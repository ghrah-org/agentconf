# SPDX-License-Identifier: Apache-2.0
"""目录命令和显式迁移；默认只读预览，裸命令打开 TUI。"""

from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from agentconf import (
    AgentsConfig,
    AgentsConfigError,
    CapabilityProfile,
    ImportPlan,
    ModelsDevSource,
    UpdatePlan,
)
from agentconf.catalog import content, prepare_import, prepare_update
from agentconf.migrations import MigrationPlan, prepare_migration, apply_migration
from agentconf.models.capability_profile import normalize_model_type
from agentconf.storage.catalog_repo import CatalogRepository


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="agentconf", description="模型配置与能力目录")
    parser.add_argument("--db-path")
    commands = parser.add_subparsers(dest="command")
    catalog = commands.add_parser("catalog")
    sub = catalog.add_subparsers(dest="operation", required=True)
    listing = sub.add_parser("list")
    listing.add_argument(
        "--origin", choices=["upstream", "derived", "local", "example"]
    )
    listing.add_argument("--type", dest="model_type")
    for name in (
        "show",
        "variants",
        "diff",
        "delete",
        "rebuild",
        "relocate",
        "derive",
        "clone",
    ):
        cmd = sub.add_parser(name)
        cmd.add_argument("key", nargs="?" if name == "diff" else None)
        if name == "diff":
            cmd.add_argument("--source", choices=["models.dev"], default="models.dev")
            cmd.add_argument("--plan")
            cmd.add_argument("--plan-out")
            cmd.add_argument("--types")
            cmd.set_defaults(
                apply=None, apply_all=False, dry_run=False, relocations=None
            )
        if name == "show":
            cmd.add_argument("--with-variants", action="store_true")
        if name in {"relocate", "derive", "clone"}:
            cmd.add_argument("--as", dest="new_key", required=True)
        if name in {"derive", "clone"}:
            cmd.add_argument("--display-name")
        if name in {"delete", "rebuild", "relocate", "derive", "clone"}:
            cmd.add_argument("--apply", action="store_true")
        if name in {"delete", "rebuild", "relocate"}:
            cmd.add_argument("--expected-revision", type=int)
    for name in ("create", "edit"):
        cmd = sub.add_parser(name)
        if name == "edit":
            cmd.add_argument("key")
            cmd.add_argument("--expected-revision", type=int)
        cmd.add_argument("--file", required=True)
        cmd.add_argument("--apply", action="store_true")
    for name in ("import", "update"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--source", choices=["models.dev"], default="models.dev")
        cmd.add_argument("--plan")
        cmd.add_argument("--plan-out")
        cmd.add_argument("--relocations", help="旧键到 local/ 新键的 JSON 映射文件")
        cmd.add_argument("--types")
        cmd.add_argument("--dry-run", action="store_true")
        group = cmd.add_mutually_exclusive_group()
        group.add_argument("--apply", nargs="+", metavar="KEY")
        group.add_argument("--apply-all", action="store_true")
    migrate = commands.add_parser("migrate").add_subparsers(
        dest="operation", required=True
    )
    migrate.add_parser("prepare").add_argument("--plan-out", required=True)
    migrate.add_parser("apply").add_argument("--plan", required=True)
    for cmd in [*sub.choices.values(), *migrate.choices.values()]:
        cmd.add_argument("--db-path", default=argparse.SUPPRESS)
    from agentconf.operations import DOMAINS

    for domain in DOMAINS:
        group = commands.add_parser(domain)
        actions = group.add_subparsers(dest="operation", required=True)
        listing = actions.add_parser("list")
        listing.add_argument("--show-secrets", action="store_true")
        if domain == "endpoint":
            listing.add_argument("--type", dest="model_type")
            listing.add_argument("--api-format")
            listing.add_argument("--provider", dest="provider_name")
            listing.add_argument("--capability", dest="capability_filter")
        if domain == "mcp":
            listing.add_argument("--transport")
        for action in (
            "show",
            "create",
            "update",
            "delete",
            "references" if domain == "provider" else "resolve",
        ):
            cmd = actions.add_parser(action)
            if action != "create":
                cmd.add_argument("key")
            if action in {"create", "update"}:
                cmd.add_argument("--file", required=True)
            if action in {"update", "delete"}:
                cmd.add_argument("--expected-revision", type=int)
                cmd.add_argument("--expected-id")
            if action in {"create", "update", "delete"}:
                cmd.add_argument("--apply", action="store_true")
            if action in {"show", "resolve"}:
                cmd.add_argument("--show-secrets", action="store_true")
        for cmd in actions.choices.values():
            cmd.add_argument("--db-path", default=argparse.SUPPRESS)
    initializing = commands.add_parser("init")
    initializing.add_argument("--db-path", default=argparse.SUPPRESS)
    removed = commands.add_parser("agent", help="旧 Agent 聚合配置已移出")
    removed.add_argument("legacy_args", nargs=argparse.REMAINDER)
    migrate.choices["prepare"].add_argument(
        "--formats", help="端点名称到 api_format 的 JSON 文件"
    )
    migrate.choices["prepare"].add_argument(
        "--relocations", help="人工能力键到 local/ 键的 JSON 文件"
    )
    args = parser.parse_args(argv)

    def emit(value):
        print(json.dumps(value, ensure_ascii=False, indent=2))

    try:
        if args.command is None:
            from agentconf.tui.app import agentconfApp

            agentconfApp(db_path=args.db_path).run()
            return
        path = Path(args.db_path or AgentsConfig.DEFAULT_DB_PATH).expanduser().resolve()
        if args.command == "agent":
            raise ValueError(
                "Agent 聚合已移出 agentconf；请解析端点并在下游保存请求参数"
            )
        if args.command == "init":
            with AgentsConfig(path) as cfg:
                emit({"schema_version": 5, "catalog_entries": len(cfg.catalog_list())})
            return
        if args.command in DOMAINS:
            run_resource(args, path, emit)
            return
        if args.command == "migrate":
            if args.operation == "prepare":
                plan = prepare_migration(
                    path,
                    api_formats=json.loads(Path(args.formats).read_text())
                    if args.formats
                    else None,
                    capability_relocations=json.loads(
                        Path(args.relocations).read_text()
                    )
                    if args.relocations
                    else None,
                )
                plan.save(args.plan_out)
                emit(plan.model_dump(mode="json"))
            else:
                plan = MigrationPlan.load(args.plan)
                if args.db_path and str(path) != plan.db_path:
                    raise ValueError("数据库路径与冻结计划不一致")
                emit(apply_migration(plan))
            return
        local = CatalogRepository.read_only(path)
        by_key = {p.model_key: p for p in local}

        def references(key):
            if not path.exists():
                return []
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as conn:
                conn.row_factory = sqlite3.Row
                return CatalogRepository(conn).references(key)

        operation = args.operation
        if operation == "list":
            emit(
                [
                    p.model_dump(mode="json")
                    for p in local
                    if (not args.origin or p.origin == args.origin)
                    and (
                        not args.model_type
                        or p.model_type == normalize_model_type(args.model_type)
                    )
                ]
            )
        elif (
            operation in {"show", "variants"}
            or operation == "diff"
            and args.key is not None
        ):
            old = by_key[args.key]
            variants = [p for p in local if p.derived_from == args.key]
            if operation == "variants":
                if old.origin != "upstream":
                    raise ValueError("只有 upstream 档案有派生变体")
                emit([p.model_dump(mode="json") for p in variants])
            elif operation == "diff":
                from agentconf.catalog import declaration_changes, fingerprint

                if old.origin != "derived":
                    raise ValueError("diff 需要 derived 档案键")
                parent = by_key[old.derived_from]
                emit(
                    {
                        "stale": old.base_fingerprint != fingerprint(parent),
                        "changes": declaration_changes(parent, old),
                    }
                )
            else:
                report = {
                    "profile": old.model_dump(mode="json"),
                    "revision": old.revision,
                    "references": references(args.key),
                }
                if args.with_variants:
                    report["variants"] = [p.model_dump(mode="json") for p in variants]
                emit(report)
        elif operation in {"import", "update", "diff"}:
            if args.dry_run and (args.apply or args.apply_all):
                raise ValueError("--dry-run 不能与应用参数同时使用")
            if args.plan and args.relocations:
                raise ValueError("保存的计划已冻结搬迁，不能另指定映射")
            cls, prepare = (
                (UpdatePlan, prepare_update)
                if operation == "update"
                else (ImportPlan, prepare_import)
            )
            if args.plan:
                plan = cls.load(args.plan)
            else:
                relocations = {}
                if args.relocations:
                    mapping = json.loads(Path(args.relocations).read_text())
                    if not isinstance(mapping, dict):
                        raise ValueError("relocations 必须为 JSON 映射")
                    if not path.exists():
                        raise ValueError("不存在的数据库不能搬迁")
                    with closing(
                        sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
                    ) as conn:
                        conn.row_factory = sqlite3.Row
                        relocations = CatalogRepository(conn).prepare_relocations(
                            mapping
                        )
                plan = prepare(
                    ModelsDevSource().fetch(), local, relocations=relocations
                )
                if isinstance(plan, UpdatePlan):
                    plan.references = {key: references(key) for key in plan.updatable}
                    if path.exists():
                        with closing(
                            sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
                        ) as conn:
                            conn.row_factory = sqlite3.Row
                            plan.reference_fingerprints = {
                                key: CatalogRepository(conn).reference_fingerprint(key)
                                for key in plan.updatable
                            }
            if args.plan_out:
                plan.save(args.plan_out)
            if args.apply or args.apply_all:
                with AgentsConfig(path) as cfg:
                    apply = (
                        cfg.catalog_update_apply
                        if operation == "update"
                        else cfg.catalog_apply
                    )
                    emit(
                        apply(
                            plan,
                            args.apply,
                            types=args.types.split(",") if args.types else None,
                        )
                    )
            else:
                report = plan.model_dump(mode="json", exclude={"snapshot"})
                if args.types:
                    wanted = {normalize_model_type(t) for t in args.types.split(",")}
                    keys = {
                        p.model_key
                        for p in plan.snapshot.profiles
                        if p.model_type in wanted
                    }
                    for group in (
                        "added",
                        "changed",
                        "unchanged",
                        "updatable",
                        "protected",
                    ):
                        if group in report:
                            report[group] = [k for k in report[group] if k in keys]
                    for group in (
                        "differences",
                        "conflicts",
                        "relocations",
                        "expected_revisions",
                        "expected_hashes",
                        "references",
                        "reference_fingerprints",
                    ):
                        if group in report:
                            report[group] = {
                                k: v for k, v in report[group].items() if k in keys
                            }
                report["source"] = plan.snapshot.model_dump(
                    mode="json", exclude={"profiles"}
                )
                emit(report)
        elif operation in {"create", "edit"}:
            profile = CapabilityProfile.model_validate_json(Path(args.file).read_text())
            old = by_key.get(profile.model_key)
            if operation == "edit":
                if args.key != profile.model_key or old is None:
                    raise ValueError("编辑键与已有档案不一致")
                if old.origin not in {"local", "derived"}:
                    raise ValueError("原件只读，请先 derive/clone")
                report = {
                    "old": content(old),
                    "new": content(profile),
                    "references": references(args.key),
                    "revision": old.revision,
                }
            else:
                if old is not None or profile.origin != "local":
                    raise ValueError("只能新建不存在的 local 档案")
                report = {"new": content(profile)}
            if args.apply:
                if operation == "edit" and args.expected_revision is None:
                    raise ValueError("应用必须填写 --expected-revision")
                with AgentsConfig(path) as cfg:
                    saved = (
                        cfg.catalog_edit(
                            profile, expected_revision=args.expected_revision
                        )
                        if operation == "edit"
                        else cfg.catalog_create(profile)
                    )
                    report["saved"] = saved.model_key
                    report["revision"] = saved.revision
            emit(report)
        else:
            old = by_key[args.key]
            report = {
                "operation": operation,
                "profile": old.model_dump(mode="json"),
                "references": references(args.key),
                "revision": old.revision,
            }
            if operation == "rebuild":
                from agentconf.catalog import fingerprint, declaration_changes

                if old.origin != "derived":
                    raise ValueError("只能重建 derived 基线")
                parent = by_key[old.derived_from]
                report["parent_fingerprint"] = fingerprint(parent)
                report["changes"] = declaration_changes(parent, old)
            if hasattr(args, "new_key"):
                report["new_key"] = args.new_key
            if args.apply:
                if (
                    operation in {"delete", "rebuild", "relocate"}
                    and args.expected_revision is None
                ):
                    raise ValueError("应用必须填写 --expected-revision")
                with AgentsConfig(path) as cfg:
                    if operation == "delete":
                        cfg.catalog_delete(
                            args.key, expected_revision=args.expected_revision
                        )
                        report["deleted"] = args.key
                    elif operation == "rebuild":
                        report["saved"] = cfg.catalog_rebuild(
                            args.key,
                            expected_revision=args.expected_revision,
                            expected_parent_fingerprint=report["parent_fingerprint"],
                        ).model_key
                    elif operation == "relocate":
                        report["saved"] = cfg.catalog_relocate(
                            args.key,
                            args.new_key,
                            expected_revision=args.expected_revision,
                            expected_references=report["references"],
                        ).model_key
                    else:
                        method = (
                            cfg.catalog_derive
                            if operation == "derive"
                            else cfg.catalog_clone
                        )
                        report["saved"] = method(
                            args.key,
                            args.new_key,
                            display_name=args.display_name,
                            expected_revision=old.revision,
                        ).model_key
            emit(report)
    except (AgentsConfigError, ValueError, KeyError, OSError, sqlite3.Error) as exc:
        parser.exit(1, f"{exc}\n")


def run_resource(args, path, emit):
    from agentconf.operations import (
        get_resource,
        list_resources,
        resource_input,
        validate_input,
        apply_resource,
        delete_resource,
        resolve_resource,
    )
    from contextlib import ExitStack

    with ExitStack() as stack:
        cfg = (
            stack.enter_context(AgentsConfig(path, read_only=True))
            if path.exists()
            else None
        )
        domain, operation = args.command, args.operation
        show = getattr(args, "show_secrets", False)
        if operation == "list":
            filters = {
                key: getattr(args, key)
                for key in (
                    "model_type",
                    "api_format",
                    "provider_name",
                    "capability_filter",
                    "transport",
                )
                if getattr(args, key, None) is not None
            }
            emit(
                [
                    m.model_dump(mode="json", context={"show_secrets": show})
                    for m in list_resources(cfg, domain, **filters)
                ]
                if cfg
                else []
            )
            return
        old = (
            get_resource(cfg, domain, args.key)
            if operation not in {"create"} and cfg
            else None
        )
        if operation != "create" and old is None:
            raise ValueError("资源不存在")
        if operation == "show":
            emit(
                {
                    "resource": old.model_dump(
                        mode="json", context={"show_secrets": show}
                    ),
                    "input": resource_input(cfg, domain, old, show_secrets=show),
                }
            )
        elif operation == "references":
            emit({"references": cfg.provider_references(args.key)})
        elif operation == "resolve":
            emit(
                resolve_resource(cfg, domain, args.key).model_dump(
                    mode="json", context={"show_secrets": show}
                )
            )
        elif operation in {"create", "update"}:
            payload = json.loads(Path(args.file).read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("资源文件必须为 JSON 对象")
            candidate, patch = validate_input(cfg, domain, payload, old)
            report = {
                "operation": operation,
                "preview": candidate.model_dump(mode="json"),
            }
            if old:
                report["revision"] = old.revision
                report["id"] = old.id
            if args.apply:
                if old and args.expected_revision is None:
                    raise ValueError("更新必须填写 --expected-revision")
                if old and (
                    args.expected_revision != old.revision
                    or (args.expected_id and args.expected_id != old.id)
                ):
                    raise ValueError("资源已变化，请重新预览")
                with AgentsConfig(path) as writer:
                    saved = apply_resource(writer, domain, patch, old=old)
                    report["saved"] = saved.model_dump(mode="json")
            emit(report)
        elif operation == "delete":
            report = {
                "delete": old.model_dump(mode="json"),
                "revision": old.revision,
                "references": cfg.provider_references(args.key)
                if domain == "provider"
                else [],
            }
            if args.apply:
                if args.expected_revision is None:
                    raise ValueError("删除必须填写 --expected-revision")
                with AgentsConfig(path) as writer:
                    delete_resource(
                        writer,
                        domain,
                        args.key,
                        args.expected_revision,
                        expected_id=args.expected_id or old.id,
                    )
                report["deleted"] = args.key
            emit(report)
