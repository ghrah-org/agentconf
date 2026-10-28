# 随包能力种子

`catalog_seed.json` 由 models.dev 的独立规范目录生成。真实模型数据来自
[models.json](https://models.dev/models.json)，上游项目采用
[MIT License](https://github.com/anomalyco/models.dev/blob/dev/LICENSE)，版权为
Copyright (c) 2025 models.dev。发行包附带 `LICENSES/MIT-models.dev.txt`。

种子头记录原始快照的 SHA256、来源 URL、规范化版本和生成时间。这里的真实模型
保留 `source=models.dev`、`origin=upstream`；`seed` 是初始化方式，不是能力来源。七条 `example-*`
由本项目编写，使用 Apache-2.0，均为虚构能力示范，`origin=example`。

维护者先保存原始 models.json，再使用固定时间重建，以便复现文件内容：

```sh
uv run python scripts/build_catalog_seed.py /path/to/models.json \
  --snapshot-at 2026-10-27T00:00:00Z
```

种子仅随 schema 首次初始化写入一次。升级包、重开数据库或主动清空目录后，程序都
不会重新播种；后续新增通过显式导入接受；手动升级可以更新同来源 upstream 原件，并保护 local/derived/example 档案。不要在这里放连接地址、API Key 或 Agent。

当前封装版本为 schema 5 / normalizer 4，保留同一份原始 models.json 的 SHA256；本次未重新拉取模型数据。旧导入/更新计划需要重新预览。
