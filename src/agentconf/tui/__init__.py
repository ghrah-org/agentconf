# SPDX-License-Identifier: Apache-2.0
"""配置 TUI 与能力目录命令；导入默认只读预览。"""

from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="agentconf", description="模型配置与能力目录")
    parser.add_argument(
        "--db-path", help="数据库文件路径（默认 ~/.agentconf/config.db）"
    )
    commands = parser.add_subparsers(dest="command")
    catalog = commands.add_parser("catalog", help="查看、导入和人工维护能力档案")
    sub = catalog.add_subparsers(dest="operation", required=True)
    listing = sub.add_parser("list")
    listing.add_argument("--kind", choices=["model", "example"])
    listing.add_argument("--type", dest="model_type")
    showing = sub.add_parser("show")
    showing.add_argument("key")
    for name in ("import", "diff"):
        command = sub.add_parser(name)
        command.add_argument("--source", choices=["models.dev"], default="models.dev")
        command.add_argument("--plan", help="读取已保存快照，不再下载")
        command.add_argument("--plan-out", help="保存本次预览与规范化快照")
        command.add_argument("--types", help="逗号分隔的模型类型")
        command.add_argument("--dry-run", action="store_true", help="只读预览（默认）")
        if name == "import":
            group = command.add_mutually_exclusive_group()
            group.add_argument(
                "--apply", nargs="+", metavar="KEY", help="接受所选新增项"
            )
            group.add_argument(
                "--apply-all", action="store_true", help="接受全部新增项"
            )
    for name in ("create", "edit"):
        command = sub.add_parser(name)
        if name == "edit":
            command.add_argument("key")
            command.add_argument(
                "--expected-revision", help="预览返回的修订指纹；应用时必填"
            )
        command.add_argument(
            "--file", required=True, help="完整 CapabilityProfile JSON 文件"
        )
        command.add_argument("--apply", action="store_true", help="写入；默认只读预览")
    deleting = sub.add_parser("delete")
    deleting.add_argument("key")
    deleting.add_argument("--apply", action="store_true")
    # 同时支持命令前后设置数据库路径，便于复制文档中的命令。
    for command in sub.choices.values():
        command.add_argument("--db-path", default=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.command is None:
        from agentconf import AgentsConfigError
        from agentconf.tui.app import agentconfApp

        try:
            agentconfApp(db_path=args.db_path).run()
        except (AgentsConfigError, OSError, sqlite3.Error) as exc:
            parser.exit(1, f"{exc}\n")
        return

    from agentconf import (
        AgentsConfig,
        AgentsConfigError,
        CapabilityProfile,
        ImportPlan,
        ModelsDevSource,
    )
    from agentconf.catalog import content, prepare_import, revision
    from agentconf.models.capability_profile import normalize_model_type
    from agentconf.storage.catalog_repo import CatalogRepository

    path = Path(args.db_path or AgentsConfig.DEFAULT_DB_PATH).expanduser().resolve()

    def references(key: str) -> list[str]:
        if not path.exists():
            return []
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as conn:
            return [
                r[0]
                for r in conn.execute(
                    "SELECT name FROM model_instances WHERE capabilities_key=? ORDER BY name",
                    (key,),
                )
            ]

    def emit(value) -> None:
        print(json.dumps(value, ensure_ascii=False, indent=2))

    try:
        local = CatalogRepository.read_only(path)
        by_key = {p.model_key: p for p in local}
        operation = args.operation
        if operation == "list":
            profiles = [
                p
                for p in local
                if (not args.kind or p.entry_kind == args.kind)
                and (
                    not args.model_type
                    or p.model_type == normalize_model_type(args.model_type)
                )
            ]
            emit([content(p) for p in profiles])
        elif operation == "show":
            p = by_key[args.key]
            emit(
                {
                    "profile": p.model_dump(mode="json"),
                    "revision": revision(p),
                    "references": references(p.model_key),
                }
            )
        elif operation in {"import", "diff"}:
            if args.dry_run and (
                getattr(args, "apply", None) or getattr(args, "apply_all", False)
            ):
                raise ValueError("--dry-run 不能与应用参数同时使用")
            plan = (
                ImportPlan.load(args.plan)
                if args.plan
                else prepare_import(ModelsDevSource().fetch(), local)
            )
            if args.plan_out:
                plan.save(args.plan_out)
            if operation == "import" and (args.apply or args.apply_all):
                with AgentsConfig(path) as config:
                    emit(
                        config.catalog_apply(
                            plan,
                            args.apply,
                            types=args.types.split(",") if args.types else None,
                        )
                    )
            else:
                report = plan.model_dump(mode="json", exclude={"snapshot"})
                if args.types:
                    keys = {
                        p.model_key
                        for p in plan.snapshot.profiles
                        if p.model_type
                        in {normalize_model_type(t) for t in args.types.split(",")}
                    }
                    for group in ("added", "changed", "unchanged"):
                        report[group] = [key for key in report[group] if key in keys]
                    report["differences"] = {
                        key: diff
                        for key, diff in report["differences"].items()
                        if key in keys
                    }
                report["source"] = plan.snapshot.model_dump(exclude={"profiles"})
                emit(report)
        elif operation in {"create", "edit"}:
            profile = CapabilityProfile.model_validate_json(
                Path(args.file).read_text(encoding="utf-8")
            )
            old = by_key.get(profile.model_key)
            if operation == "edit":
                if args.key != profile.model_key or old is None:
                    raise ValueError("编辑键与已有档案不一致")
                emit(
                    {
                        "old": content(old),
                        "new": content(profile),
                        "references": references(args.key),
                        "revision": revision(old),
                    }
                )
                if args.apply and not args.expected_revision:
                    raise ValueError("应用编辑必须填写预览中的 --expected-revision")
            else:
                if old is not None:
                    raise ValueError("档案已存在，请使用 edit")
                emit({"new": content(profile)})
            if args.apply:
                with AgentsConfig(path) as config:
                    result = (
                        config.catalog_edit(
                            profile, expected_revision=args.expected_revision
                        )
                        if operation == "edit"
                        else config.catalog_create(profile)
                    )
                    emit({"saved": result.model_key, "revision": revision(result)})
        elif operation == "delete":
            profile = by_key[args.key]
            emit({"delete": content(profile), "references": references(args.key)})
            if args.apply:
                with AgentsConfig(path) as config:
                    config.catalog_delete(args.key)
                    emit({"deleted": args.key})
    except (AgentsConfigError, ValueError, KeyError, OSError, sqlite3.Error) as exc:
        parser.exit(1, f"{exc}\n")
