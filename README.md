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
	└── verify.sh                  # 当前软件包的验证脚本
```

### scripts/
存放用于本仓库CI验证的脚本文件
scripts/
	|── check.sh                   # 仓库CI脚本
	|── update.py                  # 获取软件包版本更新情况，对新增的软件包和openEuler版本交叉安装验证
	└── verify.sh                  # conda包安装执行脚本，传入软件包名称，软件包channel, 软件包版本(默认最新版本)，依赖的channel列表(可选)

## 贡献指南
1. （新增软件包需求）开发者可根据需求在本仓库`packages/`目录下增加新的conda包（按照上述目录结构和文件要求增加内容），CI会根据新增软件包提供的脚本执行验证，验证通过后由maintainer合入
2. （新增支持版本）开发者可在`packages/{pkg}/supported_version.yml`文件中新增支持的软件版本，待CI验证后由maintainer合入
3. 暂不支持删除已验证过的版本支持信息。