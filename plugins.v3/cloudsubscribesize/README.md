# 网盘订阅助手(文件体积校验版)

整合网盘能力与多渠道资源搜索，为 MoviePilot 订阅自动查找并转存网盘资源。
插件 ID：`CloudSubscribeSize`（市场显示名：**网盘订阅助手(文件体积校验版)**）。

> ID 与原作者版本的 `CloudSubscribe` **不同**，两者可以同时安装、互不覆盖；
> 市场里各占一条，想用哪个装哪个。

## 致谢

这个插件**不是原创**。全部功能设计、代码实现和长期维护都来自 **odomu**，
本目录只是在其基础上做了两处本地加固（见下文）。

- 原版插件仓库：**https://github.com/odomu/MoviePilot-Plugins**
- 原版使用说明：https://github.com/odomu/MoviePilot-Plugins/blob/main/docs/cloudsubscribe.md
- 原作者 GitHub：https://github.com/odomu

如果能用，请去原仓库给 odomu 点个 Star —— 真正干活的是他。
本站版本是**派生版**（版本号 `1.7.13.2`，基于原版 `1.7.13`），版权归原作者所有，
不受本仓库 MIT 许可的重新授权。

## 功能概述

- 多个网盘提供方接入（夸克 / UC / 115 / 123 / 阿里 / 百度 / 天翼 / 移动 / 光鸭），按能力协商，不支持的能力自动跳过。
- 多个搜索渠道（PanSou、片库、HDHive、小雅、卧牛等），搜索 → 匹配 → 转存 → 通知/同步全链路。
- 与 MoviePilot 订阅打通：订阅缺集时自动补搜、转存，并生成 STRM。
- 平台规则组复用 MoviePilot 自身的「规则组 / 自定义规则」，用于过滤清晰度与体积。

## 本地加固（相对上游 1.7.13）

两处改动都带 `PATCH:cloudsubscribe-*` 标记，可用 `grep -rn "PATCH:cloudsubscribe" .` 定位。

### 1. 真实体积校验 —— `handlers/search/platform_rules.py`

**问题**：网盘搜索结果的 `size` 字段恒为 0，规则组的体积门槛在候选阶段完全不起作用；
标题里写的体积也不可信——实测一条标题「大小：7.37GB」的分享，点进去是 3.70GB 的 mp4 加 3.68GB 的 7z，
还有大量只有几百字节的 `.zip` 引流假档。

**做法**：在平台规则过滤阶段读取分享内的真实文件列表，只累加视频扩展名的文件体积，再与体积规则比对；
分享内没有视频文件（纯压缩包/图片/文档）直接跳过；读取失败按「未知」放行，避免网络抖动误杀。

### 2. 夸克提取码解析 —— `drive/quark/share.py`

**问题**：`extract_share_info()` 原来只解析 `提取码|密码|code` 这类文本，遇到
`https://pan.quark.cn/s/xxxx?pwd=abcd` 会拿到空密码，换取 `stoken` 失败，
于是**带提取码的分享既读不到文件列表也转存不了**。

**做法**：保留原有文本解析，匹配不到时再按 `?`/`&` 后的 `pwd`、`password`、`passcode`、`accesscode`
参数兜底。`_share_access()` 是状态检查、列目录、搜索、转存的唯一入口，一处修好全链路生效。

## 目录结构

```text
cloudsubscribe/
├── __init__.py        # 插件入口、元数据、生命周期代理
├── requirements.txt   # Python 依赖
├── core/              # 领域模型、配置、平台适配、通用服务
├── drive/             # 网盘能力接口与各网盘实现
├── handlers/          # 搜索、订阅、同步、通知、API 编排
├── search/            # 搜索源客户端
├── subscribe/         # 订阅相关服务
├── utils/             # 解析、匹配、Magnet、STRM 工具
└── dist/assets/       # Vue 模块联邦前端产物（构建结果，已随仓库提供）
```

## 安装

从本仓库的插件市场安装即可，`dist/assets` 已随源码一同提供，不依赖 Release 资产。
若手动安装，把整个 `cloudsubscribesize` 目录放到 MoviePilot 的插件目录后重载插件。
