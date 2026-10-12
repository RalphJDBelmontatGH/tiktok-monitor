# -*- coding: utf-8 -*-
"""Win32 窗口枚举 / 标题读取 / 区域截图基础胶水

B 组拆分（2026-09-07）：从 tiktok_monitor_v10.pyw 抽取，行为语义不变。
对外 API：clean_title/find_window/_setup_user32_types/_get_title_safe/
find_window_by_hwnd/list_windows/_find_hwnd_by_keyword/capture_region。
"""
import ctypes
import ctypes.wintypes
import unicodedata
from PIL import ImageGrab

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


def find_window(keyword):
    """按标题关键词找窗口，返回 (left, top, width, height, title) 或 None"""
    user32 = ctypes.windll.user32
    _setup_user32_types(user32)
    kw_clean = clean_title(keyword).lower()
    results = []

    def cb(hwnd, _):
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            title = _get_title_safe(hwnd)
            if not title:
                return True
            if kw_clean and kw_clean in clean_title(title).lower():
                rect = ctypes.wintypes.RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(rect))
                results.append({
                    "left": rect.left,
                    "top": rect.top,
                    "width": rect.right - rect.left,
                    "height": rect.bottom - rect.top,
                    "title": title,
                })
        except Exception:
            pass
        return True

    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return results[0] if results else None


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
    导致枚举窗口时整个界面卡死（按钮点了没反应）。改用 SendMessageTimeout 最多等 200ms。"""
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
    """枚举所有可见窗口，返回 [(title, hwnd)]，标题已清理隐形字符，用于调试/选择窗口标题关键词"""
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


def _find_hwnd_by_keyword(keyword):
    """按标题关键词枚举窗口返回第一个匹配 hwnd。

    find_window/find_window_by_hwnd 返回的 dict 不带 hwnd 字段，
    模式二（控件树读取）需要 hwnd 供 uiautomation.ControlFromHandle；
    无匹配返回 None。"""
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


# ==================== 截图 ====================

def capture_region(left, top, width, height):
    """截取屏幕指定区域，返回 PIL Image"""
    if width <= 0 or height <= 0:
        raise ValueError("截图区域宽高必须大于 0")
    img = ImageGrab.grab(bbox=(left, top, left + width, top + height))
    return img
