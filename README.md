# LingChat Marketplace（AI 全自动审核插件/内容市场）

基于 GitHub 的集中式注册表市场：作者通过 PR 提交插件/内容，**AI 全自动审核**，通过即合并、自动发布到 Releases，客户端从 `plugins.json` 拉取商店数据。

设计文档：`docs/marketplace-design.md`（LingChat 仓库，[SlimeBoyOwO/LingChat#626](https://github.com/SlimeBoyOwO/LingChat/issues/626) 追踪）。

## 目录结构

```
registry/                     # PR 源目录：每包一个子目录（统一包壳）
  plugin-tavily/
    manifest.toml             # 统一声明：type/id/version/network/tools/permissions/assets
    payload/tavily.py         # 类型专属内容
scripts/
  check_pr.py                 # 机器检查（一票否决规则集，零 LLM 成本）
  review_llm.py               # LLM 语义审查（硅基流动，结构化 verdict，fail-closed）
  build_package.py            # 打包统一包壳 zip + sha256
  update_registry.py          # 更新 plugins.json
.github/workflows/
  review.yml                  # PR 审核：机器检查 → LLM → 门禁（合并/要求修改/关闭/挂起）
  publish.yml                 # 发布链：打包 → 独立 Release → plugins.json
plugins.json                  # 商店数据（bot 生成，客户端拉取）
```

## 提交上架流程

1. fork 本仓库
2. 添加 `registry/<type>-<id>/`（`manifest.toml` + `payload/`），遵守统一包壳格式
3. 提 PR → `AI Review` 工作流自动审核：
   - 机器检查一票否决（禁用模块、URL 白名单、密钥、大文件、编码载荷等）
   - LLM 语义审查（恶意意图/数据外泄/权限越界/内容合规）
   - approve → 自动合并；changes → 留言等修改；reject → 关闭；无法判定 → 挂起等人工
4. 合并后 `Publish` 工作流自动打包、发布 Release、更新 `plugins.json`

## 配置（仓库 Secrets）

| Secret | 说明 |
|---|---|
| `LLM_API_KEY` | 必填。硅基流动 API Key（OpenAI 兼容） |
| `LLM_BASE_URL` | 可选。默认 `https://api.siliconflow.cn/v1` |
| `LLM_MODEL` | 可选。默认 `Qwen/Qwen2.5-7B-Instruct` |

## manifest 格式（统一包壳）

```toml
id = "tavily"                  # 唯一，字母数字下划线连字符
name = "Tavily 搜索"
type = "plugin"                # plugin / character / script / voice
version = "0.1.0"              # semver，必须递增
author = "LingChat"
description = "基于 Tavily 的联网搜索与网页提取"

# 网络白名单：审核比对 + 客户端运行时强制共用
[[network]]
host = "api.tavily.com"
# paths = ["/search"]         # 可选限路径
# https_only = true           # 默认 true

# call_tool 写工具声明（读工具免声明）
# [[permissions.tools]]
# name = "memory_add_note"

# 大文件声明（>5MB 不进 git，bot 审核下载校验后转存 Releases）
# [[assets]]
# name = "model.onnx"
# url = "https://github.com/author/repo/releases/download/v1/model.onnx"
# sha256 = "9f2c..."
# size = 52428800

# 工具声明（plugin 类型）
[[tools]]
name = "tavily_search"
description = "联网搜索，返回相关网页摘要与链接"
timeout_ms = 30000
script = "tavily.py"
parameters = '{ "type":"object", "properties":{ "query":{"type":"string"} }, "required":["query"] }'
```

## 本地验证

```bash
# 机器检查（示例插件应通过）
python3 scripts/check_pr.py registry/

# 打包 + 注册表
python3 scripts/build_package.py registry/plugin-tavily --release-tag plugin-tavily-0.1.0
python3 scripts/update_registry.py --meta <meta.json>

# LLM 审查（需 LLM_API_KEY）
LLM_API_KEY=xxx python3 scripts/review_llm.py registry/ <machine-report.json>
```
