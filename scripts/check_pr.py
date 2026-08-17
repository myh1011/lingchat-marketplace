#!/usr/bin/env python3
"""机器检查：市场 PR 审核规则集（§5.0 变更集扫描 + §5.1 一票否决规则）。

用法：
    python3 scripts/check_pr.py registry/ [--base-plugins plugins.json] [--json-out report.json]

退出码：
    0 = 通过（无 error 级 finding）
    1 = 存在 error 级 finding（一票否决，PR 应被拒绝）

规则编号对应 docs/marketplace-design.md §5.0 / §5.1。
纯规则、零 LLM 成本；LLM 语义审查在 review_llm.py。
"""

import argparse
import hashlib
import ipaddress
import json
import re
import sys
import tomllib
from pathlib import Path
from urllib.parse import urlparse

# ─── 常量 ───────────────────────────────────────────────────────

MAX_FILE_SIZE = 5 * 1024 * 1024        # 单文件 >5MB 拒绝（§5.0）
MAX_FILE_COUNT = 100                    # 单 PR 文件数上限（§5.0）
MAX_BASE64_BLOCK = 1024                 # base64/hex 连续块 >1KB 拒绝（R6）
ARCHIVE_EXTS = {".zip", ".7z", ".tar", ".gz", ".tgz", ".rar", ".bz2", ".xz"}
HIDDEN_FILES = re.compile(r"(^|/)(\.env(\.|$)|\.git|\.gitignore$|__MACOSX|\.DS_Store)")
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")

# 顶层模块黑名单：RustPython 沙箱在顶层执行后才拦截，顶层 import 真实可利用
BLOCKED_TOP_IMPORTS = re.compile(
    r"^\s*(?:import|from)\s+(os|subprocess|shutil|pathlib|ctypes|sysconfig)\b",
    re.MULTILINE,
)
# 动态导入/执行：R8/R9
# 排除方法调用（如 re.compile / obj.eval），只拦裸调用：避免误伤正则编译等安全用法
DANGEROUS_CALLS = re.compile(
    r"(importlib\s*\.|__import__\s*\(|(?<![.\w])eval\s*\(|(?<![.\w])exec\s*\(|(?<![.\w])compile\s*\()"
)
# RustPython 逃逸特征（R10）
ESCAPE_PATTERNS = re.compile(
    r"(__subclasses__\s*\(|__builtins__\b|__globals__\s*\[)"
)
# base64 / hex 大块（R6）
BASE64_BLOCK = re.compile(r"[A-Za-z0-9+/=]{1024,}")
HEX_BLOCK = re.compile(r"(?:\\x[0-9a-fA-F]{2}){512,}")

# 高置信密钥格式（R3，gitleaks 的轻量版）
SECRET_PATTERNS = [
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AWS Access Key"),
    (re.compile(r"\bghp_[0-9A-Za-z]{36}\b"), "GitHub PAT"),
    (re.compile(r"\bsk-[0-9A-Za-z]{20,}\b"), "OpenAI 风格密钥"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), "Google API Key"),
    (re.compile(r"(?i)api[_-]?key\s*[:=]\s*['\"][^'\"]{16,}['\"]"), "内联 API Key"),
]

# ELF / APK / Mach-O magic（R4）
BINARY_MAGIC = [
    (b"\x7fELF", "ELF"),
    (b"PK\x03\x04", "ZIP（含 APK）"),
    (b"\xfe\xed\xfa\xce", "Mach-O 32"),
    (b"\xfe\xed\xfa\xcf", "Mach-O 64"),
    (b"\xca\xfe\xba\xbe", "Mach-O 通用"),
]

# 内网/保留地址段（R13）
PRIVATE_NETS = ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8", "169.254.0.0/16", "0.0.0.0/8"]

# 读工具免声明（call_tool 白名单，R14）；写工具必须 manifest [[permissions.tools]] 声明
READ_TOOLS = {
    "get_current_time", "schedule_get_all",
    "memory_get_current", "memory_get_notes",
    "status_get_current", "status_get_scene",
    "scene_list", "character_list",
}

# URL 提取：字符串字面量里的 http(s) URL
URL_RE = re.compile(r"https?://[^\s\"'<>()\[\]]+")


# ─── 工具函数 ───────────────────────────────────────────────────

def err(rule, file, line, detail):
    return {"severity": "error", "rule": rule, "file": str(file), "line": line, "detail": detail}


def warn(rule, file, line, detail):
    return {"severity": "warn", "rule": rule, "file": str(file), "line": line, "detail": detail}


def is_private_host(host: str) -> bool:
    host = host.lower()
    if host in ("localhost", "::1") or host.endswith(".local"):
        return True
    if host.startswith("127."):
        return True
    try:
        ip = ipaddress.ip_address(host)
        return any(ip in ipaddress.ip_network(n) for n in PRIVATE_NETS)
    except ValueError:
        return False


def scan_file_bytes(path: Path) -> bytes | None:
    """读取文件头 8 字节用于 magic 检测；大文件只读头部避免内存压力。"""
    try:
        with open(path, "rb") as f:
            return f.read(8)
    except OSError:
        return None


def text_lines(path: Path) -> list[str]:
    """按行读文本；非 UTF-8 容错。"""
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []


# ─── manifest 解析 ──────────────────────────────────────────────

def parse_manifest(pkg_dir: Path) -> tuple[dict | None, list]:
    """解析统一包壳 manifest.toml，返回 (manifest, findings)。"""
    mf_path = pkg_dir / "manifest.toml"
    findings = []
    if not mf_path.exists():
        return None, [err("R1", pkg_dir, 0, "缺少 manifest.toml")]
    try:
        with open(mf_path, "rb") as f:
            m = tomllib.load(f)
    except Exception as e:
        return None, [err("R1", mf_path, 0, f"manifest.toml 解析失败: {e}")]

    # R1: 基本字段
    for field in ("id", "name", "type", "version"):
        if field not in m or not str(m[field]).strip():
            findings.append(err("R1", mf_path, 0, f"缺少必填字段 {field}"))
    if m.get("type") not in ("plugin", "character", "script", "voice"):
        findings.append(err("R1", mf_path, 0, f"type '{m.get('type')}' 非法（plugin/character/script/voice）"))
    v = str(m.get("version", ""))
    if not VERSION_RE.match(v):
        findings.append(err("R1", mf_path, 0, f"version '{v}' 不是 semver x.y.z"))
    # id 字符集：字母数字下划线连字符，禁止路径穿越
    if not re.match(r"^[A-Za-z0-9_-]+$", str(m.get("id", ""))):
        findings.append(err("R1", mf_path, 0, f"id '{m.get('id')}' 只能包含字母数字下划线连字符"))
    if m.get("type") == "plugin" and not m.get("tools"):
        findings.append(err("R1", mf_path, 0, "plugin 类型必须声明至少一个工具"))
    return m, findings


# ─── 规则实现 ───────────────────────────────────────────────────

def check_package(pkg_dir: Path, base_versions: dict[str, str]) -> list:
    """对单个包目录执行全部规则，返回 findings。"""
    findings = []
    manifest, mf_findings = parse_manifest(pkg_dir)
    findings += mf_findings
    if manifest is None:
        return findings

    pkg_id = str(manifest.get("id", ""))
    pkg_type = str(manifest.get("type", ""))
    version = str(manifest.get("version", ""))

    # §5.0: 文件数 / 单文件大小 / 归档 / magic
    files = [p for p in pkg_dir.rglob("*") if p.is_file()]
    if len(files) > MAX_FILE_COUNT:
        findings.append(err("5.0", pkg_dir, 0, f"文件数 {len(files)} 超过上限 {MAX_FILE_COUNT}"))
    for f in files:
        rel = f.relative_to(pkg_dir)
        if f.suffix.lower() in ARCHIVE_EXTS:
            findings.append(err("5.0", f, 0, f"归档文件 {rel} 不允许进 git（解包提交或走 [[assets]] 大文件通道）"))
        size = f.stat().st_size
        if size > MAX_FILE_SIZE:
            findings.append(err("5.0", f, 0, f"{rel} 大小 {size} 超过 {MAX_FILE_SIZE}（走 [[assets]] 大文件通道）"))

    # R2: 版本递增（与 main 分支已有版本比对）
    if pkg_id in base_versions and version <= base_versions[pkg_id]:
        findings.append(err("R2", pkg_dir / "manifest.toml", 0,
                            f"版本 {version} 不递增（现有 {base_versions[pkg_id]}）"))

    # R5: 隐藏文件
    for f in files:
        rel = str(f.relative_to(pkg_dir))
        if HIDDEN_FILES.search(rel):
            findings.append(err("R5", f, 0, f"隐藏/平台文件 {rel} 不允许"))

    # R4: 可执行二进制 magic（payload 内）
    for f in files:
        head = scan_file_bytes(f)
        if not head:
            continue
        for magic, label in BINARY_MAGIC:
            if head.startswith(magic):
                findings.append(err("R4", f, 0, f"{f.relative_to(pkg_dir)} 检测到 {label} 可执行格式"))
                break

    # R3: 高置信密钥
    for f in files:
        if f.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".onnx", ".bin", ".model"}:
            continue
        for line_no, line in enumerate(text_lines(f), 1):
            for pat, label in SECRET_PATTERNS:
                if pat.search(line):
                    findings.append(err("R3", f, line_no, f"疑似 {label}"))

    # R6: base64/hex 大块
    for f in files:
        for line_no, line in enumerate(text_lines(f), 1):
            if BASE64_BLOCK.search(line) or HEX_BLOCK.search(line):
                findings.append(err("R6", f, line_no, "检测到大块编码载荷（>1KB）"))

    # ── 插件专用 ──
    if pkg_type == "plugin":
        payload_dir = pkg_dir / "payload"
        py_files = [p for p in (payload_dir.rglob("*.py") if payload_dir.exists() else pkg_dir.rglob("*.py"))]
        network_allow = {n.get("host") for n in manifest.get("network", []) if n.get("host")}
        allowed_tools = READ_TOOLS | {t.get("name") for t in manifest.get("permissions", {}).get("tools", [])}
        # 兼容扁平写法 [[permissions.tools]] 直接挂在根
        if "permissions" in manifest and isinstance(manifest["permissions"], dict):
            pass

        for py in py_files:
            lines = text_lines(py)
            text = "\n".join(lines)

            # 顶层字符串常量（R12 静态追踪用）：NAME = "..."，无 f-string 插值
            top_constants: dict[str, str] = {}
            for line in lines:
                m = re.match(r'^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*"([^"{]*)"\s*$', line)
                if m:
                    top_constants[m.group(1)] = m.group(2)

            # R8: 顶层 import 黑名单
            for m in BLOCKED_TOP_IMPORTS.finditer(text):
                line_no = text[:m.start()].count("\n") + 1
                findings.append(err("R8", py, line_no, f"导入禁用模块 '{m.group(1)}'（顶层执行不受沙箱拦截）"))

            # R9: 动态导入/执行
            for m in DANGEROUS_CALLS.finditer(text):
                line_no = text[:m.start()].count("\n") + 1
                findings.append(err("R9", py, line_no, f"危险调用 {m.group(1).strip()}"))

            # R10: 逃逸特征
            for m in ESCAPE_PATTERNS.finditer(text):
                line_no = text[:m.start()].count("\n") + 1
                findings.append(err("R10", py, line_no, f"解释器逃逸特征 {m.group(1).strip()}"))

            # R11/R12/R13: URL 规则
            for m in URL_RE.finditer(text):
                line_no = text[:m.start()].count("\n") + 1
                url = m.group(0)
                try:
                    parsed = urlparse(url)
                except ValueError:
                    continue
                host = (parsed.hostname or "").lower()
                if not host:
                    continue
                if is_private_host(host):
                    if host not in network_allow:
                        findings.append(err("R13", py, line_no, f"内网/本地地址 {host}（需在 manifest [[network]] 显式声明）"))
                    continue
                if host not in network_allow:
                    findings.append(err("R11", py, line_no, f"未声明域名 {host}（请在 manifest [[network]] 声明或移除）"))

            # R12: http_get/http_post 的参数必须是字符串字面量或顶层常量（可静态追踪）
            for m in re.finditer(r"\b(http_get|http_post)\s*\(\s*([^,)\n]+)", text):
                arg_raw = m.group(2).strip()
                arg = arg_raw.strip("\"'")
                is_literal = arg_raw.startswith(("'", '"'))
                is_known_const = arg in top_constants
                if not (is_literal or is_known_const):
                    line_no = text[:m.start()].count("\n") + 1
                    findings.append(err("R12", py, line_no,
                                        f"{m.group(1)}() 的 URL 参数 '{arg_raw}' 无法静态追踪（动态构造禁止）"))

            # R14: call_tool 声明核对
            for m in re.finditer(r'call_tool\s*\(\s*["\']([^"\']+)["\']', text):
                tool_name = m.group(1)
                if tool_name not in allowed_tools:
                    line_no = text[:m.start()].count("\n") + 1
                    findings.append(err("R14", py, line_no,
                                        f"call_tool('{tool_name}') 未声明（写工具需 [[permissions.tools]] 声明）"))

    # R7: 资源完整性——manifest 声明的脚本必须存在
    for tool in manifest.get("tools", []):
        script = str(tool.get("script", ""))
        if script:
            candidates = [pkg_dir / "payload" / script, pkg_dir / script]
            if not any(c.exists() for c in candidates):
                findings.append(err("R7", pkg_dir / "manifest.toml", 0,
                                    f"工具 {tool.get('name')} 声明的脚本 {script} 在包内不存在"))

    # R15: 内容类 Markdown 外部 URL（character/script 的 README/描述）
    if pkg_type in ("character", "script"):
        md_files = [p for p in pkg_dir.rglob("*.md")]
        for md in md_files:
            for line_no, line in enumerate(text_lines(md), 1):
                for m in URL_RE.finditer(line):
                    host = (urlparse(m.group(0)).hostname or "").lower()
                    if host and not is_private_host(host):
                        findings.append(warn("R15", md, line_no, f"外部 URL {host}（需 LLM 判断合法性）"))

    return findings


# ─── main ───────────────────────────────────────────────────────

def load_base_versions(path: str | None) -> dict[str, str]:
    """从 main 分支 plugins.json 读取 {id: version}，用于版本递增检查。"""
    if not path:
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {e.get("id"): e.get("version", "") for e in data.get("plugins", [])}
    except (OSError, json.JSONDecodeError):
        return {}


def main() -> int:
    ap = argparse.ArgumentParser(description="市场 PR 机器检查")
    ap.add_argument("registry_dir", help="registry/ 目录路径")
    ap.add_argument("--base-plugins", default=None, help="main 分支 plugins.json（版本递增检查）")
    ap.add_argument("--pkgs", default=None, help="只检查这些包目录名（空格分隔），缺省全量")
    ap.add_argument("--json-out", default=None, help="报告输出路径")
    args = ap.parse_args()

    registry = Path(args.registry_dir)
    if not registry.is_dir():
        print(json.dumps({"verdict": "error", "findings": [err("R1", registry, 0, "registry 目录不存在")]}, ensure_ascii=False))
        return 1

    only = set(args.pkgs.split()) if args.pkgs else None
    base_versions = load_base_versions(args.base_plugins)
    findings: list = []
    for pkg_dir in sorted(registry.iterdir()):
        if not pkg_dir.is_dir() or pkg_dir.name.startswith("."):
            continue
        if only is not None and pkg_dir.name not in only:
            continue
        findings += check_package(pkg_dir, base_versions)

    errors = [f for f in findings if f["severity"] == "error"]
    report = {
        "verdict": "reject" if errors else "pass",
        "machine": {"error_count": len(errors), "warn_count": len(findings) - len(errors)},
        "findings": findings,
    }
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))

    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
