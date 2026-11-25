# 网盘订阅助手（CloudSubscribe）

整合网盘能力与多渠道资源搜索，为 MoviePilot 订阅自动查找并转存网盘资源。
中文名见插件市场：**网盘订阅助手**。

> **来源声明**：本目录基于上游 [odomu/MoviePilot-Plugins](https://github.com/odomu/MoviePilot-Plugins) 的
> `CloudSubscribe` **1.7.13**，版权与整体设计归原作者 `odomu` 所有。
> 本仓库版本号记为 `1.7.13.1`，仅在其上追加了下面「本地加固」的两处补丁，未改动功能与交互。

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
若手动安装，把整个 `cloudsubscribe` 目录放到 MoviePilot 的插件目录后重载插件。
