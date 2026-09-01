# QW_Island (QoderWork Dynamic Island)

> Windows 桌面悬浮 Widget — 实时监控 QoderWork AI 助手任务状态，支持岛内直连问答与审批。
> A floating desktop widget that monitors your QoderWork AI agent's task status in real time, with in-island question answering and approval.

## 特性 / Features

- **实时状态监控** — 2 秒轮询 QoderWork SQLite（WAL 只读模式，不影响主程序），显示空闲/运行/等待审批/等待回答/完成/失败
- **岛内直连问答** — AI 通过 AskUserQuestion 提问时，灵动岛直接展示选项并可一键回传答案
- **岛内直连审批** — AI 请求执行高危操作时，灵动岛直接展示待审批命令，点"允许/拒绝"直达 QW 弹窗
- **卡通吉祥物动画** — 随任务状态自动切换（金鱼小胖）
- **毫秒级唤起** — 双击药丸通过 Win32 API 直接激活 QoderWork（<50ms），协议降级兜底
- **窗口位置记忆** — 拖拽移动后自动保存，重启恢复

## 技术架构 / Architecture

| 项目 | 说明 |
|---|---|
| 开发语言 | Python 3.12 |
| GUI 框架 | tkinter（无边框悬浮窗口 + Canvas 绘图） |
| 图像处理 | Pillow（4x 超采样抗锯齿 + LANCZOS 缩放） |
| 数据读取 | SQLite（WAL 只读模式，不影响 QoderWork） |
| 问答/审批直连 | CDP（Chrome DevTools Protocol）：通过 renderer 调试端口连接页面 DOM |
| 窗口唤起 | Win32 API 直接激活（<50ms）优先 + `qoder-work://` 协议兜底 |
| 打包方式 | PyInstaller 单文件 exe（约 32 MB） |
| 轮询间隔 | 2 秒 |

**平台要求**：Windows only（依赖 `winsound` / `ctypes.windll` / `os.startfile`）。

## 文件清单 / Files

```
QW_Island/
├── dynamic_island_v2.py        主程序（单文件）
├── QoderWork_Island.spec       PyInstaller 打包配置
├── install.bat                 一键安装器
├── QoderWork_Island_Guardian.vbs  守护脚本（跟随 QoderWork 启停）
├── create_shortcut.vbs         桌面快捷方式（UTF-8 安全，兼容 OneDrive 桌面）
├── island.ico                  应用图标
├── mascot_64/                  吉祥物动画素材（36 帧，6 类状态）
└── outputs/                    项目文档
```

## 构建 / Build

```bash
pyinstaller --noconfirm QoderWork_Island.spec
```

spec 关键项：`icon='island.ico'`、`datas=[('mascot_64', 'mascot_64')]`、`hiddenimports=['websocket']`、`console=False`

## 安装 / Install

双击 `install.bat`（或运行 `_goto_qw` 前的完整安装流程）：

1. 复制 exe + island.ico → `%APPDATA%\QoderWork\Island\`
2. Guardian.vbs → 启动文件夹（开机自启，跟随 QoderWork 自动拉起灵动岛）
3. 创建桌面快捷方式"灵动岛"
4. 立即启动

守护脚本逻辑：10 秒轮询 `QoderWork.exe` 进程；QW 启动 → 拉起灵动岛（仅一次）；手动关闭灵动岛 → 不自动重启；QW 重启 → 再次跟随。

## 关键安全设计 / Security Notes

- **DB 只读**：`sqlite3.connect("file:...?mode=ro")` + WAL，绝不修改 QoderWork 数据
- **CDP 仅回环**：只连接 `127.0.0.1` 调试端口，`suppress_origin=True` 绕过 Chrome 130+ 跨源拒绝
- **审批双重验证**：按钮文本精确匹配"允许/拒绝" + 向上 6 层祖先含"高危操作"文案，防误点其他界面按钮
- **无凭据硬编码**：源码不含任何 API key / token / 个人路径

## License

MIT © ColaFatty