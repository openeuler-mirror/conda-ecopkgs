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
| `REPORT_SCHEMA_VERSION` | `0.6` | 报告 schema 版本，团队约定固定 `0.6` |
| `REPORT_INSTALL_METHOD` | `conda` | 报告 `install_method` 字段取值，固定 `conda` |
| `DRY_RUN` | 关闭 | 设为任意非空值（如 `1`）时：跳过 PR 评论与 dashboard 推送，改为在日志中打印 |

### 目标 OS

测试 + 报告仅对 `config/os-versions.txt` 中声明的 OS 版本执行（每行一个，兼容 `openEuler-` 前缀写法），
其余 OS 版本的新增条目仅做安装验证。该文件不是环境变量，由仓库维护者更新。

## 测试报告推送（upstream-dashboard，默认开启）

配置了 `DASHBOARD_REPO_URL` 与 `DASHBOARD_GIT_TOKEN` 后，测试完成即自动推送报告到
upstream-dashboard 仓库的 `KunpengTest` 分支，目录为
`openeuler-conda-test/<首字母>/<软件名>/<版本>/`（与 docker-images 的
`openeuler-docker-images-test/` 相互独立）。任一变量缺失时告警跳过推送，不影响 CI 主流程。
每个版本目录最多保留 3 份最新的未合入报告，`PR_merged=true` 的报告永不删除。

| 变量 | 缺省值 | 说明 |
| --- | --- | --- |
| `DASHBOARD_REPO_URL` | 空 | 报告仓库完整 URL（`https://github.com/opensourceways/upstream-dashboard.git`）；为空则跳过推送 |
| `DASHBOARD_BRANCH` | `KunpengTest` | 报告仓库目标分支 |
| `DASHBOARD_RESULTS_DIR` | `openeuler-conda-test` | 仓库内报告根目录 |
| `DASHBOARD_GIT_TOKEN` | 空 | https push token（GitHub PAT，需 upstream-dashboard 写权限），通过临时 credential helper 注入，不出现在 URL / 命令行 / .git/config |
| `DASHBOARD_COMMIT_NAME` / `DASHBOARD_COMMIT_EMAIL` | `conda-ecopkgs-ci` / `ci@openeuler.org` | 报告 commit 的显式身份，CI 节点无需全局 git config |

## 凭证（原有）

| 变量 | 说明 |
| --- | --- |
| `GITCODE_API_TOKEN` | GitCode API token，需具备读取 PR 信息/文件、发布 PR 评论权限 |

## Jenkins ARM job 参考配置（CI 实测期）

Job 参数（String Parameter，与 docker-images 那条流水线同款风格）：

| 参数名 | 默认值 | 描述 |
| --- | --- | --- |
| `DASHBOARD_REPO_URL` | `https://github.com/opensourceways/upstream-dashboard.git` | 测试报告存放仓库 |
| `DASHBOARD_BRANCH` | `KunpengTest` | 测试报告存放仓库分支 |
| `DASHBOARD_RESULTS_DIR` | `openeuler-conda-test` | 测试报告存放仓库分支目录 |

Token 通过 Jenkins credential（Secret text）绑定注入 `DASHBOARD_GIT_TOKEN`。

shell 步骤：

```bash
rm -f check.sh
curl -fSL -o check.sh https://raw.gitcode.com/unac/conda-ecopkgs/raw/ci-test/scripts/check.sh
chmod +x check.sh

export CI_REPO_SLUG="unac/conda-ecopkgs"
export CI_CODE_BRANCH="ci-test"
export HARDWARE="kunpeng920"          # 按节点实际机型填
export JOB_URL="${JOB_URL:-}"         # Jenkins 自动注入或留空
./check.sh
```

回退上游配置：去掉 `CI_REPO_SLUG` / `CI_CODE_BRANCH` 两个环境变量、raw URL 改回
`openeuler/conda-ecopkgs/raw/master`，即可恢复原有行为。
