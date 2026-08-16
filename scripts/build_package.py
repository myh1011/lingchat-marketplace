#!/usr/bin/env python3
"""打包：把 registry/<pkg>/ 打成统一包壳 zip，输出元数据（sha256/size）。

用法：
    python3 scripts/build_package.py registry/<pkg>/ --out dist/ --release-tag plugin-tavily-0.1.0

输出：dist/<dir-name>-<version>.zip + 打印 JSON 元数据：
    { id, name, type, version, zip_name, sha256, size, download_url }
"""

import argparse
import hashlib
import json
import shutil
import sys
import tomllib
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description="打包市场包")
    ap.add_argument("pkg_dir", help="registry/<pkg>/ 目录")
    ap.add_argument("--out", default="dist", help="输出目录")
    ap.add_argument("--release-tag", required=True, help="Release tag（用于生成下载 URL）")
    ap.add_argument("--repo", default="OWNER/REPO", help="marketplace 仓库 owner/repo")
    args = ap.parse_args()

    pkg_dir = Path(args.pkg_dir)
    mf_path = pkg_dir / "manifest.toml"
    with open(mf_path, "rb") as f:
        manifest = tomllib.load(f)

    pkg_id = str(manifest["id"])
    version = str(manifest["version"])
    zip_name = f"{pkg_dir.name}-{version}.zip"

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / zip_name
    if zip_path.exists():
        zip_path.unlink()

    shutil.make_archive(str(zip_path.with_suffix("")), "zip", root_dir=pkg_dir)
    sha256 = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    size = zip_path.stat().st_size

    meta = {
        "id": pkg_id,
        "name": manifest.get("name", pkg_id),
        "type": manifest.get("type", "plugin"),
        "version": version,
        "zip_name": zip_name,
        "sha256": sha256,
        "size": size,
        "author": manifest.get("author", ""),
        "description": manifest.get("description", ""),
        "download_url": f"https://github.com/{args.repo}/releases/download/{args.release_tag}/{zip_name}",
        "manifest": manifest,
    }
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
