# -*- coding: utf-8 -*-
"""
auto_close_engine.py —— TK监控器「广告计划自动暂停」真实点击引擎（自包含模块版）

来源：test_auto_close.py（v0.12.2）已实测验证的 UIA 链路，按主程序合入要求摘录为
独立模块：收树 → 表格行定位（行号+计划名双重校验）→ 行左启停开关识别（cls 判态，
不依赖 OCR/颜色）→ 点前校验 → 坐标点击 → 轮询翻转/弹窗 → 弹窗双按钮校验后点确认。
不依赖 test_refresh_filter / test_auto_close，全部内联；uiautomation 延迟导入。
v0.14（2026-09-06）：移植 test_auto_close v0.13 视口外自动滚动并修复坐标根因——
行 rect 为 Chromium 虚拟布局坐标时悬停点 clamp 进窗口客户区、无可见行时悬停 y 偏表格
下半区、滚轮前 WindowFromPoint 命中校验（详见下方 v0.14 区块注释）。
v0.15（2026-09-08）：修复确认弹窗按钮误判——22:50 实跑中弹窗确认/取消按钮被 find_confirm_dialog
纯几何全包含误当「表格行内按钮」剔除（浮层按钮 rect 落入整行宽 core-table-tr rect）致暂停失败；
排除分支增加浮层大按钮例外：name 精确=确认/取消 且 cls 含 theme-arco-btn-size-large 时放行进
独立集合 dlg_like，普通候选为空但 dlg_like 双按钮齐备视为有效弹窗；保留双按钮在场兜底校验；
排除诊断日志补打命中行 rect/行号。

主程序用法（auto_close_ad 合入）：
    import auto_close_engine as ace
    ace.run_auto_close(cfg, plan_name, row_no, reason, logger=log_fn)

安全设计（对齐 v0.9.5 / v0.11 验证版）：
    1. 点前重收树，行号 + 计划名双重定位，不一致即中止，绝不拿旧 rect 硬点；
    2. 只认行左 type-circle 开关（cls 含 arco-switch-type-circle），无开关不回退文字按钮；
    3. 关闭方向：开关必须 checked(白钮=运行中) 才点；灰钮=已暂停无需操作；
    4. IsOffscreen 不硬点，先滚动进可视区（v0.14 起：ScrollItemPattern 白嫖 →
       模拟滚轮逼近，悬停点 clamp+命中校验）并重新校验；
    5. 点击后轮询（10 轮）：开关翻转视为成功；弹窗出现须【确认类按钮+取消按钮】双按钮
       校验通过且 rect 在表格行外才自动点确认（网络报错等浮层不满足则不点，交人工）；
    6. 无人值守前置条件由主程序闸门 is_unattended() 保证，本引擎不再做键鼠空闲等待。

模块级日志：默认 print；调用方可 set_logger(fn) 注入统一日志回调（fn(msg)）。
"""
import ctypes
import ctypes.wintypes
import os
import re
import sys
import threading
import time
from datetime import datetime

# ============ 常量区（对齐 test_auto_close / test_refresh_filter 实测值） ============
TABLE_ROW_CLASS = "core-table-tr"
HEADER_WORDS = ("广告计划名称", "计划名称", "广告计划", "总收入", "总成本",
                "sku 订单数", "状态", "ROI", "成本", "预算", "目标", "收入")
ROW_ACTION_WORDS = ("暂停", "数据分析", "修改", "编辑", "删除", "复制", "推广",
                    "查看报告", "更多", "操作", "⋯", "···")
ROW_TEXT_TYPES = ("Text", "Button", "DataItem", "Edit", "Hyperlink",
                  "Custom", "Group", "ListItem")
CTRL_TYPES_CLICKABLE = ("Button", "DataItem", "ListItem", "MenuItem", "Hyperlink",
                        "TabItem", "CheckBox", "RadioButton", "ComboBox", "TreeItem")
STATUS_PAUSED_WORDS = ("已暂停", "暂停中")
STATUS_RUNNING_WORDS = ("投放中", "已生效", "启用中", "投放")
CONFIRM_CLOSE = ("暂停", "停用", "停止", "停止投放", "确认暂停", "确定暂停", "确认")
SWITCH_ZONE_PX = 160
SWITCH_CLS_MARK = "arco-switch-type-circle"
SWITCH_CHECKED_MARK = "theme-arco-switch-checked"

_logger_fn = None


def set_logger(fn):
    """注入日志回调 fn(msg)；None 时回退 print。"""
    global _logger_fn
    _logger_fn = fn


def log(msg):
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    if _logger_fn is not None:
        try:
            _logger_fn(line)
            return
        except Exception:
            pass
    try:
        print(line, flush=True)
    except Exception:
        pass


# ============ 基础工具 ============
def _safe_str(fn):
    try:
        v = fn()
        return str(v or "")
    except Exception:
        return ""


def _ct_short(ct):
    try:
        if ct.endswith("Control"):
            return ct[:-len("Control")]
    except Exception:
        pass
    return ct


def _ct_in(ct, names):
    return _ct_short(ct) in names


def _area(rect):
    try:
        return (rect[2] - rect[0]) * (rect[3] - rect[1])
    except Exception:
        return 0


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


def _is_header_row(row_info, words):
    hits = sum(1 for w in words if w in row_info["text"])
    return hits >= 2


def _fmt_ctrl(c):
    r = c["rect"]
    rs = f"({r[0]},{r[1]},{r[2]},{r[3]})" if r else "无rect"
    return (f"[{c['ct']}] name={c['name']!r} aid={c['aid']!r} cls={c['cls']!r} "
            f"rect={rs} off={c['offscreen']} d={c['depth']}")


def _ctrl_name(ctrl):
    try:
        n = ctrl.Name
    except Exception:
        n = None
    if n and isinstance(n, str) and n.strip():
        return n.strip()
    try:
        vp = ctrl.GetValuePattern()
        v = vp.Value
    except Exception:
        v = None
    if v and isinstance(v, str) and v.strip():
        return v.strip()
    return ""


# ============ 窗口定位 ============
def find_window_by_keyword(keyword):
    """顶层可见窗口标题包含 keyword → 返回 (hwnd, title)；找不到返回 (None, "")。"""
    user32 = ctypes.windll.user32

    def _get_title(hwnd):
        try:
            n = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 2)
            user32.GetWindowTextW(hwnd, buf, n + 2)
            return buf.value
        except Exception:
            return ""

    out = []
    try:
        EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def cb(hwnd, _):
            try:
                if not user32.IsWindowVisible(hwnd):
                    return True
                t = _get_title(hwnd)
                if t and keyword and keyword in t:
                    out.append((hwnd, t))
            except Exception:
                pass
            return True
        user32.EnumWindows(EnumWindowsProc(cb), 0)
    except Exception as e:
        log(f"[窗口] EnumWindows 异常: {e}")
    if not out:
        return None, ""
    out.sort(key=lambda x: -len(x[1]))
    return out[0][0], out[0][1]


# ============ 控件收集（子线程防卡死） ============
def _control_from_handle_safe(auto, hwnd, timeout=10.0):
    result = {"ctrl": None, "err": ""}

    def _run():
        try:
            with auto.UIAutomationInitializerInThread():
                result["ctrl"] = auto.ControlFromHandle(hwnd)
        except Exception as e:
            result["err"] = repr(e)

    th = threading.Thread(target=_run, daemon=True)
    th.start()
    th.join(timeout)
    if th.is_alive():
        return None, "ControlFromHandle 超时(>%.0fs)" % timeout
    if result["err"]:
        return None, result["err"]
    return result["ctrl"], ""


def _ui_call_safe(fn, timeout=8.0, desc=""):
    result = {"ok": False, "err": ""}

    def _run():
        try:
            import uiautomation as _auto
            with _auto.UIAutomationInitializerInThread():
                fn()
                result["ok"] = True
        except Exception as e:
            result["err"] = repr(e)

    th = threading.Thread(target=_run, daemon=True)
    th.start()
    th.join(timeout)
    if th.is_alive():
        return False, (desc + f"超时(>{timeout:.0f}s)" if desc else f"超时(>{timeout:.0f}s)")
    if not result["ok"]:
        return False, result["err"]
    return True, ""


def _collect_infos_with_rect(auto, root, max_nodes=12000, timeout_sec=20.0):
    result = {"infos": []}

    def _collect(root, max_nodes, deadline):
        out = []
        stack = [(root, 0)]
        visited = 0
        while stack and visited < max_nodes and time.time() < deadline:
            ctrl, depth = stack.pop()
            visited += 1
            try:
                for ch in ctrl.GetChildren():
                    stack.append((ch, depth + 1))
            except Exception:
                pass
            if depth > 0:
                info = {
                    "ctrl": ctrl,
                    "ct": _safe_str(lambda: ctrl.ControlTypeName),
                    "name": _ctrl_name(ctrl),
                    "aid": _safe_str(lambda: ctrl.AutomationId),
                    "cls": _safe_str(lambda: ctrl.ClassName),
                    "depth": depth,
                    "offscreen": False,
                }
                try:
                    info["offscreen"] = bool(ctrl.IsOffscreen)
                except Exception:
                    pass
                try:
                    r = ctrl.BoundingRectangle
                    info["rect"] = (int(r.left), int(r.top), int(r.right), int(r.bottom))
                except Exception:
                    info["rect"] = None
                out.append(info)
        return out

    def _run():
        try:
            with auto.UIAutomationInitializerInThread():
                result["infos"] = _collect(root, max_nodes, time.time() + timeout_sec)
        except Exception:
            pass

    th = threading.Thread(target=_run, daemon=True)
    th.start()
    th.join(timeout_sec + 8.0)
    if th.is_alive():
        log("[收集] 子树收集超时，可能返回部分数据")
    return result["infos"]


def collect_all(auto, hwnd, with_render=False):
    """主窗口控件整树收集；返回 info 列表。with_render=True 时额外收集渲染子窗口。"""
    out = []
    root, err = _control_from_handle_safe(auto, hwnd, timeout=10.0)
    if root is None:
        log(f"[收集] ControlFromHandle(0x{hwnd & 0xFFFFFFFFFFFFFFFF:X}) 失败: {err or 'None'}")
    else:
        infos = _collect_infos_with_rect(auto, root)
        out.extend(infos)
        log(f"[收集] 主窗口控件 {len(infos)} 个")
    if with_render:
        sub = _find_render_sub_hwnd(hwnd)
        if sub and sub != hwnd:
            sroot, err2 = _control_from_handle_safe(auto, sub, timeout=10.0)
            if sroot is not None:
                infos = _collect_infos_with_rect(auto, sroot)
                out.extend(infos)
                log(f"[收集] 渲染子窗口(0x{sub & 0xFFFFFFFFFFFFFFFF:X}) 控件 {len(infos)} 个")
    return out


def _find_render_sub_hwnd(hwnd, max_depth=12):
    user32 = ctypes.windll.user32
    result = [None]
    try:
        EnumChildProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        found = [False]

        def _walk(h, depth):
            if depth > max_depth or found[0]:
                return
            cls = ctypes.create_unicode_buffer(256)
            try:
                user32.GetClassNameW(h, cls, 256)
            except Exception:
                cls.value = ""
            if "Chrome_RenderWidgetHostHWND" in cls.value:
                result[0] = h
                found[0] = True
                return
            children = []
            def _cb(ch, _):
                children.append(ch)
                return True
            user32.EnumChildWindows(h, EnumChildProc(_cb), 0)
            for ch in children:
                _walk(ch, depth + 1)

        _walk(hwnd, 0)
    except Exception as e:
        log(f"[收集] 找渲染子窗口异常: {e}")
    return result[0]


# ============ 表格行扫描 ============
def extract_plan_name(raw):
    if not raw:
        return ""
    out = raw
    for w in tuple(ROW_ACTION_WORDS) + ("优化",):
        out = out.replace(w, " ")
    out = " ".join(out.split())
    return out.strip(" |⋯·-")


def _collect_row_text(infos, rc):
    parts = []
    for i in infos:
        if i is rc or i["rect"] is None:
            continue
        if not _inside(i["rect"], rc["rect"], pad=10):
            continue
        if not i["name"]:
            continue
        if not _ct_in(i["ct"], ROW_TEXT_TYPES):
            continue
        parts.append(i)
    parts.sort(key=lambda c: (c["rect"][0], c["rect"][1], c["rect"][2]))
    text_parts, seen = [], set()
    for c in parts:
        nm = c["name"]
        if nm not in seen:
            seen.add(nm)
            text_parts.append(nm)
    plan = ""
    for c in parts:
        if not _ct_in(c["ct"], ("DataItem", "Text")):
            continue
        cand = extract_plan_name(c["name"])
        if cand:
            plan = cand
            break
    return " | ".join(text_parts), plan


def scan_rows(infos):
    """返回 (rows, header_row)。rows=[{'no','text','plan','rect','ctrl'}]，数据行去表头。"""
    row_ctrls = [i for i in infos if TABLE_ROW_CLASS in i["cls"] and i["rect"]]
    if not row_ctrls:
        return [], None
    rows_raw = []
    for rc in row_ctrls:
        text, plan = _collect_row_text(infos, rc)
        rows_raw.append({"ctrl": rc, "rect": rc["rect"], "text": text, "plan": plan})
    rows_raw.sort(key=lambda r: _rect_key(r["rect"]))
    header_row = None
    for r in rows_raw:
        if _is_header_row(r, HEADER_WORDS):
            header_row = r
            break
    data_rows = [r for r in rows_raw if r is not header_row]
    for i, r in enumerate(data_rows, 1):
        r["no"] = i
    return data_rows, header_row


def find_row_switch(infos, row_rect):
    """行左带内定位启停开关本体；返回面积最小的 Button/ToggleButton(cls 含 circle) 或 None。"""
    if not row_rect:
        return None
    x1, y1, _x2, y2 = row_rect
    zone = (x1, y1, x1 + SWITCH_ZONE_PX, y2)
    best = None
    for i in infos:
        if i["rect"] is None or not _inside(i["rect"], zone, pad=8):
            continue
        if not _ct_in(i["ct"], ("Button", "ToggleButton")):
            continue
        if SWITCH_CLS_MARK in i["cls"]:
            if best is None or _area(i["rect"]) < _area(best["rect"]):
                best = i
    return best


def switch_is_checked(sw):
    if not sw:
        return False
    return SWITCH_CHECKED_MARK in sw["cls"]


def switch_desc(sw):
    if sw is None:
        return "未找到"
    return "白钮(checked=运行中)" if switch_is_checked(sw) else "灰钮(off=已暂停)"


# ============ 点击链路 ============
def do_invoke(ctrl_info):
    try:
        def _do():
            ip = ctrl_info["ctrl"].GetInvokePattern()
            if ip is None:
                raise RuntimeError("无 InvokePattern")
            ip.Invoke()
        return _ui_call_safe(_do, timeout=8.0, desc="Invoke")
    except Exception as e:
        return False, f"异常: {e}"


def click_by_coord(ctrl_info, hwnd=None):
    """坐标点击：先激活目标窗口，再鼠标左键点控件中心。"""
    rect = ctrl_info["rect"]
    if not rect:
        return False, "无 rect，无法坐标点击"
    x = (rect[0] + rect[2]) // 2
    y = (rect[1] + rect[3]) // 2
    try:
        if hwnd:
            ctypes.windll.user32.SetForegroundWindow(hwnd)
            time.sleep(0.4)
            ctypes.windll.user32.BringWindowToTop(hwnd)
            time.sleep(0.2)
        ctypes.windll.user32.SetCursorPos(x, y)
        time.sleep(0.15)
        ctypes.windll.user32.mouse_event(0x0002, 0, 0, 0, 0)  # LEFTDOWN
        time.sleep(0.06)
        ctypes.windll.user32.mouse_event(0x0004, 0, 0, 0, 0)  # LEFTUP
        return True, f"坐标点击 ({x},{y})" + ("（已置前窗口）" if hwnd else "（未置前窗口）")
    except Exception as e:
        return False, f"坐标点击异常: {e}"


def scroll_into_view(ctrl_info):
    ctrl = ctrl_info.get("ctrl")
    if ctrl is None:
        return False
    try:
        def _do():
            pat = ctrl.GetScrollItemPattern()
            if pat is None:
                raise RuntimeError("无 ScrollItemPattern")
            pat.ScrollIntoView()
        ok, msg = _ui_call_safe(_do, timeout=8.0, desc="ScrollIntoView")
        time.sleep(0.8)
        return ok
    except Exception:
        return False


# ============ v0.14 视口外自动滚动（2026-09-06 移植自 test_auto_close v0.13 + 修复） ============
# 背景：v0.13 工作机实测视口外滚轮无效（目标行 y 恒 2081 不动），根因：
#   1) 悬停点 x 取行 rect 中心可能飞出窗口客户区（虚拟布局坐标超屏）；
#   2) 整表无可见行时 y 锚取客户区垂直中心，可能悬停到非滚动区；
#   3) ScrollItemPattern 对 Chromium 渲染行无效。
# v0.14 修复：悬停点取 目标行rect∩客户区 交集中心并 clamp 进客户区内安全带；
#   无可见行时方向用客户区比对、悬停 y 取客户区下半区偏表格区；
#   滚轮前用 WindowFromPoint 校验鼠标确已落在目标窗口内（命中校验）。
SCROLL_WHEEL_DELTA_UNIT = 120       # 单格滚轮 delta（正=向上/负=向下）
SCROLL_MAX_WHEEL_ATTEMPTS = 12      # 单目标滚轮尝试上限
SCROLL_REFRESH_SEC = 0.8            # 滚轮后静置（等页面滚动/重排稳定）
SCROLL_CLAMP_PAD = 40               # 悬停点距客户区边缘最小内缩


def _client_rect_screen(hwnd):
    """窗口客户区在屏幕坐标下的 (left, top, right, bottom)；失败返回 None。"""
    try:
        class RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                        ("right", ctypes.c_long), ("bottom", ctypes.c_long)]
        rc = RECT()
        if not ctypes.windll.user32.GetClientRect(hwnd, ctypes.byref(rc)):
            return None
        pt = (ctypes.c_long * 2)(0, 0)
        if not ctypes.windll.user32.ClientToScreen(hwnd, pt):
            return None
        return (pt[0], pt[1], pt[0] + rc.right, pt[1] + rc.bottom)
    except Exception:
        return None


def _visible_table_viewport(infos):
    """由可见数据行推断表格可视区 y 范围 (top, bottom)；无可见行返回 None。"""
    tops, bottoms = [], []
    for i in infos:
        if TABLE_ROW_CLASS not in i["cls"] or not i["rect"]:
            continue
        if i.get("offscreen"):
            continue
        tops.append(i["rect"][1])
        bottoms.append(i["rect"][3])
    if not tops:
        return None
    return (min(tops), max(bottoms))


def _clamp_inside(x, y, cr, pad=SCROLL_CLAMP_PAD):
    """把点 (x, y) clamp 进客户区 (left, top, right, bottom) 内缩 pad 的安全带。"""
    if cr is None:
        return int(x), int(y)
    l, t, r, b = cr
    cx = max(l + pad, min(int(x), r - pad))
    cy = max(t + pad, min(int(y), b - pad))
    return cx, cy


def _top_window_at(x, y):
    """返回屏幕点 (x, y) 处顶层窗口 hwnd（WindowFromPoint 再取根）；失败返回 0。"""
    try:
        h = ctypes.windll.user32.WindowFromPoint(ctypes.wintypes.POINT(int(x), int(y)))
        if not h:
            return 0
        return ctypes.windll.user32.GetAncestor(h, 2)  # GA_ROOT
    except Exception:
        return 0


def _send_wheel(hwnd, delta, x, y):
    """鼠标移至 (x, y) 并发送滚轮事件（正=向上滚，负=向下滚）。
    v0.14：发滚轮前 WindowFromPoint 校验悬停点已落在目标窗口内；
    若落点被其它窗口遮挡（置顶/弹层），返回 ("wrong_window", ...) 交上层修正。
    返回 (ok, tag)。"""
    import ctypes
    try:
        if not ctypes.windll.user32.SetCursorPos(int(x), int(y)):
            return False, "setpos_fail"
        time.sleep(0.25)
        top = _top_window_at(x, y)
        if top != hwnd:
            return False, "wrong_window"
        ctypes.windll.user32.mouse_event(0x0800, 0, 0, int(delta), 0)
        time.sleep(SCROLL_REFRESH_SEC)
        return True, "ok"
    except Exception:
        return False, "wheel_exc"


def _find_row_by_plan(rows, row_no, plan_expected):
    """滚动后行重定位：优先按原行号；行号处计划名与预期不符/越界时，
    全表按计划名兜底（滚动不改行序但重排后行号可能漂移，防定位错行）。
    返回 (row, actual_no) 或 (None, None)。"""
    if 1 <= row_no <= len(rows):
        cand = rows[row_no - 1]
        if not plan_expected or (cand.get("plan") or "").strip() == plan_expected.strip():
            return cand, row_no
    if plan_expected:
        for i, rr in enumerate(rows, 1):
            if (rr.get("plan") or "").strip() == plan_expected.strip():
                return rr, i
    return None, None


def auto_scroll_to_visible(auto, hwnd, row_no, plan_expected, sw):
    """v0.14 视口外自动滚动主封装（engine 版）：目标开关 IsOffscreen=True 时把计划行滚进可视区。

    流程：先试 scroll_into_view（ScrollItemPattern 白嫖一次）；
    失败 → 模拟滚轮循环逼近：方向用可见行视口 y 带比对（无可见行退化为窗口客户区），
    悬停点 = 目标行中心 clamp 进客户区安全带；若行 rect 与客户区无交集（虚拟坐标整行
    在屏外），x 取客户区水平中心，y 按目标相对方向取客户区下半/上半偏表格区；
    每轮重收树 + 行号/计划名重定位后重判 offscreen，最多 SCROLL_MAX_WHEEL_ATTEMPTS 轮。
    返回 (ok, r, sw2, msg)：ok=True 时 r/sw2 为滚动后重定位的行与开关（含 r['no']）。
    """
    if not sw:
        return False, None, None, "开关为空，无法滚动"
    # 1) 前置白嫖 ScrollItemPattern（部分环境有效，成本低）
    if scroll_into_view(sw):
        log("[自动滚动] ScrollItemPattern 滚动成功，重收树校验目标行...")
    else:
        log("[自动滚动] ScrollItemPattern 不可用/失败，改用模拟滚轮滚动（v0.14 clamp+命中校验）...")
        cr = _client_rect_screen(hwnd)
        if cr is None:
            return False, None, None, "窗口客户区不可用，无法判滚动"
        last_moved_ts = None
        for attempt in range(1, SCROLL_MAX_WHEEL_ATTEMPTS + 1):
            infos_cur = collect_all(auto, hwnd, with_render=False)
            rows_cur, _h2 = scan_rows(infos_cur)
            r_cur, no_cur = _find_row_by_plan(rows_cur, row_no, plan_expected)
            if r_cur is None:
                return False, None, None, "滚动过程中目标计划丢失，中止防误滚"
            sw_cur = find_row_switch(infos_cur, r_cur["rect"])
            if sw_cur is None:
                return False, None, None, "滚动后目标开关丢失，中止防误滚"
            if not sw_cur["offscreen"]:
                r_cur["no"] = no_cur
                log(f"[自动滚动] 第{attempt}次滚动后目标进入可视区（行#{no_cur} rect={r_cur['rect']} 开关可见）。")
                return True, r_cur, sw_cur, "模拟滚轮滚动成功"
            # 目标仍不可见：算方向与滚量
            cy = (r_cur["rect"][1] + r_cur["rect"][3]) // 2
            vp = _visible_table_viewport(infos_cur)
            if vp:
                vp_top, vp_bottom = vp
                if cy < vp_top - 15:
                    delta_dir = SCROLL_WHEEL_DELTA_UNIT
                    dist = vp_top - cy
                elif cy > vp_bottom + 15:
                    delta_dir = -SCROLL_WHEEL_DELTA_UNIT
                    dist = cy - vp_bottom
                else:
                    delta_dir = -SCROLL_WHEEL_DELTA_UNIT
                    dist = 60
                y_hover = (vp_top + vp_bottom) // 2
            else:
                # 无可见行：用客户区比对方向；悬停 y 取表格偏向区（下半区）
                mid = (cr[1] + cr[3]) // 2
                delta_dir = SCROLL_WHEEL_DELTA_UNIT if cy < mid else -SCROLL_WHEEL_DELTA_UNIT
                dist = abs(cy - mid)
                y_hover = int(cr[1] + (cr[3] - cr[1]) * 0.62)  # 下半区，靠近表格滚动区
            steps = max(1, min(5, int(abs(dist) // 100) + 1))
            # v0.14 修复1：悬停 x 取 行rect∩客户区 交集中心；无交集退客户区水平中心
            x_hover = (r_cur["rect"][0] + r_cur["rect"][2]) // 2
            x_hover, y_hover = _clamp_inside(x_hover, y_hover, cr)
            # 若行整行在屏外导致 x 被钳到边界，退化为客户区水平中心更稳
            if x_hover <= cr[0] + SCROLL_CLAMP_PAD + 2 or x_hover >= cr[2] - SCROLL_CLAMP_PAD - 2:
                x_hover = (cr[0] + cr[2]) // 2
            log(f"[自动滚动] 第{attempt}次: 目标行中心 y={cy} 视口={vp or cr} → "
                f"滚 {'向上' if delta_dir > 0 else '向下'} {steps} 格 悬停=({x_hover},{y_hover})")
            ok_w, tag = _send_wheel(hwnd, delta_dir * steps, x_hover, y_hover)
            if not ok_w:
                if tag == "wrong_window":
                    log(f"[自动滚动] 第{attempt}次悬停点被其它窗口遮挡（WindowFromPoint 非目标窗口），"
                        f"改悬停客户区中心重试一次...")
                    ok_w2, tag2 = _send_wheel(hwnd, delta_dir * steps,
                                              (cr[0] + cr[2]) // 2, (cr[1] + cr[3]) // 2)
                    if not ok_w2:
                        return False, None, None, f"悬停点被遮挡且中心重试失败({tag2})，中止"
                else:
                    return False, None, None, f"滚轮事件发送失败({tag})，中止"
        return False, None, None, f"模拟滚轮 {SCROLL_MAX_WHEEL_ATTEMPTS} 次仍未进入可视区，中止"
    # 2) ScrollItemPattern 成功后的标准重定位（校验行号/计划名/开关可见）
    infos_f2 = collect_all(auto, hwnd, with_render=False)
    rows_f2, _h2 = scan_rows(infos_f2)
    r2, no2 = _find_row_by_plan(rows_f2, row_no, plan_expected)
    if r2 is None:
        return False, None, None, "滚动后计划名不一致，中止"
    sw2 = find_row_switch(infos_f2, r2["rect"])
    if sw2 is None or sw2["offscreen"]:
        return False, None, None, "滚动后开关校验失败，中止"
    r2["no"] = no2
    log(f"[自动滚动] 滚动后重定位 行#{no2} rect={r2['rect']} 开关 {switch_desc(sw2)}")
    return True, r2, sw2, "滚动成功"


_FLOAT_LARGE_CLS = "theme-arco-btn-size-large"      # v0.15: 浮层弹窗大按钮特征 cls
_FLOAT_EXACT_NAMES = ("确认", "取消")                 # v0.15: 浮层弹窗大按钮精确 name


def _is_float_large_button(c):
    """v0.15: 浮层大按钮例外判定——name 精确=确认/取消 且 cls 含 theme-arco-btn-size-large。
    真实行内迷你操作按钮（暂停/编辑/删除等）无此 cls，且 name 不在精确清单，不会被放行。"""
    nm = (c["name"] or "").strip()
    cls = c["cls"] or ""
    return nm in _FLOAT_EXACT_NAMES and _FLOAT_LARGE_CLS in cls


def find_confirm_dialog(infos, confirm_words, exclude_rects, diag=True):
    """弹窗确认候选 = 词命中 + rect 不在表格行内；弹窗有效 = 确认候选与取消按钮都存在。
    v0.15：浮层大按钮（几何落行 rect 属 core-table-tr 整行宽虚拟元素误包含）例外放行进
    dlg_like；普通候选为空但 dlg_like 同时含确认与取消时视作有效弹窗返回候选。"""
    def _hit(words):
        out = []
        dlg_like = []
        for c in infos:
            nm = c["name"] or ""
            if not nm or not c["rect"]:
                continue
            if not _ct_in(c["ct"], ("Button", "MenuItem", "ListItem")):
                continue
            if not any(w in nm for w in words):
                continue
            hit_idx = None
            hit_rect = None
            for idx, rr in enumerate(exclude_rects):
                if rr and _inside(c["rect"], rr, pad=2):
                    hit_idx = idx
                    hit_rect = rr
                    break
            if hit_idx is not None:
                if _is_float_large_button(c):
                    # v0.15: 浮层大按钮被整行宽虚拟行误包含，放行进 dlg_like
                    dlg_like.append(c)
                    if diag:
                        log(f"[弹窗诊断] 浮层大按钮例外放行（几何落行#{hit_idx + 1} "
                            f"rect={hit_rect}）: {_fmt_ctrl(c)}")
                    continue
                if diag:
                    log(f"[弹窗诊断] 词命中但被行 rect 排除（非弹窗按钮?）: {_fmt_ctrl(c)} "
                        f"命中行#{hit_idx + 1} rect={hit_rect}")
                continue
            out.append(c)
        return out, dlg_like

    dlg_buttons, dlg_like_confirm = _hit(confirm_words)
    cancel_btn, dlg_like_cancel = _hit(("取消",))
    # v0.15: 普通候选为空时用浮层大按钮集合兜底（须确认/取消同时在场才有效）
    if not dlg_buttons and dlg_like_confirm:
        dlg_buttons = dlg_like_confirm
    if not cancel_btn and dlg_like_cancel:
        cancel_btn = dlg_like_cancel
    valid = bool(dlg_buttons and cancel_btn)
    return dlg_buttons, cancel_btn, valid


# ============ 单行自动暂停（对 test_auto_close dumb_close_one 摘录适配） ============
def close_plan(auto, hwnd, plan_name, row_no, logger=None):
    """暂停单个候选行（完整安全链路）。返回 (ok: bool, msg: str)。

    plan_name/row_no 来自主程序规则判定时的真实定位参数；本函数点前重收树，
    行号+计划名双重校验，防页面切走/重排误点。
    """
    if logger is not None:
        set_logger(logger)
    log(f"[自动关闭] 处理候选 行#{row_no} [计划] {plan_name}")

    # 1. 点前重收树，行号+计划名双重定位
    infos_f = collect_all(auto, hwnd, with_render=False)
    rows_f, _h2 = scan_rows(infos_f)
    r = None
    if 1 <= row_no <= len(rows_f):
        cand = rows_f[row_no - 1]
        if plan_name and (cand.get("plan") or "").strip() != (plan_name or "").strip():
            log(f"[自动关闭] 重收树后行#{row_no} 计划名不一致（目标'{plan_name}' != "
                f"现值'{cand.get('plan') or '?'}'），按计划名全表重定位...")
            cand = None
            for _i, rr in enumerate(rows_f, 1):
                if (rr.get("plan") or "").strip() == (plan_name or "").strip():
                    cand = rr
                    row_no = _i
                    log(f"[自动关闭] 按计划名重定位到行#{_i}")
                    break
            if cand is None:
                log("[自动关闭] 全表未找到同名计划，页面可能已变化，跳过该条防误点。")
                return False, "计划名不一致，跳过"
        r = cand
    if r is None:
        log(f"[自动关闭] 重收树后行#{row_no} 不存在，跳过。")
        return False, "行不存在，跳过"
    r["no"] = row_no

    # 2. 定位启停开关（无开关不回退文字按钮）
    sw = find_row_switch(infos_f, r["rect"])
    if sw is None:
        log("[自动关闭] 未定位到启停开关，跳过（不回退文字按钮防误点）。")
        return False, "未定位开关，跳过"
    if not switch_is_checked(sw):
        log("[自动关闭] 开关当前灰钮(off=已暂停/非运行中)，无需再关，跳过。")
        return False, "已非运行中，跳过"

    # 3. 视口外先滚动进可视区并重新校验（v0.14：ScrollItemPattern 白嫖 → 滚轮逼近兜底）
    if sw["offscreen"]:
        log("[自动关闭] 开关在视口外，尝试滚动进可视区（v0.14 auto_scroll_to_visible）...")
        ok_s, r_s, sw_s, msg_s = auto_scroll_to_visible(auto, hwnd, row_no, plan_name, sw)
        if not ok_s:
            log(f"[自动关闭] 无法滚动进可视区，跳过（{msg_s}）。")
            return False, f"视口外无法滚动，跳过（{msg_s}）"
        # 滚动成功后：用滚动后重定位的行/开关覆盖，继续后续点击链路
        r = r_s
        sw = sw_s
        r["no"] = row_no
        if not switch_is_checked(sw):
            return False, "滚动后开关非运行中(灰钮)，跳过"
        log(f"[自动关闭] 滚动成功，滚动后重定位 行#{row_no} rect={r['rect']} 开关 {switch_desc(sw)}")

    # 4. 点击开关（Invoke 优先于坐标，Chromium 无 Invoke 再坐标）
    log(f"[自动关闭] 即将点击开关暂停该计划: {_fmt_ctrl(sw)}")
    was_checked = switch_is_checked(sw)
    ok, msg = do_invoke(sw)
    if not ok:
        ok, msg = click_by_coord(sw, hwnd)
    log(f"[自动关闭] 开关点击: {'成功' if ok else '失败'} {msg}")

    # 5. 轮询：开关翻转 或 弹窗出现（最长 10 轮约 20s+收树耗时）
    flipped = False
    dlg_buttons, cancel_btn, dlg_valid = [], [], False
    sw2_desc = None
    row_rect = r["rect"]
    for poll_i in range(1, 11):
        time.sleep(2.0)
        infos2 = collect_all(auto, hwnd, with_render=False)
        if not infos2:
            log(f"[自动关闭] 第{poll_i}轮 控件树为空，继续观察...")
            continue
        rows2, _ = scan_rows(infos2)
        for rr in rows2:
            if rr["rect"] == row_rect:
                sw2 = find_row_switch(infos2, rr["rect"])
                if sw2 is not None:
                    sw2_desc = switch_desc(sw2)
                    flipped = (switch_is_checked(sw2) != was_checked)
                    if flipped:
                        log(f"[自动关闭] 第{poll_i}轮 开关翻转成功: {switch_desc(sw2)}")
                break
        if flipped:
            break
        dlg_buttons, cancel_btn, dlg_valid = find_confirm_dialog(
            infos2, CONFIRM_CLOSE, [rr["rect"] for rr in rows2], diag=True)
        if dlg_buttons or cancel_btn:
            log(f"[自动关闭] 第{poll_i}轮 疑似弹窗（表格外确认候选 {len(dlg_buttons)} 个，"
                f"取消按钮 {'有' if cancel_btn else '无'}，双按钮校验 {'通过' if dlg_valid else '未通过'}）")
            break

    # 6. 弹窗处理：双按钮校验通过才自动点确认
    if not flipped and (dlg_buttons or cancel_btn):
        if not dlg_valid:
            log("[自动关闭] 弹窗未通过双按钮校验（疑为网络报错/其它浮层），不自动点，请人工看截图。")
            return False, "弹窗校验未通过，未点确认"
        hit = None
        for c in dlg_buttons:
            if "确认" in (c["name"] or ""):
                hit = c
                break
        if hit is None and dlg_buttons:
            hit = dlg_buttons[0]
        if hit is None:
            return False, "弹窗有效但未找到确认按钮，未点"
        log(f"[自动关闭] 弹窗确认按钮: {_fmt_ctrl(hit)} —— 即将点击（计划真实暂停！）")
        ok2, msg2 = do_invoke(hit)
        if not ok2:
            ok2, msg2 = click_by_coord(hit, hwnd)
        log(f"[自动关闭] 确认按钮: {'成功' if ok2 else '失败'} {msg2}")
        time.sleep(2.0)
        return True, "已在确认弹窗点确认"
    if flipped:
        return True, "开关翻转，已暂停"
    log("[自动关闭] 10 轮内未观察到开关翻转也未出现弹窗，请人工看截图确认。")
    return False, "未观察到翻转/弹窗，请人工确认"


def run_auto_close(cfg, plan_name="", row_no=0, reason="", logger=None):
    """主程序入口：按 cfg 窗口关键词定位 → 收树 → 单行自动暂停。

    返回人类可读结果串（供主程序 log 展示）。cfg 需含 window_keyword；
    plan_name/row_no 为规则判定时的真实定位参数（至少给 plan_name 或 row_no 其一）。
    """
    if logger is not None:
        set_logger(logger)
    kw = str((cfg or {}).get("window_keyword") or "商户中心").strip()
    log(f"[自动关闭] 引擎启动：关键词={kw} 计划={plan_name!r} 行={row_no} 原因={reason or ''}")
    hwnd, title = find_window_by_keyword(kw)
    if not hwnd:
        log(f"[自动关闭] 未找到标题含 '{kw}' 的可见窗口，跳过本次。")
        return f"未找到窗口[{kw}]，未执行"
    log(f"[自动关闭] 目标窗口 hwnd=0x{hwnd & 0xFFFFFFFFFFFFFFFF:X} 标题={title}")
    try:
        import uiautomation as auto
    except Exception as e:
        log(f"[自动关闭] uiautomation 导入失败: {e}")
        return f"uiautomation 导入失败: {e}"
    ok, msg = close_plan(auto, hwnd, plan_name, row_no, logger=logger)
    if ok:
        return f"已暂停计划[{plan_name}]：{msg}"
    return f"暂停失败 计划[{plan_name}]（行#{row_no}）：{msg}"


if __name__ == "__main__":
    # 命令行独立验证：python auto_close_engine.py "计划名" 行号 [窗口关键词]
    kw = sys.argv[3] if len(sys.argv) > 3 else "商户中心"
    plan = sys.argv[1] if len(sys.argv) > 1 else ""
    row = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    print(run_auto_close({"window_keyword": kw}, plan_name=plan, row_no=row))
