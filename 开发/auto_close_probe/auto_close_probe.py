# -*- coding: utf-8 -*-
"""
auto_close_probe.py —— TK监控器「自动关闭滚动方案」全量情报收集器 v1.3
=====================================================
v1.3 变更：采样改为"主线程 + 每点独立线程"单层结构（对齐本机验证通过的
  mini_sampling_check 模型），删除 v1.1 引入的外层 _point_work 线程——工作机
  两次（20:24/20:54）均在基础落盘后同秒静默失踪，怀疑两层 UIA COM 线程嵌套
  在 PyInstaller 冻结环境触发死锁/异常；另加 heartbeat_<ts>.mark 心跳文件
  与主线程异常 traceback 落盘，用于事后分辨"log 截断"与"进程被外部终止"。
v1.2 变更：采样阶段加"点级进度日志 + 心跳 + 时长提示"，消除 20:24 工作机
  运行"基础报告落盘后最长 90s 全静默→疑似卡死被提前关闭→采样结果丢失"盲区。
v1.1 变更：报告先落基础版再覆盖完整版；采样每点独立线程+3s 超时；
  补 env['class'] 采集；结尾 8s 自动退出。
定位：test_auto_close v0.13 视口外自动滚动失败的决策前置探测。老板拍板：
      "派一个程序去收集所有情报" —— 本程序在真实页面（工作机紫鸟/Chromium）上
      一次性收集滚动替代方案所需的全部只读情报，输出报告回传开发机分析。

只读保证：本程序绝不点击、不 Invoke、不 SetCursorPos、不发送滚轮、不 ScrollIntoView、
      不修改页面状态；所有 UIA 调用为属性读取，CDP 仅 HTTP GET 只读接口。

回答的决策问题（对应《会话交接_自动关闭视口外滚动》v0.14 修复方向）：
  Q1 外层表格容器/祖先是否支持 ScrollPattern？VerticalScrollPercent / ViewSize 数值？
     （若支持 → 可用 SetScrollPercent 或 Scroll 替代模拟滚轮，根治坐标问题）
  Q2 数据行控件自身是否带 ScrollItemPattern？支持率？（v0.13 记录"对 Chromium 无效"，复核全量）
  Q3 窗口客户区 vs 表格可视区真实坐标？可见行 y 带？视口外目标行距视口多远/哪一侧？
     （修复点：悬停点取行 rect ∩ 客户区交集中心 + clamp；y 锚改表格可视区）
  Q4 行内计划名/启停开关控件特征：rect、offscreen、checked 类名、左带结构；
     （滚动后按计划名/行号重定位链路所需）
  Q5 紫鸟进程命令行是否带 --remote-debugging-port？若带/若端口可达，CDP /json/version
     返回什么？（CDP 是最稳替代方案前置情报；只探测不连接操作）
  Q6 ElementFromPoint 网格采样：各候选悬停点（客户区中心/表格可视区/行中心 clamp 点）
     实际命中的 UIA 控件 —— 验证 v0.13 失败根因"悬停点落在表格外/工具栏"。

用法：
  auto_close_probe.exe                    # 自动检测店铺窗口（trf auto_detect_shop_keywords）
  auto_close_probe.exe --keyword A419     # 指定窗口关键词（可多次）
  auto_close_probe.exe --keyword 商户中心 --row 3 --plan "黑金刚喷头A"  # 指定目标行测距
  auto_close_probe.exe --outdir D:\\tmp    # 指定输出目录（默认 exe/脚本同目录）
  auto_close_probe.exe --no-cdp           # 跳过 CDP 端口探测（网络受限/慢时用）
  auto_close_probe.exe --max-nodes 40000 --timeout 120   # 调大收集预算

输出：
  <输出目录>/auto_close_probe_<YYYYmmdd_HHMMSS>.txt  完整报告（UTF-8，发回分析）
  <输出目录>/auto_close_probe_<YYYYmmdd_HHMMSS>.json 结构化数据（同内容，程序可解析）
  <输出目录>/auto_close_probe_<YYYYmmdd_HHMMSS>.log  运行日志

依赖：uiautomation + comtypes + test_refresh_filter（trf，复用窗口定位/渲染子窗口函数）。
"""

import argparse
import ctypes
import datetime
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
_TRF_DIR = os.path.join(os.path.dirname(BASE_DIR), "test_refresh_filter")
sys.path.insert(0, _TRF_DIR)

import test_refresh_filter as trf  # noqa: E402

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors="replace")
    except Exception:
        pass

# 输出目录：打包版 = exe 同级；源码版 = 脚本所在目录
if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    APP_DIR = BASE_DIR

TS = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
LOG_PATH = os.path.join(APP_DIR, f"auto_close_probe_{TS}.log")

VERSION = "v1.3"

# ============ 与主程序对齐的识别常量（test_auto_close.py 同源规则） ============
TABLE_ROW_CLASS = trf.TABLE_ROW_CLASS                      # "core-table-tr"
HEADER_WORDS = trf.READY_HEADER_STRONG + trf.READY_HEADER_WEAK
ROW_ACTION_WORDS = ("暂停", "数据分析", "修改", "编辑", "删除", "复制", "推广",
                    "查看报告", "更多", "操作", "⋯", "···")
STATUS_PAUSED_WORDS = ("已暂停", "暂停中")
STATUS_RUNNING_WORDS = ("投放中", "已生效", "启用中", "投放")
ROW_TEXT_TYPES = ("Text", "Button", "DataItem", "Edit", "Hyperlink",
                  "Custom", "Group", "ListItem")
SWITCH_CLS_MARK = "arco-switch-type-circle"
SWITCH_CHECKED_MARK = "theme-arco-switch-checked"
SWITCH_ZONE_PX = 160

# ElementFromPoint 与 pattern 全查的判定列表
SCROLL_CANDIDATE_TYPES = ("Pane", "Custom", "List", "Table", "Tree", "Group",
                          "DataGrid", "DataItem", "ListItem", "ScrollBar")

# CDP 默认探测端口（chromium 调试常用；若命令行有 --remote-debugging-port 会优先加入）
CDP_DEFAULT_PORTS = [9221, 9222, 9223, 9224, 9225, 9226, 9227, 9228, 9229,
                     9333, 8315, 9515]


_LOG_LOCK = threading.Lock()


def log(msg=""):
    line = f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {msg}"
    with _LOG_LOCK:
        print(line, flush=True)
        try:
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass


def _ct_short(ct):
    try:
        if ct and ct.endswith("Control"):
            return ct[:-len("Control")]
    except Exception:
        pass
    return ct or ""


def _ct_in(ct, names):
    return _ct_short(ct) in names


def _inside(inner, outer, pad=4):
    if not inner or not outer:
        return False
    x1, y1, x2, y2 = inner
    X1, Y1, X2, Y2 = outer
    return (x1 >= X1 - pad and y1 >= Y1 - pad and x2 <= X2 + pad and y2 <= Y2 + pad)


def _rect_key(r):
    try:
        return r[1] * 100000 + r[0]
    except Exception:
        return 0


# ==================== Win32 基础（ctypes 直调，只读） ====================
user32 = ctypes.WinDLL('user32', use_last_error=True)
kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def get_window_rect(hwnd):
    rc = RECT()
    if user32.GetWindowRect(hwnd, ctypes.byref(rc)):
        return (rc.left, rc.top, rc.right, rc.bottom)
    return None


def get_client_rect_screen(hwnd):
    """窗口客户区屏幕坐标 (l,t,r,b)；失败 None。"""
    rc = RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rc)):
        return None
    pt = (ctypes.c_long * 2)(0, 0)
    if not user32.ClientToScreen(hwnd, pt):
        return None
    return (pt[0], pt[1], pt[0] + rc.right, pt[1] + rc.bottom)


def get_window_class(hwnd):
    try:
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        return cls.value or ""
    except Exception:
        return ""


def get_process_id(hwnd):
    pid = ctypes.c_ulong()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value or None


def get_process_exe_path(pid):
    """QueryFullProcessImageNameW 取进程可执行文件完整路径。"""
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return None
    try:
        buf = ctypes.create_unicode_buffer(4096)
        size = ctypes.c_ulong(len(buf))
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value
        return None
    finally:
        kernel32.CloseHandle(h)


def get_file_version(exe_path):
    """PowerShell 取文件版本（FileVersion）。失败返回 None。"""
    if not exe_path or not os.path.isfile(exe_path):
        return None
    ps = ("$v=(Get-Item -LiteralPath '%s').VersionInfo; "
          "$v.FileVersion + ' | ' + $v.ProductVersion" % exe_path.replace("'", "''"))
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True, timeout=15, creationflags=0x08000000)  # CREATE_NO_WINDOW
        txt = (out.stdout or b"").decode("utf-8", errors="replace").strip()
        return txt or None
    except Exception:
        return None


def get_process_cmdline(pid):
    """PowerShell CIM 取进程命令行（UTF-8 落临时文件防编码坑）。"""
    tmp = os.path.join(APP_DIR, f".cmdline_{pid}_{TS}.tmp.json")
    ps = ("$p=Get-CimInstance Win32_Process -Filter \"ProcessId=%d\" -ErrorAction "
          "SilentlyContinue; if($p){$o=[pscustomobject]@{CommandLine=$p.CommandLine};"
          "$o | ConvertTo-Json -Compress | Set-Content -LiteralPath '%s' -Encoding UTF8}"
          % (pid, tmp.replace("'", "''")))
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       capture_output=True, timeout=20, creationflags=0x08000000)
        if os.path.exists(tmp):
            with open(tmp, "r", encoding="utf-8") as f:
                data = json.load(f)
            return (data or {}).get("CommandLine")
    except Exception:
        return None
    finally:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
    return None


def enum_chrome_child_windows(hwnd, max_depth=6):
    """递归枚举子窗口，返回 Chromium 渲染相关窗口（class 含 Chrome/RenderWidget）列表。"""
    out = []

    def _cb(acc, parent, depth):
        if depth > max_depth:
            return

        def cb(child, _lparam):
            if not user32.IsWindowVisible(child):
                return True
            cls = ctypes.create_unicode_buffer(512)
            user32.GetClassNameW(child, cls, 512)
            cls_v = cls.value or ""
            length = user32.GetWindowTextLengthW(child)
            title = ""
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(child, buf, length + 1)
                title = buf.value
            if "chrome" in cls_v.lower() or "renderwidget" in cls_v.lower():
                rc = get_window_rect(child)
                acc.append({"hwnd": child, "cls": cls_v, "title": title,
                            "rect": rc, "depth": depth})
            _cb(acc, child, depth + 1)
            return True

        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool,
                                         ctypes.c_void_p, ctypes.c_void_p)
        user32.EnumChildWindows(parent, WNDENUMPROC(cb), 0)

    _cb(out, hwnd, 0)
    return out


# ==================== UIA 收集（线程内执行防卡死） ====================
# Node 纯字段结构（ctrl 引用仅收集期使用，不入报告）
# {id, src, depth, parent, ct, name, aid, cls, rect, offscreen, enabled,
#  patterns, scroll, children_hint}


def _safe_str(fn):
    try:
        v = fn()
        return str(v or "")
    except Exception:
        return ""


def _uia_pattern_names(auto, ctrl, names):
    """按名称列表探测 pattern（名称->PatternId 反射）。失败返回空列表。"""
    out = []
    for pid_name in names:
        try:
            pid = getattr(auto.PatternId, pid_name)
        except Exception:
            continue
        try:
            if ctrl.GetPattern(pid) is not None:
                out.append(pid_name)
        except Exception:
            pass
    return out


def _uia_scroll_info(auto, ctrl):
    """读取 ScrollPattern 数值信息；不支持返回 None。

    兼容两代 uiautomation：
      A) 新版本 ctrl.GetScrollPattern() 返回封装类，属性名 HorizontallyScrollable/...
      B) 旧版本无快捷方法，走 GetPattern(ScrollPattern) 拿 COM 对象，
         属性名 CurrentHorizontallyScrollable/...
    """
    pat = None
    try:
        getter = getattr(ctrl, "GetScrollPattern", None)
        if callable(getter):
            pat = getter()
        else:
            pid = auto.PatternId.ScrollPattern
            pat = ctrl.GetPattern(pid)
    except Exception:
        pat = None
    if pat is None:
        return None
    attr_map = {"HorizontallyScrollable": ["HorizontallyScrollable",
                                           "CurrentHorizontallyScrollable"],
                "HorizontalScrollPercent": ["HorizontalScrollPercent",
                                            "CurrentHorizontalScrollPercent"],
                "HorizontalViewSize": ["HorizontalViewSize",
                                       "CurrentHorizontalViewSize"],
                "VerticallyScrollable": ["VerticallyScrollable",
                                         "CurrentVerticallyScrollable"],
                "VerticalScrollPercent": ["VerticalScrollPercent",
                                          "CurrentVerticalScrollPercent"],
                "VerticalViewSize": ["VerticalViewSize",
                                     "CurrentVerticalViewSize"]}
    info = {"supported": True}
    for key, cands in attr_map.items():
        val = None
        for attr in cands:
            try:
                val = getattr(pat, attr)
                break
            except Exception:
                continue
        info[key] = val if val is not None else "<不可读>"
    return info


class Collector:
    """整树收集 + 采样点 ControlFromPoint + 行/开关/祖先分析（全部在同一 UIA 线程内）。"""

    def __init__(self, auto, max_nodes, budget_sec):
        self.auto = auto
        self.max_nodes = max_nodes
        self.deadline = time.time() + budget_sec
        self.nodes = []          # 主树
        self.render_nodes = []   # 渲染子窗口树
        self.truncated = False
        self.render_truncated = False
        self.errors = []
        self._nid = 0
        self.render_hwnd = None

    # ---------- 树收集 ----------
    def _collect_one(self, ctrl, depth, parent, src):
        if self._nid >= self.max_nodes:
            return None
        self._nid += 1
        nid = self._nid
        node = {"id": nid, "src": src, "depth": depth, "parent": parent,
                "ct": _safe_str(lambda: ctrl.ControlTypeName),
                "name": trf._ctrl_name(ctrl),
                "aid": _safe_str(lambda: ctrl.AutomationId),
                "cls": _safe_str(lambda: ctrl.ClassName),
                "rect": None, "offscreen": False, "enabled": True,
                "patterns": [], "scroll": None}
        try:
            r = ctrl.BoundingRectangle
            node["rect"] = (int(r.left), int(r.top), int(r.right), int(r.bottom))
        except Exception:
            pass
        try:
            node["offscreen"] = bool(ctrl.IsOffscreen)
        except Exception:
            pass
        try:
            node["enabled"] = bool(ctrl.IsEnabled)
        except Exception:
            pass
        # 滚动候选类型：查 Scroll/ScrollItem + 数值
        if _ct_in(node["ct"], SCROLL_CANDIDATE_TYPES):
            node["patterns"] = _uia_pattern_names(
                self.auto, ctrl,
                ["ScrollPattern", "ScrollItemPattern", "InvokePattern",
                 "TogglePattern", "SelectionItemPattern",
                 "LegacyIAccessiblePattern", "ValuePattern"])
            node["scroll"] = _uia_scroll_info(self.auto, ctrl)
        return node

    def _collect_tree(self, root, src, nodes_out):
        stack = [(root, 0, 0)]
        visited = 0
        while stack and visited < self.max_nodes and time.time() < self.deadline:
            ctrl, depth, parent = stack.pop()
            visited += 1
            node = self._collect_one(ctrl, depth, parent, src)
            if node is None:
                break
            nodes_out.append(node)
            # 有 name/rect 或滚动候选深下钻；防超时
            if depth >= 60:
                continue
            try:
                children = ctrl.GetChildren()
            except Exception:
                children = []
            for ch in reversed(list(children)):
                stack.append((ch, depth + 1, node["id"]))
        if time.time() >= self.deadline:
            return "timeout"
        if visited >= self.max_nodes:
            return "max_nodes"
        return "ok"

    def collect_main(self, root):
        st = self._collect_tree(root, "main", self.nodes)
        self.truncated = st != "ok"
        return st

    def collect_render(self, hwnd):
        try:
            sub = trf._find_render_sub_hwnd_safe(hwnd)
            if sub and sub != hwnd:
                self.render_hwnd = sub
                sroot, err = trf._control_from_handle_safe(self.auto, sub, timeout=10.0)
                if sroot is not None:
                    st = self._collect_tree(sroot, "render", self.render_nodes)
                    self.render_truncated = st != "ok"
                    return True, st
                return False, f"render ControlFromHandle fail: {err}"
        except Exception as e:
            return False, f"render 收集异常: {type(e).__name__}: {e}"
        return False, "no render sub hwnd"

    # ---------- 采样点 ----------
    def _collect_hit_fields(self, c, auto_mod):
        """收集命中控件字段（须在已做 UIA 初始化的线程内调用）。"""
        hit = {
            "ct": _safe_str(lambda: c.ControlTypeName),
            "name": trf._ctrl_name(c),
            "aid": _safe_str(lambda: c.AutomationId),
            "cls": _safe_str(lambda: c.ClassName),
            "rect": None,
            "offscreen": False,
            "patterns": _uia_pattern_names(
                auto_mod, c,
                ["ScrollPattern", "ScrollItemPattern",
                 "InvokePattern", "TogglePattern",
                 "SelectionItemPattern",
                 "LegacyIAccessiblePattern", "ValuePattern"]),
        }
        try:
            r = c.BoundingRectangle
            hit["rect"] = (int(r.left), int(r.top),
                           int(r.right), int(r.bottom))
        except Exception:
            pass
        try:
            hit["offscreen"] = bool(c.IsOffscreen)
        except Exception:
            pass
        if _ct_in(hit["ct"], SCROLL_CANDIDATE_TYPES):
            hit["scroll"] = _uia_scroll_info(auto_mod, c)
        return hit

    def point_probe(self, points, per_point_timeout=3.0, max_timeouts=6):
        """对屏幕坐标点列表做 ElementFromPoint（ControlFromPoint）。

        v1.1: 每个点放进独立短线程执行并 join(per_point_timeout)，线程内
        自行 UIA 初始化并调用模块级 ControlFromPoint，避免个别坐标让
        Chromium provider 挂死拖垮整轮（v0.13 实测 11 点卡死 >40s）。
        单点超时标 timeout 继续下一颗；累计超时达 max_timeouts 提前中止。
        v1.2: 每点实时打进度日志（命中/超时/错误），避免"静默像死机"。
        """
        out = []
        lock = threading.Lock()
        timeout_cnt = [0]
        total = len(points)

        def _probe_one(p, i):
            rec = {"point": p, "hit": None}
            try:
                import uiautomation as autoX
                with autoX.UIAutomationInitializerInThread():
                    c = autoX.ControlFromPoint(p["x"], p["y"])
                    if c is not None:
                        rec["hit"] = self._collect_hit_fields(c, autoX)
            except Exception as e:
                rec["err"] = f"{type(e).__name__}: {e}"
            with lock:
                # 若主线程已因超时写入占位，则替换为真实结果，避免同点两条
                for i2, prev in enumerate(out):
                    if prev["point"] is p and prev.get("err") == "timeout":
                        out[i2] = rec
                        break
                else:
                    out.append(rec)
            _log_point(i, total, p, rec)

        def _log_point(i, total, p, rec):
            tag = p.get("tag", "")
            if rec.get("err") == "timeout":
                log(f"  采样 {i}/{total} ({tag}): 超时 >{per_point_timeout}s")
            elif rec.get("err"):
                log(f"  采样 {i}/{total} ({tag}): 错误 {rec['err']}")
            else:
                h = rec.get("hit") or {}
                r = h.get("rect")
                log(f"  采样 {i}/{total} ({tag}): 命中 {h.get('ct')} "
                    f"cls={h.get('cls', '')[:40]!r} off={h.get('offscreen')} "
                    f"rect={r}")

        for i, p in enumerate(points, 1):
            if timeout_cnt[0] >= max_timeouts:
                with lock:
                    out.append({"point": p, "hit": None,
                                "err": "skipped: too many timeouts"})
                log(f"  采样 {i}/{total} ({p.get('tag','')}): 跳过（累计超时达上限）")
                continue
            t = threading.Thread(target=_probe_one, args=(p, i), daemon=True)
            t.start()
            t.join(timeout=per_point_timeout)
            if t.is_alive():
                timeout_cnt[0] += 1
                with lock:
                    out.append({"point": p, "hit": None, "err": "timeout"})
                log(f"  采样 {i}/{total} ({p.get('tag','')}): 超时 >{per_point_timeout}s"
                    f"（线程仍在跑，若稍后返回将补打真实结果）")
        return out

    # ---------- 树后分析（纯数据，不碰 UIA） ----------
    def rows_of(self, src=None):
        """按主程序口径找数据行（cls 含 TABLE_ROW_CLASS 且有 rect）。"""
        pool = []
        for n in self.nodes + self.render_nodes:
            if src and n["src"] != src:
                continue
            if TABLE_ROW_CLASS in n["cls"] and n["rect"]:
                pool.append(n)
        return pool

    def row_text_plan(self, row):
        """行 rect 内文本控件聚合 (text, plan)。"""
        parts = []
        for n in self.nodes + self.render_nodes:
            if n is row or not n["rect"] or not n["name"]:
                continue
            if not _inside(n["rect"], row["rect"], pad=10):
                continue
            if not _ct_in(n["ct"], ROW_TEXT_TYPES):
                continue
            parts.append(n)
        parts.sort(key=lambda c: (c["rect"][0], c["rect"][1], c["rect"][2]))
        text_parts, seen = [], set()
        for c in parts:
            if c["name"] not in seen:
                seen.add(c["name"])
                text_parts.append(c["name"])
        plan = ""
        for c in parts:
            if not _ct_in(c["ct"], ("DataItem", "Text")):
                continue
            cand = self._extract_plan_name(c["name"])
            if cand:
                plan = cand
                break
        return " | ".join(text_parts), plan

    @staticmethod
    def _extract_plan_name(raw):
        if not raw:
            return ""
        out = raw
        for w in tuple(ROW_ACTION_WORDS) + ("优化",):
            out = out.replace(w, " ")
        out = " ".join(out.split())
        return out.strip(" |⋯·-")

    def scan_data_rows(self):
        """返回 (rows, header_row, errs)。rows=[{no,text,plan,rect,offscreen,node}]。"""
        raw = []
        for rc in self.rows_of():
            text, plan = self.row_text_plan(rc)
            raw.append({"node": rc, "rect": rc["rect"], "text": text,
                        "plan": plan, "offscreen": rc.get("offscreen", False)})
        raw.sort(key=lambda r: _rect_key(r["rect"]))
        header_row = None
        for r in raw:
            hits = sum(1 for w in HEADER_WORDS if w in r["text"])
            if hits >= 2:
                header_row = r
                break
        data_rows = [r for r in raw if r is not header_row]
        for i, r in enumerate(data_rows, 1):
            r["no"] = i
        return data_rows, header_row

    def find_row_switch(self, row):
        """行左带内最里层 Button/ToggleButton cls 含 arco-switch-type-circle。"""
        rect = row["rect"]
        x1, y1, _x2, y2 = rect
        zone = (x1, y1, x1 + SWITCH_ZONE_PX, y2)
        best = None
        for n in self.nodes + self.render_nodes:
            if not n["rect"] or not _inside(n["rect"], zone, pad=8):
                continue
            if not _ct_in(n["ct"], ("Button", "ToggleButton")):
                continue
            if SWITCH_CLS_MARK in n["cls"]:
                if best is None or self._area(n["rect"]) < self._area(best["rect"]):
                    best = n
        return best

    @staticmethod
    def _area(rect):
        try:
            return (rect[2] - rect[0]) * (rect[3] - rect[1])
        except Exception:
            return 0

    def visible_viewport(self, rows):
        """可见行 y 带 (top, bottom)；无可见行 None。"""
        tops, bottoms = [], []
        for r in rows:
            if not r.get("offscreen"):
                tops.append(r["rect"][1])
                bottoms.append(r["rect"][3])
        if not tops:
            return None
        return (min(tops), max(bottoms))

    def row_status(self, text):
        if not text:
            return "unknown"
        if any(w in text for w in STATUS_PAUSED_WORDS):
            return "paused"
        if "未投放" in text:
            return "nobudget"
        if any(w in text for w in STATUS_RUNNING_WORDS):
            return "running"
        return "unknown"

    def ancestors(self, node_id):
        """返回 node_id 的祖先链（近->远，含自身父级，不含 root id=0 哨兵）。"""
        by_id = {n["id"]: n for n in self.nodes + self.render_nodes}
        chain = []
        cur = node_id
        seen = 0
        while cur and cur in by_id and seen < 80:
            n = by_id[cur]
            if n["parent"] == 0:
                break
            chain.append(by_id[cur])
            cur = n["parent"]
            seen += 1
        return chain


# ==================== CDP 只读探测 ====================
def cdp_http(port, path, timeout=1.5):
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}",
            headers={"User-Agent": "auto-close-probe/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                return json.loads(raw)
            except Exception:
                return raw[:4000]
    except Exception as e:
        return f"ERR:{type(e).__name__}"


def probe_cdp(ports, only_ports):
    """探测端口列表 CDP 可达性；返回 [(port, version_json, targets)]。"""
    results = []
    for p in ports:
        if only_ports and p not in only_ports:
            continue
        ver = cdp_http(p, "/json/version")
        if isinstance(ver, dict) and ver.get("Browser"):
            targets = cdp_http(p, "/json", timeout=2.0)
            if isinstance(targets, list):
                targets = targets[:15]
            results.append((p, ver, targets))
        # 非 dict/无 Browser 字段 = 端口未开或非 CDP，静默
    return results


# ==================== 报告构建 ====================
def build_report(env, rows, header_row, cdp_res, collector, points, scan,
                 scan_note=""):
    L = []
    add = L.append
    sep = "=" * 72

    add(sep)
    add(f" auto_close_probe {VERSION} —— 自动关闭滚动方案 全量情报报告")
    add(f" 生成时间: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    add(sep)

    # ---- S2 窗口与进程 ----
    add("")
    add(sep)
    add("【S2 目标窗口与进程】")
    add(sep)
    add(f"窗口 hwnd    : 0x{env['hwnd'] & 0xFFFFFFFFFFFFFFFF:X}")
    add(f"窗口标题     : {env['title']}")
    add(f"窗口类名     : {env.get('class', '?')}")
    add(f"窗口 PID     : {env['pid']}")
    add(f"窗口 rect    : {env['win_rect']}")
    add(f"客户区(屏幕) : {env['client_rect']}")
    if env["exe_path"]:
        add(f"进程 exe     : {env['exe_path']}")
    if env["exe_ver"]:
        add(f"exe 版本     : {env['exe_ver']}")
    if env["cmdline"]:
        add(f"进程命令行   : {env['cmdline']}")
        m = re.search(r"--remote-debugging-port[=\s](\d+)", env["cmdline"], re.I)
        add(f"CDP 命令行标记: {'--remote-debugging-port=' + m.group(1) if m else '未发现（默认端口探测见 S7）'}")
    else:
        add("进程命令行   : （读取失败）")
    add("Chromium 子窗口:")
    if env["chrome_children"]:
        for ch in env["chrome_children"][:20]:
            add(f"  0x{ch['hwnd'] & 0xFFFFFFFFFFFFFFFF:X} d{ch['depth']} cls={ch['cls'][:60]} "
                f"rect={ch['rect']} title={ch['title'][:40]!r}")
    else:
        add("  （未枚举到 Chrome/RenderWidget 子窗口）")

    # ---- S3 收集统计 ----
    add("")
    add(sep)
    add("【S3 UIA 树收集统计】")
    add(sep)
    add(f"主窗口控件数 : {len(collector.nodes)}{'（截断/超时）' if collector.truncated else ''}")
    add(f"渲染子窗口   : 0x{collector.render_hwnd & 0xFFFFFFFFFFFFFFFF:X} 控件数 "
        f"{len(collector.render_nodes)}"
        f"{'（截断/超时）' if collector.render_truncated else ''}"
        if collector.render_hwnd else "（未找到渲染子窗口/未收集）")

    # ---- S4 行扫描 ----
    add("")
    add(sep)
    add("【S4 表格行与可视视口】")
    add(sep)
    add(f"数据行总数   : {len(rows)}（表头行: {'识别到' if header_row else '未识别'}）")
    if header_row:
        add(f"表头行 rect  : {header_row['rect']} 文本: {header_row['text'][:120]}")
    vp = collector.visible_viewport(rows) if rows else None
    if vp:
        add(f"可视视口 y带 : top={vp[0]} bottom={vp[1]} 高={vp[1] - vp[0]}px（由可见行推断）")
    else:
        add("可视视口 y带 : 无可见行（视口推断将退化为窗口客户区 —— v0.13 失败场景）")
    vis_n = sum(1 for r in rows if not r.get("offscreen"))
    add(f"可见行       : {vis_n}/{len(rows)}")
    if rows:
        hs = [r["rect"][3] - r["rect"][1] for r in rows if r["rect"]]
        if hs:
            add(f"行高典型值   : min={min(hs)} max={max(hs)}"
                f"（首可见行高 {hs[0] if hs else '?'}）")
    add("")
    for r in rows[:40]:
        sw = collector.find_row_switch(r)
        sw_s = "无"
        if sw:
            checked = SWITCH_CHECKED_MARK in (sw["cls"] or "")
            sw_s = ("Button checked=白钮(运行中)" if checked else
                    "Button checked=灰钮(已暂停)")
        add(f"行#{r['no']:>3} off={str(r.get('offscreen')):<5} rect={r['rect']} "
            f"状态={collector.row_status(r['text'])} 开关={sw_s}")
        if r["plan"]:
            add(f"       计划: {r['plan'][:100]}")
        else:
            add(f"       计划: （未识别） 文本: {r['text'][:120]}")
        if sw is None:
            add("       注意: 行左带未找到启停开关（结构变化?）")

    # ---- S5 滚动容器与祖先链 ----
    add("")
    add(sep)
    add("【S5 滚动容器 / 祖先链情报】（Q1/Q2 核心）")
    add(sep)
    # 全树 ScrollPattern 支持清单
    scroll_nodes = []
    for n in collector.nodes + collector.render_nodes:
        if n.get("scroll") and n["scroll"].get("supported"):
            scroll_nodes.append(n)
    add(f"全树支持 ScrollPattern 的控件数: {len(scroll_nodes)}")
    for n in scroll_nodes[:40]:
        s = n["scroll"]
        add(f"  id={n['id']} src={n['src']} d={n['depth']} [{n['ct']}] "
            f"name={n['name'][:40]!r} cls={n['cls'][:50]!r} rect={n['rect']}")
        add(f"      scroll: {json.dumps({k: v for k, v in s.items() if k != 'supported'}, ensure_ascii=False)}")
    # 数据行 ScrollItemPattern 支持率
    if rows:
        row_with_item = 0
        row_pattern_summary = {}
        for r in rows[:80]:
            node = r["node"]
            pats = node.get("patterns") or []
            if "ScrollItemPattern" in pats:
                row_with_item += 1
            for p in pats:
                row_pattern_summary[p] = row_pattern_summary.get(p, 0) + 1
        add(f"前 {min(len(rows), 80)} 行 ScrollItemPattern 支持: "
            f"{row_with_item}/{min(len(rows), 80)}")
        add(f"行控件 patterns 汇总: {row_pattern_summary or '（行本身未暴露任何候选 pattern）'}")
    # 行 -> 最近 Scrollable 祖先
    if rows:
        add("")
        add("行祖先链中最近的 Scrollable 容器（每行至多 1 条; 全行去重）:")
        seen_scroll_anc = {}
        for r in rows[:80]:
            anc = collector.ancestors(r["node"]["id"])
            found = None
            for a in anc:
                if (a.get("scroll") or {}).get("supported"):
                    found = a
                    break
            if found is not None:
                key = found["id"]
                seen_scroll_anc.setdefault(key, []).append(r["no"])
        if seen_scroll_anc:
            for nid, rows_used in seen_scroll_anc.items():
                node = next((n for n in collector.nodes + collector.render_nodes
                             if n["id"] == nid), None)
                if not node:
                    continue
                add(f"  容器 id={node['id']} [{node['ct']}] name={node['name'][:40]!r} "
                    f"cls={node['cls'][:60]!r} rect={node['rect']}")
                add(f"     scroll={json.dumps({k: v for k, v in (node['scroll'] or {}).items() if k != 'supported'}, ensure_ascii=False)}"
                    f" 覆盖行={rows_used[:30]}")
        else:
            add("  （任何数据行的祖先链中均未发现 ScrollPattern 容器 —— 容器级滚动不可用证据）")

    # ---- S6 采样点 ----
    add("")
    add(sep)
    add("【S6 ElementFromPoint 采样】（验证 v0.13 悬停点实际命中）")
    add(sep)
    if scan:
        for rec in scan:
            p = rec["point"]
            add(f"点({p['x']},{p['y']}) tag={p.get('tag', '')}:")
            hit = rec.get("hit")
            if hit is None:
                add("    -> 未命中控件（空白/桌面）")
            else:
                add(f"    -> [{hit['ct']}] name={hit['name'][:60]!r} "
                    f"cls={hit['cls'][:60]!r} rect={hit['rect']} off={hit['offscreen']}")
                if hit.get("patterns"):
                    add(f"       patterns={hit['patterns']}")
                if hit.get("scroll"):
                    add(f"       scroll={json.dumps({k: v for k, v in hit['scroll'].items() if k != 'supported'}, ensure_ascii=False)}")
            if rec.get("err"):
                add(f"    [探测异常] {rec['err']}")
    else:
        add(f"（无采样结果。{scan_note or '未执行采样'}）")

    # ---- S7 CDP ----
    add("")
    add(sep)
    add("【S7 CDP 调试端口探测】")
    add(sep)
    if cdp_res is None:
        add("（已按 --no-cdp 跳过）")
    elif not cdp_res:
        add("未发现可达 CDP 端口。探测列表: "
            + ", ".join(str(p) for p in env["cdp_ports"]))
    else:
        for port, ver, targets in cdp_res:
            add(f"端口 {port} CDP 可达:")
            add(f"  Browser  : {ver.get('Browser', '?')}")
            add(f"  Protocol : {ver.get('Protocol-Version', '?')}")
            add(f"  UA       : {ver.get('User-Agent', '?')[:120]}")
            add(f"  ws       : {ver.get('webSocketDebuggerUrl', '?')}")
            if isinstance(targets, list):
                add(f"  页面 targets ({len(targets)}):")
                for t in targets[:10]:
                    add(f"    [{t.get('type', '?')}] {t.get('title', '?')[:60]} "
                        f"url={t.get('url', '?')[:120]}")
            else:
                add(f"  /json 响应: {str(targets)[:300]}")

    # ---- S8 自动结论 ----
    add("")
    add(sep)
    add("【S8 结论速览】")
    add(sep)
    if rows:
        offs = [r for r in rows if r.get("offscreen")]
        add(f"数据行 {len(rows)} 条，视口外 {len(offs)} 条，可见 {vis_n} 条。")
        if offs:
            first_off = offs[0]
            cy = (first_off["rect"][1] + first_off["rect"][3]) // 2
            if vp:
                if cy < vp[0]:
                    d = vp[0] - cy
                    add(f"首个视口外行 #{first_off['no']} 在视口上方，中心距视口 top {d}px"
                        f"（{first_off['plan'][:50] or '计划名未识别'}）")
                else:
                    d = cy - vp[1]
                    add(f"首个视口外行 #{first_off['no']} 在视口下方，中心距视口 bottom {d}px"
                        f"（{first_off['plan'][:50] or '计划名未识别'}）")
            else:
                add(f"首个视口外行 #{first_off['no']} 中心 y={cy}，无可见行可参照视口。")
    else:
        add("未识别数据行（页面可能不在计划列表/表未加载）。")
    add(f"ScrollPattern 容器数: {len(scroll_nodes)} | "
        f"CDP 可达端口数: {len(cdp_res) if cdp_res else 0}")
    if scroll_nodes:
        add("建议: 存在 ScrollPattern 容器，v0.14 可优先验证 SetScrollPercent/Scroll 直滚。")
    if cdp_res:
        add("建议: CDP 端口可达，可评估 connectOverCDP/调试协议方案（最稳，无坐标依赖）。")
    add("")
    add("--- END ---")
    return "\n".join(L)


def dump_json(env, rows_export, cdp_export, collector, points, scan, path):
    data = {
        "version": VERSION,
        "ts": TS,
        "env": env,
        "rows": rows_export,
        "cdp": cdp_export,
        "collect_stats": {
            "main": len(collector.nodes),
            "render": len(collector.render_nodes),
            "truncated": collector.truncated,
            "render_truncated": collector.render_truncated,
        },
        "scroll_containers": [n for n in collector.nodes + collector.render_nodes
                              if (n.get("scroll") or {}).get("supported")],
        "points": scan or [],
        "sampled_points": points,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1, default=str)


# ==================== main ====================
def main():
    ap = argparse.ArgumentParser(description="auto_close_probe 全量情报收集器")
    ap.add_argument("--keyword", action="append", default=None,
                    help="窗口关键词（可多次；默认自动检测/商户中心）")
    ap.add_argument("--outdir", default=None, help="输出目录（默认 exe/脚本同目录）")
    ap.add_argument("--max-nodes", type=int, default=30000, help="最大控件数（默认30000）")
    ap.add_argument("--timeout", type=float, default=100.0, help="UIA 总预算秒数（默认100）")
    ap.add_argument("--row", type=int, default=0,
                    help="指定目标行号（行扫描列表中的 no，用于测距分析）")
    ap.add_argument("--plan", default=None, help="指定目标计划名（配合 --row）")
    ap.add_argument("--no-cdp", action="store_true", help="跳过 CDP 端口探测")
    ap.add_argument("--no-render", action="store_true", help="不收集渲染子窗口树")
    ap.add_argument("--cdp-ports", default="", help="额外 CDP 端口（逗号分隔，如 9222,9333）")
    args = ap.parse_args()

    outdir = args.outdir or APP_DIR
    os.makedirs(outdir, exist_ok=True)
    log("=" * 60)
    log(f" auto_close_probe {VERSION} 启动")
    log(f" 输出目录: {outdir}")
    log("=" * 60)

    # 1) 窗口定位
    keywords = list(args.keyword or [])
    if not keywords:
        auto_kws = trf.auto_detect_shop_keywords()
        keywords = auto_kws or ["商户中心"]
        log(f"窗口关键词: 自动={auto_kws or '无'} -> {keywords}")
    hwnd, title, hit_kw = trf.find_window_by_keywords_multi(keywords)
    if not hwnd:
        log(f"!! 未找到标题含 {keywords} 的窗口。当前可见窗口（前25）:")
        for i, (wt, _h) in enumerate(trf.list_windows()[:25], 1):
            log(f"   {i:2d}. {wt[:80]}")
        log("请确认紫鸟已打开 TK 广告计划列表页后重跑。")
        return 2
    log(f"目标窗口 hwnd=0x{hwnd & 0xFFFFFFFFFFFFFFFF:X} 标题={title} 命中={hit_kw}")

    # 2) 进程情报
    pid = get_process_id(hwnd)
    win_rect = get_window_rect(hwnd)
    client_rect = get_client_rect_screen(hwnd)
    exe_path = get_process_exe_path(pid) if pid else None
    exe_ver = get_file_version(exe_path) if exe_path else None
    cmdline = get_process_cmdline(pid) if pid else None
    chrome_children = enum_chrome_child_windows(hwnd)
    env = {
        "hwnd": hwnd, "title": title, "hit_kw": hit_kw, "class": get_window_class(hwnd),
        "pid": pid, "win_rect": win_rect, "client_rect": client_rect,
        "exe_path": exe_path, "exe_ver": exe_ver, "cmdline": cmdline,
        "chrome_children": chrome_children,
        "cdp_ports": [],
    }
    log(f"进程 PID={pid} exe={exe_path or '?'} ver={exe_ver or '?'}")
    log(f"窗口 rect={win_rect} 客户区={client_rect}")

    # 3) UIA 收集（线程内）
    cdp_res = None
    if not args.no_cdp:
        ports = list(CDP_DEFAULT_PORTS)
        m = re.search(r"--remote-debugging-port[=\s](\d+)", cmdline or "", re.I)
        if m:
            ports.insert(0, int(m.group(1)))
        for s in args.cdp_ports.split(","):
            s = s.strip()
            if s.isdigit():
                ports.insert(0, int(s))
        env["cdp_ports"] = sorted(set(ports))
        log(f"CDP 端口探测: {env['cdp_ports']}")
        cdp_res = probe_cdp(env["cdp_ports"], set(ports))
        log(f"CDP 可达: {len(cdp_res)} 个端口")

    state = {}

    def _uia_work():
        try:
            import uiautomation as auto
        except Exception as e:
            log(f"!! uiautomation import 失败: {e}")
            return
        try:
            with auto.UIAutomationInitializerInThread():
                root, err = trf._control_from_handle_safe(auto, hwnd, timeout=10.0)
                if root is None:
                    state["err"] = f"ControlFromHandle 失败: {err}"
                    log(state["err"])
                    return
                coll = Collector(auto, args.max_nodes, max(30.0, args.timeout - 20))
                st_main = coll.collect_main(root)
                log(f"主窗口树收集完成: {len(coll.nodes)} 节点 ({st_main})")
                if not args.no_render:
                    ok_r, st_r = coll.collect_render(hwnd)
                    rh = (coll.render_hwnd & 0xFFFFFFFFFFFFFFFF) if coll.render_hwnd else 0
                    log(f"渲染子窗口收集: {'成功' if ok_r else '失败'} "
                        f"({st_r if st_r else '无'}) render_hwnd=0x{rh:X}")
                state["coll"] = coll
        except Exception as e:
            state["err"] = f"UIA 收集异常: {type(e).__name__}: {e}"
            log(state["err"])

    th = threading.Thread(target=_uia_work, daemon=True)
    th.start()
    th.join(timeout=args.timeout + 15)
    if th.is_alive():
        log("!! UIA 收集超时，使用部分结果")
    if "err" in state and "coll" not in state:
        log("收集失败，中止。")
        return 3

    coll = state["coll"]
    rows, header_row = coll.scan_data_rows()
    log(f"行扫描: 数据行 {len(rows)}，表头 {'有' if header_row else '无'}")
    if not rows:
        log("未识别数据行——仍会输出树级 Scroll 容器与采样情报供分析。")
    vp = coll.visible_viewport(rows) if rows else None
    if vp:
        log(f"可视视口 y带={vp} 高={vp[1]-vp[0]}px")

    # 4) 采样点构造
    points = []
    cr = client_rect
    if cr:
        cw = cr[2] - cr[0]
        chh = cr[3] - cr[1]
        # a) 客户区 y 网格（每 20%），x 偏右（右列报表区常见滚轮悬停位）
        for frac in (0.2, 0.4, 0.6, 0.8):
            points.append({"x": cr[2] - max(60, cw // 4), "y": cr[1] + int(chh * frac),
                           "tag": f"client-{int(frac*100)}%"})
        # b) 客户区中心
        points.append({"x": (cr[0] + cr[2]) // 2, "y": (cr[1] + cr[3]) // 2,
                       "tag": "client-center"})
        # c) v0.13 失败场景点：整行中心 y=客户区中心 附近（模拟 x 超宽被 clamp 到客户区右缘）
        points.append({"x": cr[2] - 2, "y": (cr[1] + cr[3]) // 2,
                       "tag": "clamp-x-right"})
    if vp:
        points.append({"x": cr[2] - max(60, cw // 4), "y": (vp[0] + vp[1]) // 2,
                       "tag": "vp-center-right"})
    # d) 可见行中心（前 6 行）+ 视口外目标行中心（clamp 前/后）
    for r in rows[:6]:
        xc = (r["rect"][0] + r["rect"][2]) // 2
        yc = (r["rect"][1] + r["rect"][3]) // 2
        points.append({"x": xc, "y": yc, "tag": f"row#{r['no']}-center"})
    if cr:
        for r in rows[:6]:
            xc = (r["rect"][0] + r["rect"][2]) // 2
            yc = (r["rect"][1] + r["rect"][3]) // 2
            if xc > cr[2] - 1 or xc < cr[0]:
                points.append({"x": max(cr[0] + 1, min(cr[2] - 2, xc)),
                               "y": max(cr[1] + 1, min(cr[3] - 2, yc)),
                               "tag": f"row#{r['no']}-clamped"})
    off_rows = [r for r in rows if r.get("offscreen")]
    target_rows = []
    if args.row:
        for r in rows:
            if r["no"] == args.row:
                target_rows.append(r)
    elif off_rows:
        target_rows = off_rows[:1]
    for r in target_rows:
        xc = (r["rect"][0] + r["rect"][2]) // 2
        yc = (r["rect"][1] + r["rect"][3]) // 2
        points.append({"x": xc, "y": yc, "tag": f"target-row#{r['no']}-raw-center"})
        if cr and (xc > cr[2] - 1 or xc < cr[0] or yc < cr[1] or yc > cr[3] - 1):
            points.append({"x": max(cr[0] + 1, min(cr[2] - 2, xc)),
                           "y": max(cr[1] + 1, min(cr[3] - 2, yc)),
                           "tag": f"target-row#{r['no']}-clamped"})
    # 去重
    seen_pts = set()
    uniq_points = []
    for p in points:
        k = (p["x"], p["y"])
        if k not in seen_pts:
            seen_pts.add(k)
            uniq_points.append(p)
    points = uniq_points[:48]
    log(f"采样点: {len(points)} 个 -> 执行 ControlFromPoint ...")

    # 输出文件路径（同一次运行同一 TS；先落基础报告，采样完成后覆盖为完整版）
    txt_path = os.path.join(outdir, f"auto_close_probe_{TS}.txt")
    json_path = os.path.join(outdir, f"auto_close_probe_{TS}.json")
    rows_export = []
    for r in rows[:200]:
        sw = coll.find_row_switch(r)
        rows_export.append({
            "no": r["no"], "rect": r["rect"], "offscreen": r["offscreen"],
            "status": coll.row_status(r["text"]), "plan": r["plan"],
            "text": r["text"][:400],
            "switch": ({k: sw[k] for k in ("id", "ct", "name", "cls", "rect",
                                           "offscreen", "patterns", "scroll")}
                       if sw else None),
        })

    def _write_reports(scan, scan_note=""):
        txt = build_report(env, rows, header_row, cdp_res, coll, points,
                           scan, scan_note)
        with open(txt_path, "w", encoding="utf-8-sig") as f:
            f.write(txt)
        try:
            dump_json(env, rows_export, cdp_res, coll, points, scan, json_path)
        except Exception as e:
            log(f"JSON 写出失败: {e}")

    # 行扫描完成即先行落盘（即使采样卡死/进程被杀，主体情报也已保留）
    _write_reports([], "采样未执行/进行中——此为采样前快照，完整版会覆盖")
    log(f"基础报告已先行落盘: {txt_path}")

    scan = []
    if points:
        # v1.3: 采样直接在主线程执行（不再套 _point_work 外层线程）。
        # 原因：v1.1/v1.2 用"外层线程 + 内层每点线程"两层结构，工作机上
        # 采样开始后主线程连心跳都消失（20:24/20:54 两次同秒失踪）；而本机
        # mini_sampling_check 用"主线程 + 每点独立线程"单层结构 8/8 秒回。
        # point_probe 内部每点独立线程 + 3s 超时已足够防单点卡死。
        # 心跳文件 heartbeat_<ts>.mark：每点/每 5s 更新 mtime，用于分辨
        # "log 被截断/没 flush"与"进程真被外部终止"。
        log(f"采样开始：{len(points)} 点，单点上限 3s，预计最长约 40s。"
            f"期间请勿关闭本窗口/进程，进度会逐点打印。")
        mark = os.path.join(outdir, f"heartbeat_{TS}.mark")
        try:
            with open(mark, "w", encoding="utf-8") as f:
                f.write("")
        except Exception:
            mark = None

        def _touch():
            try:
                if mark:
                    with open(mark, "a", encoding="utf-8") as f:
                        f.write("")
            except Exception:
                pass

        # 纯文件心跳线程：每 5s touch 一次 mark。不碰 UIA/COM，
        # 只用于事后分辨"log 截断"与"进程被外部终止"。
        stop_beat = threading.Event()

        def _beat():
            while not stop_beat.is_set():
                _touch()
                stop_beat.wait(5)

        th_beat = threading.Thread(target=_beat, daemon=True)
        th_beat.start()

        try:
            import uiautomation as auto
            with auto.UIAutomationInitializerInThread():
                coll2 = Collector(auto, 0, 20)
                scan = coll2.point_probe(points)
        except Exception as e:
            log(f"!! 采样异常: {type(e).__name__}: {e}")
            scan = [{"point": {"x": 0, "y": 0},
                     "err": f"采样异常: {type(e).__name__}: {e}"}]
        finally:
            stop_beat.set()
            _touch()
        done = sum(1 for s in scan if s.get("hit"))
        to = sum(1 for s in scan if "timeout" in (s.get("err") or ""))
        log(f"采样完成: 命中 {done}/{len(scan)} 点，超时 {to} 点")

    # 采样完成后重写完整报告
    _write_reports(scan)
    log("")
    log("=" * 60)
    log("探测完成。报告文件：")
    log(f"  TXT: {txt_path}")
    log(f"  JSON: {json_path}")
    log("=" * 60)
    log("8 秒后自动退出...")
    time.sleep(8)
    return 0


if __name__ == "__main__":
    try:
        rc = main()
    except Exception:
        import traceback
        tb = traceback.format_exc()
        # 主线程崩溃时也把 traceback 落进 log，便于工作机回传定位
        try:
            log("!! 主线程异常:\n" + tb)
        except Exception:
            pass
        traceback.print_exc()
        rc = 1
    sys.exit(rc)
