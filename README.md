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