# conda-ecopkgs

## 介绍

这里发布在openEuler上安装验证通过的conda软件包，包括软件包的相关信息和软件包的安装验证脚本。

## 目录
### packages/
存放每个conda软件包的验证信息
```
# packages/
relion/
	|── package.yml                # 保存软件包基本信息，包括软件包名、描述、使用方法
	|── supported-versions.yml     # 当前软件包在openEuler不同版本上的支持验证情况
```

## 贡献指南
1. （新增软件包需求）开发者可根据需求在本仓库`packages/`目录下增加新的conda包（按照上述目录结构和文件要求增加内容），CI会根据新增软件包提供的脚本执行验证，验证通过后由maintainer合入
2. （新增支持版本）开发者可在`packages/{pkg}/supported-versions.yml`文件中新增支持的软件版本，待CI验证后由maintainer合入
3. 暂不支持删除已验证过的版本支持信息。

## 包功能测试（tests/）

在`packages/{pkg}/tests/cases.yml`中声明测试用例。CI在ARM节点上对最新openEuler版本（见`config/os-versions.txt`）的新增条目执行安装验证后会继续执行测试用例，任一用例`fail/error`将导致CI失败；未声明用例的包维持"仅安装验证"行为。

```yaml
# packages/{pkg}/tests/cases.yml
cases:
  - name: import                  # 用例名，结果文件内唯一
    description: "导入 bcrypt 模块"  # 用例描述（写入测试报告）
    category: functional            # functional | performance
    cmd: python -c "import bcrypt; print('ok')"
    timeout: 60                    # 秒，缺省 120
    # skip: true                   # 可选，跳过该用例；blocked: true 标记为被阻塞
```

说明：
- 用例在验证容器（`openeuler/conda:<版本>-<os>`）内、已激活的隔离conda环境中执行
- 包目录 `tests/` 下的辅助脚本/数据会挂载到容器 `/tests`，用例可通过相对路径调用
- 用例可通过环境变量感知上下文：`PKG_VERSION`（包版本）、`OS_VERSION`（openEuler版本）、`ARCH`（架构）
- 用例结果映射：退出码 0 → `pass`，非 0 → `fail`，超时 → `error`，声明跳过 → `skip`/`blocked`
- 测试报告按 KunpengTest schema（v0.6，`install_method: conda`）生成，推送至 upstream-dashboard 仓库 `KunpengTest` 分支的 `openeuler-conda-test/` 目录，同时摘要评论到 PR

## 本地验证（无需PR）

在安装了 docker 的 aarch64 机器上，仓库根目录执行：

```bash
pip3 install click requests pyyaml
python3 scripts/update.py --local -lp bcrypt [-lo 24.03-LTS-SP4] [-lpv 4.3.0] [-hw kunpeng920]
```

结果报告写入 `reports/` 目录，退出码 0 表示验证通过。