#!/usr/bin/env python3
"""注册表更新：把打包元数据合并进 plugins.json（按 id 覆盖旧版本，保留历史条目不删）。

用法：
    python3 scripts/update_registry.py --meta meta.json --registry plugins.json --out plugins.json

约定：
    - plugins.json 的 plugins 数组按 id 唯一，新版本覆盖旧条目（旧版本 zip 保留在 Releases 可回滚）
    - 纯文字帖无安装物，不进 plugins.json（由 Discussions 兼容层处理）
"""

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description="更新 plugins.json")
    ap.add_argument("--meta", required=True, help="build_package.py 输出的元数据 JSON")
    ap.add_argument("--registry", default="plugins.json")
    ap.add_argument("--out", default="plugins.json")
    ap.add_argument("--review-report-url", default="", help="本次审核报告链接（issue/PR URL）")
    args = ap.parse_args()

    meta = json.loads(Path(args.meta).read_text(encoding="utf-8"))

    registry_path = Path(args.registry)
    if registry_path.exists():
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
    else:
        registry = {"schema_version": 1, "plugins": []}

    entry = {
        "id": meta["id"],
        "name": meta["name"],
        "type": meta["type"],
        "version": meta["version"],
        "author": meta.get("author", ""),
        "description": meta.get("description", ""),
        "download_url": meta["download_url"],
        "sha256": meta["sha256"],
        "size": meta["size"],
        "manifest": meta.get("manifest", {}),
        "review_report_url": args.review_report_url,
    }

    plugins = registry.setdefault("plugins", [])
    replaced = False
    for i, p in enumerate(plugins):
        if p.get("id") == meta["id"]:
            plugins[i] = entry
            replaced = True
            break
    if not replaced:
        plugins.append(entry)

    Path(args.out).write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[update_registry] {'更新' if replaced else '新增'} {meta['id']}@{meta['version']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
