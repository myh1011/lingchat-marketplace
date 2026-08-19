#!/usr/bin/env python3
"""注册表更新：把打包元数据合并进 plugins.json（按 id 覆盖旧版本，保留历史条目不删）。

用法：
    # 新增/更新条目
    python3 scripts/update_registry.py --meta meta.json
    # 下架（标记 delisted，客户端隐藏，已装用户保留）
    python3 scripts/update_registry.py --delist <id>
    # 重新上架（去 delisted 标记）
    python3 scripts/update_registry.py --relist <id>

约定：
    - plugins.json 的 plugins 数组按 id 唯一，新版本覆盖旧条目（旧版本 zip 保留在 Releases 可回滚）
    - delisted 标记：客户端市场列表过滤，已装用户保留
    - 纯文字帖无安装物，不进 plugins.json（由 Discussions 兼容层处理）
"""

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description="更新 plugins.json")
    ap.add_argument("--meta", help="build_package.py 输出的元数据 JSON")
    ap.add_argument("--registry", default="plugins.json")
    ap.add_argument("--out", default="plugins.json")
    ap.add_argument("--review-report-url", default="", help="本次审核报告链接（issue/PR URL）")
    ap.add_argument("--delist", metavar="ID", help="标记指定 id 的包为已下架（delisted: true）")
    ap.add_argument("--relist", metavar="ID", help="去指定 id 的包的 delisted 标记（重新上架）")
    args = ap.parse_args()

    registry_path = Path(args.registry)
    if registry_path.exists():
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
    else:
        registry = {"schema_version": 1, "plugins": []}
    plugins = registry.setdefault("plugins", [])

    # 下架 / 重新上架
    if args.delist or args.relist:
        target = args.delist or args.relist
        action = "下架" if args.delist else "重新上架"
        for p in plugins:
            if p.get("id") == target:
                if args.delist:
                    p["delisted"] = True
                else:
                    p.pop("delisted", None)
                Path(args.out).write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                print(f"[update_registry] {action} {target}")
                return 0
        # 条目不存在：下架时自动创建占位条目（仅 id + delisted），重新上架时报错
        if args.delist:
            plugins.append({"id": target, "name": target, "type": "character", "version": "0.0.0", "delisted": True})
            Path(args.out).write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"[update_registry] {action} {target}（占位条目，原条目不存在）")
            return 0
        print(f"[update_registry] 错误：'{target}' 不在 plugins.json 中，无法重新上架", file=sys.stderr)
        return 1

    if not args.meta:
        ap.error("--meta 或 --delist/--relist 必须提供一个")

    meta = json.loads(Path(args.meta).read_text(encoding="utf-8"))

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
