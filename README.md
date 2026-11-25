# MoviePilot 插件仓库（davidjuk）

个人维护的 [MoviePilot v3](https://github.com/jxxghp/MoviePilot) 插件仓库，按官方插件市场规范组织：
`package.v3.json` + `plugins.v3/<plugin_id_lower>/`。

## 插件列表

| 插件 | ID | 版本 | 说明 |
| --- | --- | --- | --- |
| SeedPool 站点支持 | `SeedPoolSite` | 1.3.2 | 为未内建的 SeedPool（UNIT3D）站点补搜索；中文片名自动转 IMDb / 英文名 |
| 网盘订阅助手(文件体积校验版) | `CloudSubscribeSize` | 1.7.13.1 | 网盘订阅 + 多渠道资源搜索；本仓库为 1.7.13 的加固版 |

## 安装

在 MoviePilot 中打开 **设置 → 插件市场 → 添加仓库**，填入：

```
https://github.com/Davidjukk/MoviePilot-Plugins
```

保存后刷新插件市场，即可搜索到上表中的插件并安装。

> 仓库未配置 GitHub Actions，因此所有条目 `release` 均为 `false`，市场直接从源码目录安装，不依赖 Release 资产。

## 目录结构

```text
.
├── package.v3.json                 # 插件市场清单（ID → 元数据）
├── icons/                          # 市场图标
└── plugins.v3/
    ├── seedpoolsite/
    └── cloudsubscribesize/         # 含 dist/assets 前端联邦产物
```

## CloudSubscribeSize 与上游的差异

本仓库的 `cloudsubscribesize` 基于 **odomu** 的开源项目
[odomu/MoviePilot-Plugins](https://github.com/odomu/MoviePilot-Plugins) 的 1.7.13，
**功能与代码全部来自原作者，版权归 `odomu` 所有**，本仓库只做了两处本地加固。
详细的致谢与原文链接见 [该目录的 README](plugins.v3/cloudsubscribesize/README.md)。

- 原版插件地址：https://github.com/odomu/MoviePilot-Plugins
- 原版使用说明：https://github.com/odomu/MoviePilot-Plugins/blob/main/docs/cloudsubscribe.md

两处加固：

1. **真实体积校验**（`handlers/search/platform_rules.py`）
   网盘搜索结果的 `size` 恒为 0，标题里写的体积并不可信（实测标题 7.37GB = 3.70GB mp4 + 3.68GB 7z）。
   补丁在平台规则过滤阶段读取分享内的真实文件列表，只累加视频扩展名的体积，无视频的引流分享直接跳过。
2. **夸克提取码解析**（`drive/quark/share.py`）
   原实现只认 `提取码：xxxx` 这类文本，`https://pan.quark.cn/s/xxx?pwd=abcd` 会拿不到密码，
   导致带码分享无法读取与转存。现补上 `?pwd=` / `&pwd=` / `password=` / `accesscode=` 的参数形式。

两处改动在文件头都留有 `PATCH:cloudsubscribe-*` 标记，便于用 `grep` 确认补丁是否在位。

> **插件 ID 说明**：本加固版使用 `CloudSubscribeSize` 作为 ID，与原作者版本的 `CloudSubscribe`
> **不同**，因此两者可以在同一个 MoviePilot 里共存，市场里也不会互相覆盖。
> 想装原版就加 `odomu` 的仓库，想装加固版就加本仓库，互不影响。

## 维护说明

本仓库里的 `cloudsubscribesize` 本身就是**已打补丁的完整源码**，直接安装即可，无需额外操作。
两处补丁都在文件头留了标记，可用下面的命令确认补丁是否在位：

```bash
grep -rn "PATCH:cloudsubscribe" plugins.v3/cloudsubscribesize/
```

唯一需要注意的是**上游发新版本时**：用上游源码覆盖本目录后，补丁会被冲掉，
需要把这两处改动重新应用（改动点见上一节，标记注释被冲掉即可作为判断依据）。

## 许可

仓库结构与脚本遵循 MoviePilot 插件规范；各插件版权归其作者所有，详见各插件目录内的说明。
