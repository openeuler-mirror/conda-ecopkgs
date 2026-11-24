# CI 环境变量说明

本文档列出 conda-ecopkgs CI（Jenkins）及其脚本使用的环境变量。
未设置时均有缺省值，缺省行为与原有 CI 完全一致（仅安装验证，不跑测试、不产出报告）。

## Jenkins Job 注入的 PR 触发变量（原有）

| 变量 | 必填 | 说明 |
| --- | --- | --- |
| `prid` | 是 | PR 编号，由 PR 触发器自动注入 |
| `repo` | 是 | PR 的 source repo（如 `unac/conda-ecopkgs`） |
| `branch` | 是 | PR 的 source branch |

## 代码来源切换（测试期使用）

| 变量 | 缺省值 | 说明 |
| --- | --- | --- |
| `CI_REPO_SLUG` | `openeuler/conda-ecopkgs` | 代码仓库 slug，同时决定：check.sh clone 的仓库、PR API 地址（`https://api.gitcode.com/api/v5/repos/{slug}/pulls`）、origin 对比基准 |
| `CI_CODE_BRANCH` | 仓库默认分支 | check.sh clone 时使用的分支。测试期指向携带新脚本的 `ci-test` 分支；上游环境无需设置 |

## 测试与报告行为

| 变量 | 缺省值 | 说明 |
| --- | --- | --- |
| `TEST_ARCH` | `aarch64` | 允许执行测试用例并产出报告的节点架构。CI 在 x86/ARM 各有一个 job，只有匹配该架构的节点跑测试；其余节点仅做安装验证 |
| `HARDWARE` | `kunpeng920` | 报告 `hardware` 字段取值（KunpengTest schema 枚举：`kunpeng920` / `kunpeng950`），按 ARM 节点实际机型设置 |
| `JOB_URL` | 空 | CI Job 地址，写入报告 `job_url` 字段，可选 |
| `REPORT_SCHEMA_VERSION` | `0.6` | 报告 schema 版本。KunpengTest schema 升级（枚举加入 `conda`）后改为 `0.7` |
| `REPORT_INSTALL_METHOD` | `conda` | 报告 `install_method` 字段取值。当前 schema 0.6 枚举不含 `conda`，校验会报错，待 schema 0.7 发布后恢复合法 |
| `DRY_RUN` | 关闭 | 设为任意非空值（如 `1`）时：跳过 PR 评论与 dashboard 推送，改为在日志中打印 |

### 目标 OS

测试 + 报告仅对 `config/os-versions.txt` 中声明的 OS 版本执行（每行一个，兼容 `openEuler-` 前缀写法），
其余 OS 版本的新增条目仅做安装验证。该文件不是环境变量，由仓库维护者更新。

## 测试报告推送（upstream-dashboard）

| 变量 | 缺省值 | 说明 |
| --- | --- | --- |
| `DASHBOARD_PUSH_DISABLE` | `1`（禁用） | 设为 `0` 或空开启推送。推送目标为 upstream-dashboard 仓库 `KunpengTest` 分支，目录 `<首字母>/<软件名>/<版本>/` |
| `DASHBOARD_REPO_SLUG` | 空 | 报告仓库 slug（如 `opensourceways/upstream-dashboard`）；为空则跳过推送 |
| `DASHBOARD_BRANCH` | `KunpengTest` | 报告仓库的目标分支 |
| `DASHBOARD_GIT_TOKEN` | 回退到 `GITCODE_API_TOKEN` | 报告仓库写权限 token，通过 Jenkins credential 注入 |

## 凭证（原有）

| 变量 | 说明 |
| --- | --- |
| `GITCODE_API_TOKEN` | GitCode API token，需具备读取 PR 信息/文件、发布 PR 评论权限 |

## Jenkins ARM job 参考配置（CI 实测期）

```bash
rm -f check.sh
curl -fSL -o check.sh https://raw.gitcode.com/unac/conda-ecopkgs/raw/ci-test/scripts/check.sh
chmod +x check.sh

export CI_REPO_SLUG="unac/conda-ecopkgs"
export CI_CODE_BRANCH="ci-test"
export HARDWARE="kunpeng920"          # 按节点实际机型填
export JOB_URL="${JOB_URL:-}"         # Jenkins 自动注入或留空
# export DASHBOARD_PUSH_DISABLE=0     # 联调报告推送时开启
# export DASHBOARD_REPO_SLUG="opensourceways/upstream-dashboard"

./check.sh
```

回退上游配置：去掉 `CI_REPO_SLUG` / `CI_CODE_BRANCH` 两个环境变量、raw URL 改回
`openeuler/conda-ecopkgs/raw/master`，即可恢复原有行为。
