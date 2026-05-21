# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0


"""agentconf TUI 配置管理工具入口

运行方式:
  1. 通过安装后的入口点: agentconf
  2. 直接运行此脚本: python main.py
  3. 使用 uv: uv run agentconf
"""

from agentconf.tui import main

if __name__ == "__main__":
    main()
