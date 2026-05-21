# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""agentconf TUI 应用入口"""

import argparse


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="agentconf",
        description="agentconf 配置管理 TUI 工具",
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="数据库文件路径（默认: ~/.agentconf/config.db）",
    )
    args = parser.parse_args()

    from agentconf.tui.app import agentconfApp

    app = agentconfApp(db_path=args.db_path)
    app.run()
