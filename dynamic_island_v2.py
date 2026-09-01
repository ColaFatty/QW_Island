#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
QoderWork Dynamic Island V3
============================
像素风程序员吉祥物 + 多会话列表 + 音效 + 审批/问答卡片
读取 QoderWork SQLite，桌面悬浮显示所有活跃任务状态。

V3 能力：
- qoder-work:// 协议唤起（修复托盘隐藏场景）
- notification-click deep link 导航到具体 chat
- 卡片式审批面板（同意/拒绝按钮 + 工具详情 + 等待时长）
- 文件桥接审批（写命令到 ~/.qoderwork/island_bridge/，仅子任务可用）
- 高危操作直接审批（CDP 点击弹窗"允许/拒绝"，双重验证防误点）
- AskUserQuestion 实时问答（CDP 直连 QW 调试端口读问题/选项，岛上直接回答）
- 窗口位置记忆（拖拽后自动保存，重启恢复）

用法: python dynamic_island_v2.py
"""

import sqlite3, json, threading, time, subprocess, sys, os, math, re, urllib.request
from dataclasses import dataclass, field
from typing import Optional, Callable, List
import tkinter as tk

from PIL import Image, ImageTk
import winsound

# ═══════════════════════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════════════════════

APP_NAME = "QoderWork"
DB_PATH = os.path.expandvars(r"%APPDATA%\QoderWork\data\agents.db")
MASCOT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mascot_64")
POLL_INTERVAL = 2.0
ISLAND_HEIGHT = 44
ISLAND_WIDTH = 320
EXPANDED_HEIGHT = 240
ALPHA = 0.93
PULSE_SPEED = 0.04
BLINK_SPEED = 0.06
EXPAND_SPEED = 0.14
MASCOT_SIZE = 48
MASCOT_FPS = 8
PILL_Y_OFFSET = 8  # 药丸向下偏移，吉祥物突出顶部
PILL_PADDING = 4   # 药丸底部额外留白，防止 outline 被裁切
IDLE_TIMEOUT = 180.0  # 完成 3 分钟后回归 idle 灰色
BRIDGE_DIR = os.path.expandvars(r"%USERPROFILE%\.qoderwork\island_bridge")
LOG_PATH = os.path.join(os.path.expandvars(r"%USERPROFILE%\.qoderwork"), "island.log")
CONFIG_PATH = os.path.join(os.path.expandvars(r"%USERPROFILE%\.qoderwork"), "island_config.json")


def load_window_pos():
    """读取上次保存的窗口位置；无配置或损坏返回 None"""
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        return int(cfg["x"]), int(cfg["y"])
    except Exception:
        return None


def save_window_pos(x: int, y: int):
    """保存窗口位置（拖拽结束时调用），失败不影响主流程"""
    try:
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        cfg = {}
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            pass
        cfg["x"], cfg["y"] = int(x), int(y)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False)
    except Exception as e:
        log(f"[Config] 保存位置失败: {e}")


def log(msg: str):
    """记录日志：开发时输出到控制台，打包后（noconsole）写入 ~/.qoderwork/island.log"""
    print(msg)
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:
        pass

# ═══════════════════════════════════════════════════════════════
# 颜色
# ═══════════════════════════════════════════════════════════════

class C:
    BG          = "#1a1a2e"
    BG_HOVER    = "#222244"
    IDLE        = "#6c757d"
    RUNNING     = "#3b82f6"
    WAIT_PERM   = "#eab308"
    WAIT_ANS    = "#f97316"
    COMPLETED   = "#22c55e"
    FAILED      = "#ef4444"
    TEXT        = "#e2e8f0"
    TEXT_DIM    = "#94a3b8"
    TEXT_BRIGHT = "#ffffff"
    BTN_BG      = "#2a2a4e"
    BTN_HOVER   = "#3a3a6e"
    SEPARATOR   = "#333355"
    BTN_APPROVE = "#22c55e"
    BTN_REJECT  = "#ef4444"
    BTN_TEXT    = "#ffffff"

STATE_COLOR = {
    "idle": C.IDLE, "running": C.RUNNING,
    "wait_perm": C.WAIT_PERM, "wait_answer": C.WAIT_ANS,
    "completed": C.COMPLETED, "failed": C.FAILED,
}

# ═══════════════════════════════════════════════════════════════
# 音效
# ═══════════════════════════════════════════════════════════════

class SoundFX:
    """8-bit 风格音效（非阻塞）"""
    _last_play = 0

    @staticmethod
    def _play(freq, ms):
        now = time.time()
        if now - SoundFX._last_play < 0.5:
            return
        SoundFX._last_play = now
        threading.Thread(target=winsound.Beep, args=(freq, ms), daemon=True).start()

    @staticmethod
    def permission():
        SoundFX._play(880, 100)
        time.sleep(0.12)
        SoundFX._play(1100, 100)

    @staticmethod
    def question():
        SoundFX._play(440, 150)
        time.sleep(0.17)
        SoundFX._play(550, 150)

    @staticmethod
    def complete():
        SoundFX._play(1200, 80)
        time.sleep(0.1)
        SoundFX._play(1500, 120)

    @staticmethod
    def error():
        SoundFX._play(200, 200)
        time.sleep(0.22)
        SoundFX._play(150, 300)

# ═══════════════════════════════════════════════════════════════
# 数据模型
# ═══════════════════════════════════════════════════════════════

@dataclass
class SessionInfo:
    state: str = "idle"
    name: str = ""
    chat_id: str = ""
    updated_at: int = 0
    context_pct: float = 0.0
    current_step: str = ""
    current_tool: str = ""
    tool_input: str = ""       # 工具输入摘要（Bash命令、Write路径等）
    perm_since: float = 0.0    # 权限请求首次检测时间（用于等待时长）
    question_text: str = ""              # AskUserQuestion 问题文本（CDP 读取）
    question_options: List[str] = field(default_factory=list)  # 选项文本列表

@dataclass
class IslandState:
    sessions: List[SessionInfo] = field(default_factory=list)
    primary_state: str = "idle"

# ═══════════════════════════════════════════════════════════════
# QoderWork CDP 客户端（读取 AskUserQuestion 问题/选项 + 模拟点击）
# ═══════════════════════════════════════════════════════════════

class QWCDP:
    """通过 QoderWork 的 CDP 调试端口读取选项卡内容。

    QW 的 renderer 带 --remote-debugging-port 启动，本类从进程命令行 +
    netstat 发现监听端口，无 Origin 直连 WebSocket 执行 JS：
    - fetch_question: 读取 AskUserQuestion 的问题文本和选项
    - answer_option:  模拟点击选项按钮（限 UserQuestion-scroll 容器内）
    所有调用带超时与异常保护，失败返回 None/False，不影响主流程。
    """

    PORT_CACHE_TTL = 30.0  # 端口发现结果缓存 30 秒（QW 重启后最多晚 30s 恢复）

    def __init__(self):
        self._ports: List[int] = []
        self._ports_at = 0.0
        self._last_log = 0.0

    # ── 端口发现 ──
    def _find_ports(self) -> List[int]:
        now = time.time()
        if self._ports and now - self._ports_at < self.PORT_CACHE_TTL:
            return self._ports
        try:
            ps = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'QoderWork' } | Select-Object ProcessId | ConvertTo-Json -Compress"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=8, creationflags=subprocess.CREATE_NO_WINDOW)
            data = json.loads(ps.stdout) if ps.stdout.strip() else []
            if isinstance(data, dict):
                data = [data]
            pids = {p["ProcessId"] for p in data if p.get("ProcessId")}
            if not pids:
                self._ports = []
                return self._ports
            ns = subprocess.run(["netstat", "-ano"], capture_output=True,
                                text=True, encoding="utf-8", errors="replace",
                                timeout=8, creationflags=subprocess.CREATE_NO_WINDOW)
            ports = []
            for line in ns.stdout.splitlines():
                m = re.match(r"\s*TCP\s+127\.0\.0\.1:(\d+)\s+\S+\s+LISTENING\s+(\d+)", line)
                if m and int(m.group(2)) in pids:
                    ports.append(int(m.group(1)))
            self._ports = ports
            self._ports_at = now
        except Exception as e:
            if now - self._last_log > 30:
                self._last_log = now
                log(f"[CDP] 端口发现失败: {e}")
        return self._ports

    # ── 基础通信 ──
    def _chat_page_ws(self, chat_id) -> Optional[str]:
        """找到 chat 页面（URL 带 chat= 参数）的 WebSocket 调试地址"""
        for port in self._find_ports():
            try:
                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/json",
                    headers={"Accept": "application/json"})
                with urllib.request.urlopen(req, timeout=3) as r:
                    targets = json.loads(r.read().decode("utf-8", errors="replace"))
                for t in targets:
                    if t.get("type") == "page" and f"chat={chat_id}" in (t.get("url") or ""):
                        return t.get("webSocketDebuggerUrl")
            except Exception:
                continue
        return None

    def _eval(self, ws_url: str, js_expr: str, timeout: float = 5.0):
        """无 Origin 直连 WebSocket 执行 JS（Chrome 130+ 拒绝跨源 Origin）"""
        import websocket
        ws = websocket.create_connection(ws_url, timeout=timeout, suppress_origin=True)
        try:
            ws.send(json.dumps({
                "id": 1, "method": "Runtime.evaluate",
                "params": {"expression": js_expr, "returnByValue": True,
                           "awaitPromise": True},
            }))
            while True:
                msg = json.loads(ws.recv())
                if msg.get("id") == 1:
                    try:
                        return msg["result"]["result"].get("value")
                    except Exception:
                        return None
        finally:
            ws.close()

    # ── 对外接口 ──
    _QUESTION_JS = r"""(() => {
        const root = document.querySelector('[class*="UserQuestion-scroll"]');
        if (!root) return null;
        const btns = [...root.querySelectorAll('button')].filter(b => {
            const c = (b.className||'').toString();
            return c.includes('w-full') && c.includes('items-start');
        });
        if (!btns.length) return null;
        const lines = (root.innerText||'').split('\n').map(s=>s.trim()).filter(Boolean);
        const q = lines.find(l => /^\d+\.\s/.test(l)) || lines[0] || '';
        const opts = btns.map(b => {
            const t = (b.innerText||'').trim().split('\n').map(s=>s.trim()).filter(Boolean);
            return (t[1] || t[0] || '').slice(0, 40);
        });
        return JSON.stringify({question: q.slice(0, 80), options: opts});
    })()"""

    def fetch_question(self, chat_id):
        """读取 AskUserQuestion 选项卡：返回 (question, options) 或 None"""
        try:
            ws_url = self._chat_page_ws(chat_id)
            if not ws_url:
                return None
            raw = self._eval(ws_url, self._QUESTION_JS)
            if not raw:
                return None
            data = json.loads(raw)
            if data.get("question") and data.get("options"):
                return data["question"], data["options"]
        except Exception as e:
            log(f"[CDP] 读取问题失败: {e}")
        return None

    def answer_option(self, chat_id: str, option_text: str) -> bool:
        """模拟点击选项卡选项按钮（限 UserQuestion-scroll 容器内）。
        选完选项后自动点击"继续"按钮提交（AskUserQuestion 是两步确认流程）。"""
        try:
            ws_url = self._chat_page_ws(chat_id)
            if not ws_url:
                return False
            js = f"""(async () => {{
                const want = {json.dumps(option_text)};
                const root = document.querySelector('[class*="UserQuestion-scroll"]');
                if (!root) return 'nofound';
                const btns = [...root.querySelectorAll('button')].filter(b => {{
                    const c = (b.className||'').toString();
                    return c.includes('w-full') && c.includes('items-start');
                }});
                const target = btns.find(b => {{
                    const t = (b.innerText||'').trim().split('\\n').map(s=>s.trim()).filter(Boolean);
                    return t[1] === want || (t[1]||'').includes(want);
                }});
                if (!target) return 'notfound';
                target.click();
                // 等 React 处理选中状态，再点"继续"提交（按钮在选项卡容器外，需全局搜索）
                await new Promise(r => setTimeout(r, 400));
                const cont = [...document.querySelectorAll('button')].find(b => {{
                    const t = (b.innerText||'').trim();
                    return t.startsWith('继续') && t.length < 10;
                }});
                if (!cont) return 'clicked-no-continue';
                cont.click();
                return 'ok';
            }})()"""
            return self._eval(ws_url, js) in ("ok", "clicked-no-continue")
        except Exception as e:
            log(f"[CDP] 点击选项失败: {e}")
        return False

    def approve_tool(self, chat_id: str, approved: bool) -> bool:
        """通过 CDP 点击高危操作弹窗的"允许/拒绝"按钮。
        双重验证防误点：按钮文本精确匹配 + 按钮须位于含"高危操作"文本的容器内
        （QW 的 PermissionCard 组件无稳定 class，用文案定位）。"""
        try:
            ws_url = self._chat_page_ws(chat_id)
            if not ws_url:
                return False
            want = "\u5141\u8bb8" if approved else "\u62d2\u7edd"  # 允许 / 拒绝
            js = f"""(async () => {{
                const want = {json.dumps(want)};
                const btns = [...document.querySelectorAll('button')].filter(b => {{
                    return (b.innerText||'').trim() === want;
                }});
                if (!btns.length) return 'notfound';
                for (const b of btns) {{
                    let el = b;
                    for (let i = 0; i < 6 && el; i++, el = el.parentElement) {{
                        const t = (el.innerText||'').trim();
                        if (t.includes('\u9ad8\u5371\u64cd\u4f5c') && t.length < 1500) {{
                            b.click();
                            return 'ok';
                        }}
                    }}
                }}
                return 'notfound';
            }})()"""
            return self._eval(ws_url, js) == "ok"
        except Exception as e:
            log(f"[CDP] 点击审批按钮失败: {e}")
        return False

    def probe_permission(self, chat_id: str) -> Optional[tuple]:
        """探测 chat 页面是否弹出高危操作卡片（PermissionCard）。
        高危弹窗期间 QW 不写任何 DB 信号（实测 ext 无变化），只能读页面 DOM。
        返回 (tool_name, cmd_text)；无弹窗返回 None。
        JS：找含"高危操作"文本的最短容器，提取命令代码块和工具类型。"""
        try:
            ws_url = self._chat_page_ws(chat_id)
            if not ws_url:
                return None
            js = """(() => {
                const all = [...document.querySelectorAll('div, section, main')];
                let best = null;
                for (const el of all) {
                    const t = (el.innerText||'').trim();
                    if (t.includes('\u9ad8\u5371\u64cd\u4f5c') && t.length > 8 && t.length < 1500) {
                        if (!best || t.length < (best.innerText||'').trim().length) best = el;
                    }
                }
                if (!best) return null;
                const text = (best.innerText||'').trim();
                // 清洗容器全文：剔除标题/按钮/状态词，剩余即命令描述（code/pre 不存在时的兜底）
                let content = text;
                for (const w of ['\u9ad8\u5371\u64cd\u4f5c', '\u5141\u8bb8\u672c\u6b21', '\u672c\u6b21\u4f1a\u8bdd\u5141\u8bb8', '\u59cb\u7ec8\u5141\u8bb8', '\u5141\u8bb8', '\u62d2\u7edd', '\u53d6\u6d88', '\u6536\u8d77', '\u5c55\u5f00\u5b8c\u6574\u5185\u5bb9', '\u6b63\u5728\u5206\u6790\u547d\u4ee4\u610f\u56fe...']) {
                    content = content.split(w).join(' ');
                }
                content = content.replace(/\\s+/g, ' ').trim();
                const codes = [...best.querySelectorAll('code, pre')].map(el => (el.innerText||'').trim()).filter(t => t && t.length > 1 && t.length < 500);
                const cmd = codes[0] || content;
                let tool = 'Bash';
                if (text.includes('\u5199\u5165\u6587\u4ef6')) tool = 'Write';
                else if (text.includes('\u7f16\u8f91\u6587\u4ef6')) tool = 'Edit';
                return JSON.stringify({tool: tool, cmd: cmd.slice(0, 200)});
            })()"""
            r = self._eval(ws_url, js, timeout=3.0)
            if not r:
                return None
            d = json.loads(r)
            return (d.get("tool", "Bash"), d.get("cmd", ""))
        except Exception as e:
            log(f"[CDP] 探测审批弹窗失败: {e}")
        return None


# ═══════════════════════════════════════════════════════════════
# 数据轮询器 V2（多会话）
# ═══════════════════════════════════════════════════════════════

class DataPoller:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._stop = threading.Event()
        self.on_update: Optional[Callable[[IslandState], None]] = None
        # 每个 chat 的权限请求首次检测时间（跨 poll 保持）
        self._perm_first_time: dict = {}
        # 上一次 DB 连接失败时间（节流日志用）
        self._last_connect_fail = 0.0
        # 每个 chat 上次记录的 pending 消息（仅状态变化时记日志）
        self._last_pending_log: dict = {}
        # AskUserQuestion 问题缓存：chat_id -> (fetched_at, question, options)
        self._question_cache: dict = {}
        # CDP 探测高危弹窗结果缓存：chat_id -> (fetched_at, (tool, cmd) or None)
        self._perm_probe_cache: dict = {}
        self._cdp = QWCDP()

    def start(self):
        t = threading.Thread(target=self._loop, daemon=True)
        t.start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.is_set():
            try:
                state = self._poll()
                if state and self.on_update:
                    self.on_update(state)
            except Exception as e:
                log(f"[Poller] {e}")
            self._stop.wait(POLL_INTERVAL)

    def _poll(self) -> IslandState:
        conn = self._connect()
        if not conn:
            return IslandState()
        try:
            sessions = self._get_sessions(conn)
            for s in sessions:
                if s.state == "running":
                    self._enrich(conn, s)
            primary = self._primary_state(sessions)
            return IslandState(sessions=sessions, primary_state=primary)
        finally:
            conn.close()

    def _get_sessions(self, conn) -> List[SessionInfo]:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, name, ext, created_at, updated_at
            FROM chats WHERE deleted_at IS NULL
            ORDER BY updated_at DESC LIMIT 8
        """)
        sessions = []
        for row in cur.fetchall():
            ext = self._json(row[2])
            # 跳过定时任务生成的会话，只关注用户主会话
            if ext.get("isCronChat"):
                continue
            status = ext.get("taskStatus", "")
            # 实测 QW 只写 running/completed，从不写 failed；保留 failed 显示框架
            # （STATE_COLOR/_primary_state/_main_text）以备未来兼容
            if status not in ("running", "completed"):
                continue
            info = SessionInfo(
                state=status if status == "running" else "completed",
                name=row[1] or "",
                chat_id=row[0],
                updated_at=row[4] or 0,
            )
            cur2 = conn.cursor()
            cur2.execute(
                "SELECT ext FROM sub_chats WHERE chat_id=? ORDER BY updated_at DESC LIMIT 1",
                (row[0],))
            sc = cur2.fetchone()
            if sc and sc[0]:
                snap = self._json(sc[0]).get("contextUsageSnapshot", {})
                info.context_pct = snap.get("percentage", 0) * 100
            sessions.append(info)
        return sessions

    def _enrich(self, conn, info: SessionInfo):
        cur = conn.cursor()
        cur.execute("""
            SELECT parts, metadata FROM messages
            WHERE chat_id=? AND role='assistant'
            ORDER BY sequence DESC LIMIT 10
        """, (info.chat_id,))
        rows = cur.fetchall()
        if not rows:
            return
        self._extract_step(rows, info)
        self._check_pending(rows, info, info.chat_id)

    def _extract_step(self, rows, info):
        """从最近消息中提取当前步骤：遍历所有消息，取最新一条含工具调用的"""
        for row in rows:
            try:
                parts = json.loads(row[0]) if row[0] else []
            except (json.JSONDecodeError, TypeError):
                continue
            for part in reversed(parts):
                if not isinstance(part, dict):
                    continue
                if not part.get("type", "").startswith("tool-"):
                    continue
                tname = part.get("toolName", "")
                inp = part.get("input", {}) or {}
                skip = {"Thinking", "TodoWrite", "Read", "Glob", "Grep"}
                if tname in skip:
                    continue
                if tname == "Bash":
                    cmd = (inp.get("command") or "")[:35]
                    info.current_step = f"Bash: {cmd}"
                    info.current_tool = "Bash"
                elif tname == "Write":
                    fp = inp.get("file_path") or ""
                    info.current_step = f"Write: ...{fp[-28:]}"
                    info.current_tool = "Write"
                elif tname == "Edit":
                    info.current_step = "Edit"
                    info.current_tool = "Edit"
                elif tname == "Agent":
                    desc = (inp.get("description") or "")[:25]
                    info.current_step = f"Agent: {desc}"
                    info.current_tool = "Agent"
                elif tname.startswith("mcp__"):
                    # MCP 工具名过长，只显示短名（如 mcp__filesystem__read → read）
                    short = tname.split("__")[-1][:25]
                    info.current_step = short
                    info.current_tool = tname
                else:
                    info.current_step = tname
                    info.current_tool = tname
                return

    def _check_pending(self, rows, info, chat_id):
        """检测待审批/待回答状态。
        state='call' 是确切信号：对 Bash/Write/Edit 表示等审批。
        对 AskUserQuestion，等待用户选择期间 state 实测为 'input-streaming'
        （工具输入流式生成中），'call' 一并兼容。扫描所有近期 assistant 消息。"""

        found_any_pending = False

        # CDP 探测高危弹窗：QW 弹窗期间不写任何 DB 信号（实测 ext 无变化），
        # 只能从页面 DOM 读。结果缓存 2.5 秒避免频繁连接调试端口。
        now = time.time()
        pc = self._perm_probe_cache.get(chat_id)
        if pc is None or now - pc[0] > 2.5:
            probe = self._cdp.probe_permission(chat_id)
            self._perm_probe_cache[chat_id] = (now, probe)
        else:
            probe = pc[1]
        if probe:
            found_any_pending = True
            info.current_tool = probe[0]
            info.tool_input = (probe[1] or "")[:80]
            info.state = "wait_perm"
            if chat_id not in self._perm_first_time:
                self._perm_first_time[chat_id] = time.time()
            info.perm_since = self._perm_first_time[chat_id]
            self._log_pending(chat_id, "\u9ad8\u5371\u64cd\u4f5c waiting for approval")
            return
        for row in rows:
            try:
                parts = json.loads(row[0]) if row[0] else []
            except (json.JSONDecodeError, TypeError):
                continue

            for p in parts:
                if not isinstance(p, dict):
                    continue
                tname = p.get("toolName", "")
                pstate = p.get("state", "")

                if tname == "AskUserQuestion" and pstate in ("call", "input-streaming"):
                    found_any_pending = True
                    info.state = "wait_answer"
                    self._log_pending(chat_id, "AskUserQuestion waiting")
                    # 通过 CDP 读取问题文本和选项（成功则缓存到状态结束，失败 15s 后重试）
                    now = time.time()
                    entry = self._question_cache.get(chat_id)
                    if entry is None or (not entry[1] and now - entry[0] > 15):
                        q = self._cdp.fetch_question(chat_id)
                        if q:
                            self._question_cache[chat_id] = (now, q[0], q[1])
                        else:
                            self._question_cache[chat_id] = (now, "", [])
                    entry = self._question_cache.get(chat_id, (0, "", []))
                    info.question_text = entry[1]
                    info.question_options = entry[2]

                elif tname in ("Bash", "Write", "Edit") and pstate == "call":
                    found_any_pending = True
                    info.current_tool = tname
                    info.state = "wait_perm"
                    # 提取工具输入摘要
                    inp = p.get("input", {}) or {}
                    if tname == "Bash":
                        info.tool_input = (inp.get("command") or "")[:60]
                    elif tname in ("Write", "Edit"):
                        fp = inp.get("file_path") or ""
                        info.tool_input = f"...{fp[-40:]}" if len(fp) > 40 else fp
                    # 记录首次检测时间
                    if chat_id not in self._perm_first_time:
                        self._perm_first_time[chat_id] = time.time()
                    info.perm_since = self._perm_first_time[chat_id]
                    self._log_pending(chat_id, f"{tname} waiting for approval")

            # 找到 pending 就不用继续扫了
            if found_any_pending:
                break

        if not found_any_pending:
            self._perm_first_time.pop(chat_id, None)
            self._last_pending_log.pop(chat_id, None)
            self._question_cache.pop(chat_id, None)
            self._perm_probe_cache.pop(chat_id, None)

    def _log_pending(self, chat_id: str, msg: str):
        """pending 状态只在首次出现时记录日志，避免每 2 秒刷屏"""
        if self._last_pending_log.get(chat_id) != msg:
            self._last_pending_log[chat_id] = msg
            log(f"[Pending] {chat_id[:12]}: {msg}")

    def _primary_state(self, sessions: List[SessionInfo]) -> str:
        priority = {"wait_perm": 0, "wait_answer": 1, "running": 2,
                     "failed": 3, "completed": 4, "idle": 5}
        best = "idle"
        for s in sessions:
            if priority.get(s.state, 5) < priority.get(best, 5):
                best = s.state
        return best

    def _connect(self):
        try:
            conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
            conn.execute("PRAGMA journal_mode=wal")
            return conn
        except Exception as e:
            # 节流记录，避免每 2 秒刷日志文件
            if time.time() - self._last_connect_fail > 5:
                self._last_connect_fail = time.time()
                log(f"[Poller] DB 连接失败: {e}")
            return None

    @staticmethod
    def _json(s):
        if not s:
            return {}
        try:
            return json.loads(s)
        except (json.JSONDecodeError, TypeError):
            return {}


# ═══════════════════════════════════════════════════════════════
# 吉祥物动画管理器
# ═══════════════════════════════════════════════════════════════

class MascotAnimator:
    # 状态 → 动画名前缀映射
    STATE_MAP = {
        "idle": "idle",
        "running": "type_thinking",
        "wait_perm": "approve",
        "wait_answer": "question",
        "completed": "done",
        "failed": "error",
    }
    VERB_MAP = {
        "Bash": "type_exec", "Write": "type_write", "Edit": "type_write",
        "Read": "type_read", "Glob": "type_search", "Grep": "type_search",
        "WebSearch": "type_search", "WebFetch": "type_search",
    }
    IDLE_ROTATE_INTERVAL = 30.0  # idle 图片每 30 秒轮换

    def __init__(self, mascot_dir: str, size: int = MASCOT_SIZE, defer: bool = False):
        self.size = size
        self.mascot_dir = mascot_dir
        self.frames: dict = {}       # 帧动画: name -> [PhotoImage, ...]
        self.idle_images: list = []  # idle 轮播用的独立图片
        self._current_anim = "idle"
        self._frame_idx = 0
        self._cycle_count = 0
        self._max_cycles = 3
        self._stopped = False
        self._idle_idx = 0           # 当前显示第几张 idle 图
        self._idle_last_switch = time.time()
        if not defer:
            self.load_frames()

    def load_frames(self):
        self._load_all(self.mascot_dir)

    def _load_all(self, directory: str):
        # 帧动画状态（多帧循环播放）
        anim_frames = {
            "type_exec": 4, "type_write": 4, "type_read": 4,
            "type_search": 4, "type_thinking": 4,
            "approve": 3, "question": 3, "done": 4, "error": 3,
        }
        for anim_name, n_frames in anim_frames.items():
            frames = []
            for i in range(1, n_frames + 1):
                path = os.path.join(directory, f"{anim_name}_{i:02d}.png")
                if os.path.exists(path):
                    img = Image.open(path).convert("RGBA")
                    img = img.resize((self.size, self.size), Image.LANCZOS)
                    frames.append(ImageTk.PhotoImage(img))
            if frames:
                self.frames[anim_name] = frames

        # idle 轮播图片（独立的 3 张图，非帧动画）
        self.idle_images = []
        for i in range(1, 4):
            path = os.path.join(directory, f"idle_{i:02d}.png")
            if os.path.exists(path):
                img = Image.open(path).convert("RGBA")
                img = img.resize((self.size, self.size), Image.LANCZOS)
                self.idle_images.append(ImageTk.PhotoImage(img))

    def set_state(self, state: str, verb: str = ""):
        if state == "running" and verb:
            mapped = self.VERB_MAP.get(verb, "type_thinking")
        else:
            mapped = self.STATE_MAP.get(state, "idle")

        if mapped != self._current_anim:
            self._current_anim = mapped
            self._frame_idx = 0
            self._cycle_count = 0
            self._stopped = False
            if mapped == "idle":
                self._idle_last_switch = time.time()

    def get_frame(self) -> Optional[ImageTk.PhotoImage]:
        anim = self._current_anim

        # idle 状态：轮播 3 张不同的图，每张 30 秒
        if anim == "idle" and self.idle_images:
            now = time.time()
            if now - self._idle_last_switch >= self.IDLE_ROTATE_INTERVAL:
                self._idle_idx = (self._idle_idx + 1) % len(self.idle_images)
                self._idle_last_switch = now
            return self.idle_images[self._idle_idx]

        # 其他状态：帧动画
        if anim not in self.frames:
            # fallback 到 idle
            if self.idle_images:
                return self.idle_images[self._idle_idx]
            return None
        frames = self.frames[anim]
        if self._stopped:
            return frames[0]
        frame = frames[self._frame_idx % len(frames)]
        self._frame_idx += 1
        if self._frame_idx >= len(frames):
            self._frame_idx = 0
            self._cycle_count += 1
            if self._cycle_count >= self._max_cycles:
                self._stopped = True
        return frame


# ═══════════════════════════════════════════════════════════════
# 灵动岛 UI V2
# ═══════════════════════════════════════════════════════════════

class IslandUI:
    _TKEY = "#010101"

    def __init__(self, poller: DataPoller, mascot: MascotAnimator):
        self.poller = poller
        self.mascot = mascot
        self.root = tk.Tk()
        self.root.title("QW Island")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", ALPHA)
        self.root.configure(bg=self._TKEY)
        try:
            self.root.attributes("-transparentcolor", self._TKEY)
        except tk.TclError:
            pass

        # 确保获取准确的屏幕尺寸（处理 DPI 缩放）
        self.root.update_idletasks()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        collapsed_h = PILL_Y_OFFSET + ISLAND_HEIGHT + PILL_PADDING
        # 恢复上次保存的窗口位置；无配置或越界（分辨率/显示器变化）则顶部居中
        pos = load_window_pos()
        if pos and -sw <= pos[0] <= sw * 2 and -sh <= pos[1] <= sh * 2:
            x, y = pos
        else:
            x, y = (sw - ISLAND_WIDTH) // 2, 8
        self.root.geometry(f"{ISLAND_WIDTH}x{collapsed_h}+{x}+{y}")
        self.root.update_idletasks()

        self.state = IslandState()
        self._prev_primary = "idle"
        self._last_active_time = 0.0  # 上次有活跃任务的时间戳
        self._rotation_idx = 0  # 多任务轮播索引
        self._last_rotation_time = 0.0  # 上次轮播时间
        self._hover = False
        self._expand_t = 0.0
        self._pulse = 0.0
        self._drag_x = 0
        self._drag_y = 0
        self._dragging = False
        self._destroyed = False
        self._btn_regions = []
        self._seen_completed = set()  # 用户已点击确认的已完成任务 chat_id
        self._actioned = set()  # 已通过审批卡片操作过的 chat_id（防止重复点击）
        self._widget_start_time = time.time()  # widget 启动时间
        self._suppress_drag_until = 0.0  # 双击后抑制拖拽的截止时间
        self._mascot_last_replay = time.time()  # running 动画上次重播时间

        self.canvas = tk.Canvas(
            self.root, width=ISLAND_WIDTH, height=EXPANDED_HEIGHT,
            highlightthickness=0, bg=self._TKEY,
        )
        self.canvas.pack(fill="both", expand=True)

        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Double-Button-1>", self._on_double_click)
        self.canvas.bind("<Button-3>", self._on_rclick)
        self.canvas.bind("<Enter>", lambda e: self._set_hover(True))
        self.canvas.bind("<Leave>", lambda e: self._set_hover(False))
        self.root.protocol("WM_DELETE_WINDOW", self._cleanup)

        self._f_main = ("Microsoft YaHei UI", 10)
        self._f_small = ("Microsoft YaHei UI", 8)
        self._f_detail = ("Microsoft YaHei UI", 9)
        self._f_bold = ("Microsoft YaHei UI", 9, "bold")
        self._f_btn = ("Microsoft YaHei UI", 8, "bold")

        self._animate()
        self._mascot_tick()
        poller.on_update = self._on_update

    # ─── 绘制 ─────────────────────────────────────────────

    def _effective_state(self) -> str:
        """计算实际显示状态：考虑 idle 回归和多任务轮播"""
        ps = self.state.primary_state
        now = time.time()

        # idle 回归：completed 状态且距最近完成超过 IDLE_TIMEOUT → 显示 idle
        if ps == "completed":
            # 最近完成时刻：运行期间观察到的完成用 _last_active_time；
            # 启动前已存在的完成任务（_last_active_time 为 0）用其自身 updated_at
            last_done = self._last_active_time
            if last_done <= 0:
                last_done = max((s.updated_at or 0 for s in self.state.sessions
                                 if s.state == "completed"), default=0)
            if last_done > 0 and now - last_done >= IDLE_TIMEOUT:
                return "idle"

        # 刚启动、无任何状态也显示 idle
        if ps in ("idle",):
            return "idle"

        return ps

    def _get_display_session(self, ps: str):
        """获取要显示的会话（多任务轮播）"""
        sessions = self.state.sessions
        now = time.time()

        # 多任务轮播（仅 running 状态，每 10 秒切换）
        if ps == "running":
            running = [s for s in sessions if s.state == "running"]
            if len(running) > 1:
                if now - self._last_rotation_time >= 10.0:
                    self._rotation_idx = (self._rotation_idx + 1) % len(running)
                    self._last_rotation_time = now
                idx = self._rotation_idx % len(running)
                return running[idx]
            elif running:
                return running[0]

        # 非 running 状态：按优先级取第一个匹配的
        if ps in ("wait_perm", "wait_answer"):
            return next((s for s in sessions if s.state == ps), None)
        elif ps == "completed":
            return next((s for s in sessions if s.state == "completed"), None)
        return None

    def _draw(self):
        cv = self.canvas
        cv.delete("all")
        self._btn_regions.clear()
        w = ISLAND_WIDTH
        t = self._ease(self._expand_t)
        h = PILL_Y_OFFSET + ISLAND_HEIGHT + int((EXPANDED_HEIGHT - PILL_Y_OFFSET - ISLAND_HEIGHT) * t)
        ps = self._effective_state()
        color = STATE_COLOR.get(ps, C.IDLE)
        bg = C.BG_HOVER if self._hover else C.BG

        py = PILL_Y_OFFSET  # 药丸 y 偏移

        # 药丸主体（PIL 在透明色背景上渲染）
        r = ISLAND_HEIGHT // 2
        pill_img = self._render_pill(w, h - py, r, bg, color)
        cv.create_image(0, py, image=pill_img, anchor="nw")

        # ── 吉祥物（叠在药丸左上角，突出药丸顶部）──
        frame = self.mascot.get_frame()
        if frame:
            mx = 4
            my = py - 6  # 吉祥物顶部突出药丸顶部 6px
            cv.create_image(mx, my, image=frame, anchor="nw")

        # ── 主文本 ──
        tx = ISLAND_HEIGHT + 8
        cv.create_text(tx, py + ISLAND_HEIGHT // 2,
                       text=self._main_text(ps), fill=C.TEXT,
                       anchor="w", font=self._f_main)

        # ── 右侧状态 ──
        rx = w - 16
        if ps == "running":
            max_pct = max((s.context_pct for s in self.state.sessions if s.state == "running"), default=0)
            if max_pct > 0:
                cv.create_text(rx, py + ISLAND_HEIGHT // 2,
                               text=f"{max_pct:.0f}%", fill=C.TEXT_DIM,
                               anchor="e", font=self._f_small)
        elif ps == "completed":
            cv.create_text(rx, py + ISLAND_HEIGHT // 2, text="Done",
                           fill=C.COMPLETED, anchor="e", font=self._f_small)
        elif ps in ("wait_perm", "wait_answer"):
            if self._pulse < 0.5:
                cv.create_text(rx, py + ISLAND_HEIGHT // 2, text="!",
                               fill=color, anchor="e", font=self._f_bold)

        # ── 展开面板 ──
        if t > 0.1:
            self._draw_panel(w, h, py)

    def _draw_panel(self, w, h, py=0):
        cv = self.canvas
        sy = py + ISLAND_HEIGHT + 4
        max_y = h - 8  # 内容底部边界：超出窗口高度的内容不绘制（防溢出裁剪）

        cv.create_line(16, sy, w - 16, sy, fill=C.SEPARATOR, width=1)

        # 已通过审批卡片操作过的会话不再显示，防止重复点击
        actionable = [s for s in self.state.sessions
                      if s.state in ("wait_perm", "wait_answer")
                      and s.chat_id not in self._actioned]
        running = [s for s in self.state.sessions if s.state == "running"]
        completed = [s for s in self.state.sessions
                     if s.state == "completed"
                     and s.updated_at and s.updated_at >= self._widget_start_time
                     and s.chat_id not in self._seen_completed]

        if not actionable and not running and not completed:
            cv.create_text(w // 2, sy + 30, text="No active tasks",
                           fill=C.TEXT_DIM, font=self._f_detail)
            return

        cy = sy + 6  # 当前绘制 y 坐标

        # ── 待审批/待回答卡片（最高优先级，放不下自动截断）──
        for s in actionable[:2]:  # 最多显示 2 个卡片
            # wait_answer 需要额外一行显示问题文本，wait_perm 显示两行命令，卡片都更高
            ch = 116 if s.state in ("wait_answer", "wait_perm") else 100
            if cy + ch > max_y:
                break
            cy = self._draw_approval_card(w, s, cy, ch)
            cy += 4  # 卡片间距

        # ── 运行中任务（紧凑行）──
        row_h = 22
        for s in running[:3]:
            if cy + 2 + row_h > max_y:  # 行内容从 cy+2 开始，含顶部偏移
                break
            ry = cy + 2
            cv.create_oval(20, ry + 4, 27, ry + 11, fill=C.RUNNING, outline="")
            name = (s.name[:14] + "..") if len(s.name) > 14 else s.name
            step = s.current_step if s.current_step else "Running..."
            display = f"{name} \u2014 {step}"
            if len(display) > 40:
                display = display[:38] + ".."
            cv.create_text(34, ry + 8, text=display, fill=C.TEXT,
                           anchor="w", font=self._f_detail)
            cy += row_h

        # ── 已完成任务（紧凑行 + 可点击）──
        for s in completed[:2]:
            if cy + 2 + row_h > max_y:
                break
            ry = cy + 2
            cv.create_oval(20, ry + 4, 27, ry + 11, fill=C.COMPLETED, outline="")
            name = (s.name[:14] + "..") if len(s.name) > 14 else s.name
            cv.create_text(34, ry + 8, text=f"{name} \u2014 Done",
                           fill=C.TEXT_DIM, anchor="w", font=self._f_detail)
            self._btn_regions.append(
                (16, ry, w - 16, ry + row_h,
                 lambda sid=s.chat_id: self._on_view_completed(sid)))
            cy += row_h

    def _draw_approval_card(self, w, s, cy, card_h=100):
        """绘制一个审批/问答卡片，返回卡片底部 y 坐标"""
        cv = self.canvas
        sc = STATE_COLOR.get(s.state, C.IDLE)
        pad = 16
        card_w = w - pad * 2

        # ── 任务名 ──
        name = (s.name[:20] + "..") if len(s.name) > 20 else s.name
        cv.create_text(pad + 10, cy + 14, text=name,
                       fill=C.TEXT_BRIGHT, anchor="w", font=self._f_bold)

        # ── Chat ID（短） ──
        short_id = s.chat_id[:16] if len(s.chat_id) > 16 else s.chat_id
        cv.create_text(w - pad - 10, cy + 14, text=short_id,
                       fill=C.TEXT_DIM, anchor="e", font=self._f_small)

        # ── 工具 + 输入 / 问题文本 ──
        if s.state == "wait_perm":
            # 命令最多显示 2 行（每行 40 字符），让用户看清待审批的内容
            cmd = s.tool_input or ""
            prefix = f"{s.current_tool}: " if cmd else s.current_tool
            line1 = (prefix + cmd)[:40]
            cv.create_text(pad + 10, cy + 34, text=line1,
                           fill=C.TEXT, anchor="w", font=self._f_detail)
            rest = (prefix + cmd)[40:80]
            if rest:
                cv.create_text(pad + 10, cy + 50, text=rest,
                               fill=C.TEXT, anchor="w", font=self._f_detail)
        elif s.state == "wait_answer":
            # 问题文本最多显示 2 行（每行 22 字符，约 264px）
            q = s.question_text or "AskUserQuestion"
            cv.create_text(pad + 10, cy + 34, text=q[:22],
                           fill=C.TEXT, anchor="w", font=self._f_detail)
            if len(q) > 22:
                cv.create_text(pad + 10, cy + 50, text=q[22:44],
                               fill=C.TEXT, anchor="w", font=self._f_detail)
        else:
            tool_line = s.current_tool or "Unknown"
            cv.create_text(pad + 10, cy + 36, text=tool_line,
                           fill=C.TEXT, anchor="w", font=self._f_detail)

        # ── 状态 + 等待时长 ──
        if s.state == "wait_perm":
            status_text = "\u7b49\u5f85\u5ba1\u6279"  # 等待审批
            status_y = cy + 66
        else:
            status_text = "\u7b49\u5f85\u56de\u7b54"  # 等待回答
            status_y = cy + 64
        if s.perm_since > 0:
            elapsed = time.time() - s.perm_since
            if elapsed < 60:
                time_text = f"{elapsed:.0f}s"
            else:
                time_text = f"{elapsed / 60:.0f}m"
            cv.create_text(w - pad - 10, status_y,
                           text=time_text, fill=sc, anchor="e", font=self._f_small)

        cv.create_text(pad + 10, status_y, text=status_text,
                       fill=sc, anchor="w", font=self._f_detail)

        # ── 按钮区域 ──
        btn_y = cy + card_h - 32
        btn_h = 24
        btn_w = (card_w - 30) // 2  # 两个按钮平分宽度

        if s.state == "wait_perm":
            # 同意按钮（绿色填充）
            self._draw_filled_button(
                pad + 4, btn_y, btn_w, btn_h,
                "\u540c\u610f", C.BTN_APPROVE,
                lambda sid=s.chat_id, tool=s.current_tool: self._on_approve(sid, tool))
            # 拒绝按钮（红色填充）
            self._draw_filled_button(
                pad + btn_w + 12, btn_y, btn_w, btn_h,
                "\u62d2\u7edd", C.BTN_REJECT,
                lambda sid=s.chat_id, tool=s.current_tool: self._on_reject(sid, tool))
        else:
            # wait_answer：选项按钮（CDP 直接回答），CDP 不可用时跳转 QW
            opts = s.question_options
            if opts:
                self._draw_filled_button(
                    pad + 4, btn_y, btn_w, btn_h,
                    f"1 {opts[0][:9]}", C.BTN_BG,
                    lambda sid=s.chat_id, t=opts[0]: self._on_answer_option(sid, t))
                if len(opts) > 2:
                    # 选项超过 2 个：第二按钮跳转 QW 选择剩余选项
                    self._draw_filled_button(
                        pad + btn_w + 12, btn_y, btn_w, btn_h,
                        f"+{len(opts) - 1} \u66f4\u591a", C.BTN_BG,
                        lambda sid=s.chat_id: self._goto_qw(sid))
                elif len(opts) == 2:
                    self._draw_filled_button(
                        pad + btn_w + 12, btn_y, btn_w, btn_h,
                        f"2 {opts[1][:9]}", C.BTN_BG,
                        lambda sid=s.chat_id, t=opts[1]: self._on_answer_option(sid, t))
                else:
                    self._draw_filled_button(
                        pad + btn_w + 12, btn_y, btn_w, btn_h,
                        "\u524d\u5f80 QW", C.BTN_BG,
                        lambda sid=s.chat_id: self._goto_qw(sid))
            else:
                # CDP 不可用：整宽按钮跳转 QW 手动回答
                self._draw_filled_button(
                    pad + 4, btn_y, card_w - 8, btn_h,
                    "\u524d\u5f80 QW \u56de\u7b54", C.BTN_BG,
                    lambda sid=s.chat_id: self._goto_qw(sid))

        return cy + card_h

    def _draw_filled_button(self, x, y, w, h, text, color, callback):
        """填充色圆角按钮"""
        cv = self.canvas
        r = h // 2
        pts = self._rounded_rect_points(x, y, x + w, y + h, r)
        cv.create_polygon(pts, fill=color, outline="", smooth=True)
        cv.create_text(x + w // 2, y + h // 2, text=text,
                       fill=C.BTN_TEXT, font=self._f_btn)
        self._btn_regions.append((x, y, x + w, y + h, callback))

    # 工具名 -> 短动词映射
    _VERB_MAP = {
        "Bash": "exec", "Write": "write", "Edit": "edit",
        "Read": "read", "Glob": "search", "Grep": "search",
        "Agent": "agent", "WebFetch": "fetch", "WebSearch": "search",
        "Skill": "skill",
    }

    def _main_text(self, state: str) -> str:
        if state == "idle":
            return f"{APP_NAME} \u00b7 idle"

        target = self._get_display_session(state)

        if not target:
            return f"{APP_NAME} \u00b7 {state}"

        name = target.name[:14] if target.name else APP_NAME
        if len(target.name or "") > 14:
            name += ".."

        if state == "running":
            verb = self._VERB_MAP.get(target.current_tool, "work")
            return f"{name} \u00b7 {verb}"
        elif state in ("wait_perm", "wait_answer"):
            return f"{name} \u00b7 wait"
        elif state == "completed":
            return f"{name} \u00b7 done"
        elif state == "failed":
            return f"{name} \u00b7 error"
        return f"{name}"

    # ─── 绘图辅助 ─────────────────────────────────────────

    _pill_cache_key = None
    _pill_cache_img = None

    def _render_pill(self, w, h, r, fill, outline):
        """PIL 4x 超采样圆角矩形，透明背景，alpha 量化消除边缘灰像素"""
        from PIL import ImageDraw
        key = (w, h, fill, outline, r)
        if key == self._pill_cache_key and self._pill_cache_img:
            return self._pill_cache_img

        scale = 4
        sw, sh = w * scale, h * scale
        sr = r * scale

        # 在透明背景上画（关键：不是深色背景，避免边缘深色像素）
        img = Image.new("RGBA", (sw, sh), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle([0, 0, sw - 1, sh - 1],
                               radius=sr, fill=fill, outline=outline, width=3 * scale)

        # LANCZOS 缩放到实际尺寸
        img = img.resize((w, h), Image.LANCZOS)

        # Alpha 量化：半透明像素直接变全透明
        # 因为背景是透明的（不是深色），这些像素不会显示为深色杂点
        r_, g_, b_, a_ = img.split()
        a_ = a_.point(lambda p: 255 if p > 32 else 0)
        final = Image.merge("RGBA", (r_, g_, b_, a_))

        tk_img = ImageTk.PhotoImage(final)
        self._pill_cache_key = key
        self._pill_cache_img = tk_img
        return tk_img

    @staticmethod
    def _rounded_rect_points(x1, y1, x2, y2, r):
        """生成圆角矩形的点序列（每角 32 段弧线，光滑度更高）"""
        pts = []
        n = 32
        for cx, cy, a1, a2 in [
            (x1 + r, y1 + r, 180, 270),
            (x2 - r, y1 + r, 270, 360),
            (x2 - r, y2 - r, 0, 90),
            (x1 + r, y2 - r, 90, 180),
        ]:
            for i in range(n + 1):
                a = math.radians(a1 + (a2 - a1) * i / n)
                pts += [cx + r * math.cos(a), cy + r * math.sin(a)]
        return pts

    def _ease(self, t):
        return 4 * t * t * t if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2

    # ─── 动画 ─────────────────────────────────────────────

    def _animate(self):
        if self._destroyed:
            return
        ps = self._effective_state()
        if ps == "running":
            self._pulse = (self._pulse + PULSE_SPEED) % 1.0
        elif ps in ("wait_perm", "wait_answer"):
            self._pulse = (self._pulse + BLINK_SPEED) % 1.0

        target = 1.0 if self._hover else 0.0
        diff = target - self._expand_t
        if abs(diff) > 0.01:
            self._expand_t += diff * EXPAND_SPEED * 3
            if abs(self._expand_t - target) < 0.02:
                self._expand_t = target

        t = self._ease(self._expand_t)
        collapsed_h = PILL_Y_OFFSET + ISLAND_HEIGHT + PILL_PADDING
        expand_range = EXPANDED_HEIGHT - collapsed_h
        h = collapsed_h + int(expand_range * t)
        self.root.geometry(
            f"{ISLAND_WIDTH}x{h}+{self.root.winfo_x()}+{self.root.winfo_y()}")

        self._draw()
        self.root.after(33, self._animate)

    def _mascot_tick(self):
        if self._destroyed:
            return
        ps = self._effective_state()

        # running 状态下传入当前动词，显示对应的动画
        verb = ""
        if ps == "running":
            target = self._get_display_session(ps)
            if target:
                verb = target.current_tool
        self.mascot.set_state(ps, verb=verb)

        # running 状态：动画停 10 秒后重播
        if ps == "running" and self.mascot._stopped:
            if time.time() - self._mascot_last_replay >= 10.0:
                self.mascot._stopped = False
                self.mascot._frame_idx = 0
                self.mascot._cycle_count = 0
                self._mascot_last_replay = time.time()

        # wait_perm/wait_answer 状态：持续循环（不停）
        if ps in ("wait_perm", "wait_answer") and self.mascot._stopped:
            self.mascot._stopped = False
            self.mascot._frame_idx = 0
            self.mascot._cycle_count = 0

        self.root.after(1000 // MASCOT_FPS, self._mascot_tick)

    # ─── 轮询回调 ──────────────────────────────────────────

    def _on_update(self, state: IslandState):
        if self._destroyed:
            return

        now = time.time()

        # 跟踪活跃状态时间（用于 idle 回归）
        if state.primary_state in ("running", "wait_perm", "wait_answer"):
            self._last_active_time = now
        elif state.primary_state == "completed" and self._prev_primary in ("running", "wait_perm", "wait_answer"):
            self._last_active_time = now  # 刚完成时记录时间

        # 音效
        if state.primary_state != self._prev_primary:
            if state.primary_state == "wait_perm":
                SoundFX.permission()
            elif state.primary_state == "wait_answer":
                SoundFX.question()
            elif state.primary_state == "completed" and self._prev_primary != "idle":
                SoundFX.complete()
            elif state.primary_state == "failed":
                SoundFX.error()
            self._prev_primary = state.primary_state

        # 清理已操作标记：会话不再处于等待状态时移除，允许同一会话下次重新显示
        active_wait = {s.chat_id for s in state.sessions
                       if s.state in ("wait_perm", "wait_answer")}
        self._actioned = {cid for cid in self._actioned if cid in active_wait}

        self.state = state

    # ─── 交互 ─────────────────────────────────────────────

    def _on_view_completed(self, chat_id: str):
        """点击已完成任务行：标记为已知 + 跳转 QoderWork 对应任务"""
        self._seen_completed.add(chat_id)
        task_name = ""
        for s in self.state.sessions:
            if s.chat_id == chat_id:
                task_name = s.name
                break
        self._goto_qw(chat_id, task_name)

    def _on_approve(self, chat_id: str, tool: str = ""):
        """点击同意按钮：CDP 直接审批高危弹窗；失败则桥接兜底（子任务）+ 跳转 QW"""
        self._actioned.add(chat_id)  # 立即隐藏卡片，防止重复点击
        self.poller._perm_first_time.pop(chat_id, None)
        if self.poller._cdp.approve_tool(chat_id, True):
            log(f"[Approve] {chat_id[:12]}: approved ({tool})")
        else:
            log(f"[Approve] {chat_id[:12]}: CDP 失败，桥接兜底 + 跳转 QW")
            self._write_bridge_command(chat_id, "approve", tool)
            self._goto_qw(chat_id)
        self.poller._perm_probe_cache.pop(chat_id, None)

    def _on_reject(self, chat_id: str, tool: str = ""):
        """点击拒绝按钮：CDP 直接拒绝高危弹窗；失败则桥接兜底（子任务）+ 跳转 QW"""
        self._actioned.add(chat_id)  # 立即隐藏卡片，防止重复点击
        self.poller._perm_first_time.pop(chat_id, None)
        if self.poller._cdp.approve_tool(chat_id, False):
            log(f"[Approve] {chat_id[:12]}: denied ({tool})")
        else:
            log(f"[Approve] {chat_id[:12]}: CDP 失败，桥接兜底 + 跳转 QW")
            self._write_bridge_command(chat_id, "deny", tool)
            self._goto_qw(chat_id)
        self.poller._perm_probe_cache.pop(chat_id, None)

    def _on_answer_option(self, chat_id: str, option_text: str):
        """点击选项按钮：通过 CDP 在 QW 页面直接回答，失败则跳转 QW 手动选择"""
        self._actioned.add(chat_id)  # 立即隐藏卡片，防止重复点击
        self.poller._perm_first_time.pop(chat_id, None)
        self.poller._question_cache.pop(chat_id, None)
        if self.poller._cdp.answer_option(chat_id, option_text):
            log(f"[Answer] {chat_id[:12]}: clicked '{option_text}'")
        else:
            log(f"[Answer] {chat_id[:12]}: CDP 点击失败，跳转 QW")
            self._goto_qw(chat_id)

    def _write_bridge_command(self, chat_id: str, action: str, tool: str = ""):
        """将审批命令写入桥接目录，供 QoderWork Skill 读取执行"""
        try:
            os.makedirs(BRIDGE_DIR, exist_ok=True)
            cmd = {
                "chatId": chat_id,
                "action": action,
                "toolName": tool,
                "timestamp": int(time.time()),
            }
            fname = f"{chat_id}_{int(time.time())}.json"
            path = os.path.join(BRIDGE_DIR, fname)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(cmd, f, ensure_ascii=False)
            log(f"[Bridge] Wrote: {fname} ({action})")
        except Exception as e:
            log(f"[Bridge] Write failed: {e}")

    def _on_press(self, e):
        self._drag_x, self._drag_y = e.x, e.y
        self._dragging = False

    def _on_drag(self, e):
        # 双击后 1 秒内忽略拖拽
        if time.perf_counter() < self._suppress_drag_until:
            return
        dx, dy = e.x - self._drag_x, e.y - self._drag_y
        if abs(dx) > 3 or abs(dy) > 3:
            self._dragging = True
            self.root.geometry(
                f"+{self.root.winfo_x() + dx}+{self.root.winfo_y() + dy}")

    def _on_release(self, e):
        if self._dragging:
            # 拖拽结束：保存窗口位置（重启后恢复）
            save_window_pos(self.root.winfo_x(), self.root.winfo_y())
            return
        # 单击只处理按钮点击，不跳转 QoderWork
        for x1, y1, x2, y2, cb in self._btn_regions:
            if x1 <= e.x <= x2 and y1 <= e.y <= y2:
                cb()
                return

    def _on_double_click(self, e):
        """双击药丸 → 激活 QoderWork 到前台"""
        # 先设置拖拽抑制（立即生效）
        self._suppress_drag_until = time.perf_counter() + 1.0
        # 延迟 100ms 执行 goto_qw，确保抑制标志已设置且 tkinter 事件队列已处理
        self.root.after(100, self._goto_qw)

    def _on_rclick(self, e):
        is_pinned = self.root.attributes("-topmost")
        m = tk.Menu(self.root, tearoff=0, bg="#2a2a4e", fg=C.TEXT,
                    activebackground="#3a3a6e", activeforeground=C.TEXT_BRIGHT,
                    font=("Microsoft YaHei UI", 10))
        pin_label = "\u2714 \u56fa\u5b9a\u5728\u9876\u5c42" if is_pinned else "\u56fa\u5b9a\u5728\u9876\u5c42"
        m.add_command(label=pin_label, command=self._toggle_top)
        m.add_separator()
        sub = tk.Menu(m, tearoff=0, bg="#2a2a4e", fg=C.TEXT,
                      activebackground="#3a3a6e", activeforeground=C.TEXT_BRIGHT)
        for lbl, v in [("92%", 0.92), ("80%", 0.80), ("65%", 0.65), ("50%", 0.50)]:
            sub.add_command(label=lbl,
                            command=lambda v=v: self.root.attributes("-alpha", v))
        m.add_cascade(label="\u900f\u660e\u5ea6", menu=sub)
        m.add_separator()
        m.add_command(label="\u9000\u51fa", command=self._cleanup)
        m.tk_popup(e.x_root, e.y_root)

    def _set_hover(self, v):
        self._hover = v

    def _toggle_top(self):
        current = self.root.attributes("-topmost")
        self.root.attributes("-topmost", not current)

    def _goto_qw(self, chat_id: str = "", task_name: str = ""):
        """激活 QoderWork 到前台。优先使用 qoder-work:// 协议触发 second-instance（修复托盘隐藏场景），
        失败时降级到 Win32 API 直接操作窗口。"""
        self.root.attributes("-topmost", False)
        self.root.update_idletasks()

        # ── 优先 Win32 直接激活窗口（<50ms，比协议快 2-3 秒）──
        try:
            self._goto_qw_win32()
        except Exception as e:
            log(f"[goto_qw] Win32 activate failed: {e}")

        # ── 协议触发兜底（second-instance 恢复托盘隐藏窗口，不阻塞）──
        try:
            if chat_id:
                os.startfile(f"qoder-work://notification-click?chatId={chat_id}")
            else:
                os.startfile("qoder-work://")
        except Exception:
            pass

        self.root.after(2000, lambda: self.root.attributes("-topmost", True))

    def _goto_qw_win32(self):
        """Win32 API 降级方案：枚举窗口 → 前台激活"""
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        psapi = ctypes.windll.psapi
        kernel32 = ctypes.windll.kernel32

        EnumWindowsProc = ctypes.WINFUNCTYPE(
            wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        hwnds = []
        main_hwnd = [None]

        def _enum_cb(hwnd, _):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if user32.GetParent(hwnd) == 0:
                hproc = kernel32.OpenProcess(0x0410, False, pid.value)
                if hproc:
                    buf = ctypes.create_unicode_buffer(260)
                    psapi.GetModuleBaseNameW(hproc, None, buf, 260)
                    kernel32.CloseHandle(hproc)
                    if "qoderwork" in buf.value.lower():
                        length = user32.GetWindowTextLengthW(hwnd)
                        if length > 0:
                            tbuf = ctypes.create_unicode_buffer(length + 1)
                            user32.GetWindowTextW(hwnd, tbuf, length + 1)
                            if "QW Island" not in tbuf.value:
                                if tbuf.value == "QoderWork":
                                    main_hwnd[0] = hwnd
                                else:
                                    hwnds.append(hwnd)
            return True

        user32.EnumWindows(EnumWindowsProc(_enum_cb), 0)
        hwnd = main_hwnd[0] or (hwnds[0] if hwnds else None)
        if not hwnd:
            return

        # 前台锁绕过
        old_timeout = wintypes.DWORD()
        user32.SystemParametersInfoW(0x2000, 0, ctypes.byref(old_timeout), 0)
        user32.SystemParametersInfoW(0x2001, 0, ctypes.c_void_p(0), 0x03)

        fg_tid = user32.GetWindowThreadProcessId(
            user32.GetForegroundWindow(), None)
        cur_tid = kernel32.GetCurrentThreadId()
        attached = cur_tid != fg_tid and bool(
            user32.AttachThreadInput(cur_tid, fg_tid, True))

        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        else:
            user32.ShowWindow(hwnd, 1)  # SW_SHOWNORMAL

        user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0061)
        user32.SetWindowPos(hwnd, -2, 0, 0, 0, 0, 0x0061)
        user32.SetForegroundWindow(hwnd)

        if attached:
            user32.AttachThreadInput(cur_tid, fg_tid, False)
        user32.SystemParametersInfoW(
            0x2001, 0, ctypes.c_void_p(old_timeout.value), 0x03)

    def _cleanup(self):
        self._destroyed = True
        self.poller.stop()
        try:
            self.root.destroy()
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════════════

def main():
    if not os.path.exists(DB_PATH):
        msg = f"数据库不存在: {DB_PATH}\n请先安装并运行 QoderWork。"
        log(msg)
        try:
            import tkinter.messagebox as mb
            mb.showerror("QW Island", msg)
        except Exception:
            pass
        sys.exit(1)
    log("=== QoderWork Dynamic Island 启动 ===")
    log(f"DB: {DB_PATH}")
    log(f"Mascot: {MASCOT_DIR}")

    poller = DataPoller(DB_PATH)
    poller.start()

    mascot = MascotAnimator(MASCOT_DIR, defer=True)
    ui = IslandUI(poller, mascot)
    mascot.load_frames()
    ui.root.mainloop()

if __name__ == "__main__":
    main()
