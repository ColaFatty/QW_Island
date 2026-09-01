# QoderWork Dynamic Island 项目文档

> 桌面悬浮 Widget —— 实时监控 QoderWork AI 助手任务状态，支持岛内直连问答与审批。
> V5.0 | 2026 年 8 月 | 内部分享

---

## 一、项目概述

QoderWork Dynamic Island（灵动岛）是一个 Windows 桌面悬浮 Widget，以药丸形态常驻屏幕顶部，实时显示 QoderWork AI 助手的任务运行状态。灵感来源于 Apple Dynamic Island 与安卓灵动胶囊设计。

**核心能力：**
- 实时监控任务状态（空闲/运行/等待审批/等待回答/完成/失败）
- **岛内直连问答**：AI 通过 AskUserQuestion 提问时，灵动岛直接展示选项并可一键回传答案
- **岛内直连审批**：AI 请求执行高危操作时，灵动岛直接展示待审批命令，点"允许/拒绝"直达 QW 弹窗
- 金鱼小胖吉祥物动画随任务状态自动切换
- 双击药丸毫秒级唤起 QoderWork（Win32 直连，协议降级兜底）
- 拖拽移动 + 窗口位置记忆

## 二、技术架构

| 项目 | 说明 |
|---|---|
| 开发语言 | Python 3.12 |
| GUI 框架 | tkinter（无边框悬浮窗口 + Canvas 绘图） |
| 图像处理 | Pillow（4x 超采样抗锯齿 + LANCZOS 缩放） |
| 数据读取 | SQLite（WAL 只读模式，不影响 QoderWork） |
| **问答/审批直连** | **CDP（Chrome DevTools Protocol）**：通过 QW renderer 调试端口连接页面 DOM |
| 窗口唤起 | Win32 API 直接激活（<50ms）优先 + qoder-work:// 协议兜底 |
| 打包方式 | PyInstaller 单文件 exe（约 32 MB） |
| 轮询间隔 | 2 秒 |

### 双通道数据流

```
┌─────────────────────────────┐
│  QoderWork (Electron)       │
│  ┌───────────┐ ┌──────────┐ │
│  │ agents.db │ │ Renderer │ │── CDP 调试端口（随机，实测 127.0.0.1:1678）
│  └───────────┘ └──────────┘ │
└──────────┬──────────────┬───┘
           │ SQLite 轮询   │ DOM 探测/操作
           ▼              ▼
      ┌────────────────────────┐
      │  Dynamic Island        │
      │  DataPoller + QWCDP    │
      └────────────────────────┘
```

- **状态监控**：2 秒轮询 `%APPDATA%\QoderWork\data\agents.db`（WAL 只读）
- **AskUserQuestion 问答**：DB 中运行时 key 记录提问 → CDP 读取选项 → 岛内展示 → 点击后 CDP 回传
- **高危操作审批**：**QW 弹窗期间不写任何 DB 信号**（实测 ext 无变化），只能通过 CDP 主动探测页面 DOM 中的 PermissionCard 组件（文案双重验证定位"允许/拒绝"按钮），点击后直达弹窗

### 核心模块

| 模块 | 职责 |
|---|---|
| `DataPoller` | SQLite 轮询、任务状态机、CDP 探测缓存（2.5s TTL） |
| `QWCDP` | CDP 连接管理：问答读取/回传、审批探测/点击、窗口激活 |
| `IslandUI` | 药丸+面板渲染、动画循环、卡片绘制（审批/问答/任务） |
| `_goto_qw` | 窗口唤起：Win32 直接激活优先，协议兜底 |

## 三、文件清单

```
QW灵动岛/
├── dynamic_island_v2.py        主程序（单文件，约 67KB）
├── QoderWork_Island.spec       PyInstaller 打包配置
├── install.bat                 一键安装器
├── QoderWork_Island_Guardian.vbs  智能守护脚本（跟随 QW 启停）
├── island.ico                  应用图标（16~256 多尺寸）
├── mascot_64/                  吉祥物动画素材（36 帧，6 类状态）
├── Q_images/                   设计源稿（含金鱼原图 logo.png）
├── dist/                       构建产物 QoderWork_Island.exe
└── outputs/                    文档输出
```

## 四、打包方法

```bash
pyinstaller --noconfirm QoderWork_Island.spec
```

spec 关键项：`icon='island.ico'`、`datas=[('mascot_64', 'mascot_64')]`、`hiddenimports=['websocket']`、`console=False`

## 五、安装方案

`install.bat` 一键完成：
1. 复制 exe + island.ico → `%APPDATA%\QoderWork\Island\`
2. Guardian.vbs → 启动文件夹（开机自启，跟随 QW 自动拉起灵动岛）
3. 创建桌面快捷方式"灵动岛"（金鱼图标）
4. 立即启动

守护脚本逻辑：10 秒轮询 `QoderWork.exe` 进程；QW 启动 → 拉起灵动岛（仅一次）；手动关闭灵动岛 → 不自动重启；QW 重启 → 再次跟随。

## 六、版本历史

| 版本 | 时间 | 要点 |
|---|---|---|
| V1 | 2026-06 | 基础药丸+吉祥物（dynamic_island.py，已废弃删除） |
| V3.x | 2026-07~08 | SQLite 轮询、状态机、问答/审批卡片、位置记忆 |
| V4.0 | 2026-07-14 | 产品化交付包（守护脚本/安装器/图标/指南，参考版本已归档） |
| **V5.0** | 2026-08-10 | **CDP 直连问答+审批、高危弹窗主动探测、Win32 毫秒级唤起、位置记忆、无方框卡片 UI、island.ico 图标、守护+安装整合** |

## 七、关键技术细节

### CDP 连接
- QW renderer 带 `--remote-debugging-port=0`（随机端口）
- 通过 QW 主进程命令行解析调试端口 → 浏览器页面列表 → 匹配 chat 页面 WebSocket URL
- `websocket.create_connection(url, timeout, suppress_origin=True)` 绕过 Chrome 130+ 跨源拒绝

### 高危审批探测（probe_permission）
- 高危弹窗期间 DB 无任何信号 → 轮询前主动探测页面 DOM
- 找含"高危操作"文本的最短容器 → 提取命令文本（code/pre 优先，容器全文清洗兜底）
- 结果缓存 2.5 秒，避免频繁连接调试端口

### 审批点击（approve_tool）
- 按钮文本精确匹配"允许/拒绝" + 向上 6 层祖先含"高危操作"双重验证
- 防误点其他界面按钮

### 窗口唤起（_goto_qw）
- Win32：EnumWindows 按进程名找窗口 + SetWindowPos + SetForegroundWindow + 前台锁绕过（<50ms）
- 兜底：`qoder-work://notification-click?chatId=...` 协议触发（恢复托盘隐藏窗口）
