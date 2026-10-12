# -*- coding: utf-8 -*-
"""idle_gate.py —— 统一「空闲判断机」（tkm v10.26 增量；独立模块，非 mods 插件）

单一职责：把原先散落在主程序 tiktok_monitor_v10.pyw 里的
  ① 键鼠空闲读取（GetLastInputInfo / GetTickCount）
  ② 无人值守闸门（总开关 + 空闲阈值）
  ③ 轮询「间隔到点」刷新门控（有人不刷 + 空闲后补刷）
收敛到本模块一处实现，主程序与其它模块只按下方契约调用，不再各自实现。

对外接口（唯一契约）
--------------------
1) get_idle_sec() -> float
       系统键鼠空闲秒数。读取失败返回 0.0（视为「有人值守」，最保守；
       与主程序旧 _system_idle_seconds() 语义完全一致）。

2) idle_threshold(cfg) -> float
       无人值守空闲阈值（秒）：取 cfg["auto_ops_unattended_idle_sec"]，默认 300，
       下限 10 秒（与旧 _check_auto_hide_and_scan 的 10 秒下限、面板 Spinbox 下限一致）。

3) is_unattended(cfg, force=False, require_master=True) -> (bool, reason)
       无人值守闸门。
         · force=True          → 直接通过（手动强制路径：热键刷新/立即识别/立即检测）
         · require_master=True → 先看总开关 cfg["auto_ops_enabled"]（默认口径，与主程序
                                 旧 is_unattended(cfg) 行为等价，含 "总开关关闭" 原因文本）
         · require_master=False→ 跳过总开关，只看空闲是否达标（刷新门控 / 无人自启 使用）
       返回 (是否通过, 原因文本)；原因文本沿用旧口径，供日志与 "总开关关闭" 判定消费。

4) RefreshGate —— 轮询间隔「刷新门控」状态机
       gate.request(cfg, force=False, first_round=False) -> (allow, why, pending)
            间隔到点请求刷新：man<->有人值守则不刷新并挂起「待刷」；空闲达标则放行。
       gate.poll_compensation(cfg) -> (need_compensate, why)
            挂起期间轮询：空闲达标（无人）即返回 True，由调用方立即补偿执行一次刷新。
       gate.clear_pending(reason="") -> (cleared, why)
            解除挂起（手动路径直通、或用户手动刷新后不再需要补刷）。
       门控总闸：cfg["refresh_idle_gate_enabled"]（默认 True）；置 False 则整体直通。

依赖：仅标准库（ctypes / datetime）。与同目录兄弟模块（history_store / win32_util /
auto_close_engine）一样由主程序 import 引入；打包（PyInstaller）时随主程序一同收集。
"""

import ctypes
from datetime import datetime

__all__ = [
    "DEFAULT_IDLE_SEC",
    "MIN_IDLE_SEC",
    "get_idle_sec",
    "idle_threshold",
    "is_unattended",
    "RefreshGate",
]

# 空闲阈值缺省/下限（秒）——与面板 Spinbox(from_=10) 及旧逻辑保持一致
DEFAULT_IDLE_SEC = 300.0
MIN_IDLE_SEC = 10.0

# 刷新门控总闸配置键（默认 True=门控生效；False=不做「有人不刷」限制，全部直通）
GATE_ENABLED_KEY = "refresh_idle_gate_enabled"


# ---------------------------------------------------------------- 空闲读取
class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


_LII_SIZE = ctypes.sizeof(_LASTINPUTINFO)


def get_idle_sec():
    """系统键鼠空闲秒数（GetLastInputInfo）。失败返回 0.0（视为有人值守，最保守）。

    与主程序旧 _system_idle_seconds() 逐行等价：结构体只建一次（原实现每次调用重建，
    本模块提到模块级复用，行为不变）；GetTickCount 32 位回绕导致的负值同样按 0 处理。
    """
    try:
        lii = _LASTINPUTINFO()
        lii.cbSize = _LII_SIZE
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
            return 0.0
        millis = ctypes.windll.kernel32.GetTickCount() - lii.dwTime
        return max(0.0, millis / 1000.0)
    except Exception:
        return 0.0


# ---------------------------------------------------------------- 无人值守闸门
def idle_threshold(cfg):
    """无人值守空闲阈值（秒）：cfg["auto_ops_unattended_idle_sec"]，默认 300，下限 10。"""
    cfg = cfg or {}
    try:
        need = float(cfg.get("auto_ops_unattended_idle_sec", DEFAULT_IDLE_SEC) or DEFAULT_IDLE_SEC)
    except (TypeError, ValueError):
        need = DEFAULT_IDLE_SEC
    return max(MIN_IDLE_SEC, need)


def is_unattended(cfg, force=False, require_master=True):
    """无人值守闸门，返回 (ok, why)。

    原主程序 is_unattended(cfg) 的等价实现（含原因文本口径），并新增：
      · force=True         手动强制路径直通（热键 / 立即识别 / 立即检测）；
      · require_master=False 只判空闲、忽略 auto_ops_enabled 总开关（刷新门控 / 无人自启用）。
    """
    cfg = cfg or {}
    if force:
        return True, "手动强制直通（跳过无人值守闸门）"
    if require_master and not cfg.get("auto_ops_enabled", False):
        return False, "自动操作总开关关闭"
    idle = get_idle_sec()
    need = idle_threshold(cfg)
    if idle >= need:
        return True, f"无人值守（空闲{idle:.0f}s≥{need:.0f}s）"
    return False, f"有人值守/空闲不足（{idle:.0f}s<{need:.0f}s）"


def _now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------- 刷新门控
class RefreshGate(object):
    """轮询间隔「刷新门控」：有人值守不立即刷新（挂起待刷），空闲达标后补刷。

    只在「间隔到点」这条自动路径上生效；热键刷新与面板「立即识别/立即检测」是手动
    强制路径，不经此处（调用方额外调 clear_pending 解除挂起，语义=手动已刷，无需补刷）。
    """

    def __init__(self):
        self._pending = False       # 是否有挂起的「待刷」
        self._pending_at = ""       # 挂起时间（用于日志追溯）
        self._pending_why = ""      # 挂起原因（空闲不足的具体数值）

    # ---- 只读观测 ----
    def is_pending(self):
        return bool(self._pending)

    def pending_info(self):
        """返回 (是否挂起, 原因, 挂起时间)，供面板/日志展示。"""
        return self._pending, self._pending_why, self._pending_at

    @staticmethod
    def _enabled(cfg):
        return bool((cfg or {}).get(GATE_ENABLED_KEY, True))

    # ---- 间隔到点：请求刷新 ----
    def request(self, cfg, force=False, first_round=False):
        """间隔到点的刷新请求，返回 (allow, why, pending)。

        force=True       → 手动强制直通（并顺手解除挂起）；
        first_round=True → 启动后首轮直通（用户主动触发，不属「间隔到点」）；
        门控关闭         → 直通；
        无人达标         → 放行；
        有人值守         → (False, 原因, True)：不刷新 + 挂起「待刷」。
        """
        cfg = cfg or {}
        if force:
            self.clear_pending("手动刷新直通")
            return True, "手动强制直通（热键/面板按钮不受门控）", False
        if first_round:
            return True, "首轮直通（启动后立即执行，不适用『间隔到点』门控）", bool(self._pending)
        if not self._enabled(cfg):
            return True, f"刷新门控已关闭（{GATE_ENABLED_KEY}=False）", bool(self._pending)
        ok, why = is_unattended(cfg, force=False, require_master=False)
        if ok:
            self._pending = False
            self._pending_why = ""
            self._pending_at = ""
            return True, f"空闲门控通过：{why}", False
        self._pending = True
        self._pending_at = _now_str()
        self._pending_why = why
        return (False,
                f"有人值守，本次不刷新（{why}）；已挂起「待刷」，空闲达标后自动补刷"
                f"（挂起时间={self._pending_at}）",
                True)

    # ---- 挂起期间：补偿检测 ----
    def poll_compensation(self, cfg):
        """挂起期间轮询，返回 (need_compensate, why)。

        空闲达标（无人，或门控已被关闭）→ 清挂起并返回 True，调用方应立即补偿刷一次。
        """
        if not self._pending:
            return False, ""
        cfg = cfg or {}
        if not self._enabled(cfg):
            self._pending = False
            return True, f"刷新门控已关闭（{GATE_ENABLED_KEY}=False），立即补刷挂起请求"
        ok, why = is_unattended(cfg, force=False, require_master=False)
        if ok:
            self._pending = False
            return True, f"空闲达标，补刷挂起请求（{why}；原挂起时间={self._pending_at}）"
        return False, ""

    # ---- 解除挂起 ----
    def clear_pending(self, reason=""):
        """解除挂起「待刷」，返回 (cleared, why)。未挂起时返回 (False, "无挂起待刷")。"""
        if not self._pending:
            return False, "无挂起待刷"
        self._pending = False
        return (True,
                f"解除挂起「待刷」（{reason}；原挂起时间={self._pending_at}，"
                f"原挂起原因={self._pending_why}）")
