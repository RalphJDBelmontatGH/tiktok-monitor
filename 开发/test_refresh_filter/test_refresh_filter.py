# -*- coding: utf-8 -*-
"""
test_refresh_filter.py —— TK监控器「刷新按当天+已生效」独立测试程序（v0.10.7 测试版）

背景：紫鸟浏览器刷新 TikTok 广告后台后，页面保留上次手动设置的日期与广告计划筛选。
主程序每次刷新会读到旧日期数据，导致录错。本程序独立验证修复方案：
  刷新前先通过地址栏导航到「当天 + 已生效(delivery_ok)」的 URL，再让页面加载。

流程（默认全跑）：
  0. 页面检测：读取当前 URL，确认是否已是目标页面（ads-creation/dashboard）
     —— 非目标页面（如在支付/上传页）自动跳过步骤A，避免一上来无脑刷新
  A. 现象复现：点击『重新加载』按钮刷新 → 读 URL，确认刷新后筛选参数被保留（旧行为）
  B. 核心验证：构造「当天+已生效」URL → 地址栏写入+回车导航 → 读 URL 验证参数生效
  C. 截图留存：导航后窗口截图，人工确认页面显示当天已生效数据

抗干扰（v0.10.6）：步骤A 刷新前、步骤B 导航后自动最小化遮挡目标窗口的顶层窗口
异页等待（v0.10.7）：--wait 默认 10→15s，异页导航（如商品管理→广告计划）页面整页重建需更长等待窗口
  （豆包弹窗/Marvis悬浮窗等，重叠≥15%才处理；店铺窗口 Axxx、日志终端不动），
  避免弹窗遮挡导致 UIA 收集不到表格行而误报『表格未就绪』。

多店铺（A419/A244/...）：双击即用自动扫描可见窗口标题中形如 Axxx 的店铺关键词；
  也可 --keyword 显式指定（如 --keyword A244），命令行参数优先。

用法：
  python test_refresh_filter.py                      # 全流程（自动识别店铺窗口）
  python test_refresh_filter.py --dry-run            # 只构造/打印目标 URL，不碰窗口
  python test_refresh_filter.py --no-reload          # 跳过现象复现，只做导航验证
  python test_refresh_filter.py --ts-mode now        # 时间戳用当前时刻（默认）
  python test_refresh_filter.py --ts-mode today_start  # 时间戳用当天0点UTC
  python test_refresh_filter.py --keyword A244       # 显式指定店铺窗口关键词
  python test_refresh_filter.py --keyword 商户中心   # 窗口匹配关键词

并入主程序时：将 build_target_url / _nav_today_active 与 goto_url 链路合并，
在 _uia_reload 刷新前先检测当前页面是否目标页 + 导航到目标 URL（或刷新后检测 URL 不符再导航）；
抗干扰清场逻辑（_minimize_blocking_windows）同步合入刷新前。
"""
import argparse
import ctypes
import ctypes.wintypes
import os
import re
import sys
import time
import unicodedata
from datetime import datetime, timezone

# ---------------- 常量（与 mods/uia_recog/mod.py 保持一致） ----------------
RELOAD_AID = "view_1003"
RELOAD_NAME = "重新加载"
OMNIBOX_AID = "view_1012"
OMNIBOX_NAME = "地址和搜索栏"
TABLE_CONTAINER_CLASS = "core-table-container"
TABLE_ROW_CLASS = "core-table-tr"
RENDER_WIDGET_HINT = "Chrome_RenderWidgetHostHWND"

# 就绪表头关键词（与 mod.py _READY_HEADER_STRONG/_WEAK 一致）
READY_HEADER_STRONG = ("广告计划名称", "计划名称", "广告计划", "总收入", "总成本", "sku 订单数", "状态")
READY_HEADER_WEAK = ("ROI", "成本", "预算", "目标", "收入")

# 目标 URL 模板：用户确认的「当天+已生效」状态（时间戳占位 {ms}）
TARGET_URL_TEMPLATE = (
    "https://seller.tiktokshopglobalselling.com/ads-creation/dashboard"
    "?mpa=1&list_overview_metric=3&list_overview_metric=0"
    "&campaign_overview_metric=3&campaign_overview_metric=0"
    "&list_order_field=cost&list_order_type=descend&activated_tab_id=0"
    "&type=product&list_start_date={ms}&list_end_date={ms}"
    "&product_campaign_page=1"
    "&sp_campaign_list_pgm=%5B%28%27field%21%27campaign_status%27%7Efilter_type%210"
    "%7Ein_field_values%21%5B%27delivery_ok%27%5D%29%5D_"
)

# 目标页面检测（v0.10.5）：命中任一标记即视为「广告计划管理页」，用于防止在非目标页无脑刷新
TARGET_PAGE_MARKERS = ("ads-creation/dashboard",)

LOG_PATH = "test_refresh_filter.log"


def log(msg):
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ---------------- 窗口定位（原样复用主程序 tiktok_monitor_v10.pyw 已验证逻辑） ----------------
# 与主程序保持一致：纯标题关键词匹配 + 零宽字符清理 + 防挂起读标题 + 句柄有效性校验。
# 不用 class 过滤——紫鸟指纹浏览器顶层窗口类名虽为 Chrome_WidgetWin_1 但无保证，
# 主程序能准确定位靠的就是这套纯标题匹配，测试程序直接复用，避免两套逻辑分叉。

def clean_title(text):
    """去掉标题里的零宽字符/控制字符/首尾空格，防止指纹浏览器藏隐形字符导致匹配失败"""
    if not text:
        return ""
    out = []
    for ch in text:
        cat = unicodedata.category(ch)
        # C* 为控制/格式/代理等不可见字符，仅保留空格类
        if cat.startswith("C") and ch not in (" ", "\t"):
            continue
        out.append(ch)
    return "".join(out).strip()


def _setup_user32_types(user32):
    """显式声明 user32 API 的 64 位句柄类型，避免 hwnd 被按 32 位截断导致误判失效"""
    user32.IsWindow.argtypes = [ctypes.wintypes.HWND]
    user32.IsWindow.restype = ctypes.wintypes.BOOL
    user32.IsWindowVisible.argtypes = [ctypes.wintypes.HWND]
    user32.IsWindowVisible.restype = ctypes.wintypes.BOOL
    user32.GetWindowTextLengthW.argtypes = [ctypes.wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [ctypes.wintypes.HWND, ctypes.c_wchar_p, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetWindowRect.argtypes = [ctypes.wintypes.HWND, ctypes.POINTER(ctypes.wintypes.RECT)]
    user32.GetWindowRect.restype = ctypes.wintypes.BOOL
    user32.SendMessageTimeoutW.argtypes = [ctypes.wintypes.HWND, ctypes.wintypes.UINT, ctypes.wintypes.WPARAM, ctypes.c_void_p, ctypes.wintypes.UINT, ctypes.wintypes.UINT, ctypes.POINTER(ctypes.wintypes.LPARAM)]
    user32.SendMessageTimeoutW.restype = ctypes.wintypes.LPARAM


_WM_GETTEXT = 0x000D
_SMTO_ABORTIFHUNG = 0x0002
_TITLE_TIMEOUT_MS = 200


def _get_title_safe(hwnd):
    """带超时读取窗口标题：GetWindowText 对挂起/不响应消息的窗口会无限阻塞，
    导致枚举窗口时整个界面卡死。改用 SendMessageTimeout 最多等 200ms。"""
    try:
        user32 = ctypes.windll.user32
        _setup_user32_types(user32)
        buff = ctypes.create_unicode_buffer(1024)
        result = ctypes.wintypes.LPARAM()
        ret = user32.SendMessageTimeoutW(hwnd, _WM_GETTEXT, 1024, buff, _SMTO_ABORTIFHUNG, _TITLE_TIMEOUT_MS, ctypes.byref(result))
        if not ret:
            return ""
        return buff.value
    except Exception:
        return ""


def find_window_by_hwnd(hwnd):
    """按窗口句柄定位窗口，窗口仍有效且可见时返回 dict，否则 None"""
    if not hwnd:
        return None
    user32 = ctypes.windll.user32
    try:
        _setup_user32_types(user32)
        if not user32.IsWindow(hwnd) or not user32.IsWindowVisible(hwnd):
            return None
        length = user32.GetWindowTextLengthW(hwnd)
        title = ""
        if length > 0:
            buff = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buff, length + 1)
            title = buff.value
        rect = ctypes.wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        return {
            "left": rect.left,
            "top": rect.top,
            "width": rect.right - rect.left,
            "height": rect.bottom - rect.top,
            "title": title,
        }
    except Exception:
        return None


def list_windows():
    """枚举所有可见窗口，返回 [(title, hwnd)]，标题已清理隐形字符，用于调试/核对关键词"""
    user32 = ctypes.windll.user32
    _setup_user32_types(user32)
    windows = []

    def cb(hwnd, _):
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            title = _get_title_safe(hwnd)
            if not title:
                return True
            windows.append((clean_title(title), hwnd))
        except Exception:
            pass
        return True

    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return windows


def auto_detect_shop_keywords(max_n=3):
    """v0.10.5：扫描可见窗口标题，自动识别形如 Axxx 的店铺关键词（A419/A244/...）。
    双击即用无需参数；找不到时返回空列表，由调用方回退默认。纯 win32，不依赖 uiautomation。"""
    found = []
    try:
        for wt, _h in list_windows():
            m = re.search(r"\bA\d{3}\b", wt)
            if m and m.group(0) not in found:
                found.append(m.group(0))
            if len(found) >= max_n:
                break
    except Exception:
        pass
    return found


def is_target_page(url):
    """v0.10.5：判断当前 URL 是否目标页面（广告计划管理页 ads-creation/dashboard）。
    空 URL / 非目标页返回 False，防止在无关页面无脑刷新。"""
    return bool(url) and any(m in url for m in TARGET_PAGE_MARKERS)


def _window_rect(hwnd):
    """返回 (left, top, right, bottom)；失败返回 None。"""
    try:
        rect = ctypes.wintypes.RECT()
        if ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return (rect.left, rect.top, rect.right, rect.bottom)
    except Exception:
        pass
    return None


def _overlap_ratio(target_rect, other_rect):
    """两矩形交集面积占目标矩形面积比例。"""
    if not target_rect or not other_rect:
        return 0.0
    l = max(target_rect[0], other_rect[0])
    t = max(target_rect[1], other_rect[1])
    r = min(target_rect[2], other_rect[2])
    b = min(target_rect[3], other_rect[3])
    if r <= l or b <= t:
        return 0.0
    tw = target_rect[2] - target_rect[0]
    th = target_rect[3] - target_rect[1]
    if tw <= 0 or th <= 0:
        return 0.0
    return ((r - l) * (b - t)) / (tw * th)


def _blocking_windows(target_hwnd, min_overlap=0.15):
    """v0.10.6 抗干扰：枚举可见顶层窗口，找出覆盖在目标窗口上且重叠比例≥阈值的窗口。
    排除：目标自身、店铺窗口(Axxx)、控制台窗口（避免最小化日志终端）、标题为空。
    返回 [(hwnd, title, ratio)]。纯 win32，不依赖 uiautomation。"""
    target_rect = _window_rect(target_hwnd)
    if not target_rect:
        return []
    out = []
    try:
        user32 = ctypes.windll.user32
        for wt, wh in list_windows():
            if wh == target_hwnd:
                continue
            if re.search(r"\bA\d{3}\b", wt):
                continue  # 店铺窗口不动
            if _window_rect(wh) is None:
                continue
            try:
                buf = ctypes.create_unicode_buffer(256)
                user32.GetClassNameW(wh, buf, 256)
                if (buf.value or "") == "ConsoleWindowClass":
                    continue  # 控制台（日志终端）不动
            except Exception:
                pass
            ratio = _overlap_ratio(target_rect, _window_rect(wh))
            if ratio >= min_overlap:
                out.append((wh, wt, ratio))
    except Exception:
        pass
    out.sort(key=lambda x: -x[2])
    return out


def _minimize_blocking_windows(target_hwnd, min_overlap=0.15):
    """v0.10.6 抗干扰：把遮挡目标窗口的可见顶层窗口最小化（SW_MINIMIZE，可逆）。
    日志打印每个被处理的窗口；返回处理列表 [(title, ratio)]。"""
    handled = []
    user32 = ctypes.windll.user32
    for wh, wt, ratio in _blocking_windows(target_hwnd, min_overlap):
        try:
            if user32.IsIconic(wh):
                continue
            user32.ShowWindow(wh, 6)  # SW_MINIMIZE
            handled.append((wt, ratio))
            log(f"[抗干扰] 已最小化遮挡窗口: 『{wt}』（重叠 {ratio*100:.0f}%）")
        except Exception as e:
            log(f"[抗干扰] 最小化『{wt}』失败: {e}")
    return handled


def _find_hwnd_by_keyword(keyword):
    """按标题关键词枚举窗口返回第一个匹配 hwnd（主程序原版）。无匹配返回 None。"""
    if not keyword:
        return None
    user32 = ctypes.windll.user32
    _setup_user32_types(user32)
    kw_clean = clean_title(keyword).lower()
    result = [None]

    def cb(hwnd, _):
        if result[0] is not None:
            return False
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            title = _get_title_safe(hwnd)
            if title and kw_clean in clean_title(title).lower():
                result[0] = hwnd
                return False
        except Exception:
            pass
        return True

    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return result[0]


def find_window_by_keywords_multi(keywords):
    """对多个关键词依次调用 _find_hwnd_by_keyword（主程序逻辑），
    命中即返回 (hwnd, title, 命中关键词)；全部未命中返回 (None, "", "")。"""
    if isinstance(keywords, str):
        keywords = [keywords]
    for kw in (keywords or []):
        kw = kw.strip().lstrip("=")  # 容错：--keyword =A419 / =A419 写法
        hwnd = _find_hwnd_by_keyword(kw)
        if hwnd:
            info = find_window_by_hwnd(hwnd)
            return hwnd, (info or {}).get("title", ""), kw
    return None, "", ""


def _find_render_sub_hwnd(hwnd, max_depth=12):
    """递归 EnumChildWindows 定位渲染子窗口（同 mod.py）。"""
    user32 = ctypes.windll.user32
    best = [99, None]

    def _class_of(h):
        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(h, buf, 256)
        return buf.value or ""

    def _children(h):
        out = []

        def cb(hc, _):
            out.append(hc)
            return True

        user32.EnumChildWindows(h, ctypes.WINFUNCTYPE(
            ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)(cb), 0)
        return out

    def walk(cur, depth):
        if depth > max_depth or best[0] == 0:
            return
        cls = _class_of(cur)
        if "RenderWidgetHost" in cls:
            best[0], best[1] = 0, cur
            return
        if "Chrome_WidgetWin" in cls and best[0] > 1:
            best[0], best[1] = 1, cur
        elif "Render" in cls and best[0] > 2:
            best[0], best[1] = 2, cur
        for c in _children(cur):
            if best[0] == 0:
                break
            walk(c, depth + 1)

    for c in _children(hwnd):
        if best[0] == 0:
            break
        walk(c, 1)
    return best[1] if best[0] < 99 else None


def _find_render_sub_hwnd_safe(hwnd, timeout=8.0):
    """_find_render_sub_hwnd 带超时保护：递归 EnumChildWindows 在复杂窗口树
    可能极慢/卡死，放子线程执行，超时返回 None 不拖死主流程。"""
    import threading
    result = {"hwnd": None}

    def _run():
        try:
            result["hwnd"] = _find_render_sub_hwnd(hwnd)
        except Exception as e:
            log(f"[卡点] _find_render_sub_hwnd 异常: {e!r}")

    th = threading.Thread(target=_run, daemon=True)
    th.start()
    th.join(timeout)
    if th.is_alive():
        log(f"[卡点] _find_render_sub_hwnd 超时(>{timeout:.0f}s)，跳过渲染子树（不影响地址栏 URL 验证）")
        return None
    return result["hwnd"]


# ---------------- 控件树收集与查找 ----------------
def _control_from_handle_safe(auto, hwnd, timeout=10.0):
    """ControlFromHandle 带超时保护：uiautomation 对复杂 Chromium 窗口可能无限阻塞，
    放子线程执行，超时返回 None 不拖死主流程。返回 (ctrl, err_info)。
    子线程必须先用 UIAutomationInitializerInThread 初始化 COM（CoInitialize），
    否则报 OSError(22, '尚未调用 CoInitialize。')。"""
    import threading
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


def _collect_ctrl_items(root, max_nodes=4000, timeout_sec=15.0):
    items = []
    deadline = time.time() + timeout_sec
    try:
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
            items.append((ctrl, depth))
    except Exception:
        pass
    return items


def _collect_ctrl_items_safe(auto, root, max_nodes=4000, timeout_sec=15.0):
    """_collect_ctrl_items 带子线程超时保护：单次 GetChildren 在复杂 Chromium 树
    可能无限阻塞，deadline 循环救不了，放子线程执行，超时返回空列表不拖死主流程。
    子线程必须先用 UIAutomationInitializerInThread 初始化 COM（CoInitialize），
    否则报 OSError(22, '尚未调用 CoInitialize。')。"""
    import threading
    result = {"items": []}

    def _run():
        try:
            with auto.UIAutomationInitializerInThread():
                result["items"] = _collect_ctrl_items(root, max_nodes=max_nodes, timeout_sec=timeout_sec)
        except Exception:
            pass

    th = threading.Thread(target=_run, daemon=True)
    th.start()
    th.join(timeout_sec + 1.0)
    if th.is_alive():
        return []
    return result["items"]


def _collect_ctrl_infos_safe(auto, root, max_nodes=4000, timeout_sec=15.0):
    """子线程内完成控件树遍历+属性提取，返回 info 列表（含 ctrl/ct/name/aid/cls）。

    主线程不直接访问任何 UIA 属性——Name/ControlTypeName/AutomationId/ClassName
    同样是跨进程 COM 调用，在复杂 Chromium 窗口树可能无限阻塞（v0.7 实测卡在
    『开始收集...』后无输出即此原因：收集本身有超时，但遍历属性在主线程裸奔）。
    全部放子线程内完成，join 超时返回空列表不拖死主流程。
    """
    import threading
    result = {"infos": []}

    def _safe_str(fn):
        try:
            v = fn()
            return str(v or "")
        except Exception:
            return ""

    def _run():
        try:
            with auto.UIAutomationInitializerInThread():
                items = _collect_ctrl_items(root, max_nodes=max_nodes, timeout_sec=timeout_sec)
                infos = []
                for c, _d in items:
                    infos.append({
                        "ctrl": c,
                        "ct": _safe_str(lambda: c.ControlTypeName),
                        "name": _ctrl_name(c),
                        "aid": _safe_str(lambda: c.AutomationId),
                        "cls": _safe_str(lambda: c.ClassName),
                    })
                result["infos"] = infos
        except Exception:
            pass

    th = threading.Thread(target=_run, daemon=True)
    th.start()
    th.join(timeout_sec + 5.0)
    if th.is_alive():
        return []
    return result["infos"]


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


def _ctrl_type(ctrl):
    try:
        return str(ctrl.ControlTypeName or "")
    except Exception:
        return ""


def _collect_tree(hwnd, auto, with_render=True):
    """主窗口整树 + 渲染子窗口下钻，返回 info 列表（同 mod.py _collect_reload_tree）。"""
    infos = []

    def _walk(root_ctrl, tag):
        t0 = time.time()
        collected = _collect_ctrl_infos_safe(auto, root_ctrl, max_nodes=4000, timeout_sec=15.0)
        infos.extend(collected)
        log(f"[卡点] {tag} 控件树收集完成：{len(collected)} 个，耗时 {time.time() - t0:.1f}s")

    root, err = _control_from_handle_safe(auto, hwnd)
    if root is None:
        log(f"[卡点] ControlFromHandle(hwnd=0x{hwnd & 0xFFFFFFFFFFFFFFFF:X}) 失败/超时: {err or '返回 None'}")
    else:
        log(f"[卡点] ControlFromHandle 成功（主窗口），开始收集...")
        _walk(root, "主窗口")
    if with_render:
        sub = _find_render_sub_hwnd_safe(hwnd)
        if sub and sub != hwnd:
            # 渲染子窗口（页面 DOM 树）收集可能非常慢/单次 GetChildren 卡死，
            # 放子线程执行并超时放弃，不影响主流程（地址栏在主窗口树中已可读）。
            import threading
            done = {"ok": False}

            def _walk_sub():
                sub_root, sub_err = _control_from_handle_safe(auto, sub, timeout=8.0)
                if sub_root is None:
                    log(f"[卡点] ControlFromHandle(渲染子窗口 0x{sub & 0xFFFFFFFFFFFFFFFF:X}) 失败/超时: {sub_err or '返回 None'}")
                    return
                log(f"[卡点] ControlFromHandle 成功（渲染子窗口 0x{sub & 0xFFFFFFFFFFFFFFFF:X}），开始收集...")
                _walk(sub_root, "渲染子窗口")
                done["ok"] = True

            th = threading.Thread(target=_walk_sub, daemon=True)
            th.start()
            th.join(25.0)
            if not done["ok"] and th.is_alive():
                log("[卡点] 渲染子窗口收集超时(>25s)，跳过渲染子树继续（不影响地址栏 URL 验证）")
    return infos


def _find_reload_button(infos):
    """同 mod.py：aid+class 精确 → aid → Name 含『重新加载』。"""
    candidates = []
    for info in infos:
        if "Button" not in info["ct"]:
            continue
        if info["aid"] == RELOAD_AID and info["cls"] == "ReloadButton":
            return info, candidates
        candidates.append(info)
    for info in candidates:
        if info["aid"] == RELOAD_AID:
            return info, candidates
    for info in candidates:
        if RELOAD_NAME in info["name"]:
            return info, candidates
    return None, candidates


def _find_omnibox(infos):
    for info in infos:
        if info["aid"] == OMNIBOX_AID and "Edit" in info["ct"]:
            return info
    for info in infos:
        if info["name"] == OMNIBOX_NAME:
            return info
    return None


def _omnibox_url(info, timeout=5.0):
    """读取地址栏 URL（ValuePattern.Value），子线程带超时：UIA 跨进程调用可能阻塞，
    主线程裸调会拖死（就绪轮询每 2s 一次，必须兜底）。"""
    if not info:
        return ""
    import threading
    import uiautomation as auto
    result = {"url": ""}

    def _run():
        try:
            with auto.UIAutomationInitializerInThread():
                vp = info["ctrl"].GetValuePattern()
                if vp is not None:
                    result["url"] = (vp.Value or "").strip()
        except Exception:
            pass

    th = threading.Thread(target=_run, daemon=True)
    th.start()
    th.join(timeout)
    if th.is_alive():
        return ""
    return result["url"]


def _ui_call_safe(fn, timeout=8.0, desc=""):
    """在子线程执行 UIA 交互调用（Invoke/SetValue/SetFocus/SendKeys），带超时保护。

    UIA 跨进程调用（包括模式获取与执行）在复杂 Chromium 窗口树同样可能无限阻塞，
    主线程裸调会拖死整个流程。放子线程 + join 超时，返回 (ok, err_info)。"""
    import threading
    import uiautomation as auto
    result = {"ok": False, "err": ""}

    def _run():
        try:
            with auto.UIAutomationInitializerInThread():
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


def _get_vp_safe(ctrl, timeout=5.0):
    """子线程获取 ValuePattern，带超时保护（v0.10）。

    GetValuePattern 同为 UIA 跨进程模式调用，在复杂 Chromium 窗口树可能无限阻塞；
    步骤B 曾因主线程裸调卡死（日志断在控件树收集完成后）。超时返回 None。"""
    import threading
    import uiautomation as auto
    result = {"vp": None}

    def _run():
        try:
            with auto.UIAutomationInitializerInThread():
                result["vp"] = ctrl.GetValuePattern()
        except Exception:
            pass

    th = threading.Thread(target=_run, daemon=True)
    th.start()
    th.join(timeout)
    if th.is_alive():
        return None
    return result["vp"]


def _count_table_rows(infos):
    return sum(1 for i in infos if TABLE_ROW_CLASS in i["cls"])


def _ready_by_table(row_count, header_hits):
    if row_count <= 0 or not header_hits:
        return False
    strong = [h for h in header_hits if h in READY_HEADER_STRONG]
    if strong:
        return True
    return len(header_hits) >= 2


def _probe_table_ready(hwnd, auto, with_render=True):
    """轻量就绪探测（同 mod.py）：渲染子窗口统计表格行+表头，主窗口读 URL。"""
    url = ""
    row_count = 0
    header_hits = []
    try:
        root, _err = _control_from_handle_safe(auto, hwnd, timeout=8.0)
        if root is not None:
            for info in _collect_ctrl_infos_safe(auto, root, max_nodes=2000, timeout_sec=6.0):
                if not url and info["aid"] == OMNIBOX_AID and "Edit" in info["ct"]:
                    url = _omnibox_url(info)
    except Exception:
        pass
    if with_render:
        try:
            sub = _find_render_sub_hwnd_safe(hwnd)
            if sub and sub != hwnd:
                # 渲染子窗口收集可能卡在单次 GetChildren/属性访问，放子线程+超时，超时放弃不影响 URL 读取。
                import threading
                done = {"ok": False}

                def _probe_sub():
                    nonlocal row_count, header_hits
                    sub_root, _err2 = _control_from_handle_safe(auto, sub, timeout=6.0)
                    if sub_root is None:
                        return
                    for info in _collect_ctrl_infos_safe(auto, sub_root, max_nodes=3000, timeout_sec=6.0):
                        if TABLE_ROW_CLASS in info["cls"]:
                            row_count += 1
                        if len(header_hits) < 8:
                            nm = info["name"]
                            for h in READY_HEADER_STRONG + READY_HEADER_WEAK:
                                if h and h in nm and h not in header_hits:
                                    header_hits.append(h)
                    done["ok"] = True

                th = threading.Thread(target=_probe_sub, daemon=True)
                th.start()
                th.join(10.0)
                if not done["ok"] and th.is_alive():
                    pass  # 渲染子树探测超时，保留已收集的部分统计继续
        except Exception:
            pass
    return url, row_count, header_hits


# ---------------- URL 构造 ----------------
def utc_ms_now():
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def utc_ms_today_start():
    now = datetime.now(timezone.utc)
    return int(datetime(now.year, now.month, now.day, tzinfo=timezone.utc).timestamp() * 1000)


def build_target_url(ts_mode):
    """构造「当天+已生效」目标 URL：模板时间戳替换为当天。"""
    ms = utc_ms_now() if ts_mode == "now" else utc_ms_today_start()
    url = TARGET_URL_TEMPLATE.format(ms=ms)
    return url, ms


def extract_param(url, key):
    m = re.search(r"[?&]" + re.escape(key) + r"=([^&]+)", url or "")
    return m.group(1) if m else ""


# ---------------- 动作 ----------------
def do_reload_and_snapshot(hwnd, auto, wait_sec, with_render=True):
    """步骤A：点『重新加载』刷新，返回 (ok, steps)。v0.10.3 全程即时打印。"""
    steps = []
    _minimize_blocking_windows(hwnd)  # v0.10.6 抗干扰：先清场再刷新，避免弹窗遮挡表格
    infos = _collect_tree(hwnd, auto, with_render=with_render)
    _log_step(steps, f"控件树收集 {len(infos)} 个", "A")
    btn, candidates = _find_reload_button(infos)
    if btn is None:
        _log_step(steps, f"未找到『重新加载』按钮（Button 候选 {len(candidates)} 个）", "A")
        return False, steps
    _log_step(steps, f"找到重载按钮 aid={btn['aid']} cls={btn['cls']}", "A")
    om = _find_omnibox(infos)
    url_before = _omnibox_url(om)
    _log_step(steps, f"刷新前 URL={_url_short(url_before)}", "A")
    try:
        def _do_invoke():
            ip = btn["ctrl"].GetInvokePattern()
            if ip is None:
                raise RuntimeError("无 InvokePattern")
            ip.Invoke()
        ok, err = _ui_call_safe(_do_invoke, timeout=8.0, desc="Invoke")
        if ok:
            _log_step(steps, "Invoke 点击成功", "A")
        else:
            _log_step(steps, f"Invoke 失败: {err}", "A")
            return False, steps
    except Exception as e:
        _log_step(steps, f"Invoke 异常: {e}", "A")
        return False, steps
    # 就绪轮询（v0.10.3）：每轮前后都即时打印；_probe_table_ready 单轮最坏可达
    # 20-36s（主窗口+渲染子窗口收集），wait_sec 仅是 sleep 累加阈值，实际耗时更长。
    elapsed = 0.0
    ready = False
    poll_round = 0
    while elapsed < wait_sec:
        poll_round += 1
        _log_step(steps, f"[轮询#{poll_round}] 探测中（sleep累计{elapsed:.0f}s）…", "A")
        t_round = time.time()
        time.sleep(2.0)
        elapsed += 2.0
        url_now, rows_now, header_hits = _probe_table_ready(hwnd, auto)
        _log_step(steps, f"[轮询#{poll_round}] 完成（本轮{time.time()-t_round:.1f}s）行数={rows_now} 表头={header_hits}", "A")
        if _ready_by_table(rows_now, header_hits):
            ready = True
            _log_step(steps, f"表格就绪（行数={rows_now}，表头={header_hits}）sleep累计{elapsed:.0f}s", "A")
            break
    if not ready:
        _log_step(steps, f"等待{wait_sec:.0f}s 表格未就绪（sleep累计{elapsed:.0f}s）", "A")
    # 刷新后读 URL
    time.sleep(3.0)
    infos2 = _collect_tree(hwnd, auto)
    om2 = _find_omnibox(infos2)
    url_after = _omnibox_url(om2)
    _log_step(steps, f"刷新后 URL={_url_short(url_after)}", "A")
    if url_before and url_after and url_before == url_after:
        _log_step(steps, "结论：刷新后 URL 未变化 → 筛选被保留（复现旧行为）", "A")
    return ready, steps


def _log_step(steps, msg, prefix="B"):
    """进度即时打印（v0.10.3）：避免黑窗长时间静默被误判卡死。prefix=A 用于步骤A。"""
    steps.append(msg)
    log(f"  {prefix}| " + msg)


def nav_today_active(hwnd, auto, target_url, wait_sec, with_render=True):
    """步骤B：地址栏写入目标 URL + 回车导航，返回 (ok, steps, final_url)。v0.10.3 全程即时打印。"""
    steps = []
    target_ms = extract_param(target_url, "list_start_date")
    log("  B| 步骤B 开始：导航后持续打印轮询进度，请勿关闭窗口")
    infos = _collect_tree(hwnd, auto, with_render=with_render)
    om = _find_omnibox(infos)
    if om is None:
        _log_step(steps, f"未找到地址栏（aid={OMNIBOX_AID}/Name 含『{OMNIBOX_NAME}』）")
        return False, steps, ""
    _log_step(steps, f"找到地址栏 aid={om['aid']}")
    url_before = _omnibox_url(om)
    _log_step(steps, f"导航前 URL={_url_short(url_before)}")
    # 前台模式：恢复窗口并聚焦（同主程序 goto_url）
    user32 = ctypes.windll.user32
    try:
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
    except Exception:
        pass
    time.sleep(0.5)
    try:
        vp = _get_vp_safe(om["ctrl"])
        if vp is None:
            _log_step(steps, "地址栏无 ValuePattern")
            return False, steps, ""
        ok, err = _ui_call_safe(lambda: vp.SetValue(target_url), timeout=8.0, desc="SetValue")
        if not ok:
            _log_step(steps, f"SetValue 失败/超时: {err}")
            return False, steps, ""
        _log_step(steps, "ValuePattern.SetValue 写入成功")
        # 写后读回验证（v0.6）：SetValue 偶发失败或被浏览器自动补全下拉干扰，
        # 读回不一致则重写一次，避免在错误内容上回车
        time.sleep(0.3)
        readback = _omnibox_url(om)
        if readback is None or readback.strip() != target_url.strip():
            _log_step(steps, f"写后读回不一致（读回: {readback[:60]!r}），重写一次")
            try:
                ok2, err2 = _ui_call_safe(lambda: vp.SetValue(target_url), timeout=8.0, desc="SetValue重写")
                if not ok2:
                    _log_step(steps, f"重写失败/超时: {err2}")
                time.sleep(0.3)
                readback = _omnibox_url(om)
            except Exception as e2:
                _log_step(steps, f"重写异常: {e2}")
        if readback and readback.strip() == target_url.strip():
            _log_step(steps, "写后读回一致")
        else:
            _log_step(steps, f"写后读回仍不一致（读回: {readback[:60]!r}），继续尝试导航")
        # 焦点加固（v0.6）：keybd_event 是全局按键，发给当前焦点窗口；用户做
        # 其他操作时焦点可能被抢，回车会打偏。优先控件级 SendKeys（内部
        # SetFocus + SendInput），失败再兜底 keybd_event。
        try:
            ok, err = _ui_call_safe(lambda: om["ctrl"].SetFocus(), timeout=5.0, desc="SetFocus")
            if ok:
                _log_step(steps, "SetFocus 地址栏成功")
            else:
                _log_step(steps, f"SetFocus 地址栏失败（继续尝试回车）: {err}")
        except Exception as e:
            _log_step(steps, f"SetFocus 地址栏失败（继续尝试回车）: {e}")
        time.sleep(0.15)
        sent = False
        try:
            ok2, err2 = _ui_call_safe(lambda: om["ctrl"].SendKeys("{ENTER}"), timeout=8.0, desc="SendKeys")
            if ok2:
                _log_step(steps, "控件级 SendKeys(Enter) 已发送")
                sent = True
            else:
                _log_step(steps, f"SendKeys 失败: {err2}")
        except Exception as e:
            _log_step(steps, f"SendKeys 异常: {e}")
        if not sent:
            user32.keybd_event(0x0D, 0, 0, 0)
            user32.keybd_event(0x0D, 0, 2, 0)
            _log_step(steps, "兜底 keybd_event 回车已发送")
    except Exception as e:
        _log_step(steps, f"写入/导航异常: {e}")
        return False, steps, ""
    _minimize_blocking_windows(hwnd)  # v0.10.6 抗干扰：导航后清场，避免弹窗/悬浮窗遮挡表格就绪探测
    # 就绪轮询（v0.10.3）：每轮前后都即时打印；_probe_table_ready 单轮最坏可达
    # 20-36s（主窗口+渲染子窗口收集），wait_sec 仅是 sleep 累加阈值，实际耗时更长。
    elapsed = 0.0
    ready = False
    url_changed = False
    poll_round = 0
    while elapsed < wait_sec:
        poll_round += 1
        _log_step(steps, f"[轮询#{poll_round}] 探测中（sleep累计{elapsed:.0f}s）…")
        t_round = time.time()
        time.sleep(2.0)
        elapsed += 2.0
        url_now, rows_now, header_hits = _probe_table_ready(hwnd, auto)
        _log_step(steps, f"[轮询#{poll_round}] 完成（本轮{time.time()-t_round:.1f}s）URL已变={url_changed} 行数={rows_now} 表头={header_hits}")
        if not url_changed and url_now and "list_start_date=" in url_now \
                and extract_param(url_now, "list_start_date") == target_ms:
            url_changed = True
            _log_step(steps, f"地址栏已确认目标 URL（sleep累计{elapsed:.0f}s）")
        if _ready_by_table(rows_now, header_hits):
            ready = True
            _log_step(steps, f"表格就绪（行数={rows_now}，表头={header_hits}）sleep累计{elapsed:.0f}s")
            break
    if not ready:
        _log_step(steps, f"等待{wait_sec:.0f}s 未完全就绪（URL已变={url_changed}，sleep累计{elapsed:.0f}s）")
    time.sleep(3.0)
    infos2 = _collect_tree(hwnd, auto)
    om2 = _find_omnibox(infos2)
    final_url = _omnibox_url(om2)
    _log_step(steps, f"导航后 URL={_url_short(final_url)}")
    return ready, steps, final_url


def nav_today_new_tab(hwnd, auto, target_url, wait_sec, with_render=True):
    """步骤B2：Ctrl+T 新增标签页 → 新标签页地址栏写入目标 URL + 回车导航。

    条件分流需求：非当天页面不在旧标签覆盖地址，而是新增标签页再导航（原页面保留，
    过程尽量快）。返回 (ok, steps, final_url)。
    """
    steps = []
    target_ms = extract_param(target_url, "list_start_date")
    log("  B| 步骤B2 开始：导航后持续打印轮询进度，请勿关闭窗口")
    user32 = ctypes.windll.user32
    # 前台模式：恢复窗口并聚焦（keybd_event 是全局按键，发给当前焦点窗口）
    try:
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
    except Exception:
        pass
    prev_fg = user32.GetForegroundWindow()
    time.sleep(0.3)
    # 新增标签页（v0.6）：优先控件级 Invoke『新建标签页』按钮（不依赖键盘焦点，
    # 用户做其他操作也不受影响），找不到再退化为 Ctrl+T 全局按键
    ctrl_t_ok = False
    infos_nt = _collect_tree(hwnd, auto, with_render=with_render)
    nt_btn = _find_new_tab_button(infos_nt)
    if nt_btn is not None:
        try:
            def _do_invoke_nt():
                ip = nt_btn["ctrl"].GetInvokePattern()
                if ip is None:
                    raise RuntimeError("无 InvokePattern")
                ip.Invoke()
            ok, err = _ui_call_safe(_do_invoke_nt, timeout=8.0, desc="新标签页Invoke")
            if ok:
                _log_step(steps, "新建标签页按钮 Invoke 成功")
                ctrl_t_ok = True
            else:
                _log_step(steps, f"新建标签页按钮 Invoke 失败: {err}，改用 Ctrl+T")
        except Exception as e:
            _log_step(steps, f"新建标签页按钮 Invoke 异常: {e}，改用 Ctrl+T")
    if not ctrl_t_ok:
        try:
            user32.keybd_event(0x11, 0, 0, 0)  # VK_CONTROL down
            user32.keybd_event(0x54, 0, 0, 0)  # 'T' down
            user32.keybd_event(0x54, 0, 2, 0)  # 'T' up
            user32.keybd_event(0x11, 0, 2, 0)  # VK_CONTROL up
            _log_step(steps, "Ctrl+T 已发送（兜底）")
        except Exception as e:
            _log_step(steps, f"Ctrl+T 发送失败: {e}")
            return False, steps, ""
    time.sleep(0.5)  # 等新标签页打开，地址栏可用
    infos = _collect_tree(hwnd, auto, with_render=with_render)
    om = _find_omnibox(infos)
    if om is None:
        _log_step(steps, f"未找到地址栏（aid={OMNIBOX_AID}/Name 含『{OMNIBOX_NAME}』）")
        return False, steps, ""
    _log_step(steps, f"找到地址栏 aid={om['aid']}")
    url_before = _omnibox_url(om)
    _log_step(steps, f"新标签页导航前 URL={_url_short(url_before)}")
    try:
        vp = _get_vp_safe(om["ctrl"])
        if vp is None:
            _log_step(steps, "地址栏无 ValuePattern")
            return False, steps, ""
        ok, err = _ui_call_safe(lambda: vp.SetValue(target_url), timeout=8.0, desc="SetValue")
        if not ok:
            _log_step(steps, f"SetValue 失败/超时: {err}")
            return False, steps, ""
        _log_step(steps, "ValuePattern.SetValue 写入成功")
        # 写后读回验证（v0.6）
        time.sleep(0.3)
        readback = _omnibox_url(om)
        if readback is None or readback.strip() != target_url.strip():
            _log_step(steps, f"写后读回不一致（读回: {readback[:60]!r}），重写一次")
            try:
                ok2, err2 = _ui_call_safe(lambda: vp.SetValue(target_url), timeout=8.0, desc="SetValue重写")
                if not ok2:
                    _log_step(steps, f"重写失败/超时: {err2}")
                time.sleep(0.3)
                readback = _omnibox_url(om)
            except Exception as e2:
                _log_step(steps, f"重写异常: {e2}")
        if readback and readback.strip() == target_url.strip():
            _log_step(steps, "写后读回一致")
        else:
            _log_step(steps, f"写后读回仍不一致（读回: {readback[:60]!r}），继续尝试导航")
        # 焦点加固（v0.6）：控件级 SendKeys 优先，keybd_event 兜底
        try:
            ok, err = _ui_call_safe(lambda: om["ctrl"].SetFocus(), timeout=5.0, desc="SetFocus")
            if ok:
                _log_step(steps, "SetFocus 地址栏成功")
            else:
                _log_step(steps, f"SetFocus 地址栏失败（继续尝试回车）: {err}")
        except Exception as e:
            _log_step(steps, f"SetFocus 地址栏失败（继续尝试回车）: {e}")
        time.sleep(0.15)
        sent = False
        try:
            ok2, err2 = _ui_call_safe(lambda: om["ctrl"].SendKeys("{ENTER}"), timeout=8.0, desc="SendKeys")
            if ok2:
                _log_step(steps, "控件级 SendKeys(Enter) 已发送")
                sent = True
            else:
                _log_step(steps, f"SendKeys 失败: {err2}")
        except Exception as e:
            _log_step(steps, f"SendKeys 异常: {e}")
        if not sent:
            user32.keybd_event(0x0D, 0, 0, 0)
            user32.keybd_event(0x0D, 0, 2, 0)
            _log_step(steps, "兜底 keybd_event 回车已发送")
    except Exception as e:
        _log_step(steps, f"写入/导航异常: {e}")
        return False, steps, ""
    # 还原前台焦点（尽力而为）
    try:
        if prev_fg:
            user32.SetForegroundWindow(prev_fg)
    except Exception:
        pass
    _minimize_blocking_windows(hwnd)  # v0.10.6 抗干扰：导航后清场，避免弹窗/悬浮窗遮挡表格就绪探测
    # 就绪轮询（v0.10.3）：每轮前后都即时打印；_probe_table_ready 单轮最坏可达
    # 20-36s（主窗口+渲染子窗口收集），wait_sec 仅是 sleep 累加阈值，实际耗时更长。
    elapsed = 0.0
    ready = False
    url_changed = False
    poll_round = 0
    while elapsed < wait_sec:
        poll_round += 1
        _log_step(steps, f"[轮询#{poll_round}] 探测中（sleep累计{elapsed:.0f}s）…")
        t_round = time.time()
        time.sleep(2.0)
        elapsed += 2.0
        url_now, rows_now, header_hits = _probe_table_ready(hwnd, auto)
        _log_step(steps, f"[轮询#{poll_round}] 完成（本轮{time.time()-t_round:.1f}s）URL已变={url_changed} 行数={rows_now} 表头={header_hits}")
        if not url_changed and url_now and "list_start_date=" in url_now \
                and extract_param(url_now, "list_start_date") == target_ms:
            url_changed = True
            _log_step(steps, f"地址栏已确认目标 URL（sleep累计{elapsed:.0f}s）")
        if _ready_by_table(rows_now, header_hits):
            ready = True
            _log_step(steps, f"表格就绪（行数={rows_now}，表头={header_hits}）sleep累计{elapsed:.0f}s")
            break
    if not ready:
        _log_step(steps, f"等待{wait_sec:.0f}s 未完全就绪（URL已变={url_changed}，sleep累计{elapsed:.0f}s）")
    time.sleep(3.0)
    infos2 = _collect_tree(hwnd, auto)
    om2 = _find_omnibox(infos2)
    final_url = _omnibox_url(om2)
    _log_step(steps, f"导航后 URL={_url_short(final_url)}")
    return ready, steps, final_url


def _url_short(url, n=140):
    return (url[:n] + "...") if url and len(url) > n else (url or "(空)")


def save_screenshot(hwnd, path):
    try:
        from PIL import ImageGrab
        user32 = ctypes.windll.user32
        rect = ctypes.wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        w, h = rect.right - rect.left, rect.bottom - rect.top
        if w > 0 and h > 0:
            img = ImageGrab.grab(bbox=(rect.left, rect.top, rect.right, rect.bottom))
            img.save(path)
            return True
    except Exception as e:
        log(f"截图失败: {e}")
    return False


# ---------------- 主流程 ----------------
def main():
    ap = argparse.ArgumentParser(description="TK监控器 刷新按当天+已生效 测试程序")
    ap.add_argument("--keyword", action="append", default=None,
                    help="窗口匹配关键词（可多次传入，如 --keyword A419 --keyword 商户中心）")
    ap.add_argument("--ts-mode", choices=["now", "today_start"], default="now",
                    help="时间戳模式：now=当前时刻UTC（默认，与TikTok生成一致）/ today_start=当天0点UTC")
    ap.add_argument("--no-reload", action="store_true", help="跳过现象复现（步骤A）")
    ap.add_argument("--new-tab", action="store_true",
                    help="步骤B改用『新增标签页再导航』（Ctrl+T，条件分流方案；默认当前标签页覆盖地址）")
    ap.add_argument("--dry-run", action="store_true", help="只构造/打印目标 URL，不碰窗口")
    ap.add_argument("--wait", type=int, default=15, help="就绪等待秒数（v0.10.7 默认 15s，异页导航需更长窗口）")
    ap.add_argument("--render", action="store_true",
                    help="开启渲染子窗口（页面DOM树）定位与收集（默认关闭，双击即用）")
    ap.add_argument("--no-pause", action="store_true", help="结束后不暂停等待回车（默认暂停，便于看输出）")
    args = ap.parse_args()
    with_render = args.render

    # 关键词解析（v0.10.5）：命令行 --keyword > 自动扫描 Axxx 店铺窗口 > 默认"A419"（双击即用，免参数）
    keywords = list(args.keyword or [])
    if not keywords:
        auto_kws = auto_detect_shop_keywords()
        if auto_kws:
            keywords = auto_kws
            log(f"自动检测到店铺窗口关键词: {keywords}")
        else:
            keywords = ["A419"]

    log("=" * 60)
    log(f"TK监控器 刷新按当天+已生效 测试 v0.10.7  ts_mode={args.ts_mode} wait={args.wait}s  new_tab={args.new_tab}")

    # 1. 构造目标 URL
    target_url, ms = build_target_url(args.ts_mode)
    log(f"目标时间戳 ms={ms}")
    log(f"目标 URL（前200）: {target_url[:200]}")
    log(f"目标 URL 参数校验: list_start_date={extract_param(target_url, 'list_start_date')} "
        f"list_end_date={extract_param(target_url, 'list_end_date')} "
        f"delivery_ok={'delivery_ok' in target_url}")

    if args.dry_run:
        log("dry-run：仅打印，未操作窗口。")
        return 0

    # 2. 定位窗口（关键词列表，复用主程序 _find_hwnd_by_keyword 纯标题匹配）
    hwnd, title, hit_kw = find_window_by_keywords_multi(keywords)
    if not hwnd:
        log(f"未找到标题含 {keywords} 的可见窗口，请确认紫鸟已打开 TK 商户中心页面")
        log("--- 当前可见窗口标题（前25个，用于核对关键词） ---")
        for i, (wt, _h) in enumerate(list_windows()[:25], 1):
            log(f"  {i:2d}. {wt[:80]}")
        log("--- 结束 ---")
        return 2
    log(f"目标窗口 hwnd=0x{hwnd & 0xFFFFFFFFFFFFFFFF:X} 标题={title} 命中关键词={hit_kw}")
    log("[卡点] 开始 import uiautomation ...")
    t0 = time.time()
    try:
        import uiautomation as auto
        log(f"[卡点] uiautomation import 成功，耗时 {time.time() - t0:.2f}s")
    except Exception as e:
        log(f"uiautomation 导入失败: {e}")
        return 2

    # 3. 快照：当前 URL（读取当前筛选状态）
    infos0 = _collect_tree(hwnd, auto, with_render=with_render)
    om0 = _find_omnibox(infos0)
    url0 = _omnibox_url(om0)
    log(f"当前 URL={_url_short(url0, 200)}")
    log(f"当前筛选: start={extract_param(url0, 'list_start_date')} "
        f"end={extract_param(url0, 'list_end_date')} "
        f"delivery_ok={'delivery_ok' in (url0 or '')}")

    # 3.5 目标页面检测（v0.10.5）：防止在非广告计划管理页（支付/上传/首页等）一上来就无脑刷新
    on_target = is_target_page(url0)
    if on_target:
        log(f"当前页面: 目标页面（命中 {TARGET_PAGE_MARKERS}）✓")
    else:
        log(f"当前页面: 非目标页面（未命中 {TARGET_PAGE_MARKERS}，当前可能停在支付/上传/其他页）—— 跳过刷新复现，直接导航验证")

    results = {}

    # 4. 步骤A：现象复现（刷新后筛选保留）——仅当前已是目标页面时执行，避免无脑刷新
    if not args.no_reload and on_target:
        log("--- 步骤A：刷新（点『重新加载』）复现现象 ---")
        okA, stepsA = do_reload_and_snapshot(hwnd, auto, args.wait, with_render=with_render)
        results["A_reload"] = okA
        time.sleep(1.0)
    elif not args.no_reload:
        log("--- 步骤A：跳过（当前非目标页面，刷新无意义；直接执行步骤B 导航到 当天+已生效）---")
        results["A_reload"] = "skipped"

    # 5. 步骤B：导航到 当天+已生效（默认当前标签页覆盖地址；--new-tab 走新增标签页方案）
    if args.new_tab:
        log("--- 步骤B：新增标签页(Ctrl+T)后地址栏导航到 当天+已生效 ---")
        okB, stepsB, final_url = nav_today_new_tab(hwnd, auto, target_url, args.wait, with_render=with_render)
    else:
        log("--- 步骤B：地址栏导航到 当天+已生效 ---")
        okB, stepsB, final_url = nav_today_active(hwnd, auto, target_url, args.wait, with_render=with_render)
    results["B_nav"] = okB

    # 6. 验证最终 URL 参数
    final_start = extract_param(final_url, "list_start_date")
    final_end = extract_param(final_url, "list_end_date")
    final_delivery = "delivery_ok" in (final_url or "")
    url_param_ok = (
        final_start == str(ms) and final_end == str(ms) and final_delivery
    )
    log(f"--- 验证 ---")
    log(f"list_start_date={final_start} (期望 {ms}) {'OK' if final_start == str(ms) else 'FAIL'}")
    log(f"list_end_date={final_end} (期望 {ms}) {'OK' if final_end == str(ms) else 'FAIL'}")
    log(f"sp_campaign_list_pgm 含 delivery_ok: {'OK' if final_delivery else 'FAIL'}")
    log(f"表格就绪(步骤B): {'OK' if okB else 'FAIL'}")
    log(f"URL 参数校验: {'PASS' if url_param_ok else 'FAIL'}")

    # 7. 截图
    shot = f"shot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
    if save_screenshot(hwnd, shot):
        log(f"截图已保存: {shot}（请人工确认页面显示当天已生效数据）")
    else:
        log("截图保存失败（跳过，不影响结论）")

    log(f"结论: {'全部通过，方案可行，可并入主程序' if (url_param_ok and okB) else '未完全通过，请查看上方日志/截图'}")
    return 0 if (url_param_ok and okB) else 1


if __name__ == "__main__":
    sys.exit(main())
