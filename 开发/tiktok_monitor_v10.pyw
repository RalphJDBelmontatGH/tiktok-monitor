# -*- coding: utf-8 -*-
"""
TikTok 广告监控工具 tkm_10.26
=================================================
tkm_10.23 锚定修复版（2026-09-07，基于 v10.22 守夜源码）：
- 商品名即锚（老板拍板 2026-09-07）：品档案键/品龄判定/统计全部改用产品锚
  parse_product_anchor；款号/变体/重建（黑金刚喷头F/G/A、rN、v_topN、尾部序号）
  不重新观察——修复“老品重建/款号计划被误判新品”导致 $5 无单漏自动关；
  判定链：排期证老优先，排期显新时锚档案纠偏（resolve_product_age 单一口径，
  操作层 ops_decision_close 与告警层 in_grace 共用）。
- 修“判定不关静默”：自动关判定为不关时每轮逐计划落 debug 原因日志
  [自动关·不关原因]，回扫日志可核对品龄归属与未关理由。
- 马来店品名对齐（2026-09-07 老板纠正）：款号段正则 [A-Z][A-Z0-9]*（禁含小写），
  避免 Kain/Kotak/Pasta 等英文单词品名被当款号整段误剔（详见方案文档十·补）。
- 版本字面量同步 tkm_10.23。

【v10.24 增量（2026-09-08 发布）：③.5「立即检测」按钮 + A/B 拆分】
- ③.5 自动操作面板新增「立即检测」强制入口：点击立即复用现有完整链路跑一轮
  （_monitor_round 读表格→识别→判定→应关即真关），不等下个识别轮、跳过
  is_unattended 无人值守闸门与 60s 冷却，工位有人也执行。
- 复用链：_monitor_round(push=False, force_close=True) → run_alerts(force_close=True)
  → ops_decision_close → _auto_close_store_pending → _auto_close_watchdog(force=True)
  → auto_close_ad。手动触发不推送/不 toast/不响（人在电脑前）。
- A 组拆分：文件头历史 docstring 外迁 docs/CHANGELOG.md；B 组拆分：低耦合功能域
  抽为 history_store / win32_util / notify_channels 三模块，主程序 from-import 复用。
- 版本字面量已统一 bump 至 tkm_10.24（2026-09-08 打包交付）。

【v10.25 增量（2026-09-08 规则定稿实施）：自动关品龄口径 + 新品保护】
- 锚提取补英文复制变体剥离（copy_A / copy / copy1 等）：计划「黑金刚喷头 B copy_A r1」
  归并「黑金刚喷头」锚，英文复制类变体不再新建独立锚。
- 新品学习窗口金额线 $20→$30：烧 <$30 一律不关（有单无单都护）；满 12h 或烧够 $30
  任一先到转 ROI 评估——无单 ROI=0 必关、有单 ROI≥0.6 留 / <0.6 关；出单不续期。
- 老品有单 ROI 阈值 0.61→0.60：全链统一 ROI≥0.6 留（告警提醒线 0.65 不变）。
- 版本字面量 bump 至 tkm_10.25；config grace_cost 20→30（告警层提示口径）。

【v10.26 增量（2026-09-08）：弹窗按钮几何误杀修复打包交付】
- 版本字面量 bump 至 tkm_10.26；基于 v10.25 + auto_close_engine v0.15（find_confirm_dialog
  浮层大按钮例外放行，修复确认/取消按钮被表格行 rect 几何误杀导致暂停失败）构建交付。


完整历史版本演进（v10.14.6 起）见 docs/CHANGELOG.md（2026-09-07 A 组拆分外迁）。
"""

import ctypes
import ctypes.wintypes
import collections
import copy          # v10.7.1：预设规则深拷贝统一使用模块级 copy（消除函数内局部 import）
import importlib
import importlib.util
import json
import os
import queue          # v10.8.1：热键/单实例信号经线程安全队列转交主线程执行
import re
import ast           # v10.2 任务1：自定义规则表达式 AST 白名单校验
import sqlite3          # SQLite 历史库（主存储）
import subprocess
import threading
import time
import traceback
import unicodedata
from datetime import datetime
from pathlib import Path
import winsound        # v10.9.1：告警轻滴声（低调提醒，替代显眼 toast 的静默窗口期）

import requests
# v10.16：微云日志自动上传模块（log 按天轮转后自动备份到微云，防删目录丢日志/供信息部拉取）
try:
    import weiyun_upload
except Exception:
    weiyun_upload = None
from PIL import Image, ImageGrab
import tkinter as tk
from tkinter import messagebox
from tkinter import ttk

# ---------- DPI 感知：避免高分屏下 Win32 窗口坐标与截图坐标错位 ----------
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

import sys
# 打包成 exe 后，配置/历史/日志存到 exe 所在目录（避免写进临时解压目录）
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).parent
else:
    BASE_DIR = Path(__file__).parent
CONFIG_FILE = BASE_DIR / "config.json"
# ---- B 组拆分（2026-09-07）：低耦合功能域外迁独立模块，行为语义不变 ----
from history_store import (DB_FILE, HISTORY_FILE, load_history_from_db,
                           add_record_to_db, load_history, save_history, add_record)
from win32_util import (clean_title, find_window, _setup_user32_types, _get_title_safe,
                        find_window_by_hwnd, list_windows, _find_hwnd_by_keyword,
                        capture_region)
from notify_channels import (notify_toast, push_wecom, push_dingtalk, _safe_format,
                             _metric_label, _op_label, _plan_label, TEMPLATE_VARS,
                             _render_template, push_pushplus, send_alerts)
# v10.12：日志按天轮转——monitor 主流程 log 与 ops 操作级 log 分开独立文件，
# 文件名带日期（monitor_YYYYMMDD.log / ops_YYYYMMDD.log），新的一天自动建新文件，老文件保留不覆盖。
_LOG_KIND_PREFIX = {"monitor": "monitor_", "ops": "ops_"}
_last_log_day = None   # v10.16：最近一次写日志的日期，用于跨天轮转检测（触发微云自动备份）


def _day_log_path(kind="monitor"):
    """v10.12：按天轮转日志路径（monitor_YYYYMMDD.log / ops_YYYYMMDD.log）。"""
    return BASE_DIR / f"{_LOG_KIND_PREFIX.get(kind, 'monitor_')}{datetime.now():%Y%m%d}.log"


def _append_day_log(kind, line):
    """v10.12：按天日志增量追加（失败静默，不阻塞主流程）。"""
    global _last_log_day
    try:
        with open(_day_log_path(kind), "a", encoding="utf-8") as f:
            f.write(line)
        # v10.16：跨天轮转时自动触发微云备份（昨日日志已完整，立即上传；首次启动不触发）
        today = datetime.now().strftime("%Y%m%d")
        if _last_log_day is not None and _last_log_day != today:
            _maybe_upload_logs_async()
        _last_log_day = today
    except Exception:
        pass


# ---------- v10.16：微云日志自动上传（log 按天轮转后自动备份，防删目录丢日志/供信息部拉取） ----------
_WEIYUN_STATE_FILE = BASE_DIR / "weiyun_upload_state.json"
_weiyun_state_lock = threading.Lock()


def _load_weiyun_upload_state():
    """读取已上传日志文件名集合（防重复上传）。"""
    try:
        if _WEIYUN_STATE_FILE.exists():
            with open(_WEIYUN_STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return set(data.get("uploaded", []))
    except Exception:
        pass
    return set()


def _save_weiyun_upload_state(uploaded):
    try:
        with _weiyun_state_lock:
            with open(_WEIYUN_STATE_FILE, "w", encoding="utf-8") as f:
                json.dump({"uploaded": sorted(uploaded)}, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _upload_old_logs_to_weiyun(cfg=None):
    """后台线程：把昨天及更早的日志上传到微云（按天轮转，一个 log 周期后自动备份）。

    触发时机：程序启动时检查一次；配置开关 weiyun_upload_enabled 关闭或未配 token 时直接跳过。
    """
    if weiyun_upload is None:
        return
    try:
        if cfg is None:
            cfg = load_config()
        if not cfg.get("weiyun_upload_enabled"):
            return
        token = (cfg.get("weiyun_token") or "").strip()
        pdir_key = (cfg.get("weiyun_pdir_key") or "").strip()
        if not token:
            return
        today = datetime.now().strftime("%Y%m%d")
        uploaded = _load_weiyun_upload_state()
        candidates = []
        for p in BASE_DIR.glob("*.log"):
            name = p.name
            m = re.match(r"^(monitor|ops)_(\d{8})\.log$", name)
            if not m:
                continue
            if m.group(2) >= today:   # 今天的日志还在写，不传
                continue
            if name in uploaded:
                continue
            candidates.append(p)
        for p in sorted(candidates, key=lambda x: x.name):
            r = weiyun_upload.upload_log_to_weiyun(str(p), token, pdir_key)
            if r.get("ok"):
                uploaded.add(p.name)
                _pre_log(f"[微云上传] 已上传 {p.name} → file_id={r.get('file_id')}")
            else:
                _pre_log(f"[微云上传] 上传失败 {p.name}: {r.get('error')}")
        _save_weiyun_upload_state(uploaded)
    except Exception as e:
        _pre_log(f"[微云上传] 异常（不影响主流程）: {e}")


def _maybe_upload_logs_async():
    """启动后台线程执行日志上传（不阻塞主流程）。"""
    try:
        t = threading.Thread(target=_upload_old_logs_to_weiyun, daemon=True)
        t.start()
    except Exception:
        pass

# ---------- 全局异常钩子：程序崩溃时把原因写入 error_log.txt（便于无控制台排障） ----------
def _global_excepthook(exc_type, exc_value, exc_tb):
    try:
        with open(BASE_DIR / "error_log.txt", "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] 程序异常:\n")
            traceback.print_exception(exc_type, exc_value, exc_tb, file=f)
            f.write("\n")
    except Exception:
        pass
    try:
        import tkinter.messagebox as mb
        mb.showerror("TikTok监控 v10 出错", f"程序发生错误，详情已写入 error_log.txt\n\n{exc_value}")
    except Exception:
        pass

import sys
sys.excepthook = _global_excepthook

import auto_close_engine as ace  # v10.21: 自动暂停真实点击引擎（test_auto_close v0.12.2 链路摘录）
# v10.26：统一「空闲判断机」模块——键鼠空闲读取 + 无人值守闸门 + 轮询刷新门控收敛于此
# （非 mods 插件，同目录兄弟模块，import 引入）。接口见 idle_gate.py 顶部契约。
import idle_gate

DEFAULT_CONFIG = {
    # v10.23 自动操作（自动关值守）：总开关与无人值守空闲阈值（默认关=宁关勿开；
    # UI ③.5 保存后落盘。键常驻 DEFAULT_CONFIG，避免运行 config 缺键时被隐式当作 False、
    # 且保存其它设置时被整段 dump 抹掉导致“设了开关却不生效”的隐式差异）
    "auto_ops_enabled": False,
    "auto_ops_unattended_idle_sec": 300,
    # v10.26 增量：无人达标「自动隐藏窗口 + 自动开始扫描」（防“打开程序忘记启动监控”）。
    # 独立开关，不受 auto_ops_enabled（自动关总开关）约束；同样常驻 DEFAULT_CONFIG。
    "auto_hide_scan_enabled": False,
    # v10.26 增量：轮询间隔「刷新门控」总闸（有人不刷+空闲补刷）的开关，默认 True=门控生效。
    # 置 False 时本门控整体直通（每轮照常刷新），便于需要「无论有人无人每轮都刷新」时一键关闭。
    "refresh_idle_gate_enabled": True,
    # 窗口与识别
    "window_keyword": "商户中心",
    "roi": None,                     # [left, top, width, height] 相对窗口坐标
    "interval_minutes": 60,          # 轮询间隔（分钟），下限 1
    # 告警阈值
    "roi_low": 0.6,                  # ROI 止损阈值
    # v10.21（2026-09-06 合主）：preset_roi_good/preset_cvr_low 告警规则已废除，但 roi_good/cvr_low
    # 键保留——作为用户自定义规则表达式变量与 mini 展示 [达标] 阈值，非告警线
    "roi_good": 1.0,                 # ROI 达标展示阈值（仅 mini/变量用，无告警消费）
    "cost_max": 300.0,               # 【v10.2 废弃】周期总成本告警已拔除（概览页累计数据不作告警源），键保留仅为旧配置兼容
    "cvr_low": 0.5,                  # CVR 低参考值（%）（仅变量用，预设规则已废除）
    "cost_x_aov": 2.0,               # 消耗超过客单价倍数
    "burn_redline": 5.0,             # 空烧红线（美元）
    "grace_cost": 20.0,              # 新计划宽松观察额度（美元）：累计消耗未达前，空烧/止损类只提示不喊停
    "alert_enabled": True,           # 告警总开关
    # v10.14.3：旧式告警三件套 alert_metric/alert_operator 已删除（核心告警固定 ROI 低于阈值，由规则编辑器预设参数收编）；
    # alert_threshold 保留——作为规则编辑器预设参数 threshold 的承载键（PARAM_CFG_KEYS 写回）与核心告警阈值
    "alert_threshold": 0.6,          # 告警阈值（真正的止损告警线 0.6；1.3 是组长定的投放目标，平台算法决定，非止损线）
    "alert_cooldown_min": 0,         # 冷却已移除，每次触发立即推送
    "alert_template": "【{shop}】{plan_name}\n时间：{time}\nROI：{roi:.2f} | 消耗：${cost:.2f} | 收入：${revenue:.2f} | 订单：{orders}\n{message}",  # 告警推送文案模板，支持 {time} {shop} {plan_name} {roi} {cost} {revenue} {orders} {threshold} {metric} {operator} {message} 及辅助变量 {metric_label} {op_label} {aov} {cost_prev} {revenue_prev} {burn_redline} {cost_x}；未知变量保留原文不报错
    # 任务B：告警排除规则（用户自写正则列表，命中任意一条则跳过该行告警；空=不过滤）
    "alert_exclude_patterns": [],
    # v10.2 任务1：自定义告警规则列表（每条 {id,name,kind,expr,message,enabled}；kind=alert 告警/exempt 豁免）
    "alert_rules": [],
    # v10.3 任务1：预设告警规则列表（与 alert_rules 同 schema，preset=True 标记来源；
    # 首次运行/配置缺失时注入内置默认，见 ensure_preset_rules；用户编辑后持久生效）
    "preset_rules": None,
    # v10.2 任务1：组长策略客单价（面板可配置；0=自动取识别记录 avg_order_value）
    "avg_order_value": 0.0,
    # v10.2 任务2：目标（店铺）独立监控扩展字段在 targets[i] 内（enabled/alert_enabled/alert_threshold/alert_cooldown_min/alert_rules/avg_order_value）
    "targets": None,
    # v10.2 任务3：mod 管理——禁用 mod id 列表（load_mods 跳过；识别方式下拉只显示已启用识别器）
    "disabled_mods": [],
    # v10.2 任务5：推送余额本地计数（按日期，跨天自动清零；仅估算，以平台实际为准）
    "push_count": {"date": "", "sent": 0},
    # v10.16：微云日志自动上传——程序启动时把昨日及更早的 log 自动备份到微云指定目录，
    # 防清理本地日志/重装丢档，也便于信息部直接拉取。token/pdir_key 见 config.json。
    "weiyun_upload_enabled": False,   # 总开关（config.json 可改）
    "weiyun_token": "",               # 微云 MCP token（config.json 填写）
    "weiyun_pdir_key": "",            # 上传目标目录 key（留空=token 绑定目录；群共享"TK监控器log"目录见 config.json）
    # v10.2 任务6：自定义窗口布局（字号缩放等，启动时恢复）
    "window_layout": {},
    # ===== v10.7.6 低调化配置段（集中管理防围观：无边框/小窗/隐藏模式/全局热键/mini）=====
    # frameless: 主窗口无边框无标题栏（启动即低调，防同事围观）
    # default_geometry: 启动默认窗口尺寸（低调小窗，不再恢复历史大几何）
    # remember_geometry: 是否记忆用户手动调整后的窗口几何（低调原则默认 False=每次启动都用默认小尺寸）
    # launch_mode: 启动形态 "normal"=小窗完整界面 / "mini"=直接进入简略小窗（主窗隐藏，热键呼出）
    # mini_on_start: launch_mode=normal 时是否随启动自动打开 mini 小窗
    # hide_mode: 隐藏模式 "tray"=托盘模式（最小化到系统托盘）/ "extreme"=极端模式（无托盘，仅全局热键呼出/隐藏，默认）
    # hotkey: 全局热键（魔法按键）呼出/隐藏主窗口，格式 ctrl+alt+shift+m；留空禁用
    # quit_hotkey: 全局热键低调退出（格式同上；留空禁用）
    # mini_always_on_top: mini 简略小窗是否置顶
    # v10.8.9 window_title: 主窗口/任务栏显示的低调标题（v10.14.4 起默认"tkm_10.23"，不含产品名；
    #   可在 config.json lowkey.window_title 自行修改；留空/缺省回落"tkm_10.23"）
    "lowkey": {
        "frameless": True,
        "window_title": "tkm_10.26",
        "default_geometry": "900x600",
        "remember_geometry": False,
        "launch_mode": "normal",
        "mini_on_start": False,
        "hide_mode": "extreme",
        # v10.7.7：默认热键由 ctrl+alt+shift+m/q 改为 ctrl+alt+shift+d/x（前者易被占用）；
        # 如仍冲突可在 config.json lowkey 段自行修改（格式 ctrl+alt+shift+字母）
        "hotkey": "ctrl+alt+shift+d",
        "quit_hotkey": "ctrl+alt+shift+x",
        # v10.12：手动完整轮热键——按下执行与自动监控一轮完全相同的完整流程（识别→最小化
        # 浏览器→UIA 刷新→等待加载→快照→告警判断→写日志）并重置自动监控定时器；
        # 留空禁用；与 hotkey/quit_hotkey 同套注册机制
        "refresh_hotkey": "ctrl+alt+shift+r",
        # v10.16.2：显示/隐藏专用热键改为监控启停——ctrl+alt+shift+f 启动自动监控
        # （与「启动自动监控」同一路径，不涉及显示窗口），ctrl+alt+shift+c 停止自动监控
        # （与「停止监控」同一路径，不涉及隐藏窗口）；窗口显隐仅由 D 键魔法键负责；
        # 与 hotkey 切换键独立，防误触；留空禁用；已处于目标状态时仅播提示音不重复操作
        "show_hotkey": "ctrl+alt+shift+f",
        "hide_hotkey": "ctrl+alt+shift+c",
        "mini_always_on_top": True,
        # v10.13：最小尺寸钳制开关（默认 True=窗口强制不小于 800x600；False=不钳制可任意
        # 缩小，无边框窗口边缘拖拽与系统边框模式下同时生效；视图菜单可切换）
        "window_min_size_enabled": True
    },
    # v10.8.6 待办A：只识别"已生效（启用中）"广告计划，过滤已暂停计划（结构化行 status 非空且不含"已生效"时跳过；status 为空保留避免误杀解析失败行）
    "only_active_plans": True,
    # 任务C：板块折叠/隐藏状态持久化（collapsed=标题可见内容收起；hidden=整块不渲染）
    "collapsed_sections": ["sec_th", "sec_sc", "sec_rr", "sec_ord"],
    "hidden_sections": [],
    # 识别条件（严格校验，面板可配置，错误14）
    "strict_enabled": True,          # 严格校验总开关
    "roi_min": 0.01,                 # ROI 最小值（拒绝 0）
    "roi_max": 50.0,                 # ROI 最大值
    "cost_max_val": 1000000.0,       # 成本上限$
    "revenue_max_val": 1000000.0,    # 收入上限$
    "roi_consistency": True,         # ROI≈收入/成本 一致性校验开关
    "roi_consistency_tol": 30.0,     # 一致性容差（%）
    "no_header_policy": "fail",      # 找不到表头: "fail"=识别失败并提示 / "loose"=宽松兜底
    # 识别模式（模式二：窗口控件树文本读取，替代/补充截图OCR；v10.7.7 默认 UIA，部署版不携带 OCR）
    "recog_mode": "uia",             # 识别方式：uia=控件树读取（默认）；ocr=截图OCR（OCR mod 独立提供，主程序不带）
    # 模式二配套（懒加载对策）：滚动收集开关/最大轮数/读取前等待秒数
    "uia_scroll_collect": False,     # 滚动收集：开启时边滚边读控件树合并文本（WM_MOUSEWHEEL 发送，非模拟鼠标键盘）
    "uia_max_scroll": 20,            # 滚动收集最大轮数（连续两次行数不增长提前停止）
    "uia_wait_sec": 1.5,             # 读取前等待秒数（等 accessibility tree 构建，0=不等待）
    # 模式二（v10.2 修复：Chromium 页面内容在深层渲染子树，原 1500 节点/深度14 预算易截断只读到外壳）
    "uia_max_nodes": 5000,           # 单轮控件树遍历最大节点数（防大页面拖死主线程）
    "uia_timeout_sec": 20.0,         # 单轮控件树遍历时间预算（秒）
    "uia_max_depth": 40,             # 控件树遍历最大深度（Chromium 渲染子树可达 20+ 层）
    "cdp_port": 0,                   # CDP 调试端口（0=禁用；>0 时模式二 UIA 无结果会尝试经 CDP 读页面 DOM 文本，需浏览器以 --remote-debugging-port 启动）
    # v10.3 任务2：UIA 结构化表格读取（新模式默认启用；按窗口标题匹配店铺、读 core-table 表格控件、动态列映射）
    "uia_structured": True,          # 结构化读取开关：开启时 UIA 优先按 DOM 级表格控件读取结构化字段，失败回退文本/OCR
    # v10.14.3：刷新配置统一——仅保留 uia_reload（抢前台方案，v10.14.4 起最小化逻辑已彻底删除）；
    # refresh_enabled 默认 True（UI「刷新浏览器」勾选框绑定，取消勾选则跳过刷新直接识别）；
    # 节流 refresh_interval_sec 已删除（每轮强制刷新）；uia_filter/f5 旧路线相关配置与注释已清理
    "refresh_enabled": True,         # 刷新浏览器总开关（UI 勾选框同步；True=每轮识别前先刷新）
    "refresh_method": "uia_reload",  # 刷新方式（唯一）：uia_reload=UIA点击浏览器『重新加载』按钮，先抢前台再点击
    "refresh_wait_sec": 8,           # 就绪轮询上限秒数（mod 内 poll_sec=max(5, refresh_wait_sec)，默认 8 即用 8）
    # v10.7.5：路线C 鼠标点击降级开关（默认 false）——筛选刷新勾选/点击的 Pattern 全部不可用时，
    # 是否允许用 SetCursorPos+mouse_event 点击控件中心（会短暂移动鼠标/可能抢焦点，谨慎开启）
    "mouse_click_fallback": False,
    # 识别规则（面板可配置，错误15：框很大时限定"读哪里/怎么读"）
    "header_col_name": "广告计划名称",  # 表头·计划名列关键词
    "header_col_roi": "ROI",          # 表头·ROI列关键词
    "header_col_cost": "成本",        # 表头·成本列关键词
    "header_col_rev": "总收入",       # 表头·收入列关键词
    # 错误22：表头关键词（广告计划词命中即表头，逗号分隔多候选，自动合并OCR噪声变体）
    "header_keywords": "广告计划",
    "anchor_keyword": "",             # 起始计划名关键词，空=从表头下一行开始
    "read_row_index": 1,              # 读取第几行（从起始行起算，1=第一条，1~20）
    "max_rows_to_check": 10,          # 任务A：参与逐行告警判断的最大数据行数（业务上限：网页列表每页最多 10 条计划）
    "offset_roi": 0,                  # ROI列 x 偏移（像素，可正负）
    "offset_cost": 0,                 # 成本列 x 偏移
    "offset_rev": 0,                  # 收入列 x 偏移
    # 识别规则·错误20新增（跳过噪声词 / 品名校验 / 品名列偏移）
    # v10 跳过词改造：存储为「正则列表」（每条一个正则，命中即跳过）；
    # 兼容旧格式：若 config.json 中为逗号分隔字符串，加载时 _parse_skip_words 自动拆分。
    "skip_words": ["启停", "开关", "拨片", "状态", "数据分析", "数据趋势", "筛选", "导出",
                   "新建", "刷新", "分页", "上一页", "下一页", "设置", "帮助", "修改",
                   "优化", "编辑", "删除", "复制", "暂停", "推广", "查看报告",
                   "投放中", "已暂停", "已结束"],
    "plan_name_check": True,          # 品名校验开关（取到的品名列文本需通过校验）
    "plan_name_pattern": "",          # 品名正则（留空=宽松校验：非空且含字母；可填命名规范正则）
    "offset_name": 0,                 # 品名列 x 偏移（像素，可正负）
    # 订单数（手动维护，修复旧版恒 0 缺陷）
    "current_orders": 0,
    # 推送（企业微信 / 钉钉 / PushPlus）
    "wecom_key": "",
    "wecom_enabled": False,
    "dingtalk_url": "",
    "dingtalk_enabled": False,
    "pushplus_token": "",
    "pushplus_enabled": True,
    # v10.14.3：push_batch_minutes 已删除（v10.14 起推送时机改为每轮识别完统一打包，间隔输入框同步移除）
    # v10.9.1：告警轻滴声——自动监控命中告警时播放一声很轻的滴声（低调提醒，不占视线）；手动识别/快捷键刷新不响
    "sound_alarm": True,
    # v10.10：告警 toast 开关——True 弹 Windows 系统通知气泡；False 不弹（上班时间防被同事看到，声音提示仍可保留）
    # v10.11：默认改 False（toast 默认关闭，面板⑥推送设置区可手动勾选打开；声音/推送不受影响）
    "toast_enabled": False,
    # PushPlus 推送内容模板（其它规则告警正文，支持 {time}/{shop}/{plan_name}/{roi}/{cost}/{orders}/{revenue}/{alerts}/{message}/{threshold} 等变量，留空=内置默认）
    "pushplus_template": "【{shop}】{plan_name}\n时间：{time}\nROI：{roi:.2f} | 消耗：${cost:.2f} | 收入：${revenue:.2f} | 订单：{orders}\n{message}",
}

NUM_RE = re.compile(r"\d+(?:\.\d+)?")
MONEY_RE = re.compile(r"\$\s?([\d,]+(?:\.\d+)?)")


def _norm_num_text(s):
    """数字提取前规范化：去千分位逗号、全角点/中文句号转半角点、
    合并数字间"空格+点+空格"（OCR 常把 21.80 读成 '21 ． 80'）、
    全角数字转半角。归一化后再匹配 NUM_RE，避免一个数被拆成两个。"""
    s = s.replace(",", "").replace("．", ".").replace("。", ".")
    s = re.sub(r"(?<=\d)\s*\.\s*(?=\d)", ".", s)
    return "".join(chr(ord(c) - 0xFEE0) if "\uFF10" <= c <= "\uFF19" else c for c in s)


# ==================== 配置与历史数据 ====================

def load_config():
    cfg = DEFAULT_CONFIG.copy()
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
                for k, v in DEFAULT_CONFIG.items():
                    if k in saved:
                        cfg[k] = saved[k]
        except Exception:
            pass
    # 任务A：targets 迁移——旧版单目标配置（window_keyword/roi）自动成为第一个目标；
    # 已存在 targets 时直接使用；列表为空也视为未初始化，用旧字段兜底构造。
    _ensure_targets(cfg)
    # v10.3：预设规则注入——首次运行/配置缺失时写入内置预设并落盘（用户已编辑过则不覆盖）
    if ensure_preset_rules(cfg):
        try:
            save_config(cfg)
        except Exception:
            pass
    return cfg


def _ensure_targets(cfg):
    """确保 cfg['targets'] 为合法目标列表；旧单目标字段自动迁移为第一个目标。

    target 结构：{id:int, name:str, window_keyword:str, roi:list|None, enabled:bool}
    返回 True 表示发生了迁移/补全（调用方可决定是否落盘）。
    """
    targets = cfg.get("targets")
    if isinstance(targets, list) and targets:
        ok = True
        for i, t in enumerate(targets):
            if not isinstance(t, dict):
                ok = False
                break
        if ok:
            return False
    old_kw = str(cfg.get("window_keyword") or "商户中心").strip() or "商户中心"
    old_roi = cfg.get("roi")
    migrated = {
        "id": 1,
        "name": "目标1",
        "window_keyword": old_kw,
        "roi": old_roi if isinstance(old_roi, list) and len(old_roi) == 4 else None,
        "enabled": True,
    }
    cfg["targets"] = [migrated]
    return True


def _next_target_id(targets):
    """返回下一个自增目标 id（现有最大 id + 1）。"""
    cur = 0
    for t in targets or []:
        try:
            cur = max(cur, int(t.get("id") or 0))
        except (ValueError, TypeError):
            continue
    return cur + 1


def save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


# SQLite 历史库（主存储）
# 表结构与 history.json 记录字段保持一致：
# time/roi/cost/orders/revenue/uv/source/cumulative_cost/cumulative_orders/
# cumulative_revenue/avg_order_value/conversion_rate
def _line_nums(line):
    """提取一行里的数字并过滤噪声：时间戳型（8位20开头、6位1x/2x开头）不参与解析，
    避免把 20261451（时间 20:26:14:51）当成收入 2026 万"""
    nums = []
    for x in NUM_RE.findall(_norm_num_text(line)):
        f = float(x)
        if 20000000 <= f < 21000000:   # 8位时间戳：20261451
            continue
        if 100000 <= f < 210000:       # 6位时间戳：202614
            continue
        nums.append(f)
    return nums


# 表头行判定关键词：行内同时含 ROI 类 + 成本类 + 收入类 关键词才算表头
HEADER_ROI_RE = re.compile(r"ROI|投产|回报", re.I)
HEADER_COST_RE = re.compile(r"成本|消耗|花费|Spend|Cost", re.I)
HEADER_REV_RE = re.compile(r"收入|GMV|成交|销售额|Revenue|Sales", re.I)


def _rule_text_match(text, keyword, default_re):
    """识别规则词匹配（错误15）：配置关键词非空时按包含匹配（忽略大小写），
    否则回退到默认正则。用于表头判定与列定位的 OR 合并。"""
    kw = (keyword or "").strip()
    if kw:
        return kw.lower() in text.lower()
    if default_re is not None:
        return bool(default_re.search(text))
    return False


# 表头·计划名列特征词（错误20：整行表头结构定位需要识别品名列）
HEADER_NAME_RE = re.compile(r"广告计划|计划名称|广告名称|计划名|商品名|品名|名称|广告名|Campaign|Name", re.I)

# 错误22：表头关键词内置OCR噪声变体（灰色浅色表头文字识别率低，常见"广 告 计"缺字/拆词）
HEADER_KW_BUILTIN = ["广告计划", "广告计划名称", "广告计", "告计划", "计划名称", "计划名",
                     "广告名称", "广告名", "计划"]


def _norm_header_text(s):
    """表头关键词匹配用文本规范化：去空白、全角转半角、转小写（错误22）"""
    t = _norm_word_text(s)
    return re.sub(r"\s+", "", t)


def _header_kw_list(cfg=None):
    """返回表头判定关键词列表（错误22）：用户配置 header_keywords（逗号分隔，
    默认"广告计划"）+ 内置OCR噪声变体，全部去空格/全角/小写去重。
    任一关键词命中行内任一 OCR 词即认定该行为表头行（最高优先级判定）。"""
    cfg = cfg or {}
    raw = (cfg.get("header_keywords") or "").strip()
    cands = []
    for part in str(raw).split(","):
        p = _norm_header_text(part)
        if p and p not in cands:
            cands.append(p)
    for b in HEADER_KW_BUILTIN:
        if b not in cands:
            cands.append(b)
    return cands


def _row_hit_header_kw(words, header_kw_list):
    """行内任一 OCR 词文本命中表头关键词即返回 True（错误22：广告计划词命中即表头）"""
    for w in words:
        t = _norm_header_text(w.get("text", ""))
        if not t:
            continue
        for hk in header_kw_list:
            if hk and hk in t:
                return True
    return False


def _norm_word_text(s):
    """词文本规范化（匹配用）：去空白、全角转半角、转小写"""
    s = (s or "").strip()
    out = []
    for ch in s:
        code = ord(ch)
        if code == 0x3000:
            code = 0x20
        elif 0xFF01 <= code <= 0xFF5E:
            code -= 0xFEE0
        out.append(chr(code))
    return "".join(out).lower()


def _parse_skip_words(value):
    """将跳过词配置规范化为正则列表，兼容新旧两种存储格式。

    - 新格式（list/tuple）：["启停", r"L\d+", ...] → 过滤空串后原样返回；
    - 旧格式（str）：逗号分隔字符串 → 自动按逗号拆分（向后兼容）；
    - None / 空：返回 []。
    """
    if value is None:
        return []
    if isinstance(value, str):
        return [s.strip() for s in value.split(",") if s.strip()]
    if isinstance(value, (list, tuple)):
        return [str(s).strip() for s in value if isinstance(s, str) and s.strip()]
    return []


def _is_skip_word(w, skip_words):
    """判断词是否为跳过噪声词（错误20：启停开关/拨片/按钮/统计卡标签等不参与解析）。

    - 词文本为空 → 跳过；
    - 词匹配表头关键词（品名/ROI/成本/收入）→ 不跳过（防止误配跳过词误伤表头）；
    - 词为纯数字 → 不跳过（可能是 ROI/成本/收入数值）；
    - 其余：逐条按正则匹配（re.search），任一条正则命中词文本即视为噪声；
      普通中文/英文词不带正则元字符时等价于旧版子串包含判断；非法正则自动忽略。
    """
    t = _norm_word_text(w.get("text", ""))
    raw = w.get("text", "") or ""
    if not t:
        return True
    if HEADER_NAME_RE.search(t) or HEADER_ROI_RE.search(t) or \
            HEADER_COST_RE.search(t) or HEADER_REV_RE.search(t):
        return False
    if NUM_RE.fullmatch(t.replace("$", "").replace(",", "")):
        return False
    if not skip_words:
        return False
    if isinstance(skip_words, str):
        skip_words = _parse_skip_words(skip_words)
    for pat in skip_words:
        pat = str(pat).strip()
        if not pat:
            continue
        try:
            # 先试规范化文本（兼容旧中文/大小写词），再试原始文本（支持 L\d+ 这类
            # 按所见字符书写的大小写敏感正则——norm 会把 L60 小写化为 l60 导致失配）
            if re.search(pat, t) or re.search(pat, raw):
                return True
        except re.error:
            continue
    return False


def _check_plan_name(name, cfg=None):
    """品名列校验（错误20）：返回 (ok, reason)。

    - cfg.plan_name_pattern 非空：按该正则匹配（用户广告计划命名规范：
      商品名+变体 计划变体(A/B/C) 重建数(r1/r2/r3) ROI设定 投放时间戳；纯复制 c/ 前缀）；
    - 留空（宽松）：非空、且含至少一个字母（OCR 品名不会纯数字/纯符号）。
    """
    if not name or not str(name).strip():
        return False, "品名为空"
    cfg = cfg or {}
    pat = (cfg.get("plan_name_pattern") or "").strip()
    if pat:
        try:
            if re.search(pat, str(name)):
                return True, ""
            return False, f"品名不符合命名规范: {name!r}"
        except re.error as e:
            return False, f"品名正则无效({e}): {pat!r}"
    t = _norm_word_text(name)
    if not any(ch.isalpha() for ch in t):
        return False, f"品名不含字母（疑似噪声）: {name!r}"
    return True, ""


def _row_plan_name(words, name_x, skip_words, max_dist=200):
    """取一行中距品名列 x 最近的文本词作为品名（非数字、非跳过词、距离≤max_dist）。
    返回 (plan_name, ok)，ok=False 表示该行取不到可信品名。
    max_dist：严格表头定位时品名列精确，默认 200px；宽松配对估算品名列时放宽到 400px。"""
    best = None
    for w in words:
        if _is_skip_word(w, skip_words):
            continue
        t = _norm_word_text(w.get("text", ""))
        if NUM_RE.fullmatch(t.replace("$", "").replace(",", "")):
            continue
        d = abs(w["x"] - name_x)
        if best is None or d < best[0]:
            best = (d, w["text"])
    if best is None or best[0] > max_dist:
        return "", False
    return best[1].strip(), True


def _strict_validate(triplet, cfg=None):
    """严格校验三元组 (roi, cost, revenue)，返回 (ok, reason)。

    错误14新增：识别有效性校验条件面板化后，所有候选行在基础校验之外
    再走一遍本校验。cfg 为空或 strict_enabled=False 时直接放行（维持旧行为）：
    - roi ∈ [roi_min, roi_max]（默认 0.01~50，拒绝 0）
    - cost > 0 且 ≤ cost_max_val；revenue > 0 且 ≤ revenue_max_val
    - roi_consistency 开启且 cost>0 时：|roi - revenue/cost| / (revenue/cost) ≤ tol/100
      （cost 或 revenue 为 0 直接拒）
    """
    if not cfg or not cfg.get("strict_enabled", True):
        return True, ""
    roi, cost, revenue = triplet
    roi_min = float(cfg.get("roi_min", 0.01))
    roi_max = float(cfg.get("roi_max", 50.0))
    cost_max = float(cfg.get("cost_max_val", 1000000.0))
    revenue_max = float(cfg.get("revenue_max_val", 1000000.0))
    tol = float(cfg.get("roi_consistency_tol", 30.0)) / 100.0
    if roi < roi_min:
        return False, f"ROI={roi} 低于最小值 {roi_min}"
    if roi > roi_max:
        return False, f"ROI={roi} 高于最大值 {roi_max}"
    if cost <= 0:
        return False, f"成本={cost} 必须大于 0"
    if cost > cost_max:
        return False, f"成本={cost} 高于上限 {cost_max}"
    if revenue <= 0:
        return False, f"收入={revenue} 必须大于 0"
    if revenue > revenue_max:
        return False, f"收入={revenue} 高于上限 {revenue_max}"
    if cfg.get("roi_consistency", True) and cost > 0:
        ratio = revenue / cost
        if ratio <= 0:
            return False, f"收入/成本={ratio:.4f} 无效"
        if abs(roi - ratio) / ratio > tol:
            return False, (f"ROI={roi} 与 收入/成本={ratio:.4f} 偏差 "
                           f"超过容差 {cfg.get('roi_consistency_tol', 30.0)}%")
    return True, ""


def _table_row_analysis(text, cfg=None):
    """表头定位 + 行尾三元组扫描（供 _parse_table_row 与拾取详情可视化共用）。

    1) 逐行扫描找表头行：行内同时含 ROI 关键词 且 成本类关键词 且 收入类关键词；
    2) 找到表头后从表头下方第一行开始取行尾三元组（_line_nums 过滤后取最后 3 个，
       校验 ROI 0~50、成本/收入非负且 ≤10亿），收集所有通过校验的候选行；
    3) 找不到表头时：no_header_policy=="fail"（错误14默认）直接返回无候选，
       ==“loose”时退化为从全文第一行开始扫描（原逻辑）。

    错误14新增：候选行在基础校验之外再走 _strict_validate 严格校验（cfg 下传），
    被拒绝的候选写入 detail["rejects"]（含拒绝原因），供日志区可视化提示。

    返回拾取详情 dict：
    {
      "header_index": 表头行号(1-based) 或 None,
      "mode": "header" / "fallback" / "fail_no_header",
      "candidates": [{"row": 行号, "text": 行文本, "triplet": (roi, cost, revenue)}, ...],
      "rejects": [{"row": 行号, "text": 行文本, "triplet": ..., "reason": 拒绝原因}, ...],
      "selected_index": 选中的行号 或 None,
    }
    """
    detail = {"header_index": None, "mode": "fallback", "candidates": [], "rejects": [], "selected_index": None}
    if not text:
        return detail
    cfg = cfg or {}
    # 识别规则（错误15）：配置词非空时，行内同时含四关键词也算表头行（与默认正则 OR 合并）
    rule = {k: ((cfg.get(k) or "").strip() if isinstance(cfg.get(k), str) else "") for k in
            ("header_col_name", "header_col_roi", "header_col_cost", "header_col_rev")}
    anchor = (cfg.get("anchor_keyword") or "").strip() if isinstance(cfg.get("anchor_keyword"), str) else ""
    try:
        read_idx = max(1, int(cfg.get("read_row_index", 1) or 1))
    except Exception:
        read_idx = 1
    lines = text.splitlines()
    header_index = None
    header_kw_list = _header_kw_list(cfg)
    for i, line in enumerate(lines):
        # 表头判定（错误22最高优先级）：广告计划词命中即表头——
        # 行文本（去空格规范化后）命中 header_keywords 任一候选（含OCR噪声变体
        # "广告计"/"广 告 计"/"告计划"/"计划名称"等），即认定该行为表头行，
        # ROI/成本/收入 词不再作为表头判定的必选项（灰色浅色表头文字识别率低）。
        line_norm = _norm_header_text(line)
        header_kw_hit = any(hk and hk in line_norm for hk in header_kw_list)
        # 次级判定（错误20整行结构，广告计划词漏识别时兜底）：品名 + ROI + 收入 三词必选，
        # 成本/消耗词软选。不再只凭 ROI+收入 两词判表头，防止把其他店铺表/统计卡误当目标表。
        default_hit = (HEADER_NAME_RE.search(line) and HEADER_ROI_RE.search(line)
                       and HEADER_REV_RE.search(line))
        rule_hit = (any(rule.values())
                    and all(_rule_text_match(line, v, None) for v in rule.values()))
        if not (header_kw_hit or default_hit or rule_hit):
            continue
        # 行内含 ≥3 个有效数字时更像统计卡数据行（如"ROI 12.00 成本 2.00 收入 6.00"），
        # 不是列头，跳过，避免把顶部统计卡误当表头
        if len(_line_nums(line)) >= 3:
            continue
        header_index = i
        break
    if header_index is None:
        no_header_policy = cfg.get("no_header_policy", "fail")
        if no_header_policy == "fail":
            detail["mode"] = "fail_no_header"
            detail["fail_reason"] = "no_header"
            return detail
        # 错误21：loose 兜底若开启品名校验，文本行无品名可校验，直接返回失败
        if cfg.get("plan_name_check", True):
            detail["mode"] = "fail_no_header"
            detail["fail_reason"] = "no_header_loose_plan_name_check"
            return detail
    start = 0
    if header_index is not None:
        detail["header_index"] = header_index + 1
        detail["mode"] = "header"
        start = header_index + 1  # 从表头下方第一行开始，绕开页面顶部统计卡（GMV Max 等）
    # 起始计划名锚点（错误15）：anchor_keyword 非空时，从起始位置向下找第一个
    # 行文本含锚点关键词的行开始收集；找不到时按 no_header_policy 处理
    if anchor:
        anchor_idx = None
        for i in range(start, len(lines)):
            if anchor.lower() in lines[i].lower():
                anchor_idx = i
                break
        if anchor_idx is None:
            if cfg.get("no_header_policy", "fail") == "fail":
                detail["mode"] = "fail_no_anchor"
                detail["fail_reason"] = "no_anchor"
                return detail
            # loose：锚点缺失不阻断，仍从原起始行开始
        else:
            start = anchor_idx
    candidates = []
    rejects = []
    for i in range(start, len(lines)):
        line = lines[i]
        nums = _line_nums(line)
        if len(nums) < 3:
            continue
        roi, cost, revenue = nums[-3], nums[-2], nums[-1]
        if roi == 0 and cost == 0 and revenue == 0:
            continue
        if not (0 <= roi <= 50 and 0 <= cost <= 1e9 and 0 <= revenue <= 1e9):
            rejects.append({"row": i + 1, "text": line, "triplet": (roi, cost, revenue),
                            "reason": "基础校验失败（ROI 须 0~50，成本/收入非负且≤10亿）"})
            continue
        ok, reason = _strict_validate((roi, cost, revenue), cfg)
        if not ok:
            rejects.append({"row": i + 1, "text": line, "triplet": (roi, cost, revenue), "reason": reason})
            continue
        candidates.append({"row": i + 1, "text": line, "triplet": (roi, cost, revenue)})
    detail["candidates"] = candidates
    detail["rejects"] = rejects
    # read_row_index（错误15）：取第 N 个候选（默认 1=第一个）
    if candidates and 0 < read_idx <= len(candidates):
        detail["selected_index"] = candidates[read_idx - 1]["row"]
    return detail


def _parse_table_by_words(words, cfg=None):
    """词坐标重建表格定位 (ROI, 成本, 收入, 品名)（错误13修复核心，错误20重写）。

    背景：Windows OCR 输出字间带空格、表格多行被合并成一长串无换行文本
    （如"开/关 广告计划名称 Lilin pemulihan D 21.80 11 0819 2026 数据分析 ..."），
    按文本行解析的表头定位与行尾取数全部失效。本函数改用每个词的 (x, y)
    坐标重建表格行列：

    1) 按 y 聚类成行（y 差 ≤ 15px 视为同一行），行内按 x 排序；
       错误20：聚类前先过滤跳过噪声词（启停开关/拨片/按钮/统计卡标签等），
       非目标结构词一律不参与解析，支持全屏框选时整屏自动定位目标表；
    2) 找表头行（错误22最高优先级）：行内任一 OCR 词命中"广告计划词"
       （header_keywords 用户候选 + 内置OCR噪声变体，宽松模糊匹配）即认定
       该行为表头行，ROI/成本/收入 词不再必选（灰色浅色表头文字识别率低）；
       广告计划词漏识别时回退错误20整行结构（品名+ROI+收入/配置四词）。
       表头行识别到的 ROI/成本/收入 词用于精确定位列，缺失时用下方数据行
       数字词 x 排序推断列位置（品名右侧最近数字为 ROI，其后成本、收入）；
    3) 从表头行下方第一行起，逐行先跳过操作按钮行（含"数据分析"/"修改"/
       "优化"等词），再取品名（品名列 x 最近的非数字词，错误20新增品名校验），
       再取数字词（时间戳过滤），按 x 距列位置最近贪心取 ROI/成本/收入
       （每列取自不同数字词）；
    4) 校验：ROI 0~50、成本/收入非负且 ≤10亿、三者不全为 0，
       再走 _strict_validate 严格校验（错误14，cfg 下传）。

    返回 (roi, cost, revenue, plan_name, detail)；找不到表头或无有效行时返回
    (None, None, None, None, detail)，交由上层判定（no_header_policy=="fail"
    时直接报"没有找到广告计划"，不再静默兜底硬读）。
    detail 结构同 _table_row_analysis：header_index / mode="words" /
    candidates / rejects / selected_index，候选行 text 为重建后的行文本摘要，
    并新增 plan_name / skip_count / fail_reason / header_hit 字段。
    """
    detail = {"header_index": None, "mode": "words", "candidates": [], "rejects": [],
              "selected_index": None, "plan_name": None, "skip_count": 0,
              "fail_reason": None, "header_hit": None}
    if not words:
        return None, None, None, None, detail
    cfg = cfg or {}
    # 识别规则（错误15）：配置关键词优先，空则回退默认正则；偏移用于列 x 微调
    rule = {k: ((cfg.get(k) or "").strip() if isinstance(cfg.get(k), str) else "") for k in
            ("header_col_name", "header_col_roi", "header_col_cost", "header_col_rev")}
    try:
        offset = {k: float(cfg.get(k, 0) or 0) for k in ("offset_roi", "offset_cost", "offset_rev")}
    except Exception:
        offset = {"offset_roi": 0.0, "offset_cost": 0.0, "offset_rev": 0.0}
    try:
        offset_name = float(cfg.get("offset_name", 0) or 0)
    except Exception:
        offset_name = 0.0
    skip_words = _parse_skip_words(cfg.get("skip_words"))
    plan_name_check = bool(cfg.get("plan_name_check", True))
    anchor = (cfg.get("anchor_keyword") or "").strip() if isinstance(cfg.get("anchor_keyword"), str) else ""
    try:
        read_idx = max(1, int(cfg.get("read_row_index", 1) or 1))
    except Exception:
        read_idx = 1

    # 1) 按 y 聚类成行（错误20：先过滤跳过噪声词——拨片/按钮/统计卡标签等，
    #    非目标结构词一律不参与解析；全屏框选时也能在整屏中自动定位目标表）
    kept = [w for w in words if not _is_skip_word(w, skip_words)]
    detail["skip_count"] = len(words) - len(kept)
    rows = []  # 每行: {"y": 行平均y, "words": [...]}
    for w in sorted(kept, key=lambda w: w["y"]):
        placed = False
        for r in rows:
            if abs(r["y"] - w["y"]) <= 15:
                r["words"].append(w)
                r["y"] = sum(x["y"] for x in r["words"]) / len(r["words"])
                placed = True
                break
        if not placed:
            rows.append({"y": w["y"], "words": [w]})
    for r in rows:
        r["words"].sort(key=lambda w: w["x"])

    # 2) 找表头行并记录四列 x 坐标（错误22：广告计划词命中即表头最高优先级；
    #    广告计划词漏识别时回退错误20配置词/整行结构；统计卡过滤保留）
    header_idx = None
    col_x = {"name": None, "roi": None, "cost": None, "revenue": None}
    header_hit = None  # "header_kw"(广告计划词命中) / "full"(四词) / "relaxed"(品名+ROI+收入) / "rule"(配置词)
    header_kw_list = _header_kw_list(cfg)
    for i, r in enumerate(rows):
        def find_col_x(kw, default_re):
            if kw:
                w = next((w for w in r["words"] if kw.lower() in w["text"].lower()), None)
                if w is not None:
                    return w["x"]
            if default_re is not None:
                w = next((w for w in r["words"] if default_re.search(w["text"])), None)
                if w is not None:
                    return w["x"]
            return None
        # 表头判定（错误22最高优先级）：行内任一 OCR 词命中"广告计划词"
        # （header_keywords 用户候选 + 内置OCR噪声变体"广告计"/"广 告 计"/
        # "告计划"/"计划名称"等，宽松模糊匹配）即认定该行为表头行，
        # ROI/成本/收入 词不再作为必选项（灰色浅色表头文字识别率低）。
        header_kw_hit = _row_hit_header_kw(r["words"], header_kw_list)
        # 次级判定（错误20整行结构，广告计划词漏识别时兜底）：配置四词严格；
        # OCR 常把"消耗"读成乱码（错误18教训），成本缺失时降级为 品名+ROI+收入
        name_w = find_col_x(rule["header_col_name"], HEADER_NAME_RE)
        roi_w = find_col_x(rule["header_col_roi"], HEADER_ROI_RE)
        cost_w = find_col_x(rule["header_col_cost"], HEADER_COST_RE)
        rev_w = find_col_x(rule["header_col_rev"], HEADER_REV_RE)
        default_hit4 = (name_w is not None and roi_w is not None and cost_w is not None and rev_w is not None)
        default_hit3 = (name_w is not None and roi_w is not None and rev_w is not None)
        rule_hit = (any(rule.values())
                    and all(find_col_x(v, None) is not None for v in rule.values()))
        if not (header_kw_hit or default_hit4 or default_hit3 or rule_hit):
            continue
        # 统计卡过滤（与文本版 _table_row_analysis 一致）：
        # 行内含 ≥3 个有效数字时更像统计卡数据行（如"ROI 12.00 成本 2.00
        # 收入 6.00"），不是列头，跳过，避免把顶部统计卡误当表头
        row_nums = 0
        for w in r["words"]:
            m = NUM_RE.search(_norm_num_text(w["text"]))
            if not m:
                continue
            f = float(m.group())
            if 20000000 <= f < 21000000 or 100000 <= f < 210000:
                continue
            row_nums += 1
        if row_nums >= 3:
            continue
        col_x = {"name": name_w, "roi": roi_w,
                 "cost": cost_w, "revenue": rev_w}
        if header_kw_hit:
            # 错误22：广告计划词命中即表头——ROI/成本/收入词识别到则用于精确
            # 列定位，没识别到则留 None，稍后由数据行数字词 x 推断列位置
            header_hit = "header_kw"
            if col_x["cost"] is None and col_x["roi"] is not None and col_x["revenue"] is not None:
                # 错误18：消耗列表头被 OCR 读成乱码时，用 ROI 与总收入列中点估算列位置
                col_x["cost"] = (col_x["roi"] + col_x["revenue"]) / 2.0
            if col_x["name"] is None:
                # 品名列缺失（配置词未匹配/表头无品名词）时按 ROI 列左侧估算
                col_x["name"] = (col_x["roi"] if col_x["roi"] is not None else
                                 (col_x["revenue"] if col_x["revenue"] is not None else None))
                if col_x["name"] is not None:
                    col_x["name"] = col_x["name"] - 200.0
        else:
            # 旧路径（错误20整行结构）：ROI/收入列必须定位到才能继续
            if col_x["roi"] is None or col_x["revenue"] is None:
                continue
            if col_x["cost"] is None:
                # 错误18：消耗列表头被 OCR 读成乱码时，用 ROI 与总收入列中点估算列位置
                col_x["cost"] = (col_x["roi"] + col_x["revenue"]) / 2.0
            if col_x["name"] is None:
                # 品名列缺失（配置词未匹配/表头无品名词）时按 ROI 列左侧估算
                col_x["name"] = col_x["roi"] - 200.0
            header_hit = "full" if default_hit4 else ("relaxed" if default_hit3 else "rule")
        # 列偏移微调（错误15/错误20）：取数 x = 表头列 x + offset
        col_x = {"name": (col_x["name"] + offset_name) if col_x["name"] is not None else None,
                 "roi": (col_x["roi"] + offset["offset_roi"]) if col_x["roi"] is not None else None,
                 "cost": (col_x["cost"] + offset["offset_cost"]) if col_x["cost"] is not None else None,
                 "revenue": (col_x["revenue"] + offset["offset_rev"]) if col_x["revenue"] is not None else None}
        header_idx = i
        detail["header_index"] = i + 1  # 错误22：词坐标表头行号(1-based)，供拾取详情展示
        break
    # 错误19宽松兜底状态（严格表头命中时保持 False）
    fallback_header = False
    header_y = None
    if header_idx is None:
        # 错误19/20 宽松兜底：严格表头行未命中（表头词 y 波动被拆行/与首行数据合并）
        # 时，改用"ROI 类词 + 成本类词 + 收入类词 y 差 ≤ 30px 且 ROI.x < 收入.x"
        # 的最近配对定位表头列位置；成本词缺失时用中点；品名列按 ROI 列左侧估算。
        # 从配对行下方开始取数。
        try:
            roi_ws = [w for w in words if HEADER_ROI_RE.search(_norm_num_text(w["text"]))]
            cost_ws = [w for w in words if HEADER_COST_RE.search(_norm_num_text(w["text"]))]
            rev_ws = [w for w in words if HEADER_REV_RE.search(_norm_num_text(w["text"]))]
            best = None  # (roi_w, cost_w, rev_w, sum_y)
            for rw in roi_ws:
                for vw in rev_ws:
                    if abs(rw["y"] - vw["y"]) > 30 or rw["x"] >= vw["x"]:
                        continue
                    # 成本词可选：找 y 差 ≤ 30 的成本词
                    cw = next((cw for cw in cost_ws if abs(cw["y"] - rw["y"]) <= 30), None)
                    sum_y = rw["y"] + (cw["y"] if cw else rw["y"]) + vw["y"]
                    if best is None or sum_y < best[3]:
                        best = (rw, cw, vw, sum_y)
            if best is not None:
                rw, cw, vw, _ = best
                fallback_header = True
                header_y = (rw["y"] + vw["y"]) / 2.0
                cost_x = cw["x"] if cw else (rw["x"] + vw["x"]) / 2.0
                col_x = {"name": rw["x"] - 220.0 + offset_name,
                         "roi": rw["x"] + offset["offset_roi"],
                         "cost": cost_x + offset["offset_cost"],
                         "revenue": vw["x"] + offset["offset_rev"]}
                header_hit = "fallback"
        except Exception:
            fallback_header = False
        if fallback_header:
            detail["header_index"] = "宽松配对"
            detail["fallback_header"] = True
            detail["header_hit"] = "fallback"
        else:
            # 错误19/20诊断：输出 y 聚类前 12 行摘要，定位表头词拆行/关键词分布
            try:
                dbg = []
                for i, r in enumerate(rows[:12]):
                    tags = []
                    for w in r["words"]:
                        t = _norm_num_text(w["text"])
                        if HEADER_NAME_RE.search(t):
                            tags.append("NAME")
                        if HEADER_ROI_RE.search(t):
                            tags.append("ROI")
                        if HEADER_REV_RE.search(t):
                            tags.append("REV")
                        if HEADER_COST_RE.search(t):
                            tags.append("COST")
                    head = " ".join(w["text"] for w in r["words"][:6])
                    dbg.append(f"第{i+1}行 y={int(r['y'])} 词数={len(r['words'])} 标签=[{','.join(dict.fromkeys(tags)) or '无'}] | {head}")
                detail["debug_rows"] = dbg
            except Exception:
                pass
            detail["fail_reason"] = "no_header"
            detail["header_hit"] = None
            if cfg.get("no_header_policy", "fail") == "fail":
                detail["mode"] = "fail_no_header"
            return None, None, None, None, detail

    # 3) 起始行：anchor_keyword 非空时从含锚点关键词的行开始（错误15）
    if fallback_header:
        # 宽松配对兜底：从配对 y 下方第一行开始（避免把表头行当数据行）
        start = len(rows)
        for i, r in enumerate(rows):
            if r["y"] > header_y + 20:
                start = i
                break
    else:
        start = header_idx + 1
    if anchor:
        anchor_idx = None
        for i in range(header_idx + 1, len(rows)):
            row_text = " ".join(w["text"] for w in rows[i]["words"])
            if anchor.lower() in row_text.lower():
                anchor_idx = i
                break
        if anchor_idx is None:
            if cfg.get("no_header_policy", "fail") == "fail":
                detail["mode"] = "fail_no_anchor"
                detail["fail_reason"] = "no_anchor"
                return None, None, None, None, detail
            # loose：锚点缺失不阻断，仍从表头下一行开始
        else:
            start = anchor_idx

    # 错误22：表头 ROI/成本/收入 词未识别到时（灰色浅色表头文字识别率低），
    # 用下方数据行数字词 x 推断列位置：跳过操作按钮行，取含品名（字母词）+
    # 数字的行，行尾最右 3 个数字即 ROI/成本/收入 数值列（品名右侧最近为 ROI），
    # 多行取各列中位数抗噪声；仍缺列则明确失败，禁止硬读假数据
    if col_x["roi"] is None or col_x["cost"] is None or col_x["revenue"] is None or col_x["name"] is None:
        infer_cols = []
        name_xs = []
        for r in rows[start:]:
            row_text = " ".join(w["text"] for w in r["words"])
            # 品名检查：行内含字母文本词（中文按钮行/纯数字统计卡行不参与推断）
            if not any(re.search(r"[A-Za-z]", w["text"]) for w in r["words"]):
                continue
            # 操作按钮行（含按钮词且不含字母品名词）：不参与推断
            if any(bk in row_text for bk in ("数据分析", "修改", "优化", "编辑", "删除",
                                             "复制", "暂停", "推广", "查看报告")):
                continue
            num_xs = []
            for w in r["words"]:
                m = NUM_RE.search(_norm_num_text(w["text"]))
                if not m:
                    continue
                f = float(m.group())
                if 20000000 <= f < 21000000 or 100000 <= f < 210000:
                    continue
                num_xs.append(w["x"])
            if len(num_xs) >= 3:
                infer_cols.append(num_xs[-3:])
            # 品名列位置（错误22）：数据行内含字母的文本词 x（跳过纯数字/按钮词）
            for w in r["words"]:
                t = _norm_word_text(w.get("text", ""))
                if not any(ch.isalpha() for ch in t):
                    continue
                if NUM_RE.fullmatch(t.replace("$", "").replace(",", "")):
                    continue
                if _is_skip_word(w, skip_words):
                    continue
                name_xs.append(w["x"])
        if infer_cols:
            for k, key in enumerate(("roi", "cost", "revenue")):
                if col_x[key] is not None:
                    continue
                vals = sorted(col[k] for col in infer_cols)
                col_x[key] = vals[len(vals) // 2]
        if col_x["name"] is None:
            # 品名列缺失：用数据行字母词 x 中位数；仍无则按 ROI 列左侧估算
            if name_xs:
                name_xs.sort()
                col_x["name"] = name_xs[len(name_xs) // 2]
            elif col_x["roi"] is not None:
                col_x["name"] = col_x["roi"] - 200.0
        if col_x["roi"] is None or col_x["cost"] is None or col_x["revenue"] is None or col_x["name"] is None:
            # 数据行无法推断完整列位置 → 明确失败，不硬读
            detail["fail_reason"] = "no_header_cols"
            detail["header_hit"] = header_hit
            if cfg.get("no_header_policy", "fail") == "fail":
                detail["mode"] = "fail_no_header"
            return None, None, None, None, detail

    # 4) 从起始行起逐行取数（错误20：先取品名并校验，再贪心按 x 距列最近取三数字列；
    #    结构对不上的行明确拒绝并记录原因，禁止静默硬读假数据）
    for i in range(start, len(rows)):
        r = rows[i]
        row_text = " ".join(w["text"] for w in r["words"])
        # 错误22：跳过操作按钮行（含"数据分析"/"修改"/"优化"等词的 y 聚类行，
        # 非广告计划数据行，避免把按钮/操作区误当数据行硬读）。
        # 注意：仅当该行不含字母品名词（整行几乎都是按钮/操作区文字）才跳过——
        # 数据行末尾常带"数据分析"等按钮词，但含品名（字母词）+数字时仍是数据行。
        row_has_alpha = any(re.search(r"[A-Za-z]", w["text"]) for w in r["words"])
        if not row_has_alpha and any(bk in row_text for bk in ("数据分析", "修改", "优化",
                                                               "编辑", "删除", "复制", "暂停",
                                                               "推广", "查看报告")):
            detail["rejects"].append({"row": i + 1, "text": row_text, "triplet": (None, None, None),
                                      "reason": "操作按钮行（跳过）"})
            continue
        # 品名（错误20）：品名列 x 最近的非数字词；取不到可信品名时该行直接拒绝
        plan_name, name_ok = _row_plan_name(r["words"], col_x["name"], skip_words,
                                            max_dist=400 if fallback_header else 200)
        if not name_ok:
            detail["rejects"].append({"row": i + 1, "text": row_text, "triplet": (None, None, None),
                                      "reason": "品名列未取到可信文本（跳过）"})
            continue
        if plan_name_check:
            ok_name, name_reason = _check_plan_name(plan_name, cfg)
            if not ok_name:
                detail["rejects"].append({"row": i + 1, "text": row_text, "triplet": (None, None, None),
                                          "reason": f"品名校验失败：{name_reason}"})
                continue
        nums = []  # (词序号, 数值, x)
        for j, w in enumerate(r["words"]):
            m = NUM_RE.search(_norm_num_text(w["text"]))
            if not m:
                continue
            f = float(m.group())
            if 20000000 <= f < 21000000 or 100000 <= f < 210000:
                continue
            nums.append((j, f, w["x"]))
        if len(nums) < 3:
            detail["rejects"].append({"row": i + 1, "text": row_text, "triplet": (None, None, None),
                                      "reason": "数字词不足 3 个（跳过）"})
            continue
        used = set()
        picked = {}
        for key, col in (("roi", col_x["roi"]), ("cost", col_x["cost"]), ("revenue", col_x["revenue"])):
            best = None
            for j, f, x in nums:
                if j in used:
                    continue
                d = abs(x - col)
                if best is None or d < best[0]:
                    best = (d, j, f)
            if best is None:
                break
            used.add(best[1])
            picked[key] = best[2]
        if len(picked) != 3:
            detail["rejects"].append({"row": i + 1, "text": row_text, "triplet": (None, None, None),
                                      "reason": "ROI/成本/收入三列数字未配对齐（跳过）"})
            continue
        roi, cost, revenue = picked["roi"], picked["cost"], picked["revenue"]
        if not (0 <= roi <= 50 and 0 <= cost <= 1e9 and 0 <= revenue <= 1e9):
            detail["rejects"].append({"row": i + 1, "text": row_text, "triplet": (roi, cost, revenue),
                                      "reason": "基础校验失败（ROI 须 0~50，成本/收入非负且≤10亿）"})
            continue
        if roi == 0 and cost == 0 and revenue == 0:
            continue
        ok, reason = _strict_validate((roi, cost, revenue), cfg)
        if not ok:
            detail["rejects"].append({"row": i + 1, "text": row_text, "triplet": (roi, cost, revenue), "reason": reason})
            continue
        detail["candidates"].append({"row": i + 1, "text": row_text, "triplet": (roi, cost, revenue),
                                     "plan_name": plan_name})

    # 5) read_row_index（错误15）：取第 N 个候选（默认 1=第一个）
    if detail["candidates"] and 0 < read_idx <= len(detail["candidates"]):
        sel = detail["candidates"][read_idx - 1]
        detail["selected_index"] = sel["row"]
        detail["plan_name"] = sel.get("plan_name")
        detail["header_hit"] = header_hit
        return sel["triplet"][0], sel["triplet"][1], sel["triplet"][2], sel.get("plan_name"), detail
    # 错误20：表头/列已定位但无有效数据行 → fail_reason 供上层报"没有找到广告计划"
    detail["fail_reason"] = "no_rows"
    detail["header_hit"] = header_hit
    return None, None, None, None, detail


def _parse_table_row(text, cfg=None):
    """表格行解析（最高优先级，傻瓜式过滤，表头定位法）。

    整屏/整表框选时，OCR 每行结构为
    "计划名(含日期0819/时间戳1451/20261451/版本号21.80等噪声) + ROI + 成本 + 收入"，
    行尾最后 3 个数字即该行有效三元组。先找含 ROI+成本+收入 关键词的表头行，
    从表头行下方开始取行尾三元组（绕开页面顶部统计卡 GMV Max 等），取第一个通过
    校验的行；找不到表头时：no_header_policy=="fail"（错误14默认）返回 None，
    ==“loose”时退化为从全文第一行开始（原逻辑）。
    校验：ROI 在 0~50 之间，成本/收入非负且 ≤ 10亿，再走严格校验（cfg 下传）。
    全部行都不通过时返回 None，交由原标签行/顺序解析兜底。
    """
    detail = _table_row_analysis(text, cfg)
    if detail["selected_index"] is not None:
        for c in detail["candidates"]:
            if c["row"] == detail["selected_index"]:
                return c["triplet"]
    return None


def parse_fields(text, words=None, cfg=None):
    """
    从 OCR 结果解析 ROI / 成本 / 收入。
    解析优先级（从高到低）：
    0. 词坐标定位（新增，错误13修复）：传入 OCR 词坐标列表 words 时，
       按坐标重建表格行列（_parse_table_by_words），绕开"字间带空格、
       表格多行合并成一长串文本"导致的文本行解析失效。
    1. 表格行解析：逐行取行尾最后 3 个数字作为 (ROI, 成本, 收入) 候选，
       通过合理性校验即返回。整屏/整表框选也能从每行"计划名噪声+ROI+成本+收入"
       中正确取到行尾三元组，多行取第一行，降低对框选精度的依赖。
    2. 标签行解析：按 ROI、消耗/花费、GMV/收入 等标签匹配。
    3. 顺序解析：回退按顺序猜。
    错误14新增：cfg 下传严格校验条件；no_header_policy=="fail" 时，
    词坐标/表格行解析找不到表头直接返回 (None, None, None)，不再走标签行/顺序兜底。
    返回 (roi, cost, revenue)，缺失字段为 None。
    """
    if not text:
        return None, None, None
    no_header_policy = (cfg or {}).get("no_header_policy", "fail")
    if words:
        roi, cost, revenue, plan_name, _ = _parse_table_by_words(words, cfg)
        if roi is not None and cost is not None and revenue is not None:
            return roi, cost, revenue
        if no_header_policy == "fail":
            return None, None, None
    table = _parse_table_row(text, cfg)
    if table is not None:
        return table
    if no_header_policy == "fail":
        return None, None, None
    roi = cost = revenue = None
    roi_like = re.compile(r"ROI|投产|回报", re.I)
    cost_like = re.compile(r"消耗|花费|成本|Spend|Cost", re.I)
    rev_like = re.compile(r"GMV|收入|成交|销售额|Revenue|Sales", re.I)

    for line in text.splitlines():
        nums = _line_nums(line)
        if not nums:
            continue
        if roi is None and roi_like.search(line):
            cand = [n for n in nums if 0 < n < 50]
            if cand:
                roi = cand[0]
        if cost is None and cost_like.search(line):
            cost = max(nums)
        if revenue is None and rev_like.search(line):
            revenue = max(nums)

    # 回退：ROI=第一个 0<n<10 的数；其后第一个 >0 为成本；成本之后第一个 >0 为收入
    if roi is None or cost is None or revenue is None:
        allnums = []
        for line in text.splitlines():
            allnums.extend(_line_nums(line))
        pos = -1
        if roi is None:
            for i, n in enumerate(allnums):
                if 0 < n < 10:
                    roi = n
                    pos = i
                    break
        if cost is None:
            if pos >= 0:
                for n in allnums[pos + 1:]:
                    if n > 0:
                        cost = n
                        break
            else:
                for n in allnums:
                    if n > 0:
                        cost = n
                        break
        if revenue is None and cost is not None:
            ci = None
            for i, n in enumerate(allnums):
                if n == cost and i > pos:
                    ci = i
                    break
            if ci is None:
                for i, n in enumerate(allnums):
                    if n == cost:
                        ci = i
                        break
            if ci is not None:
                for n in allnums[ci + 1:]:
                    if n > 0:
                        revenue = n
                        break
    # 错误21：标签行/顺序解析兜底必须品名+严格校验双重把关，否则返回失败。
    # 文本路径无品名可校验，plan_name_check 开启时直接拒绝，禁止宽松硬读假数据。
    if (cfg or {}).get("plan_name_check", True):
        return None, None, None
    if roi is None or cost is None or revenue is None:
        return None, None, None
    ok, _reason = _strict_validate((roi, cost, revenue), cfg)
    if not ok:
        return None, None, None
    return roi, cost, revenue


# ==================== 推送（企业微信 + 钉钉） ====================

def auto_close_ad(cfg, plan_name="", row_no=0, reason="", logger=None):
    """【v10.21 合主入口】广告计划自动关闭执行函数（真实点击版）。

    调用时机（已由 run_alerts 把关）：仅自动监控 push=True 路径、且已通过无人值守闸门
    is_unattended() 后才会调用本函数；手动路径只报不动（13.1）。

    实现（2026-09-06 合主第二阶段）：真实点击链路已从 test_auto_close v0.12.2 摘录为
    独立模块 auto_close_engine（UIA 收树-定位开关-点前校验-坐标点击-弹窗双按钮确认），
    本函数仅做参数透传与结果串返回。plan_name/row_no 为规则判定时的真实定位参数。
    logger 可选注入日志回调（形如 fn(msg)）；缺省由引擎兜底 print。
    """
    try:
        import auto_close_engine as _ace
    except Exception as _e:
        return f"auto_close_engine 导入失败: {_e}"
    return _ace.run_auto_close(cfg, plan_name=plan_name, row_no=row_no,
                               reason=reason, logger=logger)


# ==================== v10.21 合主：操作层自动关（品龄规则表 12.4 / 无人值守闸门 13.3） ====================
# 拍板依据：决策日志_20260906_两层规则体系拍板.md / 设计文档第十一/十二/十三节。
# 本节全部为模块级只读+小写盘函数，供 run_alerts 消费；真实点击由 auto_close_ad 承接。

_PLAN_ARCHIVE_PATH = None


def _plan_archive_path():
    """品档案路径：随 config.json 同目录（BASE_DIR/plan_archive.json）。"""
    global _PLAN_ARCHIVE_PATH
    if _PLAN_ARCHIVE_PATH is None:
        _PLAN_ARCHIVE_PATH = BASE_DIR / "plan_archive.json"
    return _PLAN_ARCHIVE_PATH


def parse_product_name(plan_name):
    """品名解析（12.6 命名约定）：首个方括号内容即品名；无括号时回退整串。

    例：泰国店 (泰国店)[品名][表号链接]... → 品名；马来店 (马来店)[品名][变体]...
    → 品名（变体为第二段，不属于品名，天然被忽略）。

    （2026-09-08 起品龄键路径不再调用本函数——第二版锚口径改为「首空格前整段+小写
    归一」，本函数仅保留 12.6 命名约定参考；勿在 parse_product_anchor 品龄路径复用它。）
    """
    m = re.search(r"\[([^\[\]]+)\]", str(plan_name or ""))
    return m.group(1).strip() if m else str(plan_name or "").strip()


# tkm_10.23（2026-09-07 老板拍板：商品名即锚，款号/变体不重新观察；锚仅用于品龄追踪）
# 演进（保留说明）：10.23~10.25 采用「方括号品名 + _VARIANT_TOKEN_RE 变体 token 白名单剔除
# （rN/v_topN/v_xxx/p_6.89/复制A/copy_A/1重建/LROI/e_ROI/ROI/纯数字/款号段 F/A1/B2，款号段
# 禁含小写防误伤 Kain/Kotak 等英文单词品名）」；token 清单不可穷尽（ajaib/pembesh/蓝色款/
# 2.0 等新后缀仍会漏判且复杂度高），2026-09-08 拍板第二版口径后该清单逻辑整体退役删除，
# 不再按词枚举。原 _VARIANT_TOKEN_RE 正则已移除（v10.25 第二版，2026-09-08）。


def parse_product_anchor(plan_name):
    """产品锚归一化（v10.25 第二版定稿 2026-09-08：锚 = 首空格前整段 + 小写归一）。

    背景：品龄（新品/老品）判定锚定「商品名本体」，变体后缀只是展示给人看的；
    10.23-10.25 按 token 清单剥离枚举不全且复杂度高，现拍板最简口径。

    规则（2026-09-08 老板拍板，覆盖此前变体清单方案）：
    1. 锚 = 计划名第一个空格前的整段；空格后的一切（B / copy_A / r1 / ajaib /
       pembesh / 蓝色款 / 2.0 …）均属展示层变体，与锚无关，不再逐个剔除；
    2. 锚做小写归一：Kotak ajaib 1 / kotak ajaib 1 / kotak pembesh 1 → 同一锚 kotak；
       真独立 SKU 用连写本名（Kotakajaib → kotakajaib）区分，不因大小写/首段混淆；
    3. 兼容条：首段若以中文结尾且紧贴尾部字母数字段则剥离（黑金刚喷头C v_top3 r1
       → 首段黑金刚喷头C → 黑金刚喷头；黑金刚喷头A 1重建 → 黑金刚喷头）；循环剥到
       不再匹配；纯英文/拼音首段（kotakajaib / elixir / kotak）绝不触发此条；
    4. 空/异常回退原名（宁保留不误并）。

    注：本函数仅产出品龄档案键/统计键（resolve_product_age / ensure_plan_archived /
    ops_decision_close / 告警层 in_grace 均经此单入口）；日志/告警/展示仍保留完整计划名。
    """
    raw = str(plan_name or "").strip()
    if not raw:
        return raw
    try:
        # 锚 = 第一个空格前的整段（空格即变体分界；空格后全为展示层变体）
        head = raw.split(None, 1)[0]
        # 兼容条：首段中文结尾紧贴尾部字母数字段 → 剥离（循环剥到不再匹配）
        prev = None
        while prev != head:
            prev = head
            m = re.search(r"^(.*[\u4e00-\u9fff])[A-Za-z0-9]+$", head)
            if m:
                head = m.group(1)
        head = head.strip().lower()   # 小写归一（Kotak ajaib 1 → kotak；中文不受影响）
        return head or raw
    except Exception:
        return raw


def load_plan_archive():
    """读品档案：{品名: {"first_seen": "YYYY-MM-DD HH:MM:SS"}}；损坏/缺失返回 {}。"""
    try:
        p = _plan_archive_path()
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_plan_archive(arc):
    try:
        _plan_archive_path().write_text(json.dumps(arc, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print("[品档案] 保存失败:", e)


_PLAN_ARCHIVE_LOCK = threading.Lock()


def ensure_plan_archived(plan_name):
    """品名首见建档（12.7：新建计划当天自动建档；重建 (rN) 不重置窗口）。

    返回该品首见时间串；失败返回 None。仅当品名从未建档时写盘，不重复覆盖。
    tkm_10.23（锚定修复）：档案键改用产品锚 parse_product_anchor——
    款号/变体/重建计划（黑金刚喷头F、黑金刚喷头G、黑金刚喷头A r1…）共用
    同一商品档案「黑金刚喷头」，重建不重新观察，老档持续生效。
    """
    product = parse_product_anchor(plan_name)
    if not product:
        return None
    with _PLAN_ARCHIVE_LOCK:
        try:
            arc = load_plan_archive()
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            # tkm_10.23 旧档兼容：档案内凡锚解析 == product 的旧键（历史以
            # parse_product_name/原名整串为键，如 "黑金刚喷头F"、"黑金刚喷头F r1"）
            # 全部吸收到锚键，取最早首见并清理旧键——老品档案不因键口径升级而丢失，
            # 杜绝“老品被误判新品”回归。键级扫描幂等：锚键已存在时仅当旧键更早才覆盖。
            prod_legacy = [
                k for k in arc
                if k != product and arc[k].get("first_seen")
                and parse_product_anchor(k) == product
            ]
            if prod_legacy:
                best = min(prod_legacy, key=lambda k: arc[k]["first_seen"])
                first = arc[best]["first_seen"]
                cur = (arc.get(product) or {}).get("first_seen")
                if not cur or first < cur:
                    arc[product] = {"first_seen": first, "migrated_from": best}
                for k in prod_legacy:
                    arc.pop(k, None)
                _save_plan_archive(arc)
                return arc[product]["first_seen"]
            if product in arc:
                if not arc[product].get("first_seen"):
                    arc[product]["first_seen"] = now
                    _save_plan_archive(arc)
                return arc[product]["first_seen"]
            arc[product] = {"first_seen": now}
            _save_plan_archive(arc)
            return now
        except Exception:
            return None


def product_age_hours(first_seen):
    """首见时间距今小时数；解析失败返回极大值（视为老品，宁关勿豁免）。"""
    try:
        t0 = datetime.strptime(str(first_seen), "%Y-%m-%d %H:%M:%S")
        return max(0.0, (datetime.now() - t0).total_seconds() / 3600.0)
    except Exception:
        return 999999.0


def is_product_new(first_seen, new_hours=12.0):
    """品龄判定：首见后 new_hours（默认12h）内为新品。档案缺失走 product_age_hours 兜底为老品。"""
    return product_age_hours(first_seen) < new_hours


def parse_schedule_start(schedule_text):
    """从计划排期文本解析投放开始时间（v10.21 排期优先判定用）。

    排期采集格式形如 "2026-09-04 16:08:07 持续投放"（广告后台排期开始时间 + 投放状态）。
    返回 datetime；无排期/格式异常返回 None（调用方回退品档案判定）。
    """
    try:
        m = re.search(r"(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2})?)", str(schedule_text or ""))
        if not m:
            return None
        s = m.group(1).replace("T", " ")
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S" if len(s) == 19 else "%Y-%m-%d %H:%M")
    except Exception:
        return None


# 操作层参数（12.4/12.5 拍板值 + 2026-09-08 规则定稿；后续如需面板化在此集中）
OPS_NEW_PRODUCT_HOURS = 12.0          # 新品/老品界限：品名首见后 12h（锚首铁窗口，仅护锚级真新品）
OPS_CLOSE_NEW_NO_ORDER_COST = 30.0    # 新品学习窗口金额线（2026-09-08：烧<$30 不关；满12h/烧$30 任一先到转 ROI 评估）
OPS_CLOSE_OLD_NO_ORDER_COST = 5.0     # 老品无单烧够 $5 自动关
OPS_CLOSE_OLD_HAS_ORDER_ROI = 0.60    # 有单 ROI<0.60 自动关（≥0.60 留；2026-09-08 由 0.61 统一）
OPS_CLOSE_MIN_COST_GUARD = 1.0        # 最低消耗保护 ≥$1（防 ROI 失真误杀）
OPS_ALERT_OLD_HAS_ORDER_ROI = 0.65    # 老品有单 ROI<0.65 → 仅告警层提醒（操作层不动）


def resolve_product_age(plan_name, schedule_text):
    """统一品龄口径（tkm_10.23 锚定修复，操作层/告警层共用一处判定）。

    判定链（拍板口径：商品名即锚，款号/变体不重新观察）：
    1. 排期开始时间可解析 且 已跑≥12h → 排期证老（age 取排期，source='schedule'，
       不依赖档案，解决档案缺失老品被误判新品）；
    2. 排期可解析但显新（<12h）→ 查锚档案纠偏：锚档案首见已≥12h → 取档案首见
       age（source='archive'，重建/变体计划即使排期新，老品档案照常生效——
       修复“老品重建被误判新品”漏自动关）；锚档案缺失或同样新 → 仍按排期（新品）；
    3. 排期不可解析 → 锚档案可判则取档案（source='archive'），档案也无 → 
       (None, 'undetermined')（调用方按各自兜底策略处理）。

    副作用：首次见到的计划会以产品锚写入档案（ensure_plan_archived，12.7 建档保留）。
    返回 (age_h, source)：source ∈ {'schedule','archive','undetermined'}。
    """
    try:
        first_seen = ensure_plan_archived(plan_name)  # 锚键建档副作用
        sched_start = parse_schedule_start(schedule_text)
        if sched_start is not None:
            age_sched = max(0.0, (datetime.now() - sched_start).total_seconds() / 3600.0)
            if age_sched >= OPS_NEW_PRODUCT_HOURS:
                return age_sched, "schedule"
            # 排期显新 → 锚档案纠偏（老品重建/款号变体不重新观察）
            if first_seen:
                age_arch = product_age_hours(first_seen)
                if age_arch >= OPS_NEW_PRODUCT_HOURS:
                    return age_arch, "archive"
            return age_sched, "schedule"
        if first_seen:
            return product_age_hours(first_seen), "archive"
        return None, "undetermined"
    except Exception:
        return None, "undetermined"


def ops_decision_close(cfg, rec):
    """操作层自动关判定（两层规则表 12.4）。

    返回 (act, reason)：
      act="close" → 应自动关（后续仍须过无人值守闸门才真正执行）
      act="none"  → 不操作（新品学习窗口/新品有单/老品未达线/告警层只提醒）
    副作用：仅品名首见自动建档（12.7）。本函数不执行任何点击。
    """
    try:
        plan_name = rec.get("plan_name") or ""
        # tkm_10.23 锚定修复（替代 v10.21 排期优先段）：统一品龄口径——
        # 排期证老优先；排期显新时锚档案纠偏（重建/款号变体不重新观察）；
        # 档案与排期均不可用 → 保守不操作（宁关勿误由值守闸门兜底）。
        age_h, age_src = resolve_product_age(plan_name, rec.get("schedule"))
        if age_src == "undetermined":
            return "none", "品档案不可用且排期不可解析，跳过自动关（宁关勿误）"
        is_new = age_h < OPS_NEW_PRODUCT_HOURS
        orders = int(rec.get("cumulative_orders") or rec.get("orders") or 0)
        cost = float(rec.get("cumulative_cost") or rec.get("cost") or 0)
        revenue = float(rec.get("cumulative_revenue") or rec.get("revenue") or 0)
        roi_raw = rec.get("roi")
        roi = float(roi_raw) if roi_raw is not None else 0.0
        tag = "新品" if is_new else "老品"
        if is_new:
            # 2026-09-08 规则定稿：新品保护 = 满 12h 或烧够 $30 任一先到才转 ROI 评估；
            # $30 学习窗口内（无论有单无单）一律不关；烧够 $30 后无单 ROI=0 必关、
            # 有单 ROI≥0.60 留 / <0.60 关；出单不续期、12h 锚首铁窗口不延长。
            if cost < OPS_CLOSE_NEW_NO_ORDER_COST:
                if orders <= 0:
                    return "none", f"{tag}无单烧 ${cost:.2f} 学习窗口（<${OPS_CLOSE_NEW_NO_ORDER_COST:.0f} 不关，已跑{age_h:.1f}h）"
                return "none", f"{tag}有单烧 ${cost:.2f} 学习窗口内（<${OPS_CLOSE_NEW_NO_ORDER_COST:.0f} 不关）"
            if roi < OPS_CLOSE_OLD_HAS_ORDER_ROI:
                return "close", f"{tag}烧够 ${OPS_CLOSE_NEW_NO_ORDER_COST:.0f} ROI={roi:.2f}<{OPS_CLOSE_OLD_HAS_ORDER_ROI:.2f} 转评估关（已跑{age_h:.1f}h）"
            return "none", f"{tag}烧够 ${OPS_CLOSE_NEW_NO_ORDER_COST:.0f} ROI={roi:.2f}>={OPS_CLOSE_OLD_HAS_ORDER_ROI:.2f} 留"
        if orders <= 0:
            if cost >= OPS_CLOSE_OLD_NO_ORDER_COST:
                return "close", f"{tag}无单烧够 ${OPS_CLOSE_OLD_NO_ORDER_COST:.0f} 自动关（已跑{age_h:.1f}h）"
            return "none", f"{tag}无单烧 ${cost:.2f}（<${OPS_CLOSE_OLD_NO_ORDER_COST:.0f} 未达线）"
        if revenue <= 0 and cost >= OPS_CLOSE_OLD_NO_ORDER_COST:
            # 兜底：有订单标记但收入 0（数据延迟等）仍按无单口径防漏关
            return "close", f"{tag}订单计数异常（收入0）消耗 ${cost:.2f}，按无单达线关"
        if cost >= OPS_CLOSE_MIN_COST_GUARD and roi < OPS_CLOSE_OLD_HAS_ORDER_ROI:
            return "close", f"{tag}有单 ROI={roi:.2f}<{OPS_CLOSE_OLD_HAS_ORDER_ROI:.2f} 且消耗≥${OPS_CLOSE_MIN_COST_GUARD:.0f} 自动关"
        if roi < OPS_ALERT_OLD_HAS_ORDER_ROI:
            return "none", f"{tag}有单 ROI={roi:.2f}<{OPS_ALERT_OLD_HAS_ORDER_ROI} 仅告警提醒（操作层不动）"
        return "none", f"{tag}有单 ROI={roi:.2f} 正常"
    except Exception as e:
        return "none", f"操作层判定异常: {e}"


# ==================== v10.26：空闲/无人值守判定已外迁 idle_gate.py ====================
# 原 `_system_idle_seconds()`（GetLastInputInfo 键鼠空闲读取）与 `is_unattended(cfg)`
# （总开关 + 空闲阈值闸门）已收敛进统一「空闲判断机」模块 idle_gate.py，本文件不再保留
# 重复实现——全部调用点统一改为：
#     idle_gate.get_idle_sec()                                       → 空闲秒数
#     idle_gate.is_unattended(cfg, force=False, require_master=True) → (ok, why)
#     idle_gate.idle_threshold(cfg)                                  → 空闲阈值（默认300/下限10）
# 行为与旧实现等价（总开关关闭 → "自动操作总开关关闭"；空闲不足 → "有人值守/空闲不足(...)"）。


def last_auto_close_events(n=5):
    """最近自动关事件（供面板状态区自查）：从运行日志临时通道取——暂无独立存储，
    由调用方（UI 刷新）从内存 list 读取；此处保留占位以便后续扩展。"""
    return []

# ==================== v10.3：预设告警规则（统一入口·规则面板） ====================
# 与 alert_rules 同 schema（{id,name,kind,expr,message,enabled}），preset=True 标记来源。
# v10.7.1：核心告警条件/CVR低/消耗×客单全部收编为预设规则（原 check_alerts 硬编码分支删除）：
# ROI<0.6 走量底线 / ROI≥1.3 投放目标 / 累计消耗>300 / CVR低 / 消耗超客单倍数且无订单 /
# 空烧（收入0且成本超红线）/ 0收入豁免（收入0且未空烧时豁免 ROI/消耗类，避免 ROI=0 误报）。
# 表达式字段在 v10.2 白名单基础上新增 {roi_good}{cost_max}{cvr}{cvr_low}，随面板阈值联动。
# 首次运行/配置缺失时由 ensure_preset_rules 按 id 补齐（不覆盖用户编辑项）；
# 需还原全部默认时用面板「恢复预设」。
PRESET_RULES = [
    {"id": "preset_roi_low", "name": "ROI低于走量底线", "kind": "alert",
     "expr": "{roi} < {threshold} and {revenue} > 0",
     "message": "ROI {roi:.2f} 低于走量底线 {threshold} → 建议暂停/控量",
     "enabled": True, "preset": True},
    {"id": "preset_cost_max", "name": "累计消耗超上限", "kind": "alert",
     "expr": "{cost} > {cost_max}",
     "message": "累计消耗 ${cost:.2f} 超过 {cost_max} USD → 控制预算",
     "enabled": True, "preset": True},
    # v10.21（2026-09-06 合主）：preset_roi_good / preset_cvr_low 已废除（决策日志 2026-09-06），
    # preset_cost_x_aov 表达式加 {aov} > 0 前置保护拆 aov=0.0 恒真雷（决策 G4）。
    {"id": "preset_cost_x_aov", "name": "消耗超客单倍数且无订单", "kind": "alert",
     "expr": "{aov} > 0 and {cost} > {aov} * {cost_x} and {orders} == 0",
     "message": "消耗 ${cost:.2f} > 客单价 ${aov:.2f} × {cost_x} 且无订单 → 建议止损",
     "enabled": True, "preset": True},
    {"id": "preset_burn", "name": "空烧检测", "kind": "alert",
     "expr": "{revenue} == 0 and {cost} > {burn_redline}",
     "message": "空烧：收入 0 且消耗 ${cost:.2f} 超过红线 ${burn_redline} → 立即停投",
     "enabled": True, "preset": True},
    {"id": "preset_zero_revenue_exempt", "name": "0收入豁免", "kind": "exempt",
     "expr": "{revenue} == 0 and {cost} <= {burn_redline}",
     "message": "",
     "enabled": True, "preset": True},
]


def ensure_preset_rules(cfg):
    """确保 cfg['preset_rules'] 至少包含全部内置预设（v10.7.1：按 id 补缺失，不覆盖用户已编辑项）。

    - 字段缺失/非列表：注入全部内置预设。
    - 已存在但缺某条内置 id：仅追加缺失项，保留用户已有的编辑/停用/删除。
    - 已含全部内置 id：不覆盖用户修改，直接返回 False。
    需还原全部默认时由面板「恢复预设」写入完整内置列表。返回 True 表示发生了注入/补齐。
    """
    cur = cfg.get("preset_rules")
    if isinstance(cur, list) and cur:
        have_ids = {r.get("id") for r in cur if isinstance(r, dict)}
        missing = [copy.deepcopy(p) for p in PRESET_RULES if p["id"] not in have_ids]
        if not missing:
            return False
        cfg["preset_rules"] = cur + missing
    else:
        try:
            cfg["preset_rules"] = copy.deepcopy(PRESET_RULES)
        except Exception:
            cfg["preset_rules"] = [dict(r) for r in PRESET_RULES]
    return True


# ==================== v10.2：自定义告警规则引擎 ====================
# 字段白名单：{roi}{cost}{revenue}{orders}{plan_name}{shop}{aov}{cost_prev}
#             {revenue_prev}{burn_redline}{threshold}{cost_x}
# v10.3 增：{roi_good}{cost_max}（预设规则 ROI 达标/消耗上限联动面板阈值）。
# kind=alert 命中则告警；kind=exempt 命中则跳过该行告警（实现 0 收入豁免/延迟告警）。
# 本地自用可接受受限 eval：先正则校验仅允许白名单字段+运算符+数字+括号+引号，再 eval，
# try/except 全包裹，单条规则失败不影响其他规则且日志记录该规则 id。
RULE_ALLOWED_RE = re.compile(r"^[\s\w+\-*/%<>=!()&|.,:'\"]+$")


def _rule_env(cfg, rec, records, shop):
    """构建规则求值环境（字段名到值的映射；字段缺失给 0/''，cvr 无数据给 None）。"""
    records = records or []
    plan_name = str(rec.get("plan_name") or rec.get("name") or "未命名计划").strip()
    prev = None
    for r in reversed(records):
        if (str(r.get("plan_name") or r.get("name") or "").strip() == plan_name
                and r.get("time") != rec.get("time")):
            prev = r
            break
    roi = rec.get("roi") or 0
    cost = rec.get("cumulative_cost") or 0
    revenue = rec.get("cumulative_revenue") or 0
    orders = rec.get("cumulative_orders") or 0
    aov = rec.get("avg_order_value") or cfg.get("avg_order_value") or 0
    cvr = rec.get("conversion_rate")
    # tkm_10.23 锚定修复（替代 v10.21 in_grace 段）：口径与操作层 ops_decision_close 完全一致，
    # 共用 resolve_product_age——排期证老优先，排期显新时锚档案纠偏（重建/款号变体不重新观察），
    # 档案与排期均不可用 → in_grace=False（宁提醒不静默）。
    _in_grace = False
    try:
        _age_h, _age_src = resolve_product_age(plan_name, rec.get("schedule"))
        if _age_src != "undetermined":
            _is_new = _age_h < OPS_NEW_PRODUCT_HOURS
            _in_grace = bool(_is_new and float(cost) < float(cfg.get("grace_cost", 20.0) or 0))
    except Exception:
        _in_grace = False
    return {
        "roi": float(roi),
        "cost": float(cost),
        "revenue": float(revenue),
        "orders": float(orders),
        "plan_name": plan_name,
        "shop": str(shop or ""),
        "aov": float(aov),
        "cvr": float(cvr) if cvr is not None else None,
        "cost_prev": float(prev.get("cumulative_cost") or 0) if prev else 0.0,
        "revenue_prev": float(prev.get("cumulative_revenue") or 0) if prev else 0.0,
        "burn_redline": float(cfg.get("burn_redline", 5.0) or 0),
        "threshold": float(cfg.get("alert_threshold", 0.6) or 0),
        "cost_max": float(cfg.get("cost_max", 300.0) or 0),
        # v10.21（2026-09-06 合主）：preset_roi_good/preset_cvr_low 规则已废除，但 roi_good/cvr_low
        # 键保留——作为用户自定义规则表达式变量与 mini 展示阈值（决策：仅规则废除，变量不废）
        "roi_good": float(cfg.get("roi_good", 1.0) or 0),
        "cvr_low": float(cfg.get("cvr_low", 0.5) or 0),
        "cost_x": float(cfg.get("cost_x_aov", 2.0) or 0),
        "grace_cost": float(cfg.get("grace_cost", 20.0) or 0),
        "in_grace": _in_grace,
    }


def _safe_eval_rule(expr, env):
    """受限求值单条规则表达式。

    先做 {字段} 占位符替换（字段必须来自白名单 env，字符串自动加引号），
    再校验替换后表达式（字符白名单 + 标识符白名单），最后 eval（禁用 __builtins__）。
    任何异常返回 (False, err_text)。单条失败不影响其它规则。
    """

    def _sub(m):
        name = m.group(1)
        if name not in env:
            raise ValueError("未知字段 {" + name + "}")
        v = env[name]
        if isinstance(v, str):
            return repr(v)
        return repr(v)  # 数值/None/bool 统一 repr（repr(True)=='True'，v10.7.1 删除冗余 bool 分支）

    expr = (expr or "").strip()
    if not expr:
        return False, "空表达式"
    try:
        expr2 = re.sub(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", _sub, expr)
    except ValueError as e:
        return False, str(e)
    if "{" in expr2 or "}" in expr2:
        return False, "含未识别占位符"
    if not RULE_ALLOWED_RE.match(expr2):
        return False, "含非法字符"
    try:
        atree = ast.parse(expr2, mode="eval")
    except SyntaxError as e:
        return False, f"语法错误 {e}"
    for node in ast.walk(atree):
        if isinstance(node, ast.Name) and node.id not in env:
            return False, f"未知字段 {node.id}"
        if isinstance(node, (ast.Call, ast.Attribute, ast.Subscript)):
            return False, "含不允许的调用/属性访问"
    try:
        val = eval(compile(atree, "<rule>", "eval"), {"__builtins__": {}}, {})
        return bool(val), None
    except Exception as e:
        return False, f"求值异常 {type(e).__name__}"


def eval_alert_rules(cfg, rec, records, shop=None):
    """按启用中的规则逐条求值：预设规则（preset_rules）+ 自定义规则（alert_rules）合并执行。

    返回 (exempt_hit, alert_texts, hit_preset_ids)：
    - exempt_hit=True：该行被豁免规则命中，应跳过该行告警；
    - alert_texts：kind=alert 且命中时生成的告警文本（含来源/规则名/message）；
    - hit_preset_ids：命中的预设规则 id 列表（供 check_alerts 跳过重复的核心硬编码告警）。
    单条规则求值失败不影响其它规则，失败信息追加进 alert_texts（带规则 id 便于排查）。
    """
    preset_rules = cfg.get("preset_rules") or []
    custom_rules = cfg.get("alert_rules") or []
    rules = [(r, True) for r in preset_rules] + [(r, False) for r in custom_rules]
    exempt_hit = False
    alert_texts = []
    hit_preset_ids = []
    env = _rule_env(cfg, rec, records, shop)
    for i, (rule, is_preset) in enumerate(rules):
        if not isinstance(rule, dict):
            continue
        if not rule.get("enabled", True):
            continue
        rid = str(rule.get("id") or f"rule{i + 1}")
        kind = str(rule.get("kind") or "alert")
        expr = str(rule.get("expr") or "").strip()
        name = str(rule.get("name") or rid)
        message = str(rule.get("message") or "").strip()
        hit, err = _safe_eval_rule(expr, env)
        if err:
            alert_texts.append(f"{'预设' if is_preset else '自定义'}规则[{rid}:{name}] 表达式异常（{err}），未参与判断")
            continue
        if not hit:
            continue
        if kind == "exempt":
            exempt_hit = True
            continue
        # v10.8：新计划宽松观察期——累计消耗未达 grace_cost 前，空烧/止损类预设只提示不喊停
        # （不进入 hit_preset_ids，故不触发核心告警模板与自动关停，仅推送提示）
        if is_preset and env.get("in_grace") and rid in ("preset_burn", "preset_cost_x_aov"):
            gc = env.get("grace_cost", 20.0)
            if rid == "preset_burn":
                hint = (f"空烧观察：收入 0 且消耗 ${env['cost']:.2f} "
                        f"（观察期 ${gc:.0f} 内）→ 暂不停投，继续观察")
            else:
                hint = (f"消耗 ${env['cost']:.2f} > 客单价 ${env['aov']:.2f}×{env['cost_x']:.1f} 且无订单"
                        f"（观察期 ${gc:.0f} 内）→ 暂不建议止损，继续观察")
            alert_texts.append(f"计划[{env.get('plan_name', '未命名计划')}] 预设规则[{name}] {hint}")
            continue
        if is_preset:
            hit_preset_ids.append(rid)
            prefix = f"预设规则[{name}]"
        else:
            prefix = f"自定义规则[{name}]"
        if message:
            try:
                fmt = {k: (round(v, 2) if isinstance(v, float) else v) for k, v in env.items()}
                msg = message.format(**fmt)
            except Exception:
                msg = message
            # v10.5：告警文本带计划名，避免推送只显示 ROI 数值看不出是哪条计划
            alert_texts.append(f"计划[{env.get('plan_name', '未命名计划')}] {prefix} {msg}")
        else:
            alert_texts.append(f"计划[{env.get('plan_name', '未命名计划')}] {prefix} 命中（{expr}）")
    return exempt_hit, alert_texts, hit_preset_ids


# ==================== v10.2：推送计数（余额估算） ====================
def _push_count_state(cfg, bump=False):
    """读取（可选 +1）本地推送计数状态，返回 (今日已发送, 剩余估算)。

    bump=True 时跨天清零并 +1 后落盘；bump=False 仅读取（跨天不落盘）。
    仅本地估算提示，以平台实际为准；PushPlus 免费 200 条/日（平台可能调整政策）。
    """
    today = time.strftime("%Y-%m-%d")
    pc = cfg.get("push_count") or {}
    if not isinstance(pc, dict):
        pc = {}
    if pc.get("date") != today:
        pc = {"date": today, "sent": 0}
    if bump:
        pc["sent"] = int(pc.get("sent") or 0) + 1
        cfg["push_count"] = pc
        try:
            save_config(cfg)
        except Exception:
            pass
    sent = int(pc.get("sent") or 0)
    return sent, max(0, 200 - sent)


def bump_push_count(cfg):
    """本地推送计数：跨天自动清零 +1，返回 (今日已发送, 剩余估算)。"""
    return _push_count_state(cfg, bump=True)


def _exclude_patterns_hit(cfg, rec):
    """任务B：排除规则命中判断（先排除后判阈值）。

    对 rec 中的计划名/ROI/成本/收入等可匹配字段逐一做 re.search，
    命中任意一条规则则返回 True（该行跳过告警）。规则空时恒为 False。
    """
    patterns = cfg.get("alert_exclude_patterns") or []
    if not patterns:
        return False
    fields = []
    if isinstance(rec, dict):
        for k in ("plan_name", "roi", "cost", "revenue", "cumulative_cost", "cumulative_revenue"):
            v = rec.get(k)
            if v is not None:
                fields.append(str(v))
    for p in patterns:
        if not p:
            continue
        try:
            rx = re.compile(p)
        except re.error:
            continue
        for s in fields:
            if rx.search(s):
                return True
    return False


def check_alerts(cfg, rec, records, shop=None):
    """
    按最新记录计算告警，返回 (告警文本列表, 核心条件是否命中, 核心告警文本或None)。

    v10.7.1：告警全部收编为规则引擎驱动——核心条件（ROI走量底线/ROI达标）、CVR低、
    消耗×客单、空烧、0收入豁免均以预设规则实现（preset_roi_low/preset_roi_good/
    preset_cvr_low/preset_cost_x_aov/preset_burn/preset_zero_revenue_exempt），
    check_alerts 不再维护硬编码分支；仅「连续 3 次订单未增长」保留硬编码（无法用预设表达）。
    alert_enabled 关闭时返回 ([], False, None)，由调用方决定"仅记录不推送"。

    任务B：排除规则（alert_exclude_patterns）命中任意一条则该行跳过告警（先排除后判阈值）。
    任务A：shop 为目标名，告警文本/模板可携带店铺名。
    """
    if not cfg.get("alert_enabled", True):
        return [], False, None
    # 任务B：先排除后判断阈值（逻辑闭环）
    if _exclude_patterns_hit(cfg, rec):
        return [], False, None
    # v10.3+：预设规则+自定义规则合并求值；exempt 命中则跳过该行告警（0 收入豁免/延迟告警）
    exempt_hit, rule_texts, hit_preset_ids = eval_alert_rules(cfg, rec, records, shop=shop)
    if exempt_hit:
        return [], False, None
    alerts = list(rule_texts)
    orders = rec.get("cumulative_orders") or 0

    # v10.7.1：连续 3 次订单未增长（订单数为 0 时跳过，避免旧版误报）——唯一保留的硬编码规则
    if orders > 0 and len(records) >= 3:
        last3 = records[-3:]
        if all(r.get("cumulative_orders") == last3[0].get("cumulative_orders") for r in last3):
            alerts.append(f"连续 {len(last3)} 次记录订单数未增长（{orders} 单）→ 检查素材/受众")

    # 核心告警判定：任一告警类预设命中即视为核心命中（供 run_alerts 选择核心模板渲染），
    # 与旧版"核心条件命中"语义对齐——ROI走量底线/消耗上限/消耗×客单/空烧。
    # v10.21（2026-09-06 合主）：preset_roi_good / preset_cvr_low 已废除，从 core_ids 剔除。
    core_ids = {"preset_roi_low", "preset_cost_max",
                "preset_cost_x_aov", "preset_burn"}
    core_text = None
    hit_core = [rid for rid in hit_preset_ids if rid in core_ids]
    if hit_core:
        name_by_id = {p["id"]: p["name"] for p in PRESET_RULES}
        for rid in hit_core:
            mark = f"预设规则[{name_by_id.get(rid, rid)}]"
            for t in alerts:
                if mark in t:
                    core_text = t
                    break
            if core_text:
                break
    return alerts, core_text is not None, core_text


# ==================== 主程序面板 ====================

# v10.7 布局重构：左右分栏已移除（原 DEFAULT_SASH_MAIN_RATIO/INFO_RATIO 废弃），
# 仅保留底部冻结日志默认高度比例（占整窗高度）
DEFAULT_LOG_HEIGHT_RATIO = 0.25


class FlowFrame(tk.Frame):
    """v10.6 布局框架：通用自动换行容器（place 布局，类似 CSS flex-wrap）。

    子控件用 add() 挂入；当可用宽度不足以容纳下一个控件时自动换行到下一行。
    自身 <Configure> 与子控件尺寸变化都会触发重排。
    """

    def __init__(self, master, **kw):
        super().__init__(master, **kw)
        self._flow_items = []          # [(widget, padx, pady)]
        self._flow_dirty = False
        self.bind("<Configure>", self._flow_relayout)

    def add(self, widget, padx=4, pady=2):
        """挂入一个子控件（自动重排）。"""
        widget._flow_entry = (padx, pady)
        self._flow_items.append(widget)
        try:
            widget.bind("<Configure>", self._flow_on_child_configure, add="+")
        except tk.TclError:
            pass
        self._flow_relayout()
        return widget

    def _flow_on_child_configure(self, _evt=None):
        self._flow_schedule()

    def _flow_schedule(self):
        if self._flow_dirty:
            return
        self._flow_dirty = True
        try:
            self.after_idle(self._flow_relayout)
        except tk.TclError:
            self._flow_dirty = False

    def _flow_relayout(self, _evt=None):
        self._flow_dirty = False
        try:
            self.update_idletasks()
        except Exception:
            return
        width = self.winfo_width()
        if width <= 1:
            # 容器尚未分配宽度（首次 add 时常见）：先按单行估算排布，等 <Configure> 再精确换行
            try:
                width = sum(w.winfo_reqwidth() + 8 for w in self._flow_items if w.winfo_exists()) or 100
            except Exception:
                width = 100
        x, y, row_h = 0, 0, 0
        for w in self._flow_items:
            try:
                if not w.winfo_exists():
                    continue
                rw = w.winfo_reqwidth()
                rh = w.winfo_reqheight()
            except tk.TclError:
                continue
            padx, pady = getattr(w, "_flow_entry", (4, 2))
            if x + rw + padx * 2 > width and x > 0:
                x = 0
                y += row_h + pady
                row_h = 0
            w.place(x=x + padx, y=y + pady, anchor="nw")
            x += rw + padx * 2
            row_h = max(row_h, rh)
        # v10.6.1 修复：place 子控件不贡献父容器请求尺寸，若不显式设置高度，
        # pack(fill="x") 的 FlowFrame 会被压成 0 高，按钮全部不可见。
        total_h = y + row_h
        if self._flow_items:
            _, last_pady = getattr(self._flow_items[-1], "_flow_entry", (4, 2))
            total_h += last_pady
        if getattr(self, "_flow_height", 0) != total_h:
            self._flow_height = total_h
            self.configure(height=total_h)
        # 容器请求高度随行数变化，父级（Canvas 等）需感知
        try:
            self.event_generate("<<FlowFrameResized>>")
        except Exception:
            pass


class MonitorApp:
    def __init__(self, root):
        self.root = root
        self.cfg = load_config()
        # v10.14.1 修复：lowkey 段缺失键用默认配置补齐（load_config 为浅合并，旧版
        # config.json 的 lowkey 整段覆盖默认值，导致 refresh_hotkey 等新键缺失、
        # 手动完整轮热键不注册）；合并后热键回落默认组合 ctrl+alt+shift+r
        _saved_lowkey = self.cfg.get("lowkey") or {}
        _default_lowkey = DEFAULT_CONFIG.get("lowkey") or {}
        self._lowkey = {**_default_lowkey, **_saved_lowkey}
        lk = self._lowkey
        # v10.8.9 低调名：主窗口/任务栏标题改用 lowkey.window_title（v10.14.4 起默认"tkm_10.23"），
        # 不再显示 "TikTok 广告监控 v10.8.8"；可在 config.json lowkey.window_title 自定义
        self.root.title(str(lk.get("window_title") or "tkm_10.26"))
        # v10.2 布局增强：默认窗口宽度放宽到 1280，左侧配置区不再被挤窄；
        # 用户保存的窗口几何在 _apply_layout 中恢复（window_layout.geometry）
        # v10.7.6 低调化：默认按 lowkey.default_geometry 小窗启动，不恢复历史大几何
        if lk.get("frameless", True):
            try:
                self.root.overrideredirect(True)  # 无边框无标题栏（启动即低调）
            except Exception:
                pass
        dgeo = str(lk.get("default_geometry") or "900x600")
        self.root.geometry(dgeo)
        # v10.7 布局重构：单列纵向排列，锁定最小窗口尺寸防按钮被挤出（FlowFrame 兜底换行）
        # v10.13：最小尺寸钳制开关——window_min_size_enabled=True 钳制 800x600，
        # False 不钳制（overrideredirect 窗口下系统 minsize 本就不生效，真正钳制在
        # 边缘拖拽 resize 逻辑里；此处同步设置以兼容非无边框模式）
        if self._min_size_enabled():
            self.root.minsize(800, 600)
        else:
            self.root.minsize(1, 1)
        self.monitor_running = False
        self.monitor_thread = None
        self._manual_busy = False  # 手动识别进行中标志（防重复点击叠加 OCR）
        # v10.26：无人达标「自动隐藏窗口 + 自动开始扫描」一次性触发标记（防重复触发；
        # 开关在 ③.5 面板，落盘键 auto_hide_scan_enabled，逻辑见 _check_auto_hide_and_scan）
        self._auto_hide_scan_done = False
        # v10.26：轮询间隔「刷新门控」状态机（空闲判断机 idle_gate.RefreshGate）——
        # 有人值守时挂起「待刷」，空闲达标后补偿刷新；手动路径（热键/立即识别/立即检测）直通。
        self._refresh_gate = idle_gate.RefreshGate()
        self.selected_hwnd = None   # 通过「列出窗口」双击锁定的窗口句柄，优先用于定位
        # 任务A：若发生旧配置→targets 迁移则立即落盘，保证 targets 结构真实持久化
        if _ensure_targets(self.cfg):
            save_config(self.cfg)
        # 任务A：多目标状态
        self.current_target_id = 1
        self._target_hwnds = {}       # 运行期窗口句柄 {target_id: hwnd}（不落盘，重启后按关键词回退）
        self._reset_monitor_timer = False  # v10.12：手动完整轮完成后置位，_monitor_loop 倒计时重置
        # tkm_10.23 守夜版：待值守执行自动关候选队列 + 共享锁 + 执行中防重入标记。
        # 候选由自动监控每轮识别后入队；无人值守闸门通过即由值守步进执行（不等下个识别轮）
        self._auto_close_pending = []
        self._auto_close_watch_lock = threading.Lock()
        self._auto_close_watching = False
        # 架构健康化A：识别器注册表（mod 通过 api.register_recog_mode 注册；核心按 recog_mode 分发）
        self._recog_registry = {}    # mode_id -> {name, recognize_fn, expand_fn, config_keys, mod_id}
        self._recog_menu = None      # 识别方式下拉 OptionMenu 引用（注册后动态刷新菜单项）
        # 任务C：板块折叠/隐藏状态（从 config 读取；collapsed=标题可见内容收起，hidden=整块不渲染）
        self.collapsed_sections = list(self.cfg.get("collapsed_sections") or [])
        self.hidden_sections = list(self.cfg.get("hidden_sections") or [])
        self._section_frames = {}     # {section_key: frame} 供视图菜单/折叠统一管理
        self._section_order = []      # 板块创建顺序（用于折叠/展开后恢复排列顺序）
        self._sec_arrows = {}         # {section_key: ▼/▶ 箭头 Label}
        self._coll_bars = {}          # {section_key: 折叠态标题行 Frame}（折叠后保留标题可见）
        # v10.5：UI 去 OCR 硬编码——保存 ROI 板块/启动提示语引用，
        # 由 _apply_section_capabilities 按识别器能力声明动态显隐/改文案
        self._sec_roi_frame = None    # ② ROI 识别区域板块 frame（能力含 "roi" 时显示）
        self._lbl_start_hint = None   # ③ 启动前提示语 Label
        # tkinter 按钮回调的异常默认被静默吞掉（打包成 exe 后无控制台更是什么都看不到），
        # 这里接管为写 error_log.txt + 日志区 + 弹窗，按钮点了没反应时能立刻看到原因
        self.root.report_callback_exception = self._on_callback_error
        # 任务B：日志分级状态（默认结果级显示；_log_lines 记录全量行供切换等级后重绘）
        self._log_min_level = 3            # 显示等级阈值：result=3 / info=2 / debug=1
        self._log_lines = []               # [(ts, level, msg), ...] 全量记录（切换等级后重绘用）

        # v10.7 布局重构：单列纵向排列——板块顺序固定为 _section_keys + sec_last（不再有左右归属）
        wl = self.cfg.get("window_layout") or {}
        self._section_keys = ["sec_win", "sec_roi", "sec_mode", "sec_ctrl", "sec_auto_close",
                              "sec_th", "sec_sc", "sec_rr", "sec_ord", "sec_push", "sec_archive"]
        self._log_height_ratio = float(wl.get("log_height_ratio", DEFAULT_LOG_HEIGHT_RATIO) or DEFAULT_LOG_HEIGHT_RATIO)
        self._sash_log_ratio = float(wl.get("sash_log_ratio", self._log_height_ratio) or self._log_height_ratio)
        # v10.7 任务2：简略模式小窗状态（无边框置顶，识别成功后刷新）
        self._mini_win = None
        self._mini_drag = None
        self.last_ocr_rows = []        # 最近一次识别全行明细（plan_name/roi/cost/revenue/orders）
        self.last_ocr_target = ""      # 最近一次识别目标名

        self._build_ui()
        self._apply_config_to_ui()
        self.log("[监控] 已启动 tkm_10.26")
        # 启动时加载 Mod（扫描/导入/注册全程错误隔离，单个 mod 出错不影响核心）
        load_mods(self)
        # v10.5：识别器加载完成后按能力声明刷新 UI 显隐/文案（UIA 无 ROI 需求则隐藏 ROI 板块）
        self._apply_section_capabilities()
        # 使用步骤提示随识别器能力动态输出（UIA 模式无需 ROI 框选步骤，不再硬编码 OCR 步骤）
        roi_visible = self._sec_roi_frame is not None and self._sec_roi_frame.winfo_manager() != ""
        if roi_visible:
            self.log("使用步骤：1.打开 TikTok Shop 商户中心后台 2.标定/填写 ROI 区域 3.填写推送 Key 4.启动自动监控")
        else:
            self.log("使用步骤：1.打开 TikTok Shop 商户中心后台 2.填写推送 Key 3.启动自动监控（当前识别器无需 ROI 框选）")
        # ===== v10.7.6 低调化启动逻辑 =====
        # 无边框主窗口：极简拖动条 + 空白处拖动 + 右键菜单（低调退出/隐藏/mini）
        self._setup_frameless_chrome()
        # 全局热键（魔法按键）：极端模式必须；托盘模式也注册以便热键可用
        self._hotkey_id = 0x7610      # 呼出/隐藏主窗口
        self._quit_hotkey_id = 0x7611  # 低调退出
        self._refresh_hotkey_id = 0x7612  # v10.12：手动完整轮（ctrl+alt+shift+r）
        self._show_hotkey_id = 0x7613     # v10.16.2：启动自动监控（ctrl+alt+shift+f，不显示窗口）
        self._hide_hotkey_id = 0x7614     # v10.16.2：停止自动监控（ctrl+alt+shift+c，不隐藏窗口）
        self._hotkey_ok = False
        # v10.8.1：热键改由独立线程+隐藏消息窗口接收（tk 主循环会抢先取走并丢弃
        # 线程级 WM_HOTKEY，导致其他程序前台时热键无效）；信号经线程安全队列转交主线程
        self._hk_queue = queue.Queue()     # 热键线程 → 主线程
        self._si_queue = queue.Queue()     # 单实例监听线程 → 主线程
        self._hk_hwnd = None
        self._hk_hwnd_ready = threading.Event()
        self._hk_reg_ready = threading.Event()          # v10.8.1：热键线程内注册完成信号
        self._hk_reg_msgs = []                          # v10.8.1：热键线程内注册结果日志
        self._hk_thread_id = None
        self._hk_thread = None
        self._start_hotkey_thread()
        try:
            self._register_hotkeys()
        except Exception as exc:
            self.log(f"[热键注册失败] {exc}")
        # 轮询热键/单实例信号（200ms，从线程安全队列取用，不干扰 tk 消息循环）
        self.root.after(200, self._poll_hotkeys)
        self.root.after(200, self._poll_single_instance)
        # 启动形态：mini 默认启动 / 随启动打开 mini 小窗
        mode = str(self._lowkey.get("launch_mode") or "normal")
        if mode == "mini" or self._lowkey.get("mini_on_start", False):
            self.root.after(200, self._open_mini_window)
            if mode == "mini":
                self.root.after(350, self._hide_main_window)
        # v10.8.0 单实例：后台监听"唤出"事件（重复打开 exe 时由新进程触发，本实例呼出主窗口）
        self._start_single_instance_listener()

    def _start_single_instance_listener(self):
        """单实例激活监听：后台线程等待命名事件，收到信号后写入线程安全队列，
        由主线程 _poll_single_instance 轮询执行呼出（不跨线程直接调 tkinter）。

        与 main() 中 check_single_instance() 配套：新进程检测到本实例存在时 SetEvent，
        即使本实例处于隐藏/低调模式（withdrawn/mini）也会恢复显示并前置聚焦。
        v10.8.1：事件按 manual-reset 语义处理（收到后先 ResetEvent 再入队），避免
        SetEvent 时本线程短暂未在等待导致 auto-reset 信号丢失；监听线程异常不退出。
        """
        def _listen():
            import ctypes
            kernel32 = ctypes.windll.kernel32
            try:
                h_evt = kernel32.OpenEventW(0x001F0003, False, SINGLE_INSTANCE_EVENT)
                if not h_evt:
                    return
            except Exception:
                return
            try:
                while True:
                    try:
                        r = kernel32.WaitForSingleObject(h_evt, -1)  # 无限等待
                    except Exception:
                        break
                    if r != 0:  # WAIT_OBJECT_0 之外（出错/放弃）则退出监听
                        break
                    # manual-reset 语义：先复位再入队，信号不因短暂无人处理而丢失
                    try:
                        kernel32.ResetEvent(h_evt)
                    except Exception:
                        pass
                    try:
                        self._si_queue.put("activate")
                    except Exception:
                        pass
            finally:
                try:
                    kernel32.CloseHandle(h_evt)
                except Exception:
                    pass
        try:
            t = threading.Thread(target=_listen, daemon=True)
            t.start()
        except Exception:
            pass

    def _poll_single_instance(self):
        """主线程轮询单实例唤起信号（200ms）：收到后恢复显示并置顶聚焦。"""
        try:
            while True:
                try:
                    item = self._si_queue.get_nowait()
                except queue.Empty:
                    break
                if item == "activate":
                    self.log("收到唤起，恢复窗口")
                    self._show_main_window(force=True)
        except Exception:
            pass
        try:
            self.root.after(200, self._poll_single_instance)
        except Exception:
            pass

    def _on_callback_error(self, exc, val, tb):
        """按钮回调异常处理：写 error_log.txt + 日志区提示 + 弹窗"""
        try:
            with open(BASE_DIR / "error_log.txt", "a", encoding="utf-8") as f:
                f.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] 按钮回调异常:\n")
                traceback.print_exception(exc, val, tb, file=f)
                f.write("\n")
        except Exception:
            pass
        try:
            self.log(f"[回调异常] {val}")
        except Exception:
            pass
        try:
            mb.showerror("TikTok监控 v10 出错", f"按钮处理出错，详情已写入 error_log.txt\n\n{val}")
        except Exception:
            pass

    # ---------- UI 构建 ----------
    def _build_ui(self):
        pad = {"padx": 8, "pady": 3}

        # ===== 视图菜单（Alt 可激活，符合老程序菜单习惯）=====
        self._build_view_menu()

        # ===== v10.7 布局框架：最外层垂直分栏 pw_root（上部=主滚动区，下部=冻结日志）=====
        # 日志冻结底部：pw_root pane0=left_pane（主内容滚动区，单列全板块），pane1=f_log（横跨全宽独立滚动）
        self.pw_root = ttk.PanedWindow(self.root, orient="vertical")
        self.pw_root.pack(fill="both", expand=True)
        # ===== 主内容区：单列纵向（v10.7：去掉左右分栏与 sash，Canvas + 内部 Frame 全宽滚动）=====
        self.left_pane = tk.Frame(self.pw_root)
        self.pw_root.add(self.left_pane, weight=1)
        self.canvas = tk.Canvas(self.left_pane, highlightthickness=0)
        self.sb_main = tk.Scrollbar(self.left_pane, orient="vertical", command=self.canvas.yview)
        self.main_frame = tk.Frame(self.canvas)
        self._canvas_win = self.canvas.create_window((0, 0), window=self.main_frame, anchor="nw")
        self.canvas.configure(yscrollcommand=self.sb_main.set)
        self.sb_main.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        # 内部 Frame 尺寸变化 -> 刷新滚动区域；Canvas 宽度变化 -> 内部 Frame 宽度跟随
        self.main_frame.bind("<Configure>", self._on_main_frame_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        # 滚轮：面板任意位置滚动主面板（日志区自身绑定优先，鼠标在日志区时不触发这里）
        self.canvas.bind("<MouseWheel>", self._on_canvas_mousewheel)
        self.sb_main.bind("<MouseWheel>", self._on_canvas_mousewheel)
        self._bind_panel_wheel_recursive(self.main_frame)
        # mod 按钮行（mod 通过 api.register_menu 注册按钮时创建，默认不占空间）
        self._mod_menu_frame = None
        # sash 拖动释放 -> 防抖持久化布局（仅 pw_root 底部日志 sash）
        self.pw_root.bind("<ButtonRelease-1>", self._on_sash_released)

        # ===== 任务A：多目标切换条（顶部，朴素文案，低调）=====
        f_tgt = tk.LabelFrame(self.main_frame, text="监控目标（多店铺）", padx=8, pady=5)
        f_tgt.pack(fill="x", **pad)
        self._target_bar = tk.Frame(f_tgt)
        self._target_bar.pack(fill="x", padx=2, pady=2)
        tk.Label(self._target_bar, text="每目标独立 窗口/ROI/告警", fg="gray").pack(side="right", padx=6)
        row2 = tk.Frame(f_tgt)
        row2.pack(fill="x", padx=2, pady=2)
        tk.Button(row2, text="+ 添加目标", command=self.add_target, width=10).pack(side="left", padx=2)
        tk.Button(row2, text="重命名", command=self.rename_target, width=8).pack(side="left", padx=2)
        tk.Button(row2, text="删除目标", command=self.delete_target, width=10).pack(side="left", padx=2)
        # v10.2 任务2：每个目标独立启用/禁用自动监控（新增目标默认未启用，用户确认后再开）
        tk.Button(row2, text="启用/禁用当前目标", command=self.toggle_target_enabled, width=14).pack(side="left", padx=2)
        self._refresh_target_bar()

        # 顶部：窗口关键词
        f_top = self._mk_section("① 窗口设置", "sec_win")
        # v10.2 布局增强：长行拆为两行，避免左侧窄时按钮被挤出可视区
        row_a = tk.Frame(f_top)
        row_a.pack(fill="x")
        tk.Label(row_a, text="窗口标题关键词:").pack(side="left")
        self.e_window = tk.Entry(row_a, width=26)
        self.e_window.pack(side="left", padx=5)
        tk.Button(row_a, text="保存", command=self.save_window_keyword, width=8).pack(side="left", padx=2)
        self.lbl_win = tk.Label(row_a, text="", fg="gray")
        self.lbl_win.pack(side="left", padx=10)
        row_b = tk.Frame(f_top)
        row_b.pack(fill="x", pady=(2, 0))
        tk.Button(row_b, text="查找窗口", command=self.test_window, width=8).pack(side="left", padx=2)
        tk.Button(row_b, text="列出窗口", command=self.show_window_list, width=8, bg="#2196F3", fg="white").pack(side="left", padx=2)
        tk.Label(row_b, text="在下方输入框填写页面标题关键词后点「保存」，再点「查找窗口」定位", fg="#666").pack(side="left", padx=8)

        # ROI 区
        f_roi = self._mk_section("② ROI 识别区域（相对窗口坐标）", "sec_roi")
        # v10.2 布局增强：按钮行与坐标输入行拆分
        row_a = tk.Frame(f_roi)
        row_a.pack(fill="x")
        tk.Button(row_a, text="框选 ROI（鼠标拖拽）", command=self.calibrate_roi, width=20, bg="#FF9800", fg="white").pack(side="left", padx=5)
        tk.Button(row_a, text="保存坐标", command=self.save_roi, width=10).pack(side="left", padx=5)
        self.lbl_roi = tk.Label(row_a, text="未设置", fg="red")
        self.lbl_roi.pack(side="left", padx=8)
        row_b = tk.Frame(f_roi)
        row_b.pack(fill="x", pady=(2, 0))
        tk.Label(row_b, text="左:").pack(side="left")
        self.e_rl = tk.Entry(row_b, width=6)
        self.e_rl.pack(side="left")
        tk.Label(row_b, text="上:").pack(side="left")
        self.e_rt = tk.Entry(row_b, width=6)
        self.e_rt.pack(side="left")
        tk.Label(row_b, text="宽:").pack(side="left")
        self.e_rw = tk.Entry(row_b, width=6)
        self.e_rw.pack(side="left")
        tk.Label(row_b, text="高:").pack(side="left")
        self.e_rh = tk.Entry(row_b, width=6)
        self.e_rh.pack(side="left")
        tk.Label(row_b, text="（框选后自动填入，可手改再保存）", fg="#666").pack(side="left", padx=8)

        # 识别方式区（v10.1：识别模式/滚动收集从识别规则底部上提，先选识别方式→再启动监控）
        f_mode = self._mk_section("②.5 识别方式（保存后生效）", "sec_mode")
        # v10.2 布局增强：控件行与说明行拆分
        row_a = tk.Frame(f_mode)
        row_a.pack(fill="x", pady=2)
        tk.Label(row_a, text="识别模式:").pack(side="left", padx=(8, 2), pady=2)
        self.var_recog_mode = tk.StringVar(value=str(self.cfg.get("recog_mode", "uia")))
        # 架构健康化A：下拉选项由已注册识别器动态填充（mod 通过 register_recog_mode 注册，无需改核心）
        self._recog_menu = tk.OptionMenu(row_a, self.var_recog_mode, "")
        self._recog_menu.pack(side="left", pady=2)
        self._refresh_recog_menu()
        self.var_uia_scroll = tk.BooleanVar(value=bool(self.cfg.get("uia_scroll_collect", False)))
        tk.Checkbutton(row_a, text="滚动收集(UIA懒加载对策)", variable=self.var_uia_scroll).pack(side="left", padx=8, pady=2)
        tk.Button(row_a, text="保存识别方式", command=self.save_recog_mode, width=14).pack(side="left", padx=8, pady=2)
        row_b = tk.Frame(f_mode)
        row_b.pack(fill="x", pady=(0, 2))
        tk.Label(row_b, text="识别器由 mods 目录提供；滚动收集:边滚边读合并文本", fg="#666").pack(side="left", padx=4, pady=2)

        # 监控控制区
        f_ctrl = self._mk_section("③ 自动监控", "sec_ctrl")
        # v10.2 布局增强：启动/停止主按钮与次要操作拆分
        # v10.6 布局框架：按钮行改挂 FlowFrame（place 自动换行，过窄不截断）
        row_a = FlowFrame(f_ctrl)
        row_a.pack(fill="x")
        self.lbl_interval = tk.Label(row_a, text="轮询间隔（分钟，≥1）:")
        row_a.add(self.lbl_interval)
        self.e_interval = tk.Entry(row_a, width=8)
        row_a.add(self.e_interval, padx=5)
        tk.Button(row_a, text="保存间隔", command=self.save_interval, width=10)
        row_a.add(row_a.winfo_children()[-1], padx=2)
        self.btn_start = tk.Button(row_a, text="启动自动监控", command=self.start_monitor, width=16, bg="#F44336", fg="white")
        row_a.add(self.btn_start, padx=8)
        self.btn_stop = tk.Button(row_a, text="停止监控", command=self.stop_monitor, width=12, state="disabled")
        row_a.add(self.btn_stop, padx=2)
        row_b = FlowFrame(f_ctrl)
        row_b.pack(fill="x", pady=(2, 0))
        # v10.14.3：「立即识别一次」先刷新再识别——与自动监控同一"刷新后扫描"语义（do_ocr_once 内先 _send_refresh_to_target）
        tk.Button(row_b, text="立即识别一次", command=self.do_ocr_once, width=12)
        row_b.add(row_b.winfo_children()[-1], padx=2)
        # v10.14.7：「测试刷新」按钮已删除（刷新链路已验证稳定，后续实验走独立测试程序，§7.3）
        # v10.14.3：刷新浏览器勾选框——默认勾选=每轮识别前先刷新浏览器（绑定 refresh_enabled）；取消勾选则跳过刷新直接识别
        self.var_refresh = tk.BooleanVar(value=bool(self.cfg.get("refresh_enabled", True)))
        tk.Checkbutton(row_b, text="刷新浏览器", variable=self.var_refresh)
        row_b.add(row_b.winfo_children()[-1], padx=8)
        self._lbl_start_hint = tk.Label(row_b, text="「启动自动监控」前请先完成：窗口关键词→ROI→识别方式", fg="#666")
        row_b.add(self._lbl_start_hint, padx=8)

        # v10.21 修复轮（2026-09-06）：自动操作区 sec_auto_close（设计文档 13.2）——
        # 总开关 auto_ops_enabled（默认关，宁关勿开）+ 无人值守秒数 Spinbox + 状态只读区。
        f_auto_close = self._mk_section("③.5 自动操作（无人值守自动关）", "sec_auto_close")
        row_a = tk.Frame(f_auto_close)
        row_a.pack(fill="x", pady=2)
        self.var_auto_ops_enabled = tk.BooleanVar(value=bool(self.cfg.get("auto_ops_enabled", False)))
        tk.Checkbutton(row_a, text="启用自动操作（总开关；仅无人值守时才可能执行自动关，默认关）",
                       variable=self.var_auto_ops_enabled).pack(side="left", padx=(6, 2))
        # v10.26 增量：only_active_plans 暴露到面板可调（此前仅由 cfg 隐式消费、无任何 UI 控件）。
        # 勾选=仅处理「已生效」计划行，非已生效行在结构化识别阶段被过滤并打日志
        # （_collect_rows_* 读 cfg["only_active_plans"] → cands/rejects，见 5945/5994）。
        row_a2 = tk.Frame(f_auto_close)
        row_a2.pack(fill="x", pady=2)
        self.var_only_active_plans = tk.BooleanVar(value=bool(self.cfg.get("only_active_plans", True)))
        tk.Checkbutton(row_a2, text="仅处理已生效计划（过滤非已生效行；取消勾选则非已生效行也参与识别）",
                       variable=self.var_only_active_plans).pack(side="left", padx=(6, 2))
        row_b = tk.Frame(f_auto_close)
        row_b.pack(fill="x", pady=2)
        tk.Label(row_b, text="无人值守无操作秒数:").pack(side="left", padx=(8, 2))
        self.spin_auto_ops_idle = tk.Spinbox(row_b, from_=10, to=86400, increment=10, width=8)
        self.spin_auto_ops_idle.delete(0, "end")
        self.spin_auto_ops_idle.insert(0, str(int(self.cfg.get("auto_ops_unattended_idle_sec", 300) or 300)))
        self.spin_auto_ops_idle.pack(side="left")
        tk.Button(row_b, text="保存自动操作设置", command=self.save_auto_ops_settings, width=18).pack(side="left", padx=10)
        # v10.26 增量：无人达标自动隐藏窗口并开始扫描（防“打开程序忘记点开始监控”）。
        # 独立开关，不受上面 auto_ops_enabled 总开关约束；为真且键鼠空闲 ≥ 无人值守秒数、
        # 且自动监控未运行时，赋值式隐藏主窗口并调用现有 start_monitor 开始扫描
        # （检测循环复用 _tick_auto_ops_status → _check_auto_hide_and_scan）。
        row_b2 = tk.Frame(f_auto_close)
        row_b2.pack(fill="x", pady=2)
        self.var_auto_hide_scan = tk.BooleanVar(value=bool(self.cfg.get("auto_hide_scan_enabled", False)))
        tk.Checkbutton(row_b2, text="无人达标自动隐藏窗口并开始扫描（独立开关，不受总开关约束；默认关）",
                       variable=self.var_auto_hide_scan).pack(side="left", padx=(6, 2))
        # v10.24：「立即检测」——强制完整执行自动关链路（读表格→识别→判定→应关即真关）。
        # 复用 _monitor_round→run_alerts→ops_decision_close→_auto_close_store_pending→
        # _auto_close_watchdog(force=True)，跳过无人值守闸门，工位有人也执行。
        row_c = FlowFrame(f_auto_close)
        row_c.pack(fill="x", pady=(0, 2))
        tk.Button(row_c, text="立即检测", command=self.force_auto_close_check_now, width=14)
        row_c.add(row_c.winfo_children()[-1], padx=4)
        tk.Label(row_c, text="强制完整执行：读表格→识别→判定→应关即真关（跳过无人值守闸门，有人值守也执行，不等下轮）", fg="#666")
        row_c.add(row_c.winfo_children()[-1], padx=4)
        st_auto = tk.LabelFrame(f_auto_close, text="状态（只读，每 2 秒自动刷新）", padx=6, pady=2)
        st_auto.pack(fill="x", padx=4, pady=(2, 4))
        self.lbl_ac_idle = tk.Label(st_auto, text="键鼠空闲：— 秒", anchor="w")
        self.lbl_ac_idle.pack(fill="x")
        self.lbl_ac_unattended = tk.Label(st_auto, text="无人值守：—", anchor="w")
        self.lbl_ac_unattended.pack(fill="x")
        self.lbl_ac_last = tk.Label(st_auto, text="最近自动关事件：（无）", anchor="w", justify="left")
        self.lbl_ac_last.pack(fill="x")
        self._auto_ops_tick_id = self.root.after(2000, self._tick_auto_ops_status)

        # 告警规则区（v10.7.1：统一入口——核心条件/CVR/消耗×客单/前置消耗全部收编为预设规则，
        # 删除旧指标/比较/阈值输入区与 th_entries，仅保留总开关/冷却/模板/排除入口）
        f_th = self._mk_section("④ 告警规则（统一入口）", "sec_th")
        self.var_alert = tk.BooleanVar(value=bool(self.cfg.get("alert_enabled", True)))
        tk.Checkbutton(f_th, text="启用告警（关闭后只记录、不推送）", variable=self.var_alert).grid(
            row=0, column=0, columnspan=6, sticky="w", padx=5, pady=2)
        tk.Label(f_th, text="告警推送文案模板（可用 {time} {shop} {plan_name} {roi} {cost} {revenue} {threshold} {cvr}）:").grid(
            row=1, column=0, columnspan=6, sticky="w", padx=(8, 2), pady=2)
        self.txt_alert_template = tk.Text(f_th, width=72, height=4, font=("Consolas", 9))
        self.txt_alert_template.grid(row=2, column=0, columnspan=6, sticky="we", padx=8, pady=2)
        # v10.6 布局框架：保存/排除规则按钮行挂 FlowFrame（过窄自动换行）
        row_alert_btns = FlowFrame(f_th)
        row_alert_btns.grid(row=3, column=0, columnspan=6, sticky="w", padx=4, pady=4)
        tk.Button(row_alert_btns, text="保存告警设置", command=self.save_alert_basic, width=14)
        row_alert_btns.add(row_alert_btns.winfo_children()[-1], padx=4)
        # 任务B：告警排除规则入口（风格同「编辑跳过词」，规则由用户自写）
        self.btn_exclude_rules = tk.Button(row_alert_btns, text="", width=16, command=self.open_exclude_rules_editor)
        row_alert_btns.add(self.btn_exclude_rules, padx=4)
        tk.Label(row_alert_btns, text="排除规则：命中任意一条则跳过该行告警（先排除、后判规则）", fg="#666")
        row_alert_btns.add(row_alert_btns.winfo_children()[-1], padx=8)
        # v10.3 任务1：规则统一入口（预设规则 + 自定义规则同窗管理；v10.7.1 预设覆盖核心/CVR/消耗×客单/前置消耗）
        self.btn_rules = tk.Button(f_th, text="", width=24, command=self.open_rules_editor)
        self.btn_rules.grid(row=5, column=0, columnspan=3, sticky="w", padx=8, pady=4)
        tk.Label(f_th, text="规则：预设(ROI低/达标/消耗上限/CVR低/消耗×客单/空烧/0收入豁免)+自定义统一管理", fg="#666", anchor="w").grid(
            row=5, column=3, columnspan=3, sticky="w", padx=8, pady=4)

        # 识别条件区（严格校验，面板可配置，错误14）
        f_sc = self._mk_section("④.5 识别条件（保存后生效）", "sec_sc")
        self.var_strict = tk.BooleanVar(value=bool(self.cfg.get("strict_enabled", True)))
        tk.Checkbutton(f_sc, text="启用严格校验", variable=self.var_strict).grid(row=0, column=0, sticky="w", padx=5)
        self.var_consist = tk.BooleanVar(value=bool(self.cfg.get("roi_consistency", True)))
        tk.Checkbutton(f_sc, text="ROI≈收入/成本校验", variable=self.var_consist).grid(row=0, column=2, sticky="w", padx=5)
        self.sc_entries = {}
        sc_items = [
            ("roi_min", "ROI最小"),
            ("roi_max", "ROI最大"),
            ("cost_max_val", "成本上限$"),
            ("revenue_max_val", "收入上限$"),
            ("roi_consistency_tol", "一致性容差%"),
        ]
        for i, (key, label) in enumerate(sc_items):
            row, col = divmod(i, 3)
            tk.Label(f_sc, text=label + ":").grid(row=row + 1, column=col * 2, sticky="w", padx=(8, 2), pady=2)
            e = tk.Entry(f_sc, width=10)
            e.grid(row=row + 1, column=col * 2 + 1, sticky="w", pady=2)
            self.sc_entries[key] = e
        tk.Label(f_sc, text="找不到表头时:").grid(row=3, column=0, sticky="w", padx=(8, 2), pady=2)
        self.var_no_header = tk.StringVar(value=str(self.cfg.get("no_header_policy", "fail")))
        tk.Radiobutton(f_sc, text="识别失败并提示", variable=self.var_no_header, value="fail").grid(row=3, column=1, sticky="w")
        tk.Radiobutton(f_sc, text="宽松解析兜底", variable=self.var_no_header, value="loose").grid(row=3, column=2, sticky="w")
        # v10.6 布局框架：保存按钮行挂 FlowFrame
        row_sc_btns = FlowFrame(f_sc)
        row_sc_btns.grid(row=4, column=0, columnspan=4, sticky="w", padx=4, pady=4)
        tk.Button(row_sc_btns, text="保存识别条件", command=self.save_strict_conditions, width=14)
        row_sc_btns.add(row_sc_btns.winfo_children()[-1], padx=4)

        # 识别规则区（错误15：框很大时限定"读哪里/怎么读"，面板可配置）
        f_rr = self._mk_section("④.6 识别规则（保存后生效）", "sec_rr")
        self.rr_entries = {}
        tk.Label(f_rr, text="表头关键词:").grid(row=0, column=0, sticky="w", padx=(8, 2), pady=2)
        rr_kw_items = [("header_col_name", "计划名列"), ("header_col_roi", "ROI列"),
                       ("header_col_cost", "成本列"), ("header_col_rev", "收入列")]
        for i, (key, label) in enumerate(rr_kw_items):
            col = i * 2 + 1
            tk.Label(f_rr, text=label).grid(row=0, column=col, sticky="w", padx=(8, 2), pady=2)
            e = tk.Entry(f_rr, width=13)
            e.grid(row=0, column=col + 1, sticky="w", pady=2)
            self.rr_entries[key] = e
        tk.Label(f_rr, text="起始计划名关键词(留空=表头下一行):").grid(
            row=1, column=0, columnspan=3, sticky="w", padx=(8, 2), pady=2)
        self.rr_entries["anchor_keyword"] = tk.Entry(f_rr, width=30)
        self.rr_entries["anchor_keyword"].grid(row=1, column=3, columnspan=6, sticky="w", pady=2)
        tk.Label(f_rr, text="读取第几行:").grid(row=2, column=0, sticky="w", padx=(8, 2), pady=2)
        self.rr_entries["read_row_index"] = tk.Entry(f_rr, width=6)
        self.rr_entries["read_row_index"].grid(row=2, column=1, sticky="w", pady=2)
        tk.Label(f_rr, text="列偏移(px,可负):").grid(row=2, column=3, sticky="w", padx=(14, 2), pady=2)
        rr_off_items = [("offset_roi", "ROI"), ("offset_cost", "成本"), ("offset_rev", "收入")]
        for i, (key, label) in enumerate(rr_off_items):
            col = 4 + i * 2
            tk.Label(f_rr, text=label).grid(row=2, column=col, sticky="w", padx=(8, 2), pady=2)
            e = tk.Entry(f_rr, width=7)
            e.grid(row=2, column=col + 1, sticky="w", pady=2)
            self.rr_entries[key] = e
        # 错误20：品名校验与品名列偏移（识别引擎重写配套）
        self.var_plan_name_check = tk.BooleanVar(value=bool(self.cfg.get("plan_name_check", True)))
        tk.Checkbutton(f_rr, text="品名校验", variable=self.var_plan_name_check).grid(
            row=3, column=0, columnspan=2, sticky="w", padx=(8, 2), pady=2)
        tk.Label(f_rr, text="品名正则(留空=宽松):").grid(row=3, column=2, sticky="w", padx=(14, 2), pady=2)
        self.rr_entries["plan_name_pattern"] = tk.Entry(f_rr, width=24)
        self.rr_entries["plan_name_pattern"].grid(row=3, column=3, columnspan=4, sticky="w", pady=2)
        tk.Label(f_rr, text="品名列偏移:").grid(row=3, column=7, sticky="w", padx=(14, 2), pady=2)
        self.rr_entries["offset_name"] = tk.Entry(f_rr, width=7)
        self.rr_entries["offset_name"].grid(row=3, column=8, sticky="w", pady=2)
        tk.Label(f_rr, text="跳过词(每条一行,支持正则,命中不参与解析):").grid(
            row=4, column=0, columnspan=3, sticky="w", padx=(8, 2), pady=2)
        self.btn_skip_words = tk.Button(f_rr, text="", width=14, command=self.open_skip_words_editor)
        self.btn_skip_words.grid(row=4, column=3, columnspan=3, sticky="w", pady=2)
        self._refresh_skip_words_btn()
        # 错误22：表头关键词（广告计划词命中即表头，逗号分隔多候选，自动合并OCR噪声变体）
        tk.Label(f_rr, text="表头关键词(广告计划词命中即表头,逗号分隔):").grid(
            row=5, column=0, columnspan=3, sticky="w", padx=(8, 2), pady=2)
        self.rr_entries["header_keywords"] = tk.Entry(f_rr, width=40)
        self.rr_entries["header_keywords"].grid(row=5, column=3, columnspan=6, sticky="w", pady=2)
        # v10.1：识别模式/滚动收集已上提至"②.5 识别方式"板块（在自动监控之前），此处不再重复
        # v10.6 布局框架：保存按钮行挂 FlowFrame
        row_rr_btns = FlowFrame(f_rr)
        row_rr_btns.grid(row=7, column=0, columnspan=10, sticky="w", padx=4, pady=4)
        tk.Button(row_rr_btns, text="保存识别规则", command=self.save_rec_rules, width=14)
        row_rr_btns.add(row_rr_btns.winfo_children()[-1], padx=4)

        # 订单与手动录入区
        f_ord = self._mk_section("⑤ 订单数 / 手动录入", "sec_ord")
        # v10.2 布局增强：累计订单行与手动录入行拆分
        row_a = tk.Frame(f_ord)
        row_a.pack(fill="x")
        tk.Label(row_a, text="当前累计订单数:").pack(side="left")
        self.e_orders = tk.Entry(row_a, width=8)
        self.e_orders.pack(side="left", padx=5)
        tk.Button(row_a, text="更新订单数", command=self.save_orders, width=10).pack(side="left", padx=2)
        row_b = tk.Frame(f_ord)
        row_b.pack(fill="x", pady=(2, 0))
        tk.Label(row_b, text="手动录入：花费USD").pack(side="left")
        self.e_cost = tk.Entry(row_b, width=8)
        self.e_cost.pack(side="left", padx=3)
        tk.Label(row_b, text="订单").pack(side="left")
        self.e_orders_m = tk.Entry(row_b, width=5)
        self.e_orders_m.pack(side="left", padx=3)
        tk.Label(row_b, text="收入USD").pack(side="left")
        self.e_rev = tk.Entry(row_b, width=8)
        self.e_rev.pack(side="left", padx=3)
        tk.Label(row_b, text="UV(可选)").pack(side="left")
        self.e_uv = tk.Entry(row_b, width=6)
        self.e_uv.pack(side="left", padx=3)
        tk.Button(row_b, text="录入", command=self.do_manual_record, width=8).pack(side="left", padx=3)

        # 推送区（可折叠：点击标题展开/收起）
        f_push = tk.LabelFrame(self.main_frame, text="⑥ 推送设置（PushPlus / 企业微信 / 钉钉）", padx=8, pady=5)
        f_push.pack(fill="x", **pad)
        # 任务C：⑥推送板块纳入统一管理（视图菜单可隐藏；内部折叠状态同步持久化）
        self._register_section(f_push, "sec_push")

        # 折叠标题行
        push_header = tk.Frame(f_push)
        push_header.pack(fill="x", padx=2, pady=2)
        self.push_arrow = tk.Label(push_header, text="▼", fg="#666", cursor="hand2")
        self.push_arrow.pack(side="left", padx=(5, 2))
        self.push_header_label = tk.Label(push_header, text="推送设置（点击展开/收起）", fg="#333", cursor="hand2")
        self.push_header_label.pack(side="left")
        # v10.13：⑥推送设置板块标题行右侧 ✕ 隐藏整块（视图菜单可重开）
        hide_push = tk.Label(push_header, text="✕", fg="#999", cursor="hand2", padx=2,
                             font=("Microsoft YaHei", 8))
        hide_push.pack(side="right", padx=(0, 4))
        hide_push.bind("<Button-1>", lambda _e: self._toggle_section_visibility("sec_push"))
        self.push_collapsed = ("sec_push" in self.collapsed_sections)
        f_push_body = tk.Frame(f_push)
        f_push_body.pack(fill="x", padx=2, pady=2)
        # v10.2 布局增强：Key 输入列可伸缩（窗口变窄时 entry 收缩、按钮不被挤出/截断）
        f_push_body.columnconfigure(1, weight=1)

        def toggle_push(_evt=None):
            self.push_collapsed = not self.push_collapsed
            if self.push_collapsed:
                f_push_body.pack_forget()
                self.push_arrow.config(text="▶")
                if "sec_push" not in self.collapsed_sections:
                    self.collapsed_sections.append("sec_push")
            else:
                f_push_body.pack(fill="x", padx=2, pady=2)
                self.push_arrow.config(text="▼")
                self.collapsed_sections = [s for s in self.collapsed_sections if s != "sec_push"]
            self._persist_section_state()
        if self.push_collapsed:
            f_push_body.pack_forget()
            self.push_arrow.config(text="▶")
        self.push_arrow.bind("<Button-1>", toggle_push)
        self.push_header_label.bind("<Button-1>", toggle_push)

        # PushPlus 行（最上方，推荐通道；v10.2：entry 可伸缩，长提示移至下方提示行）
        tk.Label(f_push_body, text="PushPlus Token:", fg="#006600").grid(row=0, column=0, sticky="w", padx=5)
        self.e_pushplus = tk.Entry(f_push_body, width=30)
        self.e_pushplus.grid(row=0, column=1, sticky="we", padx=5)
        self.var_pushplus = tk.BooleanVar(value=True)
        tk.Checkbutton(f_push_body, text="启用", variable=self.var_pushplus).grid(row=0, column=2, padx=2)
        tk.Button(f_push_body, text="测试推送", command=self.test_pushplus, width=10).grid(row=0, column=3, padx=5)
        self.lbl_test_pushplus = tk.Label(f_push_body, text="—", fg="gray", anchor="w")
        self.lbl_test_pushplus.grid(row=0, column=4, sticky="w", padx=5)
        tk.Label(f_push_body, text="免费200条/日", fg="#006600").grid(row=0, column=5, sticky="w", padx=5)
        tk.Label(f_push_body, text="提示：pushplus 官网登录后右上角获取 Token", fg="gray", font=("微软雅黑", 8)).grid(row=1, column=1, columnspan=5, sticky="w", padx=5)

        # 企业微信行
        tk.Label(f_push_body, text="企业微信 Webhook Key:").grid(row=2, column=0, sticky="w", padx=5)
        self.e_wecom = tk.Entry(f_push_body, width=30)
        self.e_wecom.grid(row=2, column=1, sticky="we", padx=5)
        self.var_wecom = tk.BooleanVar(value=False)
        tk.Checkbutton(f_push_body, text="启用", variable=self.var_wecom).grid(row=2, column=2, padx=2)
        tk.Button(f_push_body, text="测试推送", command=self.test_wecom, width=10).grid(row=2, column=3, padx=5)
        self.lbl_test_wecom = tk.Label(f_push_body, text="—", fg="gray", anchor="w")
        self.lbl_test_wecom.grid(row=2, column=4, sticky="w", padx=5)
        tk.Label(f_push_body, text="个人版无群机器人", fg="gray").grid(row=2, column=5, sticky="w", padx=5)
        tk.Label(f_push_body, text="提示：企业微信 → 建群 → 群设置 → 群机器人添加", fg="gray", font=("微软雅黑", 8)).grid(row=3, column=1, columnspan=5, sticky="w", padx=5)

        # 钉钉行
        tk.Label(f_push_body, text="钉钉 Webhook 地址:").grid(row=4, column=0, sticky="w", padx=5)
        self.e_ding = tk.Entry(f_push_body, width=30)
        self.e_ding.grid(row=4, column=1, sticky="we", padx=5)
        self.var_ding = tk.BooleanVar(value=False)
        tk.Checkbutton(f_push_body, text="启用", variable=self.var_ding).grid(row=4, column=2, padx=2)
        tk.Button(f_push_body, text="测试推送", command=self.test_ding, width=10).grid(row=4, column=3, padx=5)
        self.lbl_test_ding = tk.Label(f_push_body, text="—", fg="gray", anchor="w")
        self.lbl_test_ding.grid(row=4, column=4, sticky="w", padx=5)
        tk.Label(f_push_body, text="需普通账号+小号建群", fg="gray").grid(row=4, column=5, sticky="w", padx=5)
        tk.Label(f_push_body, text="  提示：钉钉 → 机器人 → 添加 → 复制 Webhook 地址", fg="gray", font=("微软雅黑", 8)).grid(row=5, column=1, sticky="w", padx=5)

        # 保存 + 编辑推送内容模板 + 当前生效通道状态（v10.6 挂 FlowFrame 自动换行）
        row_push_btns = FlowFrame(f_push_body)
        row_push_btns.grid(row=6, column=0, columnspan=6, sticky="w", padx=4, pady=3)
        tk.Button(row_push_btns, text="保存推送设置", command=self.save_push, width=14)
        row_push_btns.add(row_push_btns.winfo_children()[-1], padx=4)
        tk.Button(row_push_btns, text="推送模板编辑", command=self.edit_pushplus_template, width=14)
        row_push_btns.add(row_push_btns.winfo_children()[-1], padx=4)
        self.lbl_push_status = tk.Label(row_push_btns, text="", fg="#006600", anchor="w")
        row_push_btns.add(self.lbl_push_status, padx=8)
        f_push_body.columnconfigure(1, weight=1)

        # v10.14.3：合并推送间隔输入框已删除（v10.14 起推送时机改为每轮识别完统一打包，不再按分钟等待）
        # v10.9.1：告警声音行（低调滴声提醒，不占视线；手动识别/快捷键刷新不响）
        tk.Label(f_push_body, text="告警声音:").grid(row=7, column=0, sticky="w", padx=5)
        self.var_sound_alarm = tk.BooleanVar(value=True)
        tk.Checkbutton(f_push_body, text="自动监控命中告警时响一声很轻的滴声", variable=self.var_sound_alarm).grid(row=7, column=1, columnspan=5, sticky="w", padx=5)
        # v10.10：告警 toast 开关行（上班时间可关闭防被同事看到；仅影响告警 toast，声音/推送不受影响）
        # v10.14.3：默认不勾选（与 DEFAULT_CONFIG toast_enabled=False 一致，上班防围观默认关闭）
        tk.Label(f_push_body, text="告警toast:").grid(row=8, column=0, sticky="w", padx=5)
        self.var_toast_enabled = tk.BooleanVar(value=False)
        tk.Checkbutton(f_push_body, text="自动监控命中告警时弹 Windows 系统通知气泡（上班可关）", variable=self.var_toast_enabled).grid(row=8, column=1, columnspan=5, sticky="w", padx=5)
        # 注意：update_push_status 依赖 Entry 内容，须在配置加载填充 Entry 后再调用
        # （见 _load_config_to_widgets 末尾）

        # v10.21 修复轮（2026-09-06）：品档案面板 sec_archive（设计文档 12.7 可视化）——
        # 品名/首见时间/已跑时长/距老品剩余(12h 倒计时)/新品或老品/名下计划数/当前空烧消耗。
        # 计划数与空烧消耗取 history.db 最新一轮快照聚合（只读）；「刷新」按钮手动重读。
        f_archive = self._mk_section("⑥.5 品档案（plan_archive）", "sec_archive")
        row_arc_top = tk.Frame(f_archive)
        row_arc_top.pack(fill="x", pady=(2, 0))
        tk.Label(row_arc_top, text="口径：品名首见 12h 内=新品（排期缺失时回退首见建档）；计划数/空烧消耗取历史库最新一轮。",
                 fg="#666").pack(side="left", padx=(6, 4))
        tk.Button(row_arc_top, text="刷新", command=self._refresh_archive_view, width=10).pack(side="right", padx=6)
        arc_cols = ("品名", "首见时间", "已跑(h)", "距老品(h)", "新/老", "名下计划数", "空烧消耗($)")
        self.tree_archive = ttk.Treeview(f_archive, columns=arc_cols, show="headings", height=6)
        for _c in arc_cols:
            _wd = 150 if _c == "品名" else 88
            self.tree_archive.heading(_c, text=_c)
            self.tree_archive.column(_c, width=_wd, anchor="w")
        self.tree_archive.pack(fill="x", padx=6, pady=4)
        self.root.after(400, self._refresh_archive_view)

        # 最近识别结果（v10.7 单列布局：挂 main_frame 最后，参与折叠/隐藏统一管理）
        f_last = tk.LabelFrame(self.main_frame, text="⑦ 最近一次识别结果", padx=8, pady=3)
        self.f_last = f_last
        self.lbl_last = tk.Label(f_last, text="尚未识别", fg="gray", anchor="w")
        self.lbl_last.pack(fill="x")
        self._register_section(f_last, "sec_last")

        # 日志区（v10 滚动改造：右侧滚动条 + 滚轮支持）
        # v10.6 布局框架：日志冻结底部独立窗格——父容器=pw_root，横跨全宽独立滚动，
        # 位置永不动、不参与归属配置；高度按 window_layout.log_height_ratio 恢复，sash 可拖。
        f_log = tk.LabelFrame(self.pw_root, text="⑧ 运行日志", padx=8, pady=3)
        self._log_frame = f_log  # 供 mod 按钮行定位（注册按钮插入到信息区上方，v10.6 日志在底部不影响）
        self.f_log = f_log
        self.pw_root.add(self.f_log, weight=0)
        log_wrap = tk.Frame(f_log)
        log_wrap.pack(fill="both", expand=True)
        self.txt_log = tk.Text(log_wrap, height=10, font=("Consolas", 9), state="disabled", wrap="word")
        # 任务B：日志分级 tag（结果级绿色醒目/告警行红色加粗/排错级灰色；信息级默认色）
        self.txt_log.tag_configure("log_result", foreground="#1565C0")
        self.txt_log.tag_configure("log_alert", foreground="#C62828")
        self.txt_log.tag_configure("log_debug", foreground="#9E9E9E")
        self.sb_log = tk.Scrollbar(log_wrap, orient="vertical", command=self.txt_log.yview)
        self.txt_log.config(yscrollcommand=self.sb_log.set)
        self.sb_log.pack(side="right", fill="y")
        self.txt_log.pack(side="left", fill="both", expand=True)
        # 滚轮：日志区内滚动；鼠标移入/移出日志区时绑定/解绑全局滚轮，避免滚轮在别处时日志跟着滚
        self.txt_log.bind("<MouseWheel>", self._on_log_mousewheel)
        self.sb_log.bind("<MouseWheel>", self._on_log_mousewheel)
        self.txt_log.bind("<Enter>", self._bind_log_wheel)
        self.txt_log.bind("<Leave>", self._unbind_log_wheel)
        self.sb_log.bind("<Enter>", self._bind_log_wheel)
        self.sb_log.bind("<Leave>", self._unbind_log_wheel)
        btn_row = tk.Frame(f_log)
        btn_row.pack(fill="x", pady=2)
        tk.Button(btn_row, text="查看历史记录", command=self.show_history, width=14).pack(side="left")
        tk.Button(btn_row, text="清空日志", command=self.clear_log, width=10).pack(side="left", padx=5)
        # v10.13：日志栏隐藏按钮（等价「视图→显示日志」取消勾选；重新打开走菜单）
        tk.Button(btn_row, text="隐藏", command=self._hide_log_panel, width=6).pack(side="right", padx=5)
        # 所有板块构建完成后，统一为主面板子控件绑定滚轮（日志区 Text/Scrollbar 自带滚轮，排除避免覆盖）
        self._bind_panel_wheel_recursive(self.main_frame)
        # 应用持久化布局（无 layout 字段则默认：左设置区 + 右信息区，上下分栏，日志可见）
        self._apply_layout()

    # ---------- 任务A：多目标管理 ----------
    def _targets(self):
        """当前 targets 列表（保证存在）。"""
        if not isinstance(self.cfg.get("targets"), list) or not self.cfg["targets"]:
            _ensure_targets(self.cfg)
        return self.cfg["targets"]

    def _target_by_id(self, tid):
        for t in self._targets():
            if t.get("id") == tid:
                return t
        return None

    def _current_target(self):
        t = self._target_by_id(self.current_target_id)
        if t is None:
            targets = self._targets()
            if targets:
                t = targets[0]
                self.current_target_id = t.get("id", 1)
        return t or {}

    def _refresh_target_bar(self):
        """渲染目标切换条：每个目标一个按钮，当前目标高亮。"""
        for w in self._target_bar.winfo_children():
            w.destroy()
        targets = self._targets()
        for t in targets:
            tid = t.get("id", 1)
            name = str(t.get("name") or f"目标{tid}")
            mark = ""
            if not t.get("enabled", True):
                mark = " (未启用)"
            is_cur = (tid == self.current_target_id)
            btn = tk.Button(self._target_bar, text=f"{name}{mark}", width=10,
                            command=lambda i=tid: self._switch_target(i),
                            bg=("#1565C0" if is_cur else "SystemButtonFace"),
                            fg=("white" if is_cur else "black"))
            btn.pack(side="left", padx=2)
        if not targets:
            tk.Label(self._target_bar, text="无目标，请添加", fg="red").pack(side="left", padx=4)

    def _switch_target(self, tid):
        """切换当前目标：刷新窗口/ROI 相关 UI 为目标的独立配置。"""
        t = self._target_by_id(tid)
        if t is None:
            return
        self.current_target_id = tid
        self._refresh_target_bar()
        # 窗口关键词
        kw = str(t.get("window_keyword") or "商户中心")
        self.e_window.delete(0, tk.END)
        self.e_window.insert(0, kw)
        # 已锁定窗口句柄（运行期）
        hwnd = self._target_hwnds.get(tid)
        if hwnd and find_window_by_hwnd(hwnd):
            self.lbl_win.config(text=f"已锁定窗口: hwnd={hwnd}", fg="green")
        else:
            self._target_hwnds.pop(tid, None)
            self.lbl_win.config(text="未锁定（按关键词匹配）", fg="gray")
        # ROI
        self.e_rl.delete(0, tk.END); self.e_rt.delete(0, tk.END)
        self.e_rw.delete(0, tk.END); self.e_rh.delete(0, tk.END)
        roi = t.get("roi")
        if isinstance(roi, list) and len(roi) == 4:
            self.e_rl.insert(0, str(roi[0])); self.e_rt.insert(0, str(roi[1]))
            self.e_rw.insert(0, str(roi[2])); self.e_rh.insert(0, str(roi[3]))
            self.lbl_roi.config(text=f"({roi[0]},{roi[1]}) {roi[2]}x{roi[3]}", fg="green")
        else:
            self.lbl_roi.config(text="未设置", fg="red")
        self.log(f"已切换到目标: {t.get('name')} (id={tid})")

    def add_target(self):
        targets = self._targets()
        if len(targets) >= 8:
            messagebox.showwarning("提示", "最多支持 8 个监控目标")
            return
        tid = _next_target_id(targets)
        name = f"目标{tid}"
        # v10.2：新增目标默认不启用自动监控（用户确认后再开，避免误监控新店）
        targets.append({"id": tid, "name": name, "window_keyword": "",
                        "roi": None, "enabled": False})
        save_config(self.cfg)
        self._switch_target(tid)
        self.log(f"已添加目标: {name} (id={tid})，默认未启用自动监控，请填写窗口关键词、框选 ROI 后手动启用")

    def rename_target(self):
        t = self._current_target()
        if not t:
            return
        old = str(t.get("name") or "")
        win = tk.Toplevel(self.root)
        win.title("重命名目标")
        win.transient(self.root)
        win.grab_set()
        win.geometry("360x140")
        tk.Label(win, text="目标名称（显示在切换条与告警推送中）：").pack(anchor="w", padx=12, pady=(12, 4))
        e = tk.Entry(win, width=30)
        e.pack(fill="x", padx=12)
        e.insert(0, old)

        def do_save():
            v = e.get().strip()
            if not v:
                messagebox.showwarning("提示", "名称不能为空")
                return
            t["name"] = v
            save_config(self.cfg)
            self._refresh_target_bar()
            self.log(f"目标已重命名: {old} → {v}")
            win.destroy()

        tk.Button(win, text="保存", command=do_save, width=10).pack(side="left", padx=(12, 4), pady=12)
        tk.Button(win, text="取消", command=win.destroy, width=10).pack(side="left", pady=12)
        e.bind("<Return>", lambda _e: do_save())
        e.focus_set()

    def toggle_target_enabled(self):
        """启用/禁用当前目标自动监控（v10.2 任务2：目标独立监控开关）。"""
        t = self._current_target()
        if not t:
            return
        t["enabled"] = not t.get("enabled", True)
        save_config(self.cfg)
        self._refresh_target_bar()
        state = "启用" if t.get("enabled") else "禁用"
        self.log(f"目标 {t.get('name')} (id={t.get('id')}) 已{state}自动监控")

    def delete_target(self):
        targets = self._targets()
        t = self._current_target()
        if not t:
            return
        if len(targets) <= 1:
            messagebox.showwarning("提示", "至少保留一个监控目标")
            return
        tid = t.get("id")
        name = str(t.get("name") or tid)
        # v10.2：用户睡觉中批量开发，删除类操作不弹确认框，直接执行（可恢复性由回收站/归档兜底）
        self.cfg["targets"] = [x for x in targets if x.get("id") != tid]
        if self.current_target_id == tid:
            nxt = self.cfg["targets"][0]
            self.current_target_id = nxt.get("id", 1)
        save_config(self.cfg)
        self._refresh_target_bar()
        self._switch_target(self.current_target_id)
        self.log(f"已删除目标: {name} (id={tid})")

    # ---------- 任务C：可折叠/可隐藏板块 ----------
    def _mk_section(self, title, section_key, default_collapsed=False):
        """创建可折叠板块（ttk.LabelFrame + 可点击标题栏）。

        返回 frame：子控件直接挂到 frame；点击标题栏折叠/展开（标题可见，内容收起）；
        视图菜单可整块显示/隐藏。折叠与隐藏状态都持久化到 config.json。
        """
        hdr = tk.Frame(self.main_frame)
        arrow = tk.Label(hdr, text="▼", fg="#666", cursor="hand2", padx=2)
        arrow.pack(side="left")
        lbl = tk.Label(hdr, text=title, fg="#333", cursor="hand2")
        lbl.pack(side="left")
        arrow.bind("<Button-1>", lambda _e: self._toggle_section(section_key))
        lbl.bind("<Button-1>", lambda _e: self._toggle_section(section_key))
        # v10.13：标题栏右侧 ✕ 隐藏整块（不占空间，菜单栏「显示/隐藏板块」可重开）
        hide = tk.Label(hdr, text="✕", fg="#999", cursor="hand2", padx=2,
                        font=("Microsoft YaHei", 8))
        hide.pack(side="right", padx=(0, 4))
        hide.bind("<Button-1>", lambda _e: self._toggle_section_visibility(section_key))
        self._sec_arrows[section_key] = arrow
        # 折叠态标题行（标题可见、内容收起）；初始不 pack，折叠时再显示
        coll = tk.Frame(self.main_frame)
        coll_arrow = tk.Label(coll, text="▶", fg="#666", cursor="hand2", padx=2)
        coll_arrow.pack(side="left")
        coll_lbl = tk.Label(coll, text=title, fg="#333", cursor="hand2")
        coll_lbl.pack(side="left")
        coll_arrow.bind("<Button-1>", lambda _e: self._toggle_section(section_key))
        coll_lbl.bind("<Button-1>", lambda _e: self._toggle_section(section_key))
        # v10.13：折叠态标题行同样提供 ✕ 隐藏入口
        hide_coll = tk.Label(coll, text="✕", fg="#999", cursor="hand2", padx=2,
                             font=("Microsoft YaHei", 8))
        hide_coll.pack(side="right", padx=(0, 4))
        hide_coll.bind("<Button-1>", lambda _e: self._toggle_section_visibility(section_key))
        self._coll_bars[section_key] = coll
        frame = ttk.LabelFrame(self.main_frame, labelwidget=hdr, padding=5)
        frame.pack(fill="x", padx=8, pady=3)
        self._section_frames[section_key] = frame
        if section_key not in self._section_order:
            self._section_order.append(section_key)
        # 应用持久化状态（初始可能折叠/隐藏）
        if section_key in self.hidden_sections:
            frame.pack_forget()
        elif section_key in self.collapsed_sections:
            self._apply_collapse(section_key, True, notify=False)
        return frame

    def _register_section(self, frame, section_key):
        """手动注册板块（如⑥推送设置保留原 tk.LabelFrame + 内部二级折叠）。"""
        self._section_frames[section_key] = frame
        if section_key not in self._section_order:
            self._section_order.append(section_key)
        if section_key in self.hidden_sections:
            frame.pack_forget()

    def _apply_collapse(self, section_key, collapsed, notify=True):
        """折叠/展开指定板块；notify=True 时落盘持久化。

        折叠=标题行可见、内容收起（pack 出标题占位行）；展开=恢复完整板块。
        """
        frame = self._section_frames.get(section_key)
        if not frame:
            return
        coll = self._coll_bars.get(section_key)
        if collapsed:
            if section_key not in self.collapsed_sections:
                self.collapsed_sections.append(section_key)
            frame.pack_forget()
            if coll is not None:
                self._pack_coll_bar(section_key)
            arrow = self._sec_arrows.get(section_key)
            if arrow:
                arrow.config(text="▶")
        else:
            self.collapsed_sections = [s for s in self.collapsed_sections if s != section_key]
            if coll is not None:
                coll.pack_forget()
            self._repack_section(section_key)
            arrow = self._sec_arrows.get(section_key)
            if arrow:
                arrow.config(text="▼")
        if notify:
            self._persist_section_state()

    def _section_container(self, section_key):
        """v10.7 布局重构：单列布局——所有板块（含 sec_last）统一挂 main_frame。

        sec_log=⑧运行日志固定挂 pw_root（底部冻结日志窗格），不参与单列滚动。
        """
        if section_key == "sec_log":
            return self.pw_root
        return self.main_frame

    def _section_order_for(self, section_key):
        """v10.7：单列统一顺序 = _section_keys（①~⑥）+ sec_last（⑦最近结果放最后）。"""
        return list(self._section_keys) + ["sec_last"]

    def _next_visible_widget(self, section_key):
        """按归属列表顺序返回下一个当前可见的板块 widget（frame 或折叠态标题行 coll_bar）。

        折叠板块的 frame 被 pack_forget 后，其 coll_bar 仍可见，必须同时考虑两者，
        否则展开某板块时会因找不到"下一个可见 frame"而被 pack 到最下面（折叠板块展开顺序错乱 bug）。
        若 next 板块无 coll_bar（如 sec_push 内部二级折叠），返回 None 由调用方兜底 pack。
        """
        order = self._section_order_for(section_key)
        try:
            idx = order.index(section_key)
        except ValueError:
            return None
        for nxt_key in order[idx + 1:]:
            nxt = self._section_frames.get(nxt_key)
            if nxt is not None and nxt.winfo_ismapped():
                return nxt
            coll = self._coll_bars.get(nxt_key)
            if coll is not None and coll.winfo_ismapped():
                return coll
        return None

    def _pack_coll_bar(self, section_key):
        """折叠态标题行插入到归属列表顺序中下一个已显示板块之前（保持原位置）。"""
        coll = self._coll_bars.get(section_key)
        if not coll:
            return
        container = self._section_container(section_key)
        anchor = self._next_visible_widget(section_key)
        if anchor is not None:
            coll.pack(in_=container, fill="x", padx=8, pady=1, before=anchor)
        else:
            coll.pack(in_=container, fill="x", padx=8, pady=1)

    def _toggle_section(self, section_key):
        """点击标题：折叠<->展开。"""
        collapsed = section_key in self.collapsed_sections
        self._apply_collapse(section_key, not collapsed)

    def _toggle_section_visibility(self, section_key):
        """视图菜单：显示<->隐藏 整块。"""
        frame = self._section_frames.get(section_key)
        if not frame:
            return
        coll = self._coll_bars.get(section_key)
        if section_key in self.hidden_sections:
            self.hidden_sections = [s for s in self.hidden_sections if s != section_key]
            self._repack_section(section_key)
            self.log(f"板块已显示: {section_key}")
        else:
            self.hidden_sections.append(section_key)
            frame.pack_forget()
            if coll is not None:
                coll.pack_forget()
            self.log(f"板块已隐藏: {section_key}")
        self._persist_section_state()

    def _repack_section(self, section_key):
        """v10.6：按归属列表顺序重新 pack 板块到归属容器，恢复折叠/展开/隐藏后的排列。"""
        frame = self._section_frames.get(section_key)
        if not frame or section_key in self.hidden_sections:
            return
        container = self._section_container(section_key)
        # 找到归属列表顺序中下一个当前已显示的板块（frame 或折叠态标题行），用 before 插回原位
        anchor = self._next_visible_widget(section_key)
        if anchor is not None:
            frame.pack(in_=container, fill="x", padx=8, pady=3, before=anchor)
            return
        frame.pack(in_=container, fill="x", padx=8, pady=3)

    def _render_sections_by_layout(self):
        """v10.7 布局重构：按单列顺序渲染全部板块到 main_frame。

        - 先全部摘除（frame + 折叠标题行），再按统一顺序 pack(in_=main_frame) 重挂载；
        - hidden_sections 中的板块不渲染；折叠板块渲染折叠标题行 coll_bar；
        - ⑧日志由 pw_root 固定管理，不参与本函数。
        """
        order = self._section_keys + ["sec_last"]
        for key in order:
            frame = self._section_frames.get(key)
            if frame is not None:
                frame.pack_forget()
            coll = self._coll_bars.get(key)
            if coll is not None:
                coll.pack_forget()
        for key in order:
            self._render_section_into(key)

    def _render_section_into(self, section_key):
        """渲染单个板块到其归属容器（隐藏不渲染；折叠渲染 coll_bar）。"""
        if section_key in self.hidden_sections:
            return
        if section_key in self.collapsed_sections:
            self._pack_coll_bar(section_key)
        else:
            self._repack_section(section_key)

    def _persist_section_state(self):
        """折叠/隐藏状态落盘 config.json（collapsed_sections / hidden_sections）。"""
        try:
            self.cfg["collapsed_sections"] = self.collapsed_sections
            self.cfg["hidden_sections"] = self.hidden_sections
            save_config(self.cfg)
        except Exception:
            pass

    def _sync_section_vars(self):
        """将折叠/隐藏状态同步到视图菜单勾选变量（显示=勾选）。"""
        for key in self._section_keys:
            var = self._section_view_vars.get(key)
            if var is not None:
                var.set(key not in self.hidden_sections)

    # ---------- 视图菜单（单列布局 + 简略模式 + 日志等级 + 板块显隐）----------
    def _build_view_menu(self):
        """视图菜单：显示/隐藏信息面板、简略模式小窗、重置布局、日志等级、板块显隐。

        v10.7 布局重构：移除「上下分栏/选项卡堆叠」单选、布局子菜单、「板块布局…」弹窗，
        新增「简略模式」小窗开关。
        """
        self.var_show_result = tk.BooleanVar(value=False)
        self.var_show_log = tk.BooleanVar(value=True)
        # v10.7：单列布局下保留 var_layout_mode 兼容旧 config（值固定 "split"，不再提供 UI 切换）
        self.var_layout_mode = tk.StringVar(value="split")
        self.var_mini_mode = tk.BooleanVar(value=False)  # v10.7 任务2：简略模式小窗
        self.menubar = tk.Menu(self.root)
        self.menu_view = tk.Menu(self.menubar, tearoff=0)
        self.menubar.add_cascade(label="视图", menu=self.menu_view)
        self.menu_view.add_checkbutton(label="显示最近识别结果", variable=self.var_show_result,
                                       command=self._toggle_result_panel)
        self.menu_view.add_checkbutton(label="显示日志", variable=self.var_show_log,
                                       command=self._toggle_log_panel)
        self.menu_view.add_checkbutton(label="简略模式", variable=self.var_mini_mode,
                                       command=self._toggle_mini_mode)
        # v10.7.6 低调化：隐藏窗口（托盘/极端模式）+ 低调退出
        self.menu_view.add_command(label="隐藏窗口", command=self._hide_main_window)
        self.menu_view.add_command(label="退出", command=self._quit_app)
        self.menu_view.add_separator()
        # v10.13：最小尺寸钳制开关（config.lowkey.window_min_size_enabled 持久化，重启保持）
        self.var_min_size = tk.BooleanVar(value=self._min_size_enabled())
        self.menu_view.add_checkbutton(label="最小尺寸钳制", variable=self.var_min_size,
                                       command=self._toggle_min_size_enabled)
        self.menu_view.add_command(label="重置布局", command=self._reset_layout)
        # 任务B：日志等级子菜单（结果级/信息级/排错级，默认结果级；显示过滤不影响落盘）
        self.var_log_level = tk.StringVar(value="result")
        menu_log = tk.Menu(self.menu_view, tearoff=0)
        menu_log.add_radiobutton(label="结果级（识别结论/告警/推送）", variable=self.var_log_level,
                                 value="result", command=self._set_log_level)
        menu_log.add_radiobutton(label="信息级（启动/锁定/保存/识别过程）", variable=self.var_log_level,
                                 value="info", command=self._set_log_level)
        menu_log.add_radiobutton(label="排错级（拒绝明细/OCR原文/异常堆栈）", variable=self.var_log_level,
                                 value="debug", command=self._set_log_level)
        self.menu_view.add_cascade(label="日志等级", menu=menu_log)
        # 任务C：板块显示/隐藏开关（显示=勾选；隐藏=整块不渲染，与折叠独立）
        self._section_view_vars = {}
        menu_sec = tk.Menu(self.menu_view, tearoff=0)
        for key in self._section_keys:
            var = tk.BooleanVar(value=True)
            self._section_view_vars[key] = var
            menu_sec.add_checkbutton(label=self._section_title(key), variable=var,
                                     command=lambda k=key: self._toggle_section_visibility(k))
        self.menu_view.add_cascade(label="显示/隐藏板块", menu=menu_sec)
        # v10.2 任务3：工具菜单 → Mod 管理（启用/禁用 mod，禁用后重启不加载、面板不出现）
        self.menu_tools = tk.Menu(self.menubar, tearoff=0)
        self.menubar.add_cascade(label="工具", menu=self.menu_tools)
        self.menu_tools.add_command(label="Mod 管理", command=self._open_mod_manager)
        # v10.8.0：快捷键配置（呼出/隐藏、低调退出；保存写 config.json lowkey 段并实时重注册）
        self.menu_tools.add_command(label="快捷键配置", command=self._open_hotkey_settings)
        self.root.config(menu=self.menubar)
        # v10.2 布局增强：sash 拖动释放保存兜底（Windows 主题下事件目标可能漂移到子控件）
        self.root.bind_all("<ButtonRelease-1>", self._on_sash_released_anywhere)

    def _open_mod_manager(self):
        """v10.2 任务3：Mod 管理弹窗——列出 mods/ 目录全部 mod，勾选=启用，禁用存 config.disabled_mods。

        禁用仅在下次启动 load_mods 时生效（当前进程已加载的 mod 保持运行）；
        识别方式下拉由已注册识别器动态填充，禁用后重启即不再出现。
        """
        import glob as _glob
        mods_dir = BASE_DIR / "mods"
        if not mods_dir.is_dir():
            messagebox.showinfo("Mod 管理", "未找到 mods 目录（exe 同级）")
            return
        mod_ids = sorted([os.path.basename(p) for p in _glob.glob(str(mods_dir / "*")) if os.path.isdir(p)])
        if not mod_ids:
            messagebox.showinfo("Mod 管理", "mods 目录为空")
            return
        disabled = set(self.cfg.get("disabled_mods") or [])
        win = tk.Toplevel(self.root)
        win.title("Mod 管理")
        win.transient(self.root)
        win.grab_set()
        win.geometry("380x420")
        tk.Label(win, text="勾选=启用，取消勾选=禁用（重启程序后生效）",
                 fg="gray", justify="left").pack(anchor="w", padx=12, pady=(10, 2))
        frame = tk.Frame(win)
        frame.pack(fill="both", expand=True, padx=12, pady=4)
        vars_ = {}
        for mid in mod_ids:
            var = tk.BooleanVar(value=mid not in disabled)
            vars_[mid] = var
            cb = tk.Checkbutton(frame, text=mid, variable=var, anchor="w")
            cb.pack(fill="x", pady=2)

        def do_save():
            new_disabled = [mid for mid, var in vars_.items() if not var.get()]
            self.cfg["disabled_mods"] = new_disabled
            save_config(self.cfg)
            self.log(f"Mod 管理已保存：禁用 {new_disabled if new_disabled else '无'}（重启后生效）")
            win.destroy()

        tk.Button(win, text="保存", command=do_save, width=10).pack(side="left", padx=(12, 4), pady=10)
        tk.Button(win, text="取消", command=win.destroy, width=10).pack(side="left", pady=10)

    # ---------- v10.8.0 快捷键配置（工具菜单入口；保存写 config.json lowkey 段并实时重注册）----------
    def _open_hotkey_settings(self):
        """快捷键配置弹窗：列出全部可配置全局热键，显示当前键值并允许输入新组合键。"""
        win = tk.Toplevel(self.root)
        win.title("快捷键配置")
        win.geometry("480x330")
        win.resizable(False, False)
        try:
            win.attributes("-topmost", True)
        except Exception:
            pass
        frame = tk.Frame(win, padx=16, pady=12)
        frame.pack(fill="both", expand=True)
        tk.Label(frame, text="全局快捷键（格式：ctrl+alt+shift+字母；留空=禁用）",
                 font=("Microsoft YaHei", 9)).pack(anchor="w", pady=(0, 8))
        rows = [
            ("呼出/隐藏主窗口", "hotkey"),
            ("低调退出", "quit_hotkey"),
            ("手动完整轮（先刷新再识别）", "refresh_hotkey"),
        ]
        self._hk_entries = {}
        for label, key in rows:
            row = tk.Frame(frame)
            row.pack(fill="x", pady=4)
            tk.Label(row, text=label, width=16, anchor="w",
                     font=("Microsoft YaHei", 9)).pack(side="left")
            ent = tk.Entry(row, font=("Consolas", 10))
            ent.insert(0, str(self._lowkey.get(key) or ""))
            ent.pack(side="left", fill="x", expand=True, padx=6)
            self._hk_entries[key] = ent
        tk.Label(frame,
                 text="支持修饰键 ctrl/alt/shift/win + 字母/F1-F12/esc/space 等；\n"
                      "三个热键不可相同；保存后立即生效（无需重启）。",
                 font=("Microsoft YaHei", 8), fg="#888888", justify="left").pack(anchor="w", pady=(8, 4))
        btns = tk.Frame(frame)
        btns.pack(fill="x", pady=(10, 0))
        tk.Button(btns, text="保存并应用", command=lambda: self._save_hotkey_settings(win),
                  width=12).pack(side="right", padx=(8, 0))
        tk.Button(btns, text="关闭", command=win.destroy, width=8).pack(side="right")

    def _save_hotkey_settings(self, win):
        """保存快捷键配置：校验格式与冲突 → 写 config.json lowkey 段 → 实时重注册。"""
        new_vals = {}
        for key, ent in self._hk_entries.items():
            new_vals[key] = str(ent.get()).strip().lower().replace(" ", "")
        # 格式校验（复用热键解析，不合法返回 None）
        for label, key in (("呼出/隐藏", "hotkey"), ("低调退出", "quit_hotkey"), ("手动完整轮", "refresh_hotkey")):
            val = new_vals[key]
            if val and self._parse_hotkey_spec(val) is None:
                messagebox.showwarning(
                    "快捷键配置",
                    f"{label}快捷键「{val}」格式不合法。\n"
                    "支持格式如 ctrl+alt+shift+d；修饰键 ctrl/alt/shift/win + 字母/F1-F12/esc/space 等。",
                    parent=win)
                return
        # 冲突校验：三个热键两两不能相同（v10.14.7 加入 refresh_hotkey 第三项）
        h, q, r = new_vals["hotkey"], new_vals["quit_hotkey"], new_vals["refresh_hotkey"]
        for l1, v1, l2, v2 in (("呼出/隐藏", h, "低调退出", q),
                               ("呼出/隐藏", h, "手动完整轮", r),
                               ("低调退出", q, "手动完整轮", r)):
            if v1 and v2 and v1 == v2:
                messagebox.showwarning("快捷键配置", f"{l1}与{l2}不能设置为同一个快捷键。", parent=win)
                return
        # 写入内存 + 落盘 config.json 的 lowkey 段
        self._lowkey["hotkey"] = h
        self._lowkey["quit_hotkey"] = q
        self._lowkey["refresh_hotkey"] = r
        self.cfg["lowkey"] = self._lowkey
        save_config(self.cfg)
        self.log(f"快捷键已保存：呼出/隐藏={h or '（禁用）'}，低调退出={q or '（禁用）'}，手动完整轮={r or '（禁用）'}")
        # 实时重注册（先注销再注册，复用热键冲突检测日志）
        self._unregister_hotkeys()
        self._register_hotkeys()
        # 界面回显实际生效值
        for key, ent in self._hk_entries.items():
            ent.delete(0, "end")
            ent.insert(0, str(self._lowkey.get(key) or ""))
        # 注册失败明确提示（沿用 _register_hotkeys 冲突检测结果）
        if not getattr(self, "_hotkey_ok", True):
            messagebox.showwarning(
                "快捷键配置",
                f"呼出/隐藏快捷键「{h}」注册失败，可能被其他程序占用。\n"
                "请更换组合键后重新保存。", parent=win)
        else:
            self.log("快捷键重注册成功（热键已实时生效）")

    @staticmethod
    def _section_title(key):
        return {
            "sec_win": "① 窗口设置",
            "sec_roi": "② ROI 识别区域",
            "sec_mode": "②.5 识别方式",
            "sec_ctrl": "③ 自动监控",
            "sec_auto_close": "③.5 自动操作（无人值守自动关）",
            "sec_th": "④ 告警条件",
            "sec_sc": "④.5 识别条件",
            "sec_rr": "④.6 识别规则",
            "sec_ord": "⑤ 订单数/手动录入",
            "sec_push": "⑥ 推送设置",
            "sec_archive": "⑥.5 品档案（plan_archive）",
        }.get(key, key)

    def _apply_layout(self):
        """从 config.json 的 window_layout（兼容旧 layout）恢复布局；无记录时使用默认。

        v10.7 布局重构：单列纵向 + 底部冻结日志——仅恢复 show_result/show_log/
        log_height_ratio/geometry 等字段；left_sections/right_sections 不再读取使用。
        """
        lay = self.cfg.get("window_layout") or {}
        if not lay:
            lay = self.cfg.get("layout") or {}
        self._first_layout_no_record = not bool(lay)
        self.var_layout_mode.set(lay.get("mode", "split"))
        self.var_show_result.set(bool(lay.get("show_result", False)))
        self.var_show_log.set(bool(lay.get("show_log", True)))
        self._log_height_ratio = float(lay.get("log_height_ratio", self._log_height_ratio) or self._log_height_ratio)
        self._sash_log_ratio = float(lay.get("sash_log_ratio", self._log_height_ratio) or self._log_height_ratio)
        geo = lay.get("geometry")
        # v10.7.6 低调化：默认不恢复历史大几何（remember_geometry=False 时保持启动小窗）
        if geo and self._lowkey.get("remember_geometry", False):
            try:
                # v10.8.6 待办B：恢复前钳制到虚拟屏幕内，防止历史几何在屏外拖不回来
                import re as _re
                _m = _re.search(r"([+-]\d+)([+-]\d+)$", str(geo))
                if _m:
                    _gx, _gy = int(_m.group(1)), int(_m.group(2))
                    _gx, _gy = self._clamp_win_pos(_gx, _gy)
                    geo = str(geo)[:_m.start()] + f"{_gx:+d}{_gy:+d}"
                self.root.geometry(str(geo))
            except Exception:
                pass
        # v10.7：按单列顺序渲染全部配置板块 + sec_last；日志显隐由 _toggle_log_panel 管理
        self._apply_layout_mode()
        self._render_sections_by_layout()
        # 窗口首次显示后可靠恢复 sash：150ms（首帧）+ 500ms（兜底，防首次布局未完成）
        self.root.after(150, self._restore_sashes)
        self.root.after(500, self._restore_sashes)
        # 首次启动无保存记录：恢复默认比例后落盘一次，让 window_layout 有基线
        if getattr(self, "_first_layout_no_record", False):
            self.root.after(700, self._save_layout)

    def _apply_layout_mode(self):
        """v10.7 布局重构：单列布局——仅管理 ⑦ 最近识别结果 的显隐。

        日志已冻结底部独立窗格（pw_root 管理，见 _toggle_log_panel）；
        show_result=True -> sec_last 挂 main_frame 末尾，否则加入 hidden_sections 隐藏。
        """
        show_result = bool(self.var_show_result.get())
        if show_result:
            if "sec_last" in self.hidden_sections:
                self.hidden_sections = [s for s in self.hidden_sections if s != "sec_last"]
            self._repack_section("sec_last")
        else:
            if "sec_last" not in self.hidden_sections:
                self.hidden_sections.append("sec_last")
            frame = self._section_frames.get("sec_last")
            if frame is not None:
                frame.pack_forget()
        self.root.after(60, self._restore_sashes)
        self.root.after(300, self._restore_sashes)

    def _restore_sashes(self):
        """v10.7 布局重构：仅恢复底部冻结日志 sash 位置（pw_root 上下，按比例）。"""
        try:
            self.root.update_idletasks()
        except Exception:
            pass
        try:
            h = self.pw_root.winfo_height()
            if h > 50 and self.f_log.winfo_manager() != "":
                self.pw_root.sashpos(0, int(h * (1 - self._sash_log_ratio)))
        except Exception:
            pass
        # 最小尺寸钳制（sash 恢复后防止面板小于内容请求尺寸）
        self._clamp_sashes()

    def _clamp_sashes(self):
        """v10.7 最小尺寸钳制：仅钳制 pw_root 上下（主滚动区 / 底部冻结日志）。

        最小高 = update_idletasks 后 winfo_reqheight；避免用户把日志拖到不可见。
        """
        try:
            self.root.update_idletasks()
        except Exception:
            return
        try:
            if self.f_log.winfo_manager() == "":
                return
            h = self.pw_root.winfo_height()
            if h > 50:
                cur = self.pw_root.sashpos(0)
                min_top = max(80, self.left_pane.winfo_reqheight())
                min_bottom = max(60, self.f_log.winfo_reqheight())
                if cur < min_top:
                    self.pw_root.sashpos(0, min_top)
                elif h - cur < min_bottom:
                    self.pw_root.sashpos(0, max(min_top, h - min_bottom))
        except Exception:
            pass

    def _toggle_result_panel(self):
        self._apply_layout_mode()
        self._save_layout()

    def _toggle_log_panel(self):
        """v10.6：显示/隐藏底部冻结日志窗格（pw_root 中 add/forget，位置永不动）。"""
        if self.var_show_log.get():
            if self.f_log.winfo_manager() == "":
                self.pw_root.add(self.f_log, weight=0)
        else:
            try:
                self.pw_root.forget(self.f_log)
            except Exception:
                pass
        self._apply_layout_mode()
        self._save_layout()

    def _hide_log_panel(self):
        """v10.13：日志栏「隐藏」按钮——等价「视图→显示日志」取消勾选，整栏收起不占空间。"""
        self.var_show_log.set(False)
        self._toggle_log_panel()

    def _reset_layout(self):
        """v10.7 恢复默认布局：单列全板块 + 底部冻结日志可见，默认日志高度 25%。"""
        self.var_layout_mode.set("split")
        self.var_show_result.set(False)
        self.var_show_log.set(True)
        self._log_height_ratio = DEFAULT_LOG_HEIGHT_RATIO
        self._sash_log_ratio = DEFAULT_LOG_HEIGHT_RATIO
        self._apply_layout_mode()
        self._save_layout()
        self.log("布局已重置为默认（单列全板块，日志可见）")

    def _layout_state(self):
        """采集当前布局状态（v10.7：单列布局——仅日志高度/显隐/窗口几何）。"""
        st = {
            "mode": self.var_layout_mode.get(),
            "show_result": bool(self.var_show_result.get()),
            "show_log": bool(self.var_show_log.get()),
            "log_height_ratio": round(self._log_height_ratio, 4),
        }
        # 底部日志 sash 实时比例（sash 位置/整窗高度）——v10.7.1 双轨合一仅写 log_height_ratio
        try:
            if self.f_log.winfo_manager() != "":
                h = self.pw_root.winfo_height()
                if h > 50:
                    cur = self.pw_root.sashpos(0)
                    ratio = (h - cur) / h
                    st["log_height_ratio"] = round(ratio, 4)
                    self._log_height_ratio = round(ratio, 4)
        except Exception:
            pass
        try:
            st["geometry"] = self.root.geometry()
        except Exception:
            pass
        return st

    def _save_layout(self):
        """布局状态写入 config.json 的 window_layout 字段并落盘（兼容读取旧 layout 键）。"""
        try:
            self.cfg["window_layout"] = self._layout_state()
            self.cfg.pop("layout", None)
            save_config(self.cfg)
        except Exception as exc:
            self.log(f"[布局保存失败] {exc}")

    def _on_sash_released(self, _evt=None):
        """sash 拖动释放后：最小尺寸钳制（回弹）+ 防抖保存布局（500ms）。

        v10.7 布局重构：仅底部冻结日志 sash 可拖动（pw_root），左右分栏已移除。
        """
        self._clamp_sashes()
        if hasattr(self, "_sash_save_job") and self._sash_save_job:
            try:
                self.root.after_cancel(self._sash_save_job)
            except Exception:
                pass
        self._sash_save_job = self.root.after(500, self._save_layout)

    def _on_sash_released_anywhere(self, _evt=None):
        """全局释放兜底：仅当事件落在底部日志分栏（pw_root）上时才触发保存，避免误保存。"""
        w = getattr(_evt, "widget", None) if _evt is not None else None
        if w is self.pw_root:
            self._on_sash_released()

    # ---------- v10.7.6 低调化：无边框拖动条 / 右键菜单 / 全局热键 / 托盘 / 退出 ----------
    # v10.13：无边框窗口四边拖拽 resize——在标题栏/空白处拖动（移动窗口）基础上，
    # 扩展上/下/左/右四边及四角拖拽调整窗口尺寸；边缘 8px 热区悬停显示方向光标，
    # 按下拖动实时改 geometry，受最小尺寸钳制约束；resize 结束保存布局。
    _RESIZE_EDGE = 8
    _RESIZE_MIN_W = 800
    _RESIZE_MIN_H = 600

    def _min_size_enabled(self):
        """v10.13：最小尺寸钳制开关（config.lowkey.window_min_size_enabled，默认 True）。"""
        try:
            return bool((self._lowkey or {}).get("window_min_size_enabled", True))
        except Exception:
            return True

    def _toggle_min_size_enabled(self, on=None):
        """v10.13：切换最小尺寸钳制开关（视图菜单调用），立即生效并落盘 config。"""
        if on is None:
            on = not self._min_size_enabled()
        self._lowkey["window_min_size_enabled"] = bool(on)
        self.cfg["lowkey"] = self._lowkey
        try:
            save_config(self.cfg)
        except Exception:
            pass
        try:
            if on:
                self.root.minsize(self._RESIZE_MIN_W, self._RESIZE_MIN_H)
            else:
                self.root.minsize(1, 1)
        except Exception:
            pass
        self.log(f"最小尺寸钳制已{'开启' if on else '关闭'}")

    def _rel_pointer(self):
        """窗口内相对坐标（基于屏幕坐标换算，避免事件目标子控件导致的局部坐标偏差）。"""
        try:
            px = self.root.winfo_pointerx() - self.root.winfo_rootx()
            py = self.root.winfo_pointery() - self.root.winfo_rooty()
            return px, py
        except Exception:
            return -1, -1

    def _hit_edges(self, px, py):
        """返回 (left, right, top, bottom) 命中元组（边缘热区 _RESIZE_EDGE 内）。"""
        w = self.root.winfo_width() or self._RESIZE_MIN_W
        h = self.root.winfo_height() or self._RESIZE_MIN_H
        return (px <= self._RESIZE_EDGE, px >= w - self._RESIZE_EDGE,
                py <= self._RESIZE_EDGE, py >= h - self._RESIZE_EDGE)

    def _resize_cursor(self, edges):
        """按命中边返回方向光标；未命中返回空串（恢复默认）。"""
        left, right, top, bottom = edges
        if top and left:
            return "size_nw_se"
        if top and right:
            return "size_ne_sw"
        if bottom and left:
            return "size_ne_sw"
        if bottom and right:
            return "size_nw_se"
        if left or right:
            return "size_we"
        if top or bottom:
            return "size_ns"
        return ""

    def _start_drag_or_resize(self, evt):
        """按下：命中边缘热区则进入 resize 模式，否则进入拖动模式（原空白处拖动）。"""
        px, py = self._rel_pointer()
        edges = self._hit_edges(px, py)
        if any(edges):
            self._resize_mode = edges
            self._resize_start = (self.root.winfo_x(), self.root.winfo_y(),
                                  self.root.winfo_width() or self._RESIZE_MIN_W,
                                  self.root.winfo_height() or self._RESIZE_MIN_H,
                                  evt.x_root, evt.y_root)
            self._drag_off = None
        else:
            self._resize_mode = None
            self._drag_off = (evt.x_root - self.root.winfo_x(), evt.y_root - self.root.winfo_y())

    def _drag_or_resize(self, evt):
        """移动：resize 模式下改窗口尺寸（钳制最小），否则移动窗口。"""
        if self._resize_mode:
            self._do_resize(evt)
            return
        if not self._drag_off:
            return
        try:
            nx = evt.x_root - self._drag_off[0]
            ny = evt.y_root - self._drag_off[1]
            nx, ny = self._clamp_win_pos(nx, ny)
            self.root.geometry(f"+{nx}+{ny}")
        except Exception:
            pass

    def _do_resize(self, evt):
        """v10.13：按命中边实时计算新几何；开启最小尺寸钳制时宽/高不低于 800x600，
        左/上边缘 resize 同步移动窗口位置保持右下角不动。"""
        try:
            mode = self._resize_mode
            if not mode:
                return
            left, right, top, bottom = mode
            sx, sy, sw, sh, mx0, my0 = self._resize_start
            dx = evt.x_root - mx0
            dy = evt.y_root - my0
            nx, ny, nw, nh = sx, sy, sw, sh
            if left:
                nw = sw - dx
                nx = sx + dx
            elif right:
                nw = sw + dx
            if top:
                nh = sh - dy
                ny = sy + dy
            elif bottom:
                nh = sh + dy
            if self._min_size_enabled():
                if nw < self._RESIZE_MIN_W:
                    if left:
                        nx = sx + sw - self._RESIZE_MIN_W
                    nw = self._RESIZE_MIN_W
                if nh < self._RESIZE_MIN_H:
                    if top:
                        ny = sy + sh - self._RESIZE_MIN_H
                    nh = self._RESIZE_MIN_H
            nx, ny = self._clamp_win_pos(nx, ny)
            self.root.geometry(f"{int(nw)}x{int(nh)}+{int(nx)}+{int(ny)}")
        except Exception:
            pass

    def _on_edge_motion(self, _evt=None):
        """悬停边缘热区时切换方向光标（仅提示，不参与拖动）。"""
        try:
            if self._resize_mode:
                return
            px, py = self._rel_pointer()
            cur = self._resize_cursor(self._hit_edges(px, py))
            if cur != getattr(self, "_edge_cursor", ""):
                self.root.config(cursor=cur or "")
                self._edge_cursor = cur
        except Exception:
            pass

    def _on_edge_release(self, _evt=None):
        """resize 释放：退出 resize 模式并保存布局（含新几何）。"""
        if self._resize_mode:
            self._resize_mode = None
            try:
                self.root.after(150, self._save_layout)
            except Exception:
                pass

    def _setup_frameless_chrome(self):
        """无边框主窗口：面板空白处拖动 + 四边拖拽 resize + 右键菜单 + Alt+F4 隐藏。
        v10.14.7：删除顶部深色拖动条、_lbl_bar 状态小字与右侧 ✕ 隐藏按钮（§8.4 方案A）；
        窗口拖动走面板空白处绑定（canvas/main_frame），四边 resize、右键菜单、
        隐藏/退出热键、托盘均不受影响。"""
        if not self._lowkey.get("frameless", True):
            return
        self._resize_mode = None
        self._resize_start = None
        self._drag_off = None
        self._edge_cursor = ""
        # 窗口空白处拖动（Canvas/主内容 Frame 空白；子按钮点击不会冒泡到这里，不干扰交互）
        for w in (self.canvas, self.main_frame):
            try:
                w.bind("<ButtonPress-1>", self._start_drag_or_resize)
                w.bind("<B1-Motion>", self._drag_or_resize)
            except Exception:
                pass
        # v10.13 四边 resize：root 级 Motion 光标提示 + 释放保存（ButtonPress/Motion 已由
        # 上述统一入口处理，root 无需重复绑定按下/移动）
        self.root.bind("<Motion>", self._on_edge_motion)
        self.root.bind("<ButtonRelease-1>", self._on_edge_release)
        # 右键菜单：主窗口任意空白处右键（输入控件除外，保留系统右键）
        self.root.bind("<Button-3>", self._on_any_right_click)
        self._ctx_menu = None
        # Alt+F4 / 系统关闭 → 隐藏（低调防误退出；真正退出走右键菜单/热键/托盘菜单）
        self.root.protocol("WM_DELETE_WINDOW", self._hide_main_window)

    def _clamp_win_pos(self, x, y):
        """v10.8.6 待办B：窗口位置钳制到虚拟屏幕内，保证至少 80x40 可见
        （v10.14.7 已无顶部拖动条，仍保留 80x40 可见下限，便于空白处拖动找回窗口）；
        窗口比虚拟屏大时贴左上角。失败静默返回原坐标。"""
        try:
            import ctypes
            user32 = ctypes.windll.user32
            vx = int(user32.GetSystemMetrics(76))   # SM_XVIRTUALSCREEN
            vy = int(user32.GetSystemMetrics(77))   # SM_YVIRTUALSCREEN
            vw = int(user32.GetSystemMetrics(78))   # SM_CXVIRTUALSCREEN
            vh = int(user32.GetSystemMetrics(79))   # SM_CYVIRTUALSCREEN
            w = self.root.winfo_width() or 900
            h = self.root.winfo_height() or 600
            if w >= vw:
                x = vx
            else:
                x = max(vx, min(int(x), vx + vw - 80))
            if h >= vh:
                y = vy
            else:
                y = max(vy, min(int(y), vy + vh - 40))
            return int(x), int(y)
        except Exception:
            return x, y

    def _reset_window_center(self):
        """v10.8.6 待办B：窗口复位到虚拟屏幕中央（右键菜单调用）。"""
        try:
            import ctypes
            user32 = ctypes.windll.user32
            vx = int(user32.GetSystemMetrics(76))
            vy = int(user32.GetSystemMetrics(77))
            vw = int(user32.GetSystemMetrics(78))
            vh = int(user32.GetSystemMetrics(79))
            w = self.root.winfo_width() or 900
            h = self.root.winfo_height() or 600
            x = vx + max(0, (vw - w) // 2)
            y = vy + max(0, (vh - h) // 2)
            self.root.geometry(f"+{int(x)}+{int(y)}")
        except Exception:
            pass

    def _on_any_right_click(self, evt):
        """主窗口任意处右键弹低调菜单；输入控件保留系统右键。"""
        w = evt.widget
        if isinstance(w, (tk.Entry, tk.Text, tk.Spinbox, tk.Listbox, ttk.Entry, ttk.Combobox)):
            return
        self._popup_context_menu(evt.x_root, evt.y_root)
        return "break"

    def _popup_context_menu(self, x, y):
        """低调右键菜单：显示/隐藏、简略模式、退出。"""
        m = getattr(self, "_ctx_menu", None)
        if m is None:
            m = tk.Menu(self.root, tearoff=0)
            m.add_command(label="显示/隐藏窗口", command=self._toggle_main_window)
            m.add_command(label="窗口复位到屏幕中央", command=self._reset_window_center)
            m.add_checkbutton(label="简略模式", variable=self.var_mini_mode,
                              command=self._toggle_mini_mode)
            m.add_separator()
            m.add_command(label="退出", command=self._quit_app)
            self._ctx_menu = m
        try:
            m.tk_popup(x, y)
        finally:
            try:
                m.grab_release()
            except Exception:
                pass

    def _toggle_main_window(self):
        """显示/隐藏主窗口（热键/托盘/右键菜单共用）。"""
        if self.root.state() == "withdrawn" or not self.root.winfo_viewable():
            self._show_main_window()
        else:
            self._hide_main_window()

    def _hide_main_window(self):
        """隐藏主窗口：托盘模式=最小化到托盘；极端模式=仅热键可召回。"""
        try:
            self.root.withdraw()
        except Exception:
            pass
        # 托盘模式：首次隐藏时创建托盘图标；极端模式无托盘
        if str(self._lowkey.get("hide_mode") or "extreme") == "tray":
            self._ensure_tray()

    def _show_main_window(self, force=False):
        """呼出主窗口（极端模式全局热键 / 托盘菜单 / mini 展开）。

        v10.8.1：force=True（单实例唤起）时在 tk deiconify/lift/focus_force 之外，
        追加 Win32 兜底（ShowWindow SW_RESTORE + BringWindowToTop + SetForegroundWindow），
        确保 withdrawn/minimized 窗口可靠恢复显示并置顶聚焦（前台锁限制时尽力而为）。
        """
        try:
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
        except Exception:
            pass
        if not force:
            return
        try:
            import ctypes
            user32 = ctypes.windll.user32
            hwnd = self._top_hwnd()
            if hwnd:
                user32.ShowWindow(hwnd, 9)   # SW_RESTORE：恢复显示（withdrawn/minimized）
                user32.BringWindowToTop(hwnd)
                user32.SetForegroundWindow(hwnd)
        except Exception:
            pass

    def _top_hwnd(self):
        """取主窗口顶层 HWND：优先按标题 FindWindow，失败回退 winfo_id 链。"""
        try:
            import ctypes
            user32 = ctypes.windll.user32
            hwnd = user32.FindWindowW(None, "tkm_10.26")
            if hwnd:
                return hwnd
        except Exception:
            pass
        try:
            wid = self.root.winfo_id()
            return int(wid)
        except Exception:
            return 0

    # ---- 全局热键（ctypes RegisterHotKey + 独立线程 GetMessage 接收，无需额外依赖） ----
    # v10.8.1 修复说明：v10.8.0 用 RegisterHotKey(None,...) 把 WM_HOTKEY 投递到 tkinter
    # 主线程消息队列，Tk 主循环会抢先 GetMessage 取走并丢弃（hwnd=NULL 的线程消息
    # DispatchMessage 无窗口处理），导致"本程序前台偶尔可用、其他程序前台无效"。
    # 现改为：独立热键线程创建隐藏消息窗口（HWND_MESSAGE），RegisterHotKey 注册到该窗口，
    # 线程内 GetMessage 循环接收 WM_HOTKEY，经线程安全队列 _hk_queue 通知主线程执行，
    # 与 tk 消息循环完全隔离；RegisterHotKey 本身即系统级全局，其他程序前台同样触发。
    def _start_hotkey_thread(self):
        """启动热键接收线程：创建隐藏消息窗口并进入 GetMessage 循环。"""
        def _run():
            import ctypes
            from ctypes import wintypes
            try:
                kernel32 = ctypes.windll.kernel32
                user32 = ctypes.windll.user32
                self._hk_thread_id = kernel32.GetCurrentThreadId()

                WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_long, wintypes.HWND,
                                             wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
                # v10.8.1 修复：ctypes.wintypes.WNDCLASS 在 Python 3.11 已移除，自定义 WNDCLASSW 结构；
                # 并显式声明 Win32 原型，避免 64 位句柄/参数被 ctypes 按 32 位截断导致
                # CreateWindowExW(HWND_MESSAGE) 失败（ERROR_INVALID_WINDOW_HANDLE 1400）。
                class _WNDCLASSW(ctypes.Structure):
                    _fields_ = [
                        ("style", wintypes.UINT),
                        ("lpfnWndProc", ctypes.c_void_p),
                        ("cbClsExtra", ctypes.c_int),
                        ("cbWndExtra", ctypes.c_int),
                        ("hInstance", wintypes.HINSTANCE),
                        ("hIcon", wintypes.HICON),
                        ("hCursor", wintypes.HANDLE),
                        ("hbrBackground", wintypes.HBRUSH),
                        ("lpszMenuName", wintypes.LPCWSTR),
                        ("lpszClassName", wintypes.LPCWSTR),
                    ]
                user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                                  wintypes.WPARAM, wintypes.LPARAM]
                user32.DefWindowProcW.restype = ctypes.c_long
                user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                                   wintypes.DWORD, ctypes.c_int, ctypes.c_int,
                                                   ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                                   wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p]
                user32.CreateWindowExW.restype = wintypes.HWND
                user32.RegisterClassW.argtypes = [ctypes.c_void_p]
                user32.RegisterClassW.restype = wintypes.ATOM
                @WNDPROC
                def _wndproc(hwnd, msg, wparam, lparam):
                    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)
                self._hk_wndproc_ref = _wndproc  # 保持引用防 GC

                cls_name = "TikTokMonitor_HotkeyWnd_v10"
                wc = _WNDCLASSW()
                wc.lpfnWndProc = ctypes.cast(_wndproc, ctypes.c_void_p)
                wc.hInstance = kernel32.GetModuleHandleW(None)
                wc.lpszClassName = cls_name
                if not user32.RegisterClassW(ctypes.byref(wc)):
                    # 类已存在（同一进程内重复启动）则继续；其他错误直接失败
                    if kernel32.GetLastError() != 1410:  # ERROR_CLASS_ALREADY_EXISTS
                        self._hk_hwnd_ready.set()
                        return
                HWND_MESSAGE = -3
                hwnd = user32.CreateWindowExW(0, cls_name, None, 0, 0, 0, 0, 0,
                                              HWND_MESSAGE, None, None, None)
                if not hwnd:
                    try:
                        self.log(f"[热键线程] CreateWindowExW 失败 GetLastError={kernel32.GetLastError()}")
                    except Exception:
                        pass
                    self._hk_hwnd_ready.set()
                    return
                self._hk_hwnd = hwnd
                # v10.8.1：RegisterHotKey 必须与窗口同线程调用（跨线程返回
                # ERROR_INVALID_WINDOW_HANDLE 1408），故热键注册移入热键线程内完成，
                # 结果经 _hk_reg_msgs 回传主线程记录日志。
                try:
                    user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int,
                                                      wintypes.UINT, wintypes.UINT]
                    user32.RegisterHotKey.restype = wintypes.BOOL
                    user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
                    user32.UnregisterHotKey.restype = wintypes.BOOL
                    spec = self._lowkey.get("hotkey") or ""
                    parsed = self._parse_hotkey_spec(spec)
                    if parsed:
                        mods, vk = parsed
                        if user32.RegisterHotKey(hwnd, self._hotkey_id, mods, vk):
                            self._hk_toggle = (mods, vk)
                            self._hotkey_ok = True
                            self._hk_reg_msgs.append(f"全局热键已注册：{spec}（呼出/隐藏）")
                        else:
                            self._hotkey_ok = False
                            err = kernel32.GetLastError()
                            if err == 1409:
                                self._hk_reg_msgs.append(
                                    f"[热键注册失败] {spec} 可能被其他程序占用；请在 config.json 的 "
                                    f"lowkey.hotkey 中改为其他组合（如 ctrl+alt+shift+d）后重启")
                            else:
                                self._hk_reg_msgs.append(
                                    f"[热键注册失败] {spec} 注册错误 GetLastError={err}")
                    qspec = self._lowkey.get("quit_hotkey") or ""
                    qparsed = self._parse_hotkey_spec(qspec)
                    if qparsed:
                        mods, vk = qparsed
                        if user32.RegisterHotKey(hwnd, self._quit_hotkey_id, mods, vk):
                            self._hk_quit = (mods, vk)
                            self._hk_reg_msgs.append(f"退出热键已注册：{qspec}（低调退出）")
                        else:
                            err = kernel32.GetLastError()
                            if err == 1409:
                                self._hk_reg_msgs.append(
                                    f"[退出热键注册失败] {qspec} 可能被其他程序占用；请在 config.json 的 "
                                    f"lowkey.quit_hotkey 中改为其他组合（如 ctrl+alt+shift+x）后重启")
                            else:
                                self._hk_reg_msgs.append(
                                    f"[退出热键注册失败] {qspec} 注册错误 GetLastError={err}")
                    rspec = self._lowkey.get("refresh_hotkey") or ""
                    rparsed = self._parse_hotkey_spec(rspec)
                    if rparsed:
                        mods, vk = rparsed
                        if user32.RegisterHotKey(hwnd, self._refresh_hotkey_id, mods, vk):
                            self._hk_refresh = (mods, vk)
                            self._hk_reg_msgs.append(f"刷新热键已注册：{rspec}（手动完整轮：刷新+识别+告警）")
                        else:
                            err = kernel32.GetLastError()
                            if err == 1409:
                                self._hk_reg_msgs.append(
                                    f"[刷新热键注册失败] {rspec} 可能被其他程序占用；请在 config.json 的 "
                                    f"lowkey.refresh_hotkey 中改为其他组合（如 ctrl+alt+shift+r）后重启")
                            else:
                                self._hk_reg_msgs.append(
                                    f"[刷新热键注册失败] {rspec} 注册错误 GetLastError={err}")
                    # v10.16.2：启动热键（ctrl+alt+shift+f）——启动自动监控（不显示窗口）
                    sspec = self._lowkey.get("show_hotkey") or ""
                    spar = self._parse_hotkey_spec(sspec)
                    if spar:
                        mods, vk = spar
                        if user32.RegisterHotKey(hwnd, self._show_hotkey_id, mods, vk):
                            self._hk_show = (mods, vk)
                            self._hk_reg_msgs.append(f"启动热键已注册：{sspec}（启动自动监控）")
                        else:
                            err = kernel32.GetLastError()
                            if err == 1409:
                                self._hk_reg_msgs.append(
                                    f"[启动热键注册失败] {sspec} 可能被其他程序占用；请在 config.json 的 "
                                    f"lowkey.show_hotkey 中改为其他组合（如 ctrl+alt+shift+f）后重启")
                            else:
                                self._hk_reg_msgs.append(
                                    f"[启动热键注册失败] {sspec} 注册错误 GetLastError={err}")
                    # v10.16.2：停止热键（ctrl+alt+shift+c）——停止自动监控（不隐藏窗口）
                    hspec = self._lowkey.get("hide_hotkey") or ""
                    hpar = self._parse_hotkey_spec(hspec)
                    if hpar:
                        mods, vk = hpar
                        if user32.RegisterHotKey(hwnd, self._hide_hotkey_id, mods, vk):
                            self._hk_hide = (mods, vk)
                            self._hk_reg_msgs.append(f"停止热键已注册：{hspec}（停止自动监控）")
                        else:
                            err = kernel32.GetLastError()
                            if err == 1409:
                                self._hk_reg_msgs.append(
                                    f"[停止热键注册失败] {hspec} 可能被其他程序占用；请在 config.json 的 "
                                    f"lowkey.hide_hotkey 中改为其他组合（如 ctrl+alt+shift+c）后重启")
                            else:
                                self._hk_reg_msgs.append(
                                    f"[停止热键注册失败] {hspec} 注册错误 GetLastError={err}")
                except Exception as exc:
                    self._hk_reg_msgs.append(f"[热键注册异常] {exc!r}")
                finally:
                    self._hk_reg_ready.set()
            except Exception as exc:
                try:
                    self.log(f"[热键线程异常] {exc!r}")
                except Exception:
                    pass
                self._hk_hwnd_ready.set()
                return
            self._hk_hwnd_ready.set()

            class MSG(ctypes.Structure):
                _fields_ = [("hwnd", wintypes.HWND), ("message", wintypes.UINT),
                            ("wParam", wintypes.WPARAM), ("lParam", wintypes.LPARAM),
                            ("time", wintypes.DWORD), ("pt", wintypes.POINT)]
            WM_HOTKEY = 0x0312
            msg = MSG()
            while True:
                r = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if r <= 0:   # 0=WM_QUIT，-1=出错 → 退出线程
                    break
                if msg.message == WM_HOTKEY:
                    try:
                        self._hk_queue.put(msg.wParam)
                    except Exception:
                        pass
                else:
                    user32.TranslateMessage(ctypes.byref(msg))
                    user32.DispatchMessageW(ctypes.byref(msg))

        # v10.8.1 修复：补上线程启动（此前只定义了 _run 未 start，热键线程从未运行）
        th = threading.Thread(target=_run, name="hotkey-thread", daemon=True)
        th.start()

    def _stop_hotkey_thread(self):
        """退出热键线程：向线程消息队列投递 WM_QUIT。"""
        try:
            if getattr(self, "_hk_thread_id", None):
                import ctypes
                ctypes.windll.user32.PostThreadMessageW(self._hk_thread_id, 0x0012, 0, 0)  # WM_QUIT
        except Exception:
            pass

    @staticmethod
    def _parse_hotkey_spec(spec):
        """解析 'ctrl+alt+shift+m' 为 (mods, vk)；不合法返回 None。"""
        if not spec:
            return None
        mod_map = {"ctrl": 0x2, "alt": 0x1, "shift": 0x4, "win": 0x8}
        vk_map = {"esc": 0x1B, "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73,
                  "f5": 0x74, "f6": 0x75, "f7": 0x76, "f8": 0x77, "f9": 0x78,
                  "f10": 0x79, "f11": 0x7A, "f12": 0x7B, "space": 0x20,
                  "enter": 0x0D, "tab": 0x09, "delete": 0x2E, "backspace": 0x08}
        mods, key = 0, None
        for p in str(spec).lower().replace(" ", "").split("+"):
            if not p:
                continue
            if p in mod_map:
                mods |= mod_map[p]
            else:
                key = p
        if key is None:
            return None
        if len(key) == 1 and key.isalnum():
            vk = ord(key.upper())
        elif key in vk_map:
            vk = vk_map[key]
        else:
            return None
        return (mods | 0x4000, vk)  # MOD_NOREPEAT 防连发

    def _register_hotkeys(self):
        """回收热键线程内注册结果并写日志。

        v10.8.1：RegisterHotKey 与窗口必须同线程（跨线程返回 ERROR_INVALID_WINDOW_HANDLE
        1408），因此注册动作整体在热键线程内完成，本方法只等待完成信号并输出结果日志。
        RegisterHotKey 为系统级全局注册：注册到隐藏窗口后，无论哪个程序在前台，
        WM_HOTKEY 都会投递到该窗口所属的热键线程消息队列，由 GetMessage 循环接收。
        返回 FALSE 即占用冲突，保留原冲突检测提示。
        """
        # 等待热键线程创建隐藏窗口（最长 2s）；超时/失败则本次注册直接按失败处理
        try:
            self._hk_hwnd_ready.wait(2)
        except Exception:
            pass
        hk_hwnd = getattr(self, "_hk_hwnd", None)
        if not hk_hwnd:
            self._hotkey_ok = False
            self.log("[热键注册失败] 热键接收窗口创建失败，全局热键不可用")
            return
        try:
            self._hk_reg_ready.wait(3)
        except Exception:
            pass
        for msg in list(getattr(self, "_hk_reg_msgs", []) or []):
            try:
                self.log(msg)
            except Exception:
                pass
        if not getattr(self, "_hk_toggle", None):
            self._hotkey_ok = False

    def _unregister_hotkeys(self):
        try:
            u = getattr(self, "_user32", None)
            if u is None:
                return
            hk_hwnd = getattr(self, "_hk_hwnd", None) or None
            if getattr(self, "_hk_toggle", None):
                u.UnregisterHotKey(hk_hwnd, self._hotkey_id)
            if getattr(self, "_hk_quit", None):
                u.UnregisterHotKey(hk_hwnd, self._quit_hotkey_id)
            if getattr(self, "_hk_refresh", None):
                u.UnregisterHotKey(hk_hwnd, self._refresh_hotkey_id)
            if getattr(self, "_hk_show", None):
                u.UnregisterHotKey(hk_hwnd, self._show_hotkey_id)
            if getattr(self, "_hk_hide", None):
                u.UnregisterHotKey(hk_hwnd, self._hide_hotkey_id)
        except Exception:
            pass

    def _poll_hotkeys(self):
        """200ms 轮询热键线程队列（独立线程 GetMessage 收到 WM_HOTKEY 后入队，主线程取用执行）。"""
        try:
            while True:
                try:
                    wparam = self._hk_queue.get_nowait()
                except queue.Empty:
                    break
                if wparam == self._hotkey_id:
                    self._on_toggle_hotkey()
                elif wparam == self._quit_hotkey_id:
                    self._quit_app()
                elif wparam == self._refresh_hotkey_id:
                    self._on_refresh_hotkey()
                elif wparam == self._show_hotkey_id:
                    self._on_show_hotkey()
                elif wparam == self._hide_hotkey_id:
                    self._on_hide_hotkey()
        except Exception:
            pass
        try:
            self.root.after(200, self._poll_hotkeys)
        except Exception:
            pass

    def _on_toggle_hotkey(self):
        """魔法按键：呼出/隐藏主窗口。"""
        if self.root.state() == "withdrawn" or not self.root.winfo_viewable():
            self._show_main_window()
        else:
            self._hide_main_window()

    # ---- v10.15：显示/隐藏专用热键（ctrl+alt+shift+f / ctrl+alt+shift+c）----
    # v10.16.2：语义为监控启停——F=启动自动监控，C=停止自动监控（均不涉及窗口显隐）
    def _on_show_hotkey(self):
        """ctrl+alt+shift+f：启动自动监控（不涉及显示窗口）。

        与「启动自动监控」按钮同一路径（start_monitor）；窗口显隐仅由 D 键魔法键负责。
        未运行则启动监控；已运行时只播提示音（防连按不重复操作）。
        """
        if not self.monitor_running:
            self.start_monitor()
            self._play_hotkey_sound("open_done")
            self.log("[热键] ctrl+alt+shift+f 已启动自动监控")
            self.ops_log("[热键] ctrl+alt+shift+f 启动自动监控")
        else:
            self._play_hotkey_sound("open_already")
            self.log("[热键] ctrl+alt+shift+f 自动监控已在运行，仅提示（防连按）")

    def _on_hide_hotkey(self):
        """ctrl+alt+shift+c：停止自动监控（不涉及隐藏窗口）。

        与「停止监控」按钮同一路径（stop_monitor）；窗口显隐仅由 D 键魔法键负责。
        运行中则停止监控；未运行时只播提示音（防连按不重复操作）。
        """
        if self.monitor_running:
            self.stop_monitor()
            self._play_hotkey_sound("hide_done")
            self.log("[热键] ctrl+alt+shift+c 已停止自动监控")
            self.ops_log("[热键] ctrl+alt+shift+c 停止自动监控")
        else:
            self._play_hotkey_sound("hide_already")
            self.log("[热键] ctrl+alt+shift+c 自动监控未在运行，仅提示（防连按）")

    def _play_hotkey_sound(self, kind):
        """热键提示音（后台线程播放，不阻塞主线程）。

        频率与现有告警音区分（现有：220Hz 负面 / 880Hz 正常滴 / 1300Hz 加预算）：
        open_done=1568→2093 上行双音（打开成功）；open_already=1568 单短音（已打开）；
        hide_done=1046→784 下行双音（关闭成功）；hide_already=1046 单短音（已隐藏）。
        """
        seqs = {
            "open_done":    [(1568, 110), (0, 60), (2093, 150)],
            "open_already": [(1568, 90)],
            "hide_done":    [(1046, 110), (0, 60), (784, 150)],
            "hide_already": [(1046, 90)],
        }
        seq = seqs.get(kind)
        if not seq:
            return

        def _play():
            try:
                for freq, ms in seq:
                    if freq == 0:
                        time.sleep(ms / 1000.0)
                    else:
                        winsound.Beep(freq, ms)
            except Exception:
                pass

        threading.Thread(target=_play, daemon=True, name="hotkey-sound").start()

    def _on_refresh_hotkey(self):
        """手动热键 ctrl+alt+shift+r（v10.14.3）=「刷新后扫描」的另一种触发方式。

        与自动监控同一路径：统一走 _monitor_round，内部先 _maybe_refresh_target
        （uia_reload 抢前台刷新，是否刷新由 UI「刷新浏览器」勾选框 refresh_enabled 决定）
        再识别入库（push=False）。差异仅在于触发来源与 push 参数。
        修正 v10.14.2 实现：先独立 _send_refresh_to_target 再 _monitor_round 的两段式，
        且 mod 内最小化窗口导致 Chromium 暂停渲染、UIA 读不到表格（no_header 根因）。
        后台线程执行不阻塞 UI；手动场景沿用 v10.9 原则：告警只记录不远程推送（push=False）、
        不响声音（用户人在电脑前）。
        """
        self.log("[热键] 手动触发『刷新后扫描』（与自动监控同一路径，push=False）")
        self.ops_log("[热键] 手动触发（ctrl+alt+shift+r）")
        # v10.26：热键为手动强制路径——刷新门控 force 直通（并解除可能挂起的「待刷」）
        self._refresh_gate_force_direct("热键刷新")
        try:
            t = self._current_target()
        except Exception:
            t = None
        if not t:
            # v10.14.1：从 debug 提升为 result——否则默认日志面板不显示，表现为"热键没反应"
            self.log("[热键] 已触发但未定位到当前目标，请先在目标栏设置/启用监控目标（含窗口锁定）后重试", level="result")
            self.ops_log("[热键] 未定位到当前目标，已中止")
            return

        def _worker():
            try:
                # v10.14.3：统一走 _monitor_round——内部先 _maybe_refresh_target（勾选框决定是否刷新）再识别
                self._monitor_round(t, push=False)
            except Exception as e:
                self.log(f"[热键] 异常 {e}", level="debug")
                self.ops_log(f"[热键] 异常 {e}")
                return
            # 完成后重置自动监控定时器重新计时（若自动监控在运行）
            if self.monitor_running:
                self._reset_monitor_timer = True
                self.log("[热键] 完成，自动监控定时器已重置重新计时")
                self.ops_log("[热键] 完成，已请求重置自动监控定时器")
            else:
                self.log("[热键] 完成（自动监控未运行，仅执行本轮）")
                self.ops_log("[热键] 完成（自动监控未运行）")

        threading.Thread(target=_worker, daemon=True, name="manual-refresh-scan").start()

    # ---- 托盘模式（pystray，仅 hide_mode=tray 时启用） ----
    def _ensure_tray(self):
        """托盘模式首次隐藏时创建系统托盘图标（极端模式不创建）。"""
        if getattr(self, "_tray_icon", None) is not None:
            return
        try:
            import pystray
            from PIL import Image, ImageDraw, ImageFont
        except Exception as exc:
            self.log(f"[托盘不可用] {exc}")
            return
        try:
            img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
            d = ImageDraw.Draw(img)
            d.rounded_rectangle([2, 2, 61, 61], radius=14, fill=(43, 43, 43, 255))
            try:
                f = ImageFont.truetype("arial.ttf", 30)
            except Exception:
                f = ImageFont.load_default()
            d.text((32, 30), "T", fill=(255, 255, 255, 255), font=f, anchor="mm")
            menu = pystray.Menu(
                pystray.MenuItem("显示/隐藏窗口", self._tray_toggle, default=True),
                pystray.MenuItem("简略模式", self._tray_mini,
                                 checked=lambda item: bool(self.var_mini_mode.get())),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("退出", self._tray_quit),
            )
            self._tray_icon = pystray.Icon("tkm_10.26", img, "tkm_10.26", menu)
            self._tray_icon.run_detached()
            self.log("托盘模式已启用（隐藏后可从托盘恢复）")
        except Exception as exc:
            self.log(f"[托盘启动失败] {exc}")

    def _tray_toggle(self, _icon=None, _item=None):
        self.root.after(0, self._toggle_main_window)

    def _tray_mini(self, _icon=None, _item=None):
        self.root.after(0, self._toggle_mini_mode)

    def _tray_quit(self, _icon=None, _item=None):
        self.root.after(0, self._quit_app)

    def _quit_app(self):
        """低调退出：注销热键、停热键线程、停托盘、销毁窗口。"""
        try:
            self._unregister_hotkeys()
        except Exception:
            pass
        try:
            self._stop_hotkey_thread()
        except Exception:
            pass
        try:
            if getattr(self, "_tray_icon", None) is not None:
                self._tray_icon.stop()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass
        try:
            import os
            os._exit(0)
        except Exception:
            pass

    # ---------- 简略模式小窗（v10.7 任务2：音乐播放器 mini 式，低调防围观）----------
    def _toggle_mini_mode(self):
        """视图菜单「简略模式」开关：创建/销毁置顶无边框小窗。"""
        if self.var_mini_mode.get():
            self._open_mini_window()
        else:
            self._close_mini_window()

    def _open_mini_window(self):
        """创建简略模式小窗：约 320x180、置顶、无边框可拖动、右键/Esc/角标关闭。"""
        win = getattr(self, "_mini_win", None)
        if win is not None and win.winfo_exists():
            return
        win = tk.Toplevel(self.root)
        win.title(str(self._lowkey.get("window_title") or "tkm_10.26"))
        win.geometry("320x180")
        if self._lowkey.get("mini_always_on_top", True):
            win.attributes("-topmost", True)
        try:
            win.overrideredirect(True)  # 无边框更低调
        except Exception:
            pass
        self._mini_win = win
        self._mini_drag = {"x": 0, "y": 0}
        # 标题栏（拖动区 + 关闭角标）
        bar = tk.Frame(win, bg="#2b2b2b", cursor="fleur")
        bar.pack(fill="x")
        lbl_title = tk.Label(bar, text="简略", bg="#2b2b2b", fg="white",
                             font=("Microsoft YaHei", 9, "bold"))
        lbl_title.pack(side="left", padx=8, pady=4)
        btn_close = tk.Label(bar, text=" ✕ ", bg="#2b2b2b", fg="white", cursor="hand2")
        btn_close.pack(side="right", padx=4)
        btn_close.bind("<Button-1>", lambda e: self._close_mini_window(restore_main=True))

        def _start_drag(evt):
            self._mini_drag["x"] = evt.x_root - win.winfo_x()
            self._mini_drag["y"] = evt.y_root - win.winfo_y()

        def _do_drag(evt):
            try:
                win.geometry(f"+{evt.x_root - self._mini_drag['x']}+{evt.y_root - self._mini_drag['y']}")
            except Exception:
                pass

        for wdg in (bar, lbl_title):
            wdg.bind("<ButtonPress-1>", _start_drag)
            wdg.bind("<B1-Motion>", _do_drag)
        win.bind("<Escape>", lambda e: self._close_mini_window())
        # v10.7.6：mini 右键菜单（展开完整界面 / 隐藏 / 退出，低调退出入口）
        mini_menu = tk.Menu(win, tearoff=0)
        mini_menu.add_command(label="展开完整界面", command=self._expand_from_mini)
        mini_menu.add_command(label="隐藏", command=self._hide_main_window)
        mini_menu.add_separator()
        mini_menu.add_command(label="退出", command=self._quit_app)

        def _popup_mini_menu(evt):
            try:
                mini_menu.tk_popup(evt.x_root, evt.y_root)
            finally:
                try:
                    mini_menu.grab_release()
                except Exception:
                    pass

        win.bind("<Button-3>", _popup_mini_menu)
        # 内容区：目标名 + 每计划一行 + 最近识别时间
        body = tk.Frame(win, bg="#1e1e1e")
        body.pack(fill="both", expand=True, padx=8, pady=6)
        self._mini_lbl_target = tk.Label(body, text="目标：-", anchor="w", bg="#1e1e1e", fg="#e8e8e8",
                                         font=("Microsoft YaHei", 9, "bold"))
        self._mini_lbl_target.pack(fill="x")
        self._mini_txt = tk.Text(body, bg="#1e1e1e", fg="#8fd08f", font=("Consolas", 9),
                                 relief="flat", highlightthickness=0, state="disabled", height=6)
        self._mini_txt.pack(fill="both", expand=True, pady=(4, 0))
        self._mini_lbl_time = tk.Label(body, text="最近识别：-", anchor="w", bg="#1e1e1e", fg="#aaaaaa",
                                       font=("Microsoft YaHei", 8))
        self._mini_lbl_time.pack(fill="x", pady=(2, 0))
        # v10.7.6：双击内容区展开完整界面
        body.bind("<Double-Button-1>", lambda e: self._expand_from_mini())
        win.protocol("WM_DELETE_WINDOW", self._close_mini_window)
        self._refresh_mini_window()
        # v10.7.6：mini 持续刷新（每 2 秒），不依赖识别触发
        self.root.after(2000, self._poll_mini_loop)

    def _close_mini_window(self, restore_main=False):
        """销毁简略模式小窗并同步菜单勾选状态。

        restore_main=True 时若主窗口处于隐藏状态则一并呼出（防止 mini 关闭后找不到界面）。
        """
        win = getattr(self, "_mini_win", None)
        if win is not None and win.winfo_exists():
            try:
                win.destroy()
            except Exception:
                pass
        self._mini_win = None
        if getattr(self, "var_mini_mode", None) is not None and self.var_mini_mode.get():
            self.var_mini_mode.set(False)
        if restore_main:
            if self.root.state() == "withdrawn" or not self.root.winfo_viewable():
                self._show_main_window()

    def _expand_from_mini(self):
        """mini → 展开完整界面：关闭 mini 并呼出主窗口。"""
        self._close_mini_window()
        self._show_main_window()

    def _poll_mini_loop(self):
        """mini 小窗持续刷新：每 2 秒更新一次数据（独立于识别触发，识别间隔大也能实时看数）。"""
        win = getattr(self, "_mini_win", None)
        if win is None or not win.winfo_exists():
            return
        self._refresh_mini_window()
        try:
            self.root.after(2000, self._poll_mini_loop)
        except Exception:
            pass

    def _refresh_mini_window(self):
        """主线程刷新简略模式小窗内容（识别线程通过 root.after 调用，线程安全）。"""
        win = getattr(self, "_mini_win", None)
        if win is None or not win.winfo_exists():
            return
        rows = getattr(self, "last_ocr_rows", None)
        target = getattr(self, "last_ocr_target", "") or ""
        tstr = getattr(self, "last_ocr_time", "") or ""
        try:
            status = ""
            if rows:
                vals = [r.get("roi") for r in rows if r.get("roi") is not None]
                if vals:
                    best = max(vals)
                    if best >= self.cfg.get("roi_good", 1.3):
                        status = "  [达标]"
                    elif best < self.cfg.get("roi_low", 0.6):
                        status = "  [走量线下]"
                    else:
                        status = "  [正常]"
            self._mini_lbl_target.config(text=f"目标：{target}{status}")
            self._mini_txt.config(state="normal")
            self._mini_txt.delete("1.0", "end")
            if rows:
                for r in rows:
                    name = r.get("plan_name") or r.get("计划名") or "-"
                    roi = r.get("roi") if r.get("roi") is not None else (r.get("ROI") or "-")
                    cost = r.get("cost") if r.get("cost") is not None else (r.get("成本") or "-")
                    revenue = r.get("revenue") if r.get("revenue") is not None else (r.get("收入") or "-")
                    orders = r.get("orders") if r.get("orders") is not None else (r.get("订单") or "-")
                    self._mini_txt.insert("end", f"{name}  ROI {roi}  成本{cost}  收入{revenue}  订单{orders}\n")
            else:
                self._mini_txt.insert("end", "待识别")
            self._mini_txt.config(state="disabled")
            self._mini_lbl_time.config(text=f"最近识别：{tstr}")
        except Exception:
            pass

    # ---------- 主面板滚动容器 ----------
    def _on_main_frame_configure(self, _evt=None):
        """内部 Frame 尺寸变化 -> 刷新 Canvas 滚动区域"""
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event):
        """Canvas 宽度变化 -> 内部 Frame 宽度跟随（内容整宽显示，不横向滚动）"""
        self.canvas.itemconfig(self._canvas_win, width=event.width)

    def _on_canvas_mousewheel(self, event):
        """Windows 滚轮滚动主面板（delta 为 120 的倍数）"""
        if event.delta:
            self.canvas.yview_scroll(-int(event.delta / 120), "units")
        return "break"

    def _bind_panel_wheel_recursive(self, widget):
        """递归为主面板子控件绑定滚轮；日志区 Text/Scrollbar 排除（它们自带独立滚动）"""
        if widget is getattr(self, "txt_log", None) or widget is getattr(self, "sb_log", None):
            return
        try:
            widget.bind("<MouseWheel>", self._on_canvas_mousewheel)
        except tk.TclError:
            pass
        for child in widget.winfo_children():
            self._bind_panel_wheel_recursive(child)

    def _apply_config_to_ui(self):
        # 任务A：窗口关键词/ROI 显示当前目标的独立配置
        cur = self._current_target()
        self.e_window.delete(0, tk.END)
        self.e_window.insert(0, str(cur.get("window_keyword") or self.cfg.get("window_keyword", "商户中心")))
        roi = cur.get("roi")
        if roi and len(roi) == 4:
            self.e_rl.insert(0, str(roi[0]))
            self.e_rt.insert(0, str(roi[1]))
            self.e_rw.insert(0, str(roi[2]))
            self.e_rh.insert(0, str(roi[3]))
            self.lbl_roi.config(text=f"({roi[0]},{roi[1]}) {roi[2]}x{roi[3]}", fg="green")
        self.e_interval.insert(0, str(self.cfg.get("interval_minutes", 60)))
        self.var_alert.set(bool(self.cfg.get("alert_enabled", True)))
        self.txt_alert_template.delete("1.0", tk.END)
        self.txt_alert_template.insert("1.0", self.cfg.get("alert_template", ""))
        for key, e in self.sc_entries.items():
            e.insert(0, str(self.cfg.get(key, "")))
        self.var_strict.set(bool(self.cfg.get("strict_enabled", True)))
        self.var_consist.set(bool(self.cfg.get("roi_consistency", True)))
        self.var_no_header.set(str(self.cfg.get("no_header_policy", "fail")))
        for key, e in self.rr_entries.items():
            e.delete(0, tk.END)
            e.insert(0, str(self.cfg.get(key, "")))
        self._refresh_skip_words_btn()
        self._refresh_exclude_btn()
        self._refresh_rules_btn()
        self.var_plan_name_check.set(bool(self.cfg.get("plan_name_check", True)))
        # 识别模式（模式二控件树读取）与滚动收集开关
        self.var_recog_mode.set(str(self.cfg.get("recog_mode", "uia") or "uia"))
        self.var_uia_scroll.set(bool(self.cfg.get("uia_scroll_collect", False)))
        self.e_orders.insert(0, str(self.cfg.get("current_orders", 0)))
        self.e_wecom.insert(0, self.cfg.get("wecom_key", ""))
        self.var_wecom.set(bool(self.cfg.get("wecom_enabled", False)))
        self.e_ding.insert(0, self.cfg.get("dingtalk_url", ""))
        self.var_ding.set(bool(self.cfg.get("dingtalk_enabled", False)))
        self.e_pushplus.insert(0, self.cfg.get("pushplus_token", ""))
        self.var_pushplus.set(bool(self.cfg.get("pushplus_enabled", True)))
        # v10.14.3：刷新浏览器勾选框回填（默认 True）；v10.9.1：告警声音开关回填；v10.10：告警 toast 开关回填（默认 False 与 DEFAULT_CONFIG 一致）
        self.var_refresh.set(bool(self.cfg.get("refresh_enabled", True)))
        self.var_sound_alarm.set(bool(self.cfg.get("sound_alarm", True)))
        self.var_toast_enabled.set(bool(self.cfg.get("toast_enabled", False)))
        # 任务A：刷新目标切换条
        self._refresh_target_bar()
        # 任务C：视图菜单勾选状态与折叠/隐藏状态同步
        self._sync_section_vars()
        # 配置加载完成后刷新推送状态提示（逻辑闭环：状态文字与 UI 实际勾选一致）
        self.update_push_status()

    # ---------- 日志（任务B：三级分级 result/info/debug） ----------
    @staticmethod
    def _log_level_no(level):
        """等级数值：result=3 / info=2 / debug=1（越大越重要）"""
        return {"result": 3, "info": 2, "debug": 1}.get(level, 2)

    @staticmethod
    def _log_level_tag(level):
        """等级中文标签（落盘与面板显示均带）"""
        return {"result": "结果", "info": "信息", "debug": "排错"}.get(level, "信息")

    def _log_tag(self, level, msg=""):
        """面板 tag：结果级醒目（告警行更醒目红色），排错级灰色，信息级默认"""
        if level == "result":
            if str(msg).startswith("[告警]"):
                return "log_alert"
            return "log_result"
        if level == "debug":
            return "log_debug"
        return ()

    def log(self, msg, level="info"):
        def _do():
            ts = datetime.now().strftime("%H:%M:%S")
            tag = self._log_level_tag(level)
            full = f"[tkm_10.26] {msg}"
            # 落盘：所有等级全量写入 monitor_YYYYMMDD.log（v10.12 起按天轮转；显示过滤不影响落盘）
            _append_day_log("monitor", f"[{ts}][{tag}] {full}\n")
            # 显示过滤：仅显示 >= 当前显示等级的行；记录全量行供切换等级后重绘
            self._log_lines.append((ts, level, full))
            if self._log_level_no(level) < self._log_min_level:
                return
            self.txt_log.config(state="normal")
            at_bottom = self._log_at_bottom()
            line_tag = self._log_tag(level, full)
            self.txt_log.insert(tk.END, f"[{ts}][{tag}] {full}\n", line_tag)
            # 仅在日志区处于底部时自动跟随新日志；用户上拉查看历史时不强制跳底
            if at_bottom:
                self.txt_log.see(tk.END)
            self.txt_log.config(state="disabled")
        self.root.after(0, _do)

    def ops_log(self, msg):
        """v10.12：操作级日志——记录 UIA 控件操作细节（找到的控件/点击动作/最小化动作/
        快照数值/就绪轮询等），独立落盘 ops_YYYYMMDD.log（按天轮转），与主流程 monitor 日志分开。
        v10.14.4：统一带版本前缀 [tkm_10.14.5]，msg 内自带模块标签（如 [窗口]）。
        仅落盘不显示 UI；可被后台线程直接调用（不经过 root.after）。"""
        try:
            _append_day_log("ops", f"[{datetime.now():%H:%M:%S}][tkm_10.26] {msg}\n")
        except Exception:
            pass

    def _set_log_level(self):
        """视图菜单「日志等级」切换：更新显示阈值并按新等级重绘日志框"""
        self._log_min_level = self._log_level_no(self.var_log_level.get())
        self._redraw_log()

    def _redraw_log(self):
        """按当前显示等级重绘日志框（落盘内容不受影响）"""
        self.txt_log.config(state="normal")
        self.txt_log.delete("1.0", tk.END)
        for ts, level, msg in self._log_lines:
            if self._log_level_no(level) < self._log_min_level:
                continue
            line_tag = self._log_tag(level, msg)
            tag_name = self._log_level_tag(level)
            self.txt_log.insert(tk.END, f"[{ts}][{tag_name}] {msg}\n", line_tag)
        self.txt_log.config(state="disabled")
        self.txt_log.see(tk.END)

    def _log_at_bottom(self):
        """判断日志区是否已滚动到底部（yview 末位接近 1.0）"""
        try:
            _first, last = self.txt_log.yview()
            return last >= 0.999
        except Exception:
            return True

    def _on_log_mousewheel(self, event):
        """Windows 滚轮滚动日志区：delta 为 120 的倍数，每格滚动 3 行"""
        if event.delta:
            step = -1 * (event.delta // 120) * 3
            self.txt_log.yview_scroll(step, "units")
        return "break"

    def _bind_log_wheel(self, _evt=None):
        """鼠标进入日志区：绑定全局滚轮，日志区优先响应"""
        self.root.bind_all("<MouseWheel>", self._on_log_mousewheel)

    def _unbind_log_wheel(self, _evt=None):
        """鼠标离开日志区：解绑全局滚轮，恢复其他控件默认滚轮行为"""
        self.root.unbind_all("<MouseWheel>")

    def clear_log(self):
        self._log_lines = []
        self.txt_log.config(state="normal")
        self.txt_log.delete("1.0", tk.END)
        self.txt_log.config(state="disabled")

    def set_last_result(self, text):
        def _do():
            self.lbl_last.config(text=text, fg="#1565C0")
        self.root.after(0, _do)

    def _store_last_ocr_result(self, roi, cost, revenue, detail, rows=None, target=None):
        """结构化记录最近一次识别结果（供 AI 调试 mod GET /status 等读取）。

        v10.7：额外保存全行明细 last_ocr_rows + 目标名 last_ocr_target + 时间 last_ocr_time，
        供简略模式小窗展示；线程安全——只写 Python 普通字段，UI 刷新统一 root.after 到主线程。
        """
        plan_name = None
        try:
            if isinstance(detail, dict):
                plan_name = detail.get("plan_name")
                if not plan_name and detail.get("selected_index") is not None:
                    cands = detail.get("candidates") or []
                    sel = detail["selected_index"]
                    if isinstance(cands, list):
                        for c in cands:
                            if isinstance(c, dict) and c.get("row") == sel:
                                plan_name = c.get("plan_name")
                                break
        except Exception:
            plan_name = None
        self.last_ocr_result = {
            "roi": roi,
            "cost": cost,
            "revenue": revenue,
            "plan_name": plan_name,
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        # v10.7 任务2：简略模式数据源（全行明细 + 目标名 + 时间）
        self.last_ocr_rows = rows if isinstance(rows, list) else []
        self.last_ocr_target = (target.get("name") if isinstance(target, dict) else "") or ""
        self.last_ocr_time = self.last_ocr_result["time"]
        try:
            self.root.after(0, self._refresh_mini_window)
        except Exception:
            pass

    # ---------- 设置保存 ----------
    def save_window_keyword(self):
        # 任务A：窗口关键词写入当前目标（多店铺独立）
        t = self._current_target()
        kw = self.e_window.get().strip()
        t["window_keyword"] = kw
        self.cfg["window_keyword"] = kw  # 保留旧字段兼容
        save_config(self.cfg)
        self.log(f"目标[{t.get('name')}] 窗口关键词已保存: {kw}")

    def get_target_window(self, target=None):
        """优先按已锁定的窗口句柄定位，句柄失效时回退到目标关键词匹配。

        target: 目标 dict；None 时用当前目标（兼容旧调用）。
        """
        t = target if target is not None else self._current_target()
        tid = t.get("id")
        hwnd = self._target_hwnds.get(tid) if tid else None
        win = find_window_by_hwnd(hwnd)
        if win:
            return win
        kw = str(t.get("window_keyword") or "").strip()
        if not kw:
            kw = self.e_window.get().strip()
        return find_window(kw)

    def test_window(self):
        self.log(">>> 点击查找窗口")
        kw = self.e_window.get().strip()
        win = self.get_target_window()
        if win:
            self.lbl_win.config(text=f"找到: {win['title'][:28]} ({win['width']}x{win['height']})", fg="green")
            self.log(f"找到窗口: {win['title']} @ ({win['left']},{win['top']}) {win['width']}x{win['height']}")
        else:
            self.lbl_win.config(text="未找到窗口", fg="red")
            if self.selected_hwnd and not find_window_by_hwnd(self.selected_hwnd):
                self.log(f"已锁定的窗口已失效（窗口可能被关闭或隐藏），关键词 [{kw}] 也匹配失败，请重新点『列出窗口』锁定")
            else:
                self.log(f"未找到标题含 [{kw}] 的窗口，请先打开后台")
            # 诊断：输出当前所有可见窗口标题，方便定位为什么匹配不上
            wins = list_windows()
            self.log(f"[诊断] 当前可见窗口共 {len(wins)} 个：")
            for i, (title, hwnd) in enumerate(wins, 1):
                self.log(f"[诊断] {i}. {title!r} (hwnd={hwnd})")

    def show_window_list(self):
        """弹出当前所有可见窗口标题，双击某行自动填入关键词输入框"""
        wins = list_windows()
        if not wins:
            messagebox.showinfo("列出窗口", "没有找到任何可见窗口")
            return
        top = tk.Toplevel(self.root)
        top.title("当前可见窗口列表")
        top.geometry("560x420")
        top.attributes("-topmost", True)
        tk.Label(top, text="找到 %d 个可见窗口，双击某一行自动填入关键词（填窗口标题里最稳的一段）" % len(wins),
                 fg="blue", padx=8, pady=4).pack(fill="x")
        frm = tk.Frame(top)
        frm.pack(fill="both", expand=True, padx=8, pady=4)
        sb = tk.Scrollbar(frm)
        sb.pack(side="right", fill="y")
        lb = tk.Listbox(frm, font=("Microsoft YaHei", 9), yscrollcommand=sb.set)
        lb.pack(side="left", fill="both", expand=True)
        sb.config(command=lb.yview)
        for title, hwnd in wins:
            lb.insert(tk.END, "%s    (hwnd=%s)" % (title, hwnd))

        def on_pick(_evt):
            sel = lb.curselection()
            if not sel:
                return
            idx = sel[0]
            title, hwnd = wins[idx]
            self.e_window.delete(0, tk.END)
            self.e_window.insert(0, title)
            # 任务A：句柄锁定到当前目标（各目标独立）
            t = self._current_target()
            tid = t.get("id")
            self._target_hwnds[tid] = hwnd
            self.selected_hwnd = hwnd
            t["window_keyword"] = title
            self.cfg["window_keyword"] = title
            save_config(self.cfg)
            self.lbl_win.config(text=f"已锁定窗口: {title[:24]} (hwnd={hwnd})", fg="green")
            self.log(f"已锁定目标[{t.get('name')}]窗口: {title} (hwnd={hwnd})，监控将优先按句柄定位（程序重启后需重新锁定）")
            top.destroy()

        lb.bind("<Double-Button-1>", on_pick)

    def save_roi(self):
        try:
            left, top = int(self.e_rl.get()), int(self.e_rt.get())
            width, height = int(self.e_rw.get()), int(self.e_rh.get())
            if width <= 0 or height <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("错误", "ROI 坐标必须是数字，宽高必须大于 0")
            return
        self.cfg["roi"] = [left, top, width, height]  # 保留旧字段兼容
        # 任务A：ROI 写入当前目标（多店铺独立）
        t = self._current_target()
        t["roi"] = [left, top, width, height]
        save_config(self.cfg)
        self.lbl_roi.config(text=f"({left},{top}) {width}x{height}", fg="green")
        self.log(f"目标[{t.get('name')}] ROI 已保存: ({left},{top}) {width}x{height}")

    def save_interval(self):
        try:
            val = int(self.e_interval.get())
        except ValueError:
            messagebox.showerror("错误", "间隔必须是数字")
            return
        if val < 1:
            messagebox.showerror("错误", "间隔至少 1 分钟")
            return
        self.cfg["interval_minutes"] = val
        save_config(self.cfg)
        self.log(f"轮询间隔已保存: {val} 分钟")

    def save_alert_basic(self):
        """v10.7.1：保存告警基础设置（总开关/推送模板）并落盘 config.json。

        核心条件/CVR/消耗×客单/前置消耗等阈值已全部收编到规则面板（open_rules_editor），
        此处不再保存 alert_metric/alert_operator/alert_threshold 等旧字段（兼容保留在 cfg 中）。
        """
        self.cfg["alert_enabled"] = bool(self.var_alert.get())
        tmpl = self.txt_alert_template.get("1.0", tk.END).strip()
        self.cfg["alert_template"] = tmpl if tmpl else ""
        save_config(self.cfg)
        self.log(f"告警设置已保存: {'开' if self.cfg['alert_enabled'] else '关'}")
        messagebox.showinfo("成功", "告警设置已保存")

    # ---------- v10.21 修复轮：自动操作面板 + 品档案面板（设计文档 13.2 / 12.7） ----------
    def save_auto_ops_settings(self):
        """保存自动操作设置（13.2）：总开关 / 无人值守秒数 / 仅已生效计划 / 无人自启开关，
        落盘 config.json 并立即刷新状态区。

        v10.26 增量：新增 only_active_plans 与 auto_hide_scan_enabled 两个键的落盘
        （对应 ③.5 面板两枚勾选框），保证 UI ↔ config ↔ 识别/自启逻辑贯通。
        """
        try:
            sec = int(float(self.spin_auto_ops_idle.get()))
        except ValueError:
            messagebox.showerror("错误", "无人值守秒数必须是数字")
            return
        if sec < 10:
            messagebox.showerror("错误", "无人值守秒数至少 10 秒")
            return
        self.cfg["auto_ops_enabled"] = bool(self.var_auto_ops_enabled.get())
        self.cfg["auto_ops_unattended_idle_sec"] = sec
        # v10.26：仅处理已生效计划（默认 True，与既有隐式消费口径一致）
        try:
            self.cfg["only_active_plans"] = bool(self.var_only_active_plans.get())
        except Exception:
            self.cfg["only_active_plans"] = bool(self.cfg.get("only_active_plans", True))
        # v10.26：无人达标自动隐藏并开始扫描开关（独立于总开关；默认关）
        _prev_hide_scan = bool(self.cfg.get("auto_hide_scan_enabled", False))
        try:
            self.cfg["auto_hide_scan_enabled"] = bool(self.var_auto_hide_scan.get())
        except Exception:
            self.cfg["auto_hide_scan_enabled"] = _prev_hide_scan
        # 本次保存把该开关由关→开：重置一次性触发标记，允许在下一个检测轮重新武装
        if self.cfg["auto_hide_scan_enabled"] and not _prev_hide_scan:
            self._auto_hide_scan_done = False
        save_config(self.cfg)
        self.log(
            f"自动操作设置已保存: 总开关={'开' if self.cfg['auto_ops_enabled'] else '关'}, "
            f"无人值守阈值={sec}s, 仅已生效计划={'开' if self.cfg['only_active_plans'] else '关'}, "
            f"无人自启={'开' if self.cfg['auto_hide_scan_enabled'] else '关'}"
        )
        self._refresh_auto_ops_status()

    def force_auto_close_check_now(self):
        """③.5「立即检测」按钮（v10.24）：强制完整执行自动关检测——立即复用完整链路跑一轮
        （读表格→识别→判定→应关即真关），不等下个识别轮、不过无人值守闸门、有人值守也执行。

        复用链（与自动路径 push=True 等价）：_monitor_round（先刷新后识别、逐行入库）
        → run_alerts(push=False, force_close=True) → ops_decision_close 逐行品龄判定
        收集 auto_close_rows → _auto_close_store_pending 入值守队列 →
        _auto_close_watchdog(force=True) 跳过 is_unattended 闸门立即真关（auto_close_ad 点击）。
        手动触发故 push=False：不远程推送/不 toast/不响（用户在电脑前，v10.9 原则），
        但 force_close=True 保证自动关候选仍立即执行（不做"只报不动"）。

        防重入：_monitor_round 识别链单目标并发可能打架，用 _force_check_busy 串行——
        重复点击直接提示等待，不叠加执行。
        """
        if getattr(self, "_force_check_busy", False):
            self.log("立即检测：上一轮强制检测仍在执行中，请稍候再点", level="result")
            return
        try:
            t = self._current_target()
        except Exception:
            t = None
        if not t:
            self.log("立即检测：未定位到当前目标，请先在目标栏设置/启用监控目标（含窗口锁定）后重试", level="result")
            self.ops_log("[立即检测] 未定位到当前目标，已中止")
            return
        name = t.get("name") or f"目标{t.get('id')}"
        self.log(f"[立即检测] 强制完整执行开始（目标：{name}）：读表格→识别→判定→应关即真关", level="result")
        self.ops_log(f"[立即检测] 手动强制完整执行：{name}")
        self._force_check_busy = True
        # v10.26：面板「立即检测」为手动强制路径——刷新门控 force 直通（解除挂起「待刷」）
        self._refresh_gate_force_direct("面板立即检测")

        def _worker():
            try:
                # 与自动路径同一执行链；push=False（手动不推送不响）+ force_close=True（应关即真关）
                self._monitor_round(t, push=False, force_close=True)
            except Exception as e:
                self.log(f"[立即检测] 异常: {e}", level="debug")
                self.ops_log(f"[立即检测] 异常: {e}")
                return
            finally:
                try:
                    self._force_check_busy = False
                except Exception:
                    pass
            # 手动完整轮结束：若自动监控在运行，重置其轮询定时器重新计时（同热键语义）
            if self.monitor_running:
                self._reset_monitor_timer = True
                self.log("[立即检测] 完成，自动监控定时器已重置重新计时")
                self.ops_log("[立即检测] 完成，已请求重置自动监控定时器")
            else:
                self.log("[立即检测] 完成（自动监控未运行，仅执行本轮）")
                self.ops_log("[立即检测] 完成（自动监控未运行）")
            # 面板状态只读区立即刷新（最近自动关事件/无人值守可能已变化）
            try:
                self._refresh_auto_ops_status()
            except Exception:
                pass

        threading.Thread(target=_worker, daemon=True, name="force-close-check").start()

    def _refresh_gate_force_direct(self, src):
        """v10.26：刷新门控「手动强制直通」入口。

        手动路径（热键刷新 / 面板「立即识别」/「立即检测」）不参与「有人不刷」门控——用户
        主动操作即视为授权立即刷新（与 idle_gate.is_unattended(force=True) 的 force 语义一致）。
        同时解除可能挂起的「待刷」标记：本次手动刷新已满足那一次刷新需求，无需再补刷。
        """
        try:
            _cleared, _why = self._refresh_gate.clear_pending(reason=src)
            if _cleared:
                self.log(f"[刷新门控] {src}：手动强制直通，{_why}")
                self.ops_log(f"[刷新门控] {src}：手动强制直通，{_why}")
            else:
                self.log(f"[刷新门控] {src}：手动强制直通（无挂起待刷）")
        except Exception as e:
            self.log(f"[刷新门控] {src}：门控直通处理异常（不影响本次刷新）: {e}", level="debug")

    def _tick_auto_ops_status(self):
        """周期刷新自动操作状态只读区（每 2 秒；仅 GetLastInputInfo/GetTickCount，开销极小）。

        v10.26 增量：同轮顺带执行「无人达标自动隐藏并开始扫描」检测
        （_check_auto_hide_and_scan，复用本 2 秒检测循环，不另起定时器）。
        """
        try:
            self._refresh_auto_ops_status()
        except Exception:
            pass
        try:
            self._check_auto_hide_and_scan()
        except Exception:
            pass
        try:
            self._auto_ops_tick_id = self.root.after(2000, self._tick_auto_ops_status)
        except Exception:
            pass

    def _check_auto_hide_and_scan(self):
        """v10.26：无人达标 → 赋值式隐藏主窗口 + 自动开始监控扫描（防“打开程序忘记启动”）。

        触发条件（须同时满足）：
          ① cfg["auto_hide_scan_enabled"] 为真（独立开关，不受 auto_ops_enabled 总开关约束）；
          ② 空闲判断机（idle_gate.is_unattended(cfg, force=False, require_master=False)，
             只判空闲、不受 auto_ops_enabled 总开关约束）判定「无人达标」；
          ③ 自动监控当前未运行（self.monitor_running 为假）。
        动作：
          ① 隐藏——复用程序现有 _hide_main_window（withdraw，赋值式“隐藏”语义，非 toggle
             状态翻转；已隐藏则保持隐藏），隐藏过程不弹窗、不抢焦点；
          ② 启动——调用现有 start_monitor（等价于点「开始监控」按钮，不模拟鼠标点击/热键）。
        触发后置 _auto_hide_scan_done 一次性标记防重复；监控已在运行同样短路。
        """
        if getattr(self, "_auto_hide_scan_done", False):
            return
        if not bool(self.cfg.get("auto_hide_scan_enabled", False)):
            # 开关关闭：解除一次性标记，便于用户再次开启后重新武装
            self._auto_hide_scan_done = False
            return
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if bool(getattr(self, "monitor_running", False)):
            self._auto_hide_scan_done = True
            self.log(f"[无人自启] 开关=开但自动监控已在运行，跳过（不隐藏、不重复启动）；时间={ts}")
            return
        # v10.26：空闲判定统一走「空闲判断机」idle_gate（require_master=False——
        # 本开关独立于 auto_ops_enabled 总开关，只看键鼠空闲是否 ≥ 无人值守秒数）
        try:
            _idle_ok, _idle_why = idle_gate.is_unattended(self.cfg, force=False, require_master=False)
        except Exception as e:
            self.log(f"[无人自启] 空闲判断机异常，本轮不触发: {e}", level="debug")
            return
        if not _idle_ok:
            return
        thr = idle_gate.idle_threshold(self.cfg)
        idle = float(idle_gate.get_idle_sec())
        # 一次性标记先置位，防止隐藏/启动过程重入本检测
        self._auto_hide_scan_done = True
        try:
            self.log(f"[无人自启] 触发：开关=开，空闲={idle:.0f}s ≥ 阈值={thr}s，时间={ts}")
            self.ops_log(f"[无人自启] 触发：开关=开，空闲={idle:.0f}s ≥ 阈值={thr}s，时间={ts}")
        except Exception:
            pass
        # ① 赋值式隐藏主窗口：走现有 hide 实现（withdraw），非切换；已隐藏则保持隐藏
        try:
            _was_hidden = False
            try:
                _was_hidden = (str(self.root.state()) == "withdrawn") or (not self.root.winfo_viewable())
            except Exception:
                _was_hidden = False
            if not _was_hidden:
                self._hide_main_window()
            self.log(f"[无人自启] 已隐藏主窗口（赋值式 withdraw，非切换；隐藏前已隐藏={_was_hidden}）："
                     f"空闲={idle:.0f}s ≥ 阈值={thr}s，开关=开，时间={ts}")
            self.ops_log(f"[无人自启] 已隐藏主窗口（赋值式，非切换）：空闲={idle:.0f}s ≥ 阈值={thr}s，"
                         f"开关=开，时间={ts}")
        except Exception as e:
            self.log(f"[无人自启] 隐藏主窗口失败: {e}")
        # ② 自动开始监控扫描：复用现有 start_monitor（等价点「开始监控」按钮，非模拟点击热键）
        try:
            self.start_monitor()
            self.log(f"[无人自启] 已自动开始监控扫描（start_monitor）：空闲={idle:.0f}s ≥ 阈值={thr}s，"
                     f"开关=开，时间={ts}")
            self.ops_log(f"[无人自启] 已自动开始监控扫描：空闲={idle:.0f}s ≥ 阈值={thr}s，开关=开，时间={ts}")
        except Exception as e:
            self.log(f"[无人自启] 自动开始监控失败: {e}")
            self.ops_log(f"[无人自启] 自动开始监控失败: {e}")
        # 状态只读区立即刷新（窗口被召回后可直观看到“无人自启已触发”）
        try:
            self._refresh_auto_ops_status()
        except Exception:
            pass

    def _refresh_auto_ops_status(self):
        """刷新状态只读区：键鼠空闲秒数 / 是否无人值守 / 最近自动关事件。

        tkm_10.23 守夜版：若面板控件当前值与已保存 cfg 不一致（改过未点保存），
        状态区显示控件预览值并标注“未保存”，避免“我明明开了却显示否/300秒”的困惑；
        已保存值/值守待命数/执行中一并展示。
        """
        try:
            idle = idle_gate.get_idle_sec()   # v10.26：统一走空闲判断机（原 _system_idle_seconds 已外迁）
            cfg_thr = int(self.cfg.get("auto_ops_unattended_idle_sec", 300) or 300)
            cfg_en = bool(self.cfg.get("auto_ops_enabled", False))
            # 面板控件实时预览（存在才读；未保存时以控件值为准提示）
            ui_thr, ui_en, has_ui = cfg_thr, cfg_en, False
            try:
                _spin = getattr(self, "spin_auto_ops_idle", None)
                _var = getattr(self, "var_auto_ops_enabled", None)
                if _spin is not None and _var is not None:
                    ui_thr = int(float(_spin.get()))
                    ui_en = bool(_var.get())
                    has_ui = True
            except Exception:
                pass
            if has_ui and (ui_thr != cfg_thr or ui_en != cfg_en):
                thr, en, mark = ui_thr, ui_en, "（未保存，保存后生效）"
            else:
                thr, en, mark = cfg_thr, cfg_en, ""
            self.lbl_ac_idle.config(text=f"键鼠空闲：{idle:.0f} 秒（无人值守阈值 {thr} 秒 {mark}".rstrip() + "）")
            if not en:
                _pend = 0
                try:
                    with self._auto_close_watch_lock:
                        _pend = len(self._auto_close_pending)
                except Exception:
                    pass
                _ext = f"，待命 {_pend} 候选" if _pend else ""
                self.lbl_ac_unattended.config(text=f"无人值守：否（自动操作总开关已关闭{_ext}）", fg="#333")
            elif idle >= thr:
                _watching = bool(getattr(self, "_auto_close_watching", False))
                _act = "值守执行中…" if _watching else "值守待命中"
                self.lbl_ac_unattended.config(text=f"无人值守：是（空闲 {idle:.0f}s ≥ {thr}s，{_act}）", fg="#0A7A0A")
            else:
                self.lbl_ac_unattended.config(text=f"无人值守：否（空闲 {idle:.0f}s < {thr}s，尚有人值守）", fg="#A66A00")
            evs = getattr(self, "_auto_close_events", None)
            if evs:
                lines = [f"{ts}  {pn}：{rs}" for ts, pn, rs in list(evs)[-3:]]
                self.lbl_ac_last.config(text="最近自动关事件：\n" + "\n".join(lines), fg="#333")
            else:
                self.lbl_ac_last.config(text="最近自动关事件：（无）", fg="#888")
        except Exception:
            pass

    def _record_auto_close_event(self, plan_name, result):
        """记录最近自动关事件（内存 deque，主/监控线程均写，供面板状态只读区展示）。"""
        try:
            from collections import deque
            ev = getattr(self, "_auto_close_events", None)
            if ev is None:
                ev = self._auto_close_events = deque(maxlen=20)
            ev.append((datetime.now().strftime("%H:%M:%S"), plan_name, str(result or "已执行")))
        except Exception:
            pass

    def _refresh_archive_view(self):
        """读取 plan_archive.json + history.db 最新一轮，刷新品档案表格（12.7 可视化）。"""
        try:
            arc = load_plan_archive()
            self.tree_archive.delete(*self.tree_archive.get_children())
            if not isinstance(arc, dict) or not arc:
                self.tree_archive.insert("", "end", values=("（品档案为空——自动监控识别后将自动建档）", "", "", "", "", "", ""))
                return
            stats = self._latest_round_product_stats()
            now = datetime.now()
            rows = []
            for pname, info in sorted(arc.items(), key=lambda kv: str(kv[0])):
                fs = info.get("first_seen") if isinstance(info, dict) else info
                fs_dt = None
                for _fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
                    try:
                        fs_dt = datetime.strptime(str(fs), _fmt)
                        break
                    except Exception:
                        continue
                if fs_dt is None:
                    rows.append((str(pname), str(fs or "—"), "—", "—", "档案异常", "—", "—"))
                    continue
                age_h = max(0.0, (now - fs_dt).total_seconds() / 3600.0)
                remain = max(0.0, 12.0 - age_h)
                tag = "新品" if age_h < 12.0 else "老品"
                st = stats.get(str(pname)) or {}
                rows.append((str(pname), fs_dt.strftime("%Y-%m-%d %H:%M"), f"{age_h:.1f}", f"{remain:.1f}", tag,
                             str(st.get("plan_count", "—")), f"{st.get('burn_cost', 0.0):.2f}"))
            for r in rows:
                self.tree_archive.insert("", "end", values=r)
        except Exception as e:
            self.tree_archive.delete(*self.tree_archive.get_children())
            self.tree_archive.insert("", "end", values=(f"（品档案刷新失败: {e}）", "", "", "", "", "", ""))

    def _latest_round_product_stats(self):
        """从 history.db 最新一轮（time=MAX(time)）快照聚合：名下计划数/当前空烧消耗（只读）。

        空烧消耗=该品名下 订单=0 且消耗>0 的累计消耗合计；任何异常返回空 dict（UI 显示 —）。
        """
        out = {}
        try:
            import sqlite3 as _sq
            conn = _sq.connect(str(DB_FILE), timeout=3)
            try:
                cur = conn.cursor()
                cur.execute("SELECT MAX(time) FROM history")
                latest = cur.fetchone()
                if latest and latest[0] is not None:
                    cur.execute("SELECT plan_name, cumulative_orders, cumulative_cost FROM history WHERE time = ?",
                                (latest[0],))
                    for pn, c_orders, c_cost in cur.fetchall():
                        if not pn:
                            continue
                        # tkm_10.23：统计键用产品锚，与档案键对齐（品档案按商品名聚合）
                        prod = parse_product_anchor(str(pn))
                        d = out.setdefault(prod, {"plan_count": 0, "burn_cost": 0.0})
                        d["plan_count"] += 1
                        try:
                            if not (c_orders or 0) and (c_cost or 0) > 0:
                                d["burn_cost"] += float(c_cost or 0)
                        except Exception:
                            pass
            finally:
                conn.close()
        except Exception:
            pass
        return out

    def save_strict_conditions(self):
        try:
            for key, e in self.sc_entries.items():
                self.cfg[key] = float(e.get())
            self.cfg["strict_enabled"] = bool(self.var_strict.get())
            self.cfg["roi_consistency"] = bool(self.var_consist.get())
            self.cfg["no_header_policy"] = self.var_no_header.get()
        except ValueError:
            messagebox.showerror("错误", "识别条件必须是数字")
            return
        save_config(self.cfg)
        self.log(f"识别条件已保存: 严格校验={'开' if self.cfg['strict_enabled'] else '关'} | "
                 f"ROI[{self.cfg['roi_min']},{self.cfg['roi_max']}] | "
                 f"成本≤{self.cfg['cost_max_val']} | 收入≤{self.cfg['revenue_max_val']} | "
                 f"一致性={'开(' + str(self.cfg['roi_consistency_tol']) + '%)' if self.cfg['roi_consistency'] else '关'} | "
                 f"无表头={self.cfg['no_header_policy']}")
        messagebox.showinfo("成功", "识别条件已保存")

    def open_skip_words_editor(self):
        """打开跳过词编辑弹窗（v10 改造：按钮入口 → Toplevel 逐条正则编辑）。"""
        self._open_list_editor(
            title="编辑跳过词（正则，命中不参与解析）",
            desc="每条一行，支持正则，如 L\\d+ 可跳过计划名中的 L60 这类噪声；\n命中跳过的词不参与解析",
            initial=_parse_skip_words(self.cfg.get("skip_words")),
            on_save=lambda vals: self._save_skip_words(vals),
            show_regex_btn=True,
        )

    def _save_skip_words(self, vals):
        self.cfg["skip_words"] = vals
        save_config(self.cfg)
        self._refresh_skip_words_btn()
        self.log(f"跳过词已保存: {len(vals)} 条")

    def _open_list_editor(self, title, desc, initial, on_save, show_regex_btn=False):
        """公共逐条正则编辑弹窗（跳过词/排除规则共用）。

        Listbox 逐行展示；下方输入框+添加；选中删除/清空；
        「保存」回调 on_save(vals) 后关闭；「取消」不保存关闭。
        """
        win = tk.Toplevel(self.root)
        win.title(title)
        win.transient(self.root)
        win.grab_set()
        win.geometry("520x460")
        tk.Label(win, text=desc, fg="#333", justify="left", anchor="w").pack(fill="x", padx=12, pady=(8, 4))
        frame = tk.Frame(win)
        frame.pack(fill="both", expand=True, padx=12, pady=4)
        lb = tk.Listbox(frame, selectmode=tk.SINGLE, font=("Consolas", 10))
        sb = tk.Scrollbar(frame, orient="vertical", command=lb.yview)
        lb.config(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        lb.pack(side="left", fill="both", expand=True)
        for it in initial:
            lb.insert(tk.END, it)
        entry_row = tk.Frame(win)
        entry_row.pack(fill="x", padx=12, pady=4)
        e_new = tk.Entry(entry_row, font=("Consolas", 10))
        e_new.pack(side="left", fill="x", expand=True, padx=(0, 4))

        def add_one():
            v = e_new.get().strip()
            if not v:
                return
            lb.insert(tk.END, v)
            e_new.delete(0, tk.END)

        def del_selected():
            sel = lb.curselection()
            if sel:
                lb.delete(sel[0])

        def clear_all():
            lb.delete(0, tk.END)

        def save():
            vals = [str(lb.get(i)).strip() for i in range(lb.size())]
            vals = [v for v in vals if v]
            on_save(vals)
            win.destroy()

        def cancel():
            win.destroy()

        tk.Button(entry_row, text="添加", command=add_one, width=6).pack(side="left")
        btn_row = tk.Frame(win)
        btn_row.pack(fill="x", padx=12, pady=(2, 10))
        tk.Button(btn_row, text="删除选中", command=del_selected, width=10).pack(side="left", padx=(0, 4))
        tk.Button(btn_row, text="清空", command=clear_all, width=8).pack(side="left", padx=(0, 12))
        if show_regex_btn:
            tk.Button(btn_row, text="正则符号速查", command=self.show_regex_help, width=12).pack(side="right", padx=(4, 0))
        tk.Button(btn_row, text="保存", command=save, width=8).pack(side="right", padx=(4, 0))
        tk.Button(btn_row, text="取消", command=cancel, width=8).pack(side="right")
        e_new.bind("<Return>", lambda _e: add_one())
        win.bind("<Escape>", lambda _e: cancel())

    def show_regex_help(self):
        """正则符号速查帮助窗（只读 Toplevel，Text 可滚动，不影响跳过词主弹窗）。"""
        help_win = tk.Toplevel(self.root)
        help_win.title("正则符号速查")
        help_win.transient(self.root)
        help_win.geometry("540x480")
        help_win.resizable(True, True)
        txt = tk.Text(help_win, font=("Consolas", 10), wrap="word", state="normal",
                      padx=10, pady=6)
        sb = tk.Scrollbar(help_win, orient="vertical", command=txt.yview)
        txt.config(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        txt.pack(side="left", fill="both", expand=True)
        rows = [
            ("\\d", "数字（0-9）", "\\d+ 匹配连续数字，如 60"),
            ("\\w", "字母、数字、下划线", "\\w+ 匹配 abc_123"),
            ("\\s", "空白字符（空格/Tab/换行）", "a\\s+b 匹配 a b"),
            (".", "任意单个字符", "a.c 匹配 abc、a1c"),
            ("*", "前一项 0 次或多次", "ab* 匹配 a、ab、abb"),
            ("+", "前一项 1 次或多次", "ab+ 匹配 ab、abb（不匹配 a）"),
            ("?", "前一项 0 次或 1 次", "ab? 匹配 a、ab"),
            ("[]", "字符集（任选其一）", "[abc] 匹配 a/b/c；[0-9] 匹配数字"),
            ("[^]", "取反（不在字符集中）", "[^0-9] 匹配非数字"),
            ("()", "分组", "(ab)+ 匹配 ab、abab"),
            ("{}", "重复次数", "a{2,3} 匹配 aa、aaa"),
            ("^", "行首", "^L\\d+ 匹配行首的 L60"),
            ("$", "行尾", "\\d+$ 匹配行尾数字"),
            ("|", "或", "ab|cd 匹配 ab 或 cd"),
        ]
        for sym, desc, example in rows:
            txt.insert(tk.END, f"{sym:<6}  {desc}\n")
            txt.insert(tk.END, f"      示例：{example}\n\n")
        txt.config(state="disabled")
        tk.Button(help_win, text="关闭", command=help_win.destroy, width=10).pack(pady=(0, 8))
        help_win.bind("<Escape>", lambda _e: help_win.destroy())

    def _refresh_skip_words_btn(self):
        """刷新「编辑跳过词(N)」按钮文本，N=当前跳过词条数。"""
        n = len(_parse_skip_words(self.cfg.get("skip_words")))
        if hasattr(self, "btn_skip_words"):
            self.btn_skip_words.config(text=f"编辑跳过词({n})")

    # ---------- 任务B：告警排除规则（用户自写正则） ----------
    def open_exclude_rules_editor(self):
        """排除规则编辑弹窗：逐条正则，re.search 匹配计划名/ROI/成本/收入串。"""
        self._open_list_editor(
            title="编辑排除规则（正则，命中任意一条跳过告警）",
            desc="排除规则：命中任意一条则跳过告警。可用匹配内容：计划名、ROI、成本、收入。\n"
                 "示例：^New_ 排除以 New_ 开头的计划；^$ 排除空值",
            initial=self.cfg.get("alert_exclude_patterns") or [],
            on_save=lambda vals: self._save_exclude_rules(vals),
        )

    def _save_exclude_rules(self, vals):
        self.cfg["alert_exclude_patterns"] = vals
        save_config(self.cfg)
        self._refresh_exclude_btn()
        self.log(f"排除规则已保存: {len(vals)} 条")

    def _refresh_exclude_btn(self):
        """刷新「编辑排除规则(N)」按钮文本，N=当前排除规则条数。"""
        n = len(self.cfg.get("alert_exclude_patterns") or [])
        if hasattr(self, "btn_exclude_rules"):
            self.btn_exclude_rules.config(text=f"编辑排除规则({n})")

    # ---------- v10.2 任务1：自定义告警规则（用户自写表达式） ----------
    def open_rules_editor(self):
        """规则统一入口编辑弹窗：预设规则（preset_rules）+ 自定义规则（alert_rules）同窗管理。

        每条 {id,name,kind,expr,message,enabled}；preset=True 标记预设来源。
        kind=alert：表达式命中则告警（message 支持占位）；kind=exempt：命中则豁免该行。
        表达式字段：{roi}{cost}{revenue}{orders}{plan_name}{shop}{aov}{cost_prev}{revenue_prev}
        {burn_redline}{threshold}{roi_good}{cost_max}；支持 < > <= >= == != and or not 与括号；
        受限 eval 全包裹，单条失败不影响其它规则。
        """
        win = tk.Toplevel(self.root)
        win.title("规则（预设+自定义统一入口）")
        win.geometry("820x600")
        tk.Label(win, text="预设规则：内置已对齐规则（ROI<0.6 走量底线 / ROI≥1.3 投放目标 / 累计消耗>300 / 空烧 / 0收入豁免 / CVR低 / 消耗×客单），默认启用。\n"
                           "自定义规则：自己添加，alert 命中进告警队列、exempt 命中跳过该行告警。\n"
                           "字段：{roi}{cost}{revenue}{orders}{cvr}{plan_name}{shop}{aov}{cost_prev}{revenue_prev}{burn_redline}{threshold}{roi_good}{cost_max}{cost_x}{cvr_low}；"
                           "支持 < > <= >= == != and or not 与括号。\n"
                           "示例：{roi}==0 and {revenue}==0（exempt，0收入豁免）；{revenue}==0 and {cost}>{burn_redline}（alert，空烧）",
                 fg="#333", justify="left", anchor="w").pack(fill="x", padx=12, pady=(8, 4))

        frame = tk.Frame(win)
        frame.pack(fill="both", expand=True, padx=12, pady=4)
        lb = tk.Listbox(frame, selectmode=tk.SINGLE, font=("Consolas", 10))
        sb = tk.Scrollbar(frame, orient="vertical", command=lb.yview)
        lb.config(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        lb.pack(side="left", fill="both", expand=True)
        entries = []  # [{"src": "preset"/"custom", "rule": {...}}]

        def reload_entries():
            entries.clear()
            for r in (self.cfg.get("preset_rules") or []):
                entries.append({"src": "preset", "rule": r})
            for r in (self.cfg.get("alert_rules") or []):
                entries.append({"src": "custom", "rule": r})

        def rule_label(e):
            r = e["rule"]
            rid = str(r.get("id") or "-")
            name = str(r.get("name") or rid)
            kind = str(r.get("kind") or "alert")
            enabled = "启用" if r.get("enabled", True) else "禁用"
            expr = str(r.get("expr") or "")
            msg = str(r.get("message") or "")
            tail = f" | 消息:{msg}" if msg else ""
            src = "预设" if e["src"] == "preset" else "自定义"
            return f"[{src}·{enabled}] {name} ({kind}) {expr}{tail}"

        def reload_list():
            lb.delete(0, tk.END)
            for e in entries:
                lb.insert(tk.END, rule_label(e))

        def selected_index():
            sel = lb.curselection()
            return sel[0] if sel else None

        reload_entries()
        reload_list()
        # 编辑区：来源/名称/类型/表达式/消息/启用
        ed = tk.Frame(win)
        ed.pack(fill="x", padx=12, pady=4)
        tk.Label(ed, text="来源:").grid(row=0, column=0, sticky="w")
        var_src = tk.StringVar(value="自定义")
        tk.Label(ed, textvariable=var_src, width=8, fg="#666").grid(row=0, column=1, sticky="w", padx=(2, 8))
        tk.Label(ed, text="名称:").grid(row=0, column=2, sticky="w")
        e_name = tk.Entry(ed, width=16)
        e_name.grid(row=0, column=3, sticky="w", padx=(2, 8))
        tk.Label(ed, text="类型:").grid(row=0, column=4, sticky="w")
        var_kind = tk.StringVar(value="alert")
        tk.OptionMenu(ed, var_kind, "alert", "exempt").grid(row=0, column=5, sticky="w", padx=(2, 8))
        tk.Label(ed, text="启用:").grid(row=0, column=6, sticky="w")
        var_enabled = tk.BooleanVar(value=True)
        tk.Checkbutton(ed, variable=var_enabled).grid(row=0, column=7, sticky="w", padx=(0, 8))
        tk.Label(ed, text="表达式:").grid(row=1, column=0, sticky="w", pady=(4, 0))
        e_expr = tk.Entry(ed, width=58, font=("Consolas", 10))
        e_expr.grid(row=1, column=1, columnspan=8, sticky="we", padx=(2, 0), pady=(4, 0))
        tk.Label(ed, text="消息(alert 告警正文，支持 {roi}{cost}{revenue} 等占位):").grid(row=2, column=0, columnspan=9, sticky="w", pady=(4, 0))
        e_msg = tk.Entry(ed, width=64, font=("Consolas", 10))
        e_msg.grid(row=3, column=0, columnspan=9, sticky="we", padx=(0, 0), pady=(2, 0))

        # v10.7.1：预设参数数值编辑入口（写回 cfg，注入 _rule_env）
        PARAM_CFG_KEYS = {"threshold": "alert_threshold", "cost_max": "cost_max", "roi_good": "roi_good",
                          "burn_redline": "burn_redline", "cost_x": "cost_x_aov", "cvr_low": "cvr_low"}
        pvars = {}
        tk.Label(ed, text="预设参数:").grid(row=4, column=0, sticky="w", pady=(6, 0))
        _pcol = 1
        for k, ck in PARAM_CFG_KEYS.items():
            tk.Label(ed, text=k).grid(row=4, column=_pcol, sticky="w", pady=(6, 0))
            pvars[k] = tk.StringVar(value=str(self.cfg.get(ck) if ck != "alert_threshold" else self.cfg.get("alert_threshold", 0.6)))
            tk.Entry(ed, textvariable=pvars[k], width=8).grid(row=4, column=_pcol + 1, sticky="w", padx=(2, 6), pady=(6, 0))
            _pcol += 2

        # v10.7.1：变量可点击插入（插入到表达式光标处）
        INSERT_VARS = ["roi", "cost", "revenue", "orders", "cvr", "aov", "cost_prev", "revenue_prev",
                       "burn_redline", "threshold", "roi_good", "cost_max", "cost_x", "cvr_low", "plan_name", "shop"]

        def insert_var(n):
            try:
                idx = e_expr.index(tk.INSERT)
            except Exception:
                idx = len(e_expr.get())
            e_expr.insert(idx, "{" + n + "}")
            e_expr.focus_set()

        tk.Label(ed, text="插入变量:").grid(row=5, column=0, sticky="w", pady=(6, 0))
        _vcol = 1
        _vrow = 5
        for i, n in enumerate(INSERT_VARS):
            tk.Button(ed, text="{" + n + "}", command=lambda _n=n: insert_var(_n),
                      width=len(n) + 3).grid(row=_vrow, column=_vcol, padx=1, pady=(6, 0))
            _vcol += 1
            if _vcol > 8:
                _vcol = 1
                _vrow = 6

        def _validate_expr(expr):
            """保存前即时校验：用 mock 环境试算，非法表达式不让入库。"""
            env = {k: 1.0 for k in ("roi", "cost", "revenue", "orders", "aov", "cost_prev", "revenue_prev",
                                    "burn_redline", "threshold", "roi_good", "cost_max", "cost_x", "cvr_low")}
            env["cvr"] = 0.5
            env["plan_name"] = "测试计划"
            env["shop"] = "测试店铺"
            _ok, err = _safe_eval_rule(expr, env)
            return _ok, err

        def _persist_param_vars():
            for k, ck in PARAM_CFG_KEYS.items():
                try:
                    self.cfg[ck] = float(pvars[k].get())
                except ValueError:
                    messagebox.showwarning("参数格式错误", f"预设参数 {k} 需要数值，已取消保存")
                    return False
            return True

        def fill_from_selected():
            idx = selected_index()
            if idx is None or idx >= len(entries):
                return
            e = entries[idx]
            r = e["rule"]
            var_src.set("预设" if e["src"] == "preset" else "自定义")
            e_name.delete(0, tk.END)
            e_name.insert(0, str(r.get("name") or ""))
            var_kind.set(str(r.get("kind") or "alert"))
            var_enabled.set(bool(r.get("enabled", True)))
            e_expr.delete(0, tk.END)
            e_expr.insert(0, str(r.get("expr") or ""))
            e_msg.delete(0, tk.END)
            e_msg.insert(0, str(r.get("message") or ""))

        lb.bind("<<ListboxSelect>>", lambda _e: fill_from_selected())

        def add_rule():
            expr = e_expr.get().strip()
            if not expr:
                return
            ok, err = _validate_expr(expr)
            if not ok:
                messagebox.showwarning("表达式校验失败", err)
                return
            rules = self.cfg.get("alert_rules") or []
            rid = max([int(r.get("id") or 0) for r in rules] or [0]) + 1
            rules.append({
                "id": rid,
                "name": (e_name.get().strip() or f"规则{rid}"),
                "kind": var_kind.get(),
                "expr": expr,
                "message": e_msg.get().strip(),
                "enabled": var_enabled.get(),
            })
            self.cfg["alert_rules"] = rules
            reload_entries()
            reload_list()
            lb.selection_clear(0, tk.END)
            e_expr.delete(0, tk.END)

        def update_selected():
            idx = selected_index()
            if idx is None or idx >= len(entries):
                return
            expr = e_expr.get().strip()
            if not expr:
                return
            ok, err = _validate_expr(expr)
            if not ok:
                messagebox.showwarning("表达式校验失败", err)
                return
            e = entries[idx]
            r = e["rule"]
            r["name"] = e_name.get().strip() or str(r.get("id") or "")
            r["kind"] = var_kind.get()
            r["enabled"] = var_enabled.get()
            r["expr"] = expr
            r["message"] = e_msg.get().strip()
            reload_list()
            lb.selection_set(idx)

        def del_selected():
            idx = selected_index()
            if idx is None or idx >= len(entries):
                return
            e = entries.pop(idx)
            if e["src"] == "preset":
                self.cfg["preset_rules"] = [r for r in (self.cfg.get("preset_rules") or []) if r is not e["rule"]]
            else:
                self.cfg["alert_rules"] = [r for r in (self.cfg.get("alert_rules") or []) if r is not e["rule"]]
            reload_list()

        def restore_presets():
            """一键恢复全部默认预设（覆盖当前 preset_rules，自定义规则不动）。"""
            self.cfg["preset_rules"] = copy.deepcopy(PRESET_RULES)
            reload_entries()
            reload_list()
            lb.selection_clear(0, tk.END)

        def save_rules():
            if not _persist_param_vars():
                return
            save_config(self.cfg)
            self._refresh_rules_btn()
            n_pre = len([r for r in (self.cfg.get("preset_rules") or []) if r.get("enabled", True)])
            n_cus = len([r for r in (self.cfg.get("alert_rules") or []) if r.get("enabled", True)])
            self.log(f"规则已保存: 预设启用 {n_pre} 条 / 自定义启用 {n_cus} 条")
            win.destroy()

        def cancel():
            win.destroy()

        btn_row = tk.Frame(win)
        btn_row.pack(fill="x", padx=12, pady=(2, 10))
        tk.Button(btn_row, text="添加自定义", command=add_rule, width=10).pack(side="left", padx=(0, 4))
        tk.Button(btn_row, text="更新选中", command=update_selected, width=10).pack(side="left", padx=(0, 4))
        tk.Button(btn_row, text="删除选中", command=del_selected, width=10).pack(side="left", padx=(0, 4))
        tk.Button(btn_row, text="恢复预设", command=restore_presets, width=10).pack(side="left", padx=(0, 12))
        tk.Button(btn_row, text="保存", command=save_rules, width=8).pack(side="right", padx=(4, 0))
        tk.Button(btn_row, text="取消", command=cancel, width=8).pack(side="right")
        e_expr.bind("<Return>", lambda _e: add_rule())
        win.bind("<Escape>", lambda _e: cancel())

    def _refresh_rules_btn(self):
        """刷新「规则(预设N+自定义M)」按钮文本，N/M=启用中的规则条数。
        v10.14.7：lbl_status 状态小字随顶部拖动条删除（§8.4-1），此处仅刷新规则按钮。"""
        n_pre = len([r for r in (self.cfg.get("preset_rules") or []) if r.get("enabled", True)])
        n_cus = len([r for r in (self.cfg.get("alert_rules") or []) if r.get("enabled", True)])
        if hasattr(self, "btn_rules"):
            self.btn_rules.config(text=f"规则(预设{n_pre}+自定义{n_cus})")

    def rec_rules_summary(self):
        """当前生效识别规则摘要（供保存与识别日志复用，错误15；错误20增品名/跳过词；错误22增表头关键词；架构健康化A：识别器名由注册表动态展示）"""
        anchor = (self.cfg.get("anchor_keyword") or "").strip()
        start_txt = f"从[{anchor}]行开始" if anchor else "从表头下一行开始"
        mode = str(self.cfg.get("recog_mode", "uia") or "uia").strip() or "uia"
        recog = self._recog_registry.get(mode)
        mode_txt = recog["name"] if recog else f"识别器[{mode}]"
        return (f"识别模式={mode_txt} "
                f"表头={self.cfg.get('header_col_name', '')}/{self.cfg.get('header_col_roi', '')}/"
                f"{self.cfg.get('header_col_cost', '')}/{self.cfg.get('header_col_rev', '')} "
                f"表头关键词={self.cfg.get('header_keywords', '广告计划')} "
                f"起始={start_txt} 读取第{self.cfg.get('read_row_index', 1)}行 "
                f"偏移={self.cfg.get('offset_roi', 0)}/{self.cfg.get('offset_cost', 0)}/{self.cfg.get('offset_rev', 0)} "
                f"品名校验={'开' if self.cfg.get('plan_name_check', True) else '关'} "
                f"品名偏移={self.cfg.get('offset_name', 0)} "
                f"跳过词={len(_parse_skip_words(self.cfg.get('skip_words')))}个")

    def save_rec_rules(self):
        try:
            self.cfg["header_col_name"] = self.rr_entries["header_col_name"].get().strip()
            self.cfg["header_col_roi"] = self.rr_entries["header_col_roi"].get().strip()
            self.cfg["header_col_cost"] = self.rr_entries["header_col_cost"].get().strip()
            self.cfg["header_col_rev"] = self.rr_entries["header_col_rev"].get().strip()
            self.cfg["anchor_keyword"] = self.rr_entries["anchor_keyword"].get().strip()
            self.cfg["read_row_index"] = int(self.rr_entries["read_row_index"].get())
            for key in ("offset_roi", "offset_cost", "offset_rev"):
                self.cfg[key] = int(float(self.rr_entries[key].get()))
            # 错误20：品名校验/品名正则/品名列偏移/跳过词
            self.cfg["plan_name_check"] = bool(self.var_plan_name_check.get())
            self.cfg["plan_name_pattern"] = self.rr_entries["plan_name_pattern"].get().strip()
            self.cfg["offset_name"] = int(float(self.rr_entries["offset_name"].get()))
            # v10 跳过词（正则列表，由弹窗维护）：此处仅规范化确保格式正确（兼容旧字符串）
            self.cfg["skip_words"] = _parse_skip_words(self.cfg.get("skip_words"))
            # 错误22：表头关键词（广告计划词命中即表头，逗号分隔多候选）
            self.cfg["header_keywords"] = self.rr_entries["header_keywords"].get().strip()
            # 识别模式（模式一截图OCR / 模式二控件树）+ 滚动收集开关（模式二懒加载对策）
            self.cfg["recog_mode"] = str(self.var_recog_mode.get()).strip() or "uia"
            self.cfg["uia_scroll_collect"] = bool(self.var_uia_scroll.get())
        except ValueError:
            messagebox.showerror("错误", "读取行数须为整数（1~20），列偏移须为数字")
            return
        if self.cfg["read_row_index"] < 1 or self.cfg["read_row_index"] > 20:
            messagebox.showerror("错误", "读取第几行范围 1~20")
            return
        save_config(self.cfg)
        self.log("识别规则已保存: " + self.rec_rules_summary())
        messagebox.showinfo("成功", "识别规则已保存，下次识别生效")

    def save_recog_mode(self):
        """保存识别方式（②.5 板块）：识别模式 + 滚动收集开关（模式二懒加载对策）。"""
        try:
            self.cfg["recog_mode"] = str(self.var_recog_mode.get()).strip() or "uia"
            self.cfg["uia_scroll_collect"] = bool(self.var_uia_scroll.get())
        except Exception:
            messagebox.showerror("错误", "识别方式保存失败，请重试")
            return
        save_config(self.cfg)
        mode = str(self.cfg.get("recog_mode", "uia") or "uia").strip() or "uia"
        recog = self._recog_registry.get(mode)
        mode_txt = recog["name"] if recog else f"识别器[{mode}]"
        scroll_txt = "开" if self.cfg.get("uia_scroll_collect") else "关"
        self.log(f"识别方式已保存: 识别模式={mode_txt} | 滚动收集={scroll_txt}")
        # v10.5：切换识别方式后按新识别器能力声明即时刷新 UI 显隐/文案
        self._apply_section_capabilities()
        messagebox.showinfo("成功", "识别方式已保存，下次识别生效")

    def _set_current_orders(self, orders, log_txt):
        """公共写入：更新当前累计订单数并落盘（save_orders/do_manual_record 复用）。"""
        self.cfg["current_orders"] = orders
        save_config(self.cfg)
        self.log(log_txt)

    def save_orders(self):
        try:
            val = int(self.e_orders.get())
        except ValueError:
            messagebox.showerror("错误", "订单数必须是整数")
            return
        if val < 0:
            messagebox.showerror("错误", "订单数不能为负数")
            return
        self._set_current_orders(val, f"当前累计订单数已更新为 {val}（后续自动识别将使用该值）")

    def _sync_push_ui_to_cfg(self):
        """将推送设置区 UI 实时状态（勾选+内容）同步进 self.cfg，不落盘。

        逻辑闭环：任何推送行为（测试/告警发送）前先同步 UI 开关状态，
        保证开关真实参与判断，杜绝"界面取消勾选但逻辑仍按旧配置发送"。
        """
        self.cfg["pushplus_token"] = self.e_pushplus.get().strip()
        self.cfg["pushplus_enabled"] = bool(self.var_pushplus.get())
        self.cfg["wecom_key"] = self.e_wecom.get().strip()
        self.cfg["wecom_enabled"] = bool(self.var_wecom.get())
        self.cfg["dingtalk_url"] = self.e_ding.get().strip()
        self.cfg["dingtalk_enabled"] = bool(self.var_ding.get())
        # v10.14.3：合并推送间隔（push_batch_minutes）已删除——推送时机固定为每轮识别完统一打包，不再同步
        # v10.9.1：告警声音开关
        self.cfg["sound_alarm"] = bool(self.var_sound_alarm.get())
        # v10.10：告警 toast 开关
        self.cfg["toast_enabled"] = bool(self.var_toast_enabled.get())
        # v10.14.3：刷新浏览器开关（自动监控每轮识别前是否先刷新浏览器）
        self.cfg["refresh_enabled"] = bool(self.var_refresh.get())

    def save_push(self):
        self._sync_push_ui_to_cfg()
        save_config(self.cfg)
        self.log("推送设置已保存")
        self.update_push_status()

    def update_push_status(self):
        """刷新底部当前生效推送通道状态提示。

        逻辑闭环：状态文字基于 UI 实时勾选状态与已填内容计算，
        保证"显示已启用"与"实际 enabled"严格一致，禁止摆设开关。
        """
        parts = []
        if bool(self.var_pushplus.get()) and self.e_pushplus.get().strip():
            parts.append("PushPlus（微信）已启用")
        if bool(self.var_wecom.get()) and self.e_wecom.get().strip():
            parts.append("企业微信已启用")
        if bool(self.var_ding.get()) and self.e_ding.get().strip():
            parts.append("钉钉已启用")
        if parts:
            self.lbl_push_status.config(text="当前推送通道：" + "、".join(parts), fg="#006600")
        else:
            self.lbl_push_status.config(text="当前未启用任何推送通道（请填写 Token 并勾选启用）", fg="#cc0000")

    def edit_pushplus_template(self):
        """v10.2 推送模板编辑器：分模板编辑（核心告警/其它规则），
        全部变量开放到面板（说明 + 一键插入），支持恢复默认模板，保存落盘 config.json。
        TEMPLATE_VARS 统一使用模块级单一常量（v10.7.1）。"""
        win = tk.Toplevel(self.root)
        win.title("推送模板编辑器")
        win.geometry("760x640")
        win.transient(self.root)
        win.grab_set()
        # 变量说明区（可点击按钮一键插入到当前模板光标处）
        tip_head = tk.Label(win, text="可用变量（点击按钮插入到当前模板光标处；未知变量保留原文不报错）：",
                            justify="left", anchor="w", fg="#333")
        tip_head.pack(fill="x", padx=10, pady=(10, 2))
        var_row = tk.Frame(win)
        var_row.pack(fill="x", padx=10)
        for name, desc in TEMPLATE_VARS:
            b = tk.Button(var_row, text=name, relief="groove", font=("Consolas", 9),
                          command=lambda n=name: self._template_insert_var(nb, tabs, n))
            b.pack(side="left", padx=2, pady=2)
        # 变量说明明细
        tip_detail = "\n".join(f"  {{{name}}}  {desc}" for name, desc in TEMPLATE_VARS)
        tk.Label(win, text=tip_detail, justify="left", anchor="w", fg="#666",
                 font=("Microsoft YaHei UI", 9)).pack(fill="x", padx=10, pady=(2, 4))
        # 分模板编辑（Notebook）
        nb = ttk.Notebook(win)
        nb.pack(fill="both", expand=True, padx=10, pady=4)
        tabs = []  # [(frame, txt, key, default)]

        def build_tab(title, key):
            fr = tk.Frame(nb)
            txt = tk.Text(fr, font=("Consolas", 10), wrap="word")
            txt.pack(fill="both", expand=True, padx=4, pady=4)
            txt.insert("1.0", self.cfg.get(key) or DEFAULT_CONFIG.get(key, ""))
            nb.add(fr, text=title)
            tabs.append([fr, txt, key, DEFAULT_CONFIG.get(key, "")])
            return fr, txt

        build_tab("核心告警模板 (alert_template)", "alert_template")
        build_tab("其它规则模板 (pushplus_template)", "pushplus_template")
        btn_row = tk.Frame(win)
        btn_row.pack(fill="x", padx=10, pady=(0, 10))

        def current_txt():
            sel = nb.select()
            for fr, txt, _k, _d in tabs:
                if str(fr) == sel:
                    return txt
            return tabs[0][1]

        def do_restore_default():
            txt = current_txt()
            key = None
            for fr, t, k, d in tabs:
                if t is txt:
                    key = k
                    break
            if key:
                txt.delete("1.0", "end")
                txt.insert("1.0", DEFAULT_CONFIG.get(key, ""))

        def do_save():
            for fr, txt, key, _d in tabs:
                content = txt.get("1.0", "end-1c")
                self.cfg[key] = content
            save_config(self.cfg)
            self.log("推送模板已保存（下次推送生效）")
            win.destroy()

        def do_cancel():
            win.destroy()

        tk.Button(btn_row, text="恢复默认模板", command=do_restore_default, width=12).pack(side="left", padx=(0, 8))
        tk.Button(btn_row, text="保存", command=do_save, width=10).pack(side="left", padx=(0, 8))
        tk.Button(btn_row, text="取消", command=do_cancel, width=10).pack(side="left")
        tk.Label(btn_row, text="阈值（0.6 走量底线 / 1.3 投放目标 / 成本上限 / CVR低 / 消耗×客单）在 ④ 告警规则 面板配置",
                 fg="#888").pack(side="right")

    def _template_insert_var(self, nb, tabs, name):
        """把 {name} 插入当前活动模板光标处（无光标则追加末尾）。"""
        try:
            sel = nb.select()
            txt = None
            for fr, t, _k, _d in tabs:
                if str(fr) == sel:
                    txt = t
                    break
            if txt is None:
                txt = tabs[0][1]
            try:
                txt.insert(tk.INSERT, "{" + name + "}")
            except Exception:
                txt.insert("end", "{" + name + "}")
            txt.focus_set()
        except Exception:
            pass

    # ---------- ROI 框选 ----------
    def calibrate_roi(self):
        win = self.get_target_window()
        if not win:
            # 诊断：为什么找不到目标窗口
            self.log("[诊断] 框选 ROI 失败：get_target_window() 返回空")
            if self.selected_hwnd:
                locked = find_window_by_hwnd(self.selected_hwnd)
                self.log(f"[诊断] 已锁定句柄: {self.selected_hwnd} (0x{self.selected_hwnd & 0xFFFFFFFFFFFFFFFF:X})，"
                         f"按句柄查找结果={'有效' if locked else '失效（窗口已关闭或不可见）'}")
            else:
                self.log("[诊断] 未锁定任何窗口句柄（selected_hwnd 为空，请点『列出窗口』双击锁定）")
            kw = (self.cfg.get("window_keyword", "商户中心") or "").strip()
            self.log(f"[诊断] 回退关键词匹配: [{kw}]")
            wins = list_windows()
            self.log(f"[诊断] 当前可见窗口共 {len(wins)} 个：")
            for i, (title, hwnd) in enumerate(wins, 1):
                self.log(f"[诊断] {i}. {title!r} (hwnd={hwnd})")
            messagebox.showerror("错误", "找不到目标窗口，请先打开后台，再点『列出窗口』双击锁定窗口")
            return
        if win["width"] < 200 or win["height"] < 100:
            messagebox.showerror("错误", "窗口过小或已最小化，请还原窗口")
            return

        cal = tk.Toplevel(self.root)
        cal.title("选择 ROI 区域")
        cal.attributes("-fullscreen", True)
        cal.attributes("-alpha", 0.3)
        cal.configure(bg="gray")
        canvas = tk.Canvas(cal, bg="gray", highlightthickness=0)
        canvas.pack(fill="both", expand=True)
        tk.Label(cal, text=f"窗口已找到：{win['title'][:40]}\n可以框大一点，程序会自动识别表格行，框住当前计划所在行即可\n右键取消",
                 bg="yellow", font=("Arial", 12)).place(x=10, y=10)

        state = {"sx": 0, "sy": 0, "ex": 0, "ey": 0, "rect": None}

        def on_press(event):
            state["sx"], state["sy"] = event.x_root, event.y_root

        def on_drag(event):
            if state["rect"]:
                canvas.delete(state["rect"])
            state["rect"] = canvas.create_rectangle(
                state["sx"], state["sy"], event.x_root, event.y_root,
                outline="red", width=2)

        def on_release(event):
            state["ex"], state["ey"] = event.x_root, event.y_root
            left = min(state["sx"], state["ex"]) - win["left"]
            top = min(state["sy"], state["ey"]) - win["top"]
            width = abs(state["ex"] - state["sx"])
            height = abs(state["ey"] - state["sy"])
            if width < 10 or height < 10:
                messagebox.showwarning("提示", "框选区域过小，已取消")
                cal.destroy()
                return
            self.cfg["roi"] = [left, top, width, height]
            save_config(self.cfg)
            self.e_rl.delete(0, tk.END); self.e_rl.insert(0, str(left))
            self.e_rt.delete(0, tk.END); self.e_rt.insert(0, str(top))
            self.e_rw.delete(0, tk.END); self.e_rw.insert(0, str(width))
            self.e_rh.delete(0, tk.END); self.e_rh.insert(0, str(height))
            self.lbl_roi.config(text=f"({left},{top}) {width}x{height}", fg="green")
            self.log(f"ROI 已框选保存: 相对窗口 ({left},{top}) {width}x{height}")
            cal.destroy()

        def on_right(event):
            cal.destroy()

        canvas.bind("<ButtonPress-1>", on_press)
        canvas.bind("<B1-Motion>", on_drag)
        canvas.bind("<ButtonRelease-1>", on_release)
        canvas.bind("<Button-3>", on_right)
        cal.focus_set()

    # ---------- 识别器注册与调度（架构健康化A） ----------
    def register_recog_mode(self, mode_id, name, recognize_fn, config_keys=None, expand_fn=None,
                            requires_sections=None, mod_id="core"):
        """注册识别器：mode_id 唯一（如 ocr/uia），写入注册表并刷新识别方式下拉。

        recognize_fn(core, target) -> (text, words)；expand_fn 可选（OCR 扩框兜底）。
        config_keys 用于识别规则摘要展示（按识别器声明展示引擎名）。
        v10.5：requires_sections——识别器能力声明，声明依赖的 UI 板块/能力（如 OCR 需要
        "roi" 板块），核心按声明做条件渲染（_apply_section_capabilities），去 UI 硬编码。
        """
        mode_id = str(mode_id or "").strip()
        if not mode_id or not callable(recognize_fn):
            self.log(f"[mod:{mod_id}] register_recog_mode 参数无效（mode_id/recognize_fn 必填）")
            return False
        self._recog_registry[mode_id] = {
            "name": str(name or mode_id),
            "recognize_fn": recognize_fn,
            "expand_fn": expand_fn if callable(expand_fn) else None,
            "config_keys": list(config_keys or []),
            "requires_sections": list(requires_sections or []),
            "mod_id": str(mod_id),
        }
        self.log(f"[mod:{mod_id}] 识别器已注册: [{mode_id}] -> {self._recog_registry[mode_id]['name']}"
                 f"（能力声明: requires_sections={list(requires_sections or [])}）")
        self._refresh_recog_menu()
        return True

    def _refresh_recog_menu(self):
        """把已注册识别器刷进识别方式下拉（OptionMenu 动态重建，兼容运行期注册）"""
        if self._recog_menu is None:
            return
        try:
            menu = self._recog_menu["menu"]
            menu.delete(0, "end")
            keys = sorted(self._recog_registry.keys())
            for mid in keys:
                info = self._recog_registry[mid]
                menu.add_radiobutton(label=f"{info['name']} ({mid})",
                                     variable=self.var_recog_mode, value=mid)
            cur = str(self.var_recog_mode.get())
            if cur not in self._recog_registry and keys:
                self.var_recog_mode.set(keys[0])
        except Exception:
            pass

    def _apply_section_capabilities(self):
        """v10.5：按已注册识别器能力声明做 UI 条件渲染。

        - 当前识别器声明 requires_sections 含 "roi" → 显示 ② ROI 识别区域板块，
          启动提示语含 ROI 步骤（识别依赖 ROI 截图区域）；
        - 否则（如 UIA 声明 []）→ 隐藏 ROI 板块，提示语去掉 ROI。
        - ④.6 识别规则板块常驻（UIA 文本回退仍用表头关键词/列偏移/跳过词等配置）；
          「立即识别一次」按钮文案固定（v10.14.7：测试按钮已删除）。
        调用时机：load_mods 之后一次（初始渲染）；save_recog_mode 成功后一次（切换即时生效）。
        """
        try:
            mode = str(self.cfg.get("recog_mode", "uia") or "uia").strip() or "uia"
            recog = self._recog_registry.get(mode)
            need_roi = bool(recog and "roi" in (recog.get("requires_sections") or []))
            # ② ROI 识别区域板块显隐（用户手动隐藏 hidden_sections 时保持隐藏）
            frame = self._sec_roi_frame or self._section_frames.get("sec_roi")
            if frame is not None:
                if need_roi:
                    if frame.winfo_manager() == "" and "sec_roi" not in (self.hidden_sections or []):
                        self._repack_section("sec_roi")
                else:
                    if frame.winfo_manager() != "":
                        frame.pack_forget()
            # ③ 启动提示语
            if self._lbl_start_hint is not None:
                hint = ("「启动自动监控」前请先完成：窗口关键词→ROI→识别方式"
                        if need_roi else "「启动自动监控」前请先完成：窗口关键词→识别方式")
                self._lbl_start_hint.config(text=hint)
            self.log(f"识别器能力渲染: mode={mode} requires_roi={'是' if need_roi else '否'}", level="info")
        except Exception as e:
            self.log(f"识别器能力渲染失败: {e}", level="info")

    def _get_recog(self, mode):
        """按 mode_id 取识别器；未注册时返回 None"""
        return self._recog_registry.get(mode)

    def _capture_full_window(self, target=None):
        """截取目标窗口整个可见区域（窗口可能超出屏幕边界，自动裁剪到屏幕可见区，避免黑边空白）。

        共享能力：OCR 识别器扩框兜底复用本方法。
        返回 PIL Image 或 None（未定位/窗口不可见/截图失败时返回 None，不抛异常）"""
        try:
            t = target if target is not None else self._current_target()
            win = self.get_target_window(t)
            if not win or win.get("width", 0) <= 0 or win.get("height", 0) <= 0:
                return None
            left, top = win["left"], win["top"]
            right = left + win["width"]
            bottom = top + win["height"]
            try:
                import ctypes as _c
                _u = _c.windll.user32
                sw, sh = _u.GetSystemMetrics(0), _u.GetSystemMetrics(1)
            except Exception:
                sw = sh = 0
            if sw > 0 and sh > 0:
                c_left, c_top = max(left, 0), max(top, 0)
                c_right, c_bottom = min(right, sw), min(bottom, sh)
            else:
                c_left, c_top, c_right, c_bottom = left, top, right, bottom
            if c_right <= c_left or c_bottom <= c_top:
                return None
            return capture_region(c_left, c_top, c_right - c_left, c_bottom - c_top)
        except Exception:
            return None

    # ---------- 识别 ----------
    def capture_roi(self, target=None):
        """返回 (img, win) 或抛异常；target 为空时用当前目标（多店铺各目标独立 ROI/窗口）。"""
        t = target if target is not None else self._current_target()
        win = self.get_target_window(t)
        if not win:
            raise RuntimeError("找不到目标窗口，请先点『列出窗口』双击锁定窗口，或检查窗口是否已关闭")
        if win["width"] < 200 or win["height"] < 100:
            raise RuntimeError("窗口过小或已最小化，请还原窗口")
        roi = t.get("roi")
        if not roi or len(roi) != 4:
            raise RuntimeError("尚未设置 ROI 区域，请先框选或填写坐标")
        left, top, width, height = roi
        img = capture_region(win["left"] + left, win["top"] + top, width, height)
        return img, win

    def recognize_once(self, target=None):
        """按当前识别方式调用已注册识别器产出 (text, words)，再走共享解析管线。

        返回 (roi, cost, revenue, text, detail)；target 为空时用当前目标（多店铺轮询时传入各自目标）。
        识别器由 mods/ 目录提供（uia_recog=控件树读取），未注册时直接报错；
        识别器可选提供 expand_fn 做扩框兜底（OCR 全窗口再识别一次，错误17）。
        """
        t = target if target is not None else self._current_target()
        mode = str(self.cfg.get("recog_mode", "uia") or "uia").strip() or "uia"
        recog = self._get_recog(mode)
        if recog is None:
            raise RuntimeError("未注册任何识别器（请检查 exe 同级 mods/ 目录是否完整，应包含 uia_recog），无法识别")
        result = recog["recognize_fn"](self, t)
        # v10.3 任务2：识别器可返回 (text, words, structured_rows) 结构化三元组；
        # structured_rows 非空时直接走结构化管线（detail.candidates 带真实字段），绕开文本解析
        # v10.4 修复：先收 result 再按长度分发，避免直接对 3 元组二元解包抛
        # "too many values to unpack (expected 2)"（原三元组适配判断永远执行不到）
        structured_rows = None
        if isinstance(result, tuple) and len(result) >= 3:
            text, words, structured_rows = result[0], result[1], result[2]
        else:
            text, words = result
        if structured_rows:
            roi, cost, revenue, detail = self._structured_detail(structured_rows, t)
            if roi is None and cost is None and revenue is None:
                raise RuntimeError(f"结构化读取未取到有效数据行（{detail.get('fail_reason') or '数值缺失'}）")
            return roi, cost, revenue, text, detail
        if not text:
            raise RuntimeError("识别未读取到任何文字，请检查 ROI 区域或窗口内容")
        roi, cost, revenue, detail = self._parse_recognized(text, words, t)
        # 错误17：框选区域未找到表头/未取到数时，若识别器支持扩框兜底（OCR 全窗口再识别一次，
        # 兜住"ROI 区域右边界没框到数值列/表头"的场景——识别器只能读到框内内容）
        expand = recog.get("expand_fn")
        if expand is not None and (
                (detail and detail.get("mode") in ("fail_no_header", "fail_no_anchor")) or
                (roi is None and cost is None and revenue is None)):
            try:
                e_text, e_words = expand(self, t)
            except Exception:
                e_text, e_words = "", []
            if e_text:
                e_roi, e_cost, e_rev, e_detail = self._parse_recognized(e_text, e_words, t)
                if e_roi is not None and e_cost is not None and e_rev is not None:
                    if e_detail:
                        e_detail = dict(e_detail)
                        e_detail["expanded"] = True
                    self.log("框选区域未找到表头，已自动扩展至全窗口识别成功（建议重新框选，把 ROI/成本/收入 三列数值框进来）")
                    return e_roi, e_cost, e_rev, e_text, e_detail
        # v10.8.5：『有一条显示一条』——strict 路径 no_header/no_anchor 但页面上存在可见
        # 数据行（ROI/成本/收入词配对可定位）时，按宽松模式重新解析按行入库，避免整轮
        # 跳过一条不录；宽松解析仍无候选行才返回原失败（UIA 只读到外壳/页面确无表格时
        # 宽松也救不了，此时报"没有找到广告计划"是如实反映）
        if (detail and detail.get("mode") in ("fail_no_header", "fail_no_anchor")) and words:
            try:
                cfg_loose = dict(self.cfg)
                cfg_loose["no_header_policy"] = "loose"
                l_roi, l_cost, l_rev, _, l_detail = _parse_table_by_words(words, cfg_loose)
                if l_detail and l_detail.get("candidates"):
                    l_detail = dict(l_detail)
                    l_detail["loose_fallback"] = True
                    self.log("未识别到表头，已按『有一条显示一条』宽松解析：按可见行入库")
                    return l_roi, l_cost, l_rev, text, l_detail
            except Exception:
                pass
        return roi, cost, revenue, text, detail

    def _parse_recognized(self, text, words, target=None):
        """共享识别后解析：词坐标表格重建优先，失败回退全文解析（OCR 与 UIA 共用）。

        返回 (roi, cost, revenue, detail)；detail 供日志区可视化拾取过程。"""
        roi = cost = revenue = None
        detail = None
        if words:
            roi, cost, revenue, _, detail = _parse_table_by_words(words, self.cfg)
        if roi is None or cost is None or revenue is None:
            if words and detail and detail.get("debug_rows"):
                self.log(f"诊断：词坐标表头未命中（识别词 {len(words)} 个，无宽松配对），y 聚类前 12 行：")
                for line in detail["debug_rows"]:
                    self.log("  " + line)
            roi, cost, revenue = parse_fields(text, cfg=self.cfg)
            detail = _table_row_analysis(text, self.cfg)
        return roi, cost, revenue, detail

    def _structured_detail(self, structured_rows, target=None):
        """v10.3 任务2：结构化识别行 → 兼容 detail 结构（candidates 带 triplet/plan_name）。

        下游 _collect_rows_from_detail / run_alerts / _log_pick_detail 完全复用：
        只换数据来源，空烧判断、告警、推送逻辑零改动。

        v10.7.8：detail 补 selected_index（选中行=结构化首行），修复 _log_pick_detail
        打出"已选中第None行"；数值 cell 解析失败的行进 rejects（带 cell 原文），
        便于日志直接看到 roi/cost/revenue 列实际取到的文本。"""
        cands = []
        rejects = []
        filtered_count = 0
        only_active = bool(self.cfg.get("only_active_plans", True))
        for i, r in enumerate(structured_rows):
            if not isinstance(r, dict):
                continue
            status = r.get("status")
            if only_active and status is not None and str(status).strip() and "已生效" not in str(status):
                plan_name = str(r.get("plan_name") or "").strip() or f"第{i + 1}行"
                self.log(f"已过滤非已生效计划：{plan_name}（状态={status}）")
                filtered_count += 1
                continue
            roi = r.get("roi")
            cost = r.get("cost")
            revenue = r.get("revenue")
            plan_name = str(r.get("plan_name") or "").strip() or f"第{i + 1}行"
            if roi is None or cost is None or revenue is None:
                rejects.append({
                    "row": i + 1,
                    "text": f"{plan_name} | ROI cell={r.get('roi_raw')!r} | 成本 cell={r.get('cost_raw')!r} | 收入 cell={r.get('revenue_raw')!r}",
                    "triplet": (roi, cost, revenue),
                    "reason": "数值 cell 解析失败（列映射取值后非数值）",
                })
                continue
            try:
                roi = float(roi)
                cost = float(cost)
                revenue = float(revenue)
            except (ValueError, TypeError):
                rejects.append({
                    "row": i + 1,
                    "text": f"{plan_name} | ROI cell={r.get('roi_raw')!r} | 成本 cell={r.get('cost_raw')!r} | 收入 cell={r.get('revenue_raw')!r}",
                    "triplet": (roi, cost, revenue),
                    "reason": "数值转换异常",
                })
                continue
            cands.append({
                "row": i + 1,
                "plan_name": plan_name,
                "triplet": (roi, cost, revenue),
                "text": f"{plan_name} ROI={roi} 成本={cost} 收入={revenue}",
                "orders": r.get("orders"),
                "status": r.get("status"),
                "budget": r.get("budget"),
                "target_roi": r.get("target_roi"),
                "structured": True,
                "raw": r,
            })
        detail = {"mode": "structured", "candidates": cands, "rejects": rejects}
        if not cands:
            if filtered_count > 0:
                detail["fail_reason"] = f"全部 {filtered_count} 行为非已生效计划被过滤（only_active_plans 开启）"
            else:
                detail["fail_reason"] = "结构化读取无有效数据行（ROI/成本/收入数值缺失）"
                if rejects:
                    detail["fail_reason"] += f"；{len(rejects)} 行 cell 解析失败（日志已输出 cell 原文）"
            return None, None, None, detail
        sel = cands[0]
        detail["selected_index"] = sel["row"]
        return sel["triplet"][0], sel["triplet"][1], sel["triplet"][2], detail

    def _resolve_hwnd(self, target=None):
        """模式二辅助：解析目标窗口 hwnd。优先锁定句柄（_target_hwnds/selected_hwnd），
        失效或无锁定时按关键词枚举匹配；返回 hwnd 或 None。"""
        t = target if target is not None else self._current_target()
        tid = t.get("id")
        hwnd = self._target_hwnds.get(tid) if tid else None
        if hwnd:
            if find_window_by_hwnd(hwnd):
                return hwnd
            self._target_hwnds.pop(tid, None)
        if self.selected_hwnd and find_window_by_hwnd(self.selected_hwnd):
            return self.selected_hwnd
        kw = str(t.get("window_keyword") or "").strip()
        if not kw:
            kw = self.e_window.get().strip()
        return _find_hwnd_by_keyword(kw)

    # ---------- v10.14.3：刷新链路统一（仅保留 uia_reload 抢前台方案） ----------
    def _uia_action(self, key):
        """取 uia_recog mod 注册的控件操作函数（v10.14.3 仅 "reload"）。"""
        actions = getattr(self, "_uia_mod_actions", None) or {}
        return actions.get(key)

    # ---------- v10.19：并行刷新融合（test_parallel_refresh v0.3 并入）+ 防双刷 ----------
    def _send_refresh_to_target(self, target=None):
        """对目标窗口执行一次刷新（v10.19：防重入壳 + uia_reload）。

        v10.19 改造：原函数体迁至 _do_refresh_with_detail（返回 ok/missing/waited），
        此处套同店防重入壳 _refresh_once_guarded——两路调度（自动并行刷新阶段 + 手动
        完整轮/立即识别）对同一店并发触发时只允许一路执行点击，其余路等待在途刷新完成，
        结构性消灭 9/1-9/2『每店每轮双刷』。返回 True/False。"""
        t = target if target is not None else self._current_target()
        name = t.get("name") or f"目标{t.get('id')}"
        ok, _missing, _waited = self._refresh_once_guarded(t, name)
        return ok

    def _refresh_once_guarded(self, t, name):
        """v10.19：同店刷新防重入——返回 (ok, missing, waited)。

        同店已在途时不重复发起点击，等待在途刷新完成（最长 refresh_wait_sec+15 秒），
        按已刷新处理；点击段串行由 mod 内全局点击锁保证，多店互不影响。"""
        inflight = getattr(self, "_refresh_inflight", None)
        if inflight is None:
            inflight = self._refresh_inflight = {}
        tid = str(t.get("id"))
        if inflight.get(tid):
            _wait_max = max(25, int(self.cfg.get("refresh_wait_sec", 8) or 8) + 15)
            self.log(f"[{name}] [刷新] 该店刷新已在途（并行刷新阶段/手动轮冲突），"
                     f"不重复发起点击，等待在途刷新完成（v10.19 防双刷）")
            _deadline = time.time() + _wait_max
            while inflight.get(tid) and time.time() < _deadline:
                time.sleep(0.5)
            self.log(f"[{name}] [刷新] 在途刷新等待结束（最长 {_wait_max}s），按已刷新处理，接续识别")
            return True, [], 0
        inflight[tid] = True
        try:
            return self._do_refresh_with_detail(t, name)
        finally:
            inflight.pop(tid, None)

    def _do_refresh_with_detail(self, t, name):
        """v10.14.3：实际执行一次 uia_reload 刷新（原 _send_refresh_to_target 函数体）。

        返回 (ok, missing, waited)，供防重入壳/重试决策使用。"""
        hwnd = self._resolve_hwnd(t)
        if not hwnd:
            self.log(f"[{name}] [刷新] 未定位到目标窗口，刷新失败（请检查窗口锁定/关键词匹配）", level="debug")
            return False, ["目标窗口未定位"], 0
        method = str(self.cfg.get("refresh_method", "uia_reload") or "uia_reload").strip().lower()
        if method != "uia_reload":
            self.log(f"[{name}] [刷新] refresh_method={method} 已不再支持（仅 uia_reload），按 uia_reload 执行", level="debug")
        fn = self._uia_action("reload")
        if not fn:
            self.log(f"[{name}] [刷新] uia_recog mod 未加载/未注册，刷新失败（请检查 mods 目录）", level="debug")
            return False, ["uia_recog mod 未加载"], 0
        try:
            res = fn(self, t)
        except Exception as e:
            self.log(f"[{name}] [刷新] UIA 重载刷新异常: {e}", level="debug")
            return False, ["UIA 重载刷新异常"], 0
        ok = bool(res and res.get("ok"))
        missing = (res or {}).get("missing") or []
        waited = (res or {}).get("waited") or 0
        self.log(f"[{name}] [刷新] 方式=uia_reload 结果={'刷新已触发' if ok else '刷新失败'}"
                 + (f"（缺失/失败：{'、'.join(missing)}）" if missing else "")
                 + (f"，就绪轮询等待 {waited:.1f}s" if ok and waited else ""))
        return ok, missing, waited

    def _maybe_refresh_target(self, t):
        """v10.14.3：每轮识别前统一执行「刷新后扫描」——是否刷新由 UI「刷新浏览器」
        勾选框（refresh_enabled）决定：勾选则每轮强制刷新一次，取消则跳过刷新直接识别。
        刷新走 uia_reload（mod 内完成就绪轮询）。v10.14.7：失败自动重试 1 次（等 5 秒）。
        v10.19：仅『点击前失败』才自动重试——目标窗口/uiautomation 初始化/未找到重载
        按钮/按钮点击失败类；『表格就绪未确认』表示点击已发生，只是探测没等到就绪信号，
        重试会再点一次 = 双刷（9/1-9/2 根因之一），改为不重试直接进入识别（识别失败由
        识别层自动重试兜底）。"""
        if not bool(self.cfg.get("refresh_enabled", True)):
            return
        name = t.get("name") or f"目标{t.get('id')}"
        try:
            ok, missing, _waited = self._refresh_once_guarded(t, name)
        except Exception as e:
            self.log(f"[{name}] [刷新] 第1次刷新异常: {e}", level="debug")
            ok, missing = False, ["刷新异常"]
        if not ok:
            _miss_str = "、".join(str(m) for m in (missing or [])) or "未知原因"
            if not any("就绪" in str(m) for m in (missing or [])):
                # 点击前失败 -> 自动重试 1 次（v10.14.7 语义，v10.19 限定为点击前失败）
                self.log(f"[{name}] [刷新] 第1次刷新失败（{_miss_str}，点击前失败），等待 5 秒后自动重试")
                time.sleep(5)
                try:
                    ok, missing, _waited = self._refresh_once_guarded(t, name)
                except Exception as e:
                    self.log(f"[{name}] [刷新] 重试刷新异常: {e}", level="debug")
                    ok, missing = False, ["刷新异常"]
                if not ok:
                    _miss_str = "、".join(str(m) for m in (missing or [])) or "未知原因"
                    self.log(f"[{name}] [刷新] 重试仍失败（{_miss_str}，见上方日志），本轮跳过刷新直接识别", level="result")
            else:
                # 点击已发生但未确认就绪 -> 不重试点击（v10.19 防双刷），直接进入识别
                self.log(f"[{name}] [刷新] 点击已发生但未确认就绪（{_miss_str}），不再自动重试点击"
                         f"（v10.19 防双刷），直接进入识别；识别失败由识别层自动重试兜底", level="result")

    def _selected_snippet(self, detail, maxlen=40):
        """取最终选中行的 OCR 文本片段，用于『最近一次识别结果』展示"""
        if not detail:
            return ""
        sel = detail.get("selected_index")
        if sel is None:
            return ""
        for c in detail.get("candidates") or []:
            if c["row"] == sel:
                t = c["text"].strip().replace("\n", " ")
                return t if len(t) <= maxlen else t[:maxlen] + "…"
        return ""

    def _last_result_text(self, roi, cost, revenue, detail):
        base = (f"识别成功 ROI={roi if roi is not None else '-'} "
                f"成本={cost if cost is not None else '-'} 收入={revenue if revenue is not None else '-'}")
        snippet = self._selected_snippet(detail)
        if snippet and detail.get("selected_index"):
            base += f" | 第{detail['selected_index']}行: {snippet}"
        return base

    def _log_pick_detail(self, detail):
        """日志区输出拾取详情（v10.8.3：正常流程归信息级，仅真正解析失败/读取异常归排错级）：
        生效规则 + 候选数据行 + 严格校验拒绝行 + 选中行标注"""
        if not detail:
            return
        self.log("识别规则： " + self.rec_rules_summary(), level="info")
        if detail.get("expanded"):
            self.log("（本次识别框选区域未取到有效数据，已自动扩展至全窗口识别成功；建议重新框选，把 ROI/成本/收入 三列数值框进来以提升精度）", level="info")
        if detail.get("mode") == "structured":
            self.log("拾取详情：结构化读取命中（UIA 按表头 name 动态列映射，逐行按列 x 就近取 cell 并清洗解析），绕开文本表头扫描", level="info")
        elif detail.get("mode") == "words":
            if detail.get("fallback_header"):
                self.log("拾取详情：词坐标定位成功（严格表头行未命中，已用 ROI+总收入 词宽松配对定位列位置，按列 x 坐标从下方取数）", level="info")
            else:
                self.log(f"拾取详情：词坐标定位成功（找到表头行第{detail['header_index']}行，按 ROI/成本/收入 列 x 坐标从下方取数，绕开字间空格+多行合并）", level="info")
        elif detail.get("mode") == "fail_no_header":
            self.log("拾取详情：未找到表头行（ROI+成本+收入），识别条件已设为『找不到表头→识别失败并提示』，本次识别返回无结果", level="debug")
            return
        elif detail.get("mode") == "fail_no_anchor":
            self.log("拾取详情：起始计划名关键词未在表头下方找到，识别条件已设为『找不到表头→识别失败并提示』，本次识别返回无结果", level="debug")
            return
        elif detail.get("header_index"):
            self.log(f"拾取详情：找到表头行（第{detail['header_index']}行），从其下方开始扫描数据行，绕开顶部统计卡", level="info")
        else:
            self.log("拾取详情：未找到表头行（ROI+成本+收入），识别条件为『宽松解析兜底』，从全文第一行开始扫描", level="info")
        rejects = detail.get("rejects") or []
        for rj in rejects:
            t = rj["text"].strip()
            if len(t) > 45:
                t = t[:45] + "…"
            tag = "结构化数值解析失败" if detail.get("mode") == "structured" else "严格校验拒绝"
            self.log(f"  {tag}第{rj['row']}行（{rj.get('reason', '')}）: 三元组=({rj['triplet'][0]}, {rj['triplet'][1]}, {rj['triplet'][2]}) | OCR: {t}", level="debug")
        cands = detail.get("candidates") or []
        if not cands:
            if rejects:
                self.log(f"拾取详情：无通过校验的数据行（严格校验拒绝 {len(rejects)} 行），走标签行/顺序解析兜底", level="debug")
            else:
                self.log("拾取详情：无通过校验的数据行，走标签行/顺序解析兜底", level="debug")
            return
        sel = detail.get("selected_index")
        for c in cands:
            mark = "→ 已选中" if c["row"] == sel else ""
            t = c["text"].strip()
            if len(t) > 45:
                t = t[:45] + "…"
            if detail.get("mode") == "structured":
                raw = c.get("raw") or {}
                self.log(f"  第{c['row']}行 {mark} 三元组=({c['triplet'][0]}, {c['triplet'][1]}, {c['triplet'][2]}) | cell: ROI={raw.get('roi_raw')!r} 成本={raw.get('cost_raw')!r} 收入={raw.get('revenue_raw')!r}", level="info")
            else:
                self.log(f"  第{c['row']}行 {mark} 三元组=({c['triplet'][0]}, {c['triplet'][1]}, {c['triplet'][2]}) | OCR: {t}", level="info")
        if detail.get("mode") == "structured":
            self.log(f"已选中第{sel}行（结构化读取首行）", level="info")
        elif detail.get("mode") == "words":
            self.log(f"已选中第{sel}行（词坐标重建表格后第一个数据行）", level="info")
        elif detail.get("mode") == "header":
            self.log(f"已选中第{sel}行（表头后第一个数据行）", level="info")
        else:
            self.log(f"已选中第{sel}行（全文第一个数据行）", level="info")

    # ---------- 手动识别（后台线程执行，避免点按钮时界面冻结） ----------
    def _manual_ocr_done(self, test, result=None, error=None):
        """手动识别完成回调（主线程）：渲染结果/错误，恢复界面"""
        self._manual_busy = False
        self.root.config(cursor="")
        if error is not None:
            self.log(f"[{'测试' if test else '识别'}失败] {error}")
            messagebox.showwarning("OCR 测试失败" if test else "识别失败", str(error))
            return
        roi, cost, revenue, text, detail = result
        msg = (f"{'OK  ' if test else '识别成功  '}ROI={roi if roi is not None else '-'}  "
               f"成本={cost if cost is not None else '-'}  收入={revenue if revenue is not None else '-'}")
        self.log(msg)
        self.set_last_result(self._last_result_text(roi, cost, revenue, detail))
        # v10.7：手动识别也向简略模式小窗供数（当前目标 + 全行明细）
        try:
            _rows_manual = self._collect_rows_from_detail(detail)
        except Exception:
            _rows_manual = []
        self._store_last_ocr_result(roi, cost, revenue, detail, rows=_rows_manual, target=self._current_target())
        self._log_pick_detail(detail)
        if roi is None or cost is None or revenue is None:
            reason = (detail or {}).get("fail_reason") or "结构对不上（找不到表头/列对不上/数据异常）"
            # v10.6.2 错误42落地：手动识别同样提示（UIA 页面不对场景，如 GMV Max 概览页）
            _rmode = str(self.cfg.get("recog_mode", "uia") or "uia").strip() or "uia"
            _rs = str(reason or "")
            if _rmode == "uia" and (
                    _rs in ("no_header", "no_anchor", "no_header_cols", "no_rows")
                    or _rs.startswith("结构化读取")
                    or "core-table-tr" in _rs
                    or (detail or {}).get("mode") in ("fail_no_header", "fail_no_anchor")):
                _hint = "当前窗口可能不是广告计划列表页（如 GMV Max 概览页），请切换到广告计划列表页后重试"
                self.log(_hint, level="result")
                # v10.14.7：toast 统一受 toast_enabled 管控（§8.4-4，关闭后一律不弹）
                if self.cfg.get("toast_enabled", False):
                    try:
                        notify_toast("数据看板提醒", _hint)
                    except Exception:
                        pass
            if test:
                self.log(f"【没有找到广告计划】（原因:{reason}），请重新框选（须包含表头+品名+ROI/成本/收入三列）")
            else:
                self.log(f"【没有找到广告计划】{reason}，请重新框选（须包含表头+品名+ROI/成本/收入三列）或手动录入兜底")
                self.log("OCR 原文（完整）:\n" + text)
        else:
            self.log("OCR 原文（前 300 字）:\n" + text[:300])
            # v10.8.3：手动识别与自动监控入库链路对齐——识别成功后对全部候选行
            # 逐行 add_record（每行补 shop 字段），不再只入"已选中第1行"；
            # 推送条件不影响入库（未达成推送条件的行仅不推送）；测试模式仅测试不入库。
            if not test:
                _tgt = self._current_target() or {}
                _shop = _tgt.get("name", "") or ""
                _rows_save = _rows_manual or []
                try:
                    if not _rows_save:
                        _rows_save = self._collect_rows_from_detail(detail)
                except Exception:
                    _rows_save = []
                if _rows_save:
                    for _r in _rows_save:
                        _r["shop"] = _shop
                        add_record(_r)
                    for _r in _rows_save:
                        _pn = _r.get("plan_name") or "-"
                        self.log(f"计划[{_pn}] ROI={_r['roi']:.2f} 成本=${_r['cumulative_cost']:.2f} "
                                 f"收入=${_r['cumulative_revenue']:.2f} 订单={_r['cumulative_orders']}", level="result")
                    self.log(f"[识别] 手动识别已入库 {len(_rows_save)} 行（未达成推送条件的行仅不推送，不影响入库）", level="result")
                    self.run_alerts(_rows_save, shop=_shop)
                else:
                    _orders = int(self.cfg.get("current_orders", 0) or 0)
                    _rec = self.make_record(roi, cost, revenue, _orders, source="ocr")
                    _rec["shop"] = _shop
                    add_record(_rec)
                    self.log(f"手动识别已入库: ROI={_rec['roi']:.2f} 成本=${_rec['cumulative_cost']:.2f} "
                             f"订单={_rec['cumulative_orders']} 收入=${_rec['cumulative_revenue']:.2f}", level="result")
                    self.run_alerts(_rec, shop=_shop, push=False)

    def _run_manual_ocr(self, do_refresh=True):
        """手动识别（v10.14.3）：do_refresh=True 时先刷新浏览器再识别，与自动监控同一
        『刷新后扫描』语义；do_refresh=False 时跳过刷新直接识别。"""
        if self._manual_busy:
            self.log(">>> 上一轮识别还在进行中，请稍候")
            return
        self.log(">>> 立即识别一次（先刷新浏览器再识别）" if do_refresh else ">>> 立即识别一次（跳过刷新，直接识别）")
        self._manual_busy = True
        # v10.26：面板「立即识别」为手动强制路径——刷新门控 force 直通（解除挂起「待刷」）
        self._refresh_gate_force_direct("面板立即识别")
        self.root.config(cursor="watch")

        def _worker():
            try:
                # v10.14.6 校验上提：手动路径与自动路径一致，识别前先预检窗口定位，
                # 未定位直接跳过，不再白跑刷新+识别后抛"未取得窗口句柄"异常
                _cur = self._current_target()
                _cname = _cur.get("name") or f"目标{_cur.get('id')}"
                try:
                    _pre_hwnd = self._resolve_hwnd(_cur)
                except Exception:
                    _pre_hwnd = None
                if not _pre_hwnd:
                    try:
                        _kw = str(_cur.get("window_keyword") or "").strip() or self.e_window.get().strip()
                    except Exception:
                        _kw = str(_cur.get("window_keyword") or "").strip()
                    self.log(f"[{_cname}] 未定位到目标窗口（锁定句柄失效或标题关键词[{_kw}]无匹配），跳过识别；请先打开后台并『列出窗口』锁定", level="result")
                    self.root.after(0, lambda: self._manual_ocr_done(False, error="未定位到目标窗口，请先打开后台并『列出窗口』锁定"))
                    return
                if do_refresh:
                    self._send_refresh_to_target(_cur)
                result = self.recognize_once()
                self.root.after(0, lambda: self._manual_ocr_done(False, result=result))
            except Exception as e:
                err_msg = str(e)
                self.root.after(0, lambda: self._manual_ocr_done(False, error=err_msg))

        threading.Thread(target=_worker, daemon=True).start()

    def do_ocr_once(self):
        # v10.14.3：「立即识别一次」= 先刷新再识别（与自动监控同一"刷新后扫描"语义）
        self._run_manual_ocr(do_refresh=True)

        # v10.14.7：「测试刷新」按钮与 do_test_refresh/_manual_refresh_done 方法已删除（§7.3）

    # ---------- 入库 ----------
    def make_record(self, roi, cost, revenue, orders, uv=None, source="ocr"):
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        rec = {
            "time": now,
            "roi": round(roi, 4) if roi is not None else 0,
            "cost": cost if cost is not None else 0,
            "orders": orders,
            "revenue": revenue if revenue is not None else 0,
            "uv": uv,
            "source": source,
            "cumulative_cost": cost if cost is not None else 0,
            "cumulative_orders": orders,
            "cumulative_revenue": revenue if revenue is not None else 0,
        }
        rec["avg_order_value"] = round(rec["revenue"] / orders, 2) if orders > 0 else 0
        if uv and uv > 0:
            rec["conversion_rate"] = round((orders / uv) * 100, 2)
        else:
            rec["conversion_rate"] = None
        return rec

    def do_manual_record(self):
        try:
            cost = float(self.e_cost.get().strip() or 0)
            orders = int(self.e_orders_m.get().strip() or 0)
            revenue = float(self.e_rev.get().strip() or 0)
            uv_text = self.e_uv.get().strip()
            uv = int(uv_text) if uv_text else None
        except ValueError:
            messagebox.showerror("输入错误", "请检查手动录入的数字格式")
            return
        rec = self.make_record(None, cost, revenue, orders, uv, source="manual")
        add_record(rec)
        self._set_current_orders(orders, f"手动录入完成: 成本${cost:.2f} 订单{orders} 收入${revenue:.2f} UV={uv}")
        self.e_orders.delete(0, tk.END)
        self.e_orders.insert(0, str(orders))
        self.run_alerts(rec)

    # ---------- 告警与推送 ----------
    def run_alerts(self, rec_or_rows, shop=None, push=True, force_close=False):
        """任务A：告警入口，支持单 rec（dict）或行列表（逐行告警）。

        逐行模式：每行独立跑 check_alerts（每行独立比较 ROI 与阈值）；
        多条同时触发时合并为一条推送（避免刷屏）。兼容旧调用（do_manual_record 等传单 rec dict）。
        push=False（v10.9 手动识别/快捷键刷新）：只记录不推送——用户人在电脑前，
        推送仅作为离席告警方式，手动场景不消耗推送额度。
        force_close（v10.24 ③.5「立即检测」按钮）：push=False 时也强制执行自动关——
        候选不走"只报不动"，直接入值守队列并调 _auto_close_watchdog(force=True)，
        跳过无人值守闸门立即真关（不等下轮、工位有人也执行）。
        """
        rows = rec_or_rows if isinstance(rec_or_rows, list) else [rec_or_rows]
        records = load_history()["records"]
        # 逻辑闭环：发送前将 UI 开关实时状态同步进 cfg，确保开关真实参与判断
        self._sync_push_ui_to_cfg()
        # v10.2：目标独立监控——按店铺合并该目标的独立告警配置（无则回退全局）
        cfg = self.cfg
        if shop:
            for t in self._targets():
                if str(t.get("name") or "") == str(shop):
                    cfg = dict(self.cfg)
                    cfg["alert_threshold"] = t.get("alert_threshold") if t.get("alert_threshold") is not None else cfg.get("alert_threshold", 0.6)
                    cfg["alert_cooldown_min"] = 0
                    cfg["alert_rules"] = t.get("alert_rules") if t.get("alert_rules") is not None else cfg.get("alert_rules") or []
                    cfg["avg_order_value"] = t.get("avg_order_value") if t.get("avg_order_value") is not None else cfg.get("avg_order_value", 0.0)
                    break
        # 每行独立判断，收集命中行
        hit_rows = []  # [(rec, alerts, core_hit, core_text)]
        for rec in rows:
            alerts, core_hit, core_text = check_alerts(cfg, rec, records, shop=shop)
            if not alerts:
                continue
            hit_rows.append((rec, alerts, core_hit, core_text))
        # 任务C：告警总开关关闭时只记录不推送
        if not cfg.get("alert_enabled", True):
            self.log("告警总开关已关闭，本轮仅记录不推送")
            return
        if not hit_rows:
            self.log("本轮无告警")
            # v10.11：正常扫描完成——仅自动监控（push=True）且声音开关开启时播放现有轻滴声；
            # 手动识别/快捷键刷新（push=False，用户人在电脑前）不响
            if push and self.cfg.get("sound_alarm", True):
                try:
                    winsound.Beep(880, 120)
                except Exception:
                    pass
            return
        for rec, alerts, _h, _c in hit_rows:
            for a in alerts:
                self.log("[告警] " + a, level="result")

        # 冷却已移除：所有命中行直接进入推送列表，每次触发立即推送
        pending = []  # [(rec, alerts, core_hit, core_text, plan_key)]
        for rec, alerts, core_hit, core_text in hit_rows:
            plan_name = (rec.get("plan_name") or rec.get("name") or "未命名计划").strip() or "未命名计划"
            plan_key = f"{shop}:{plan_name}" if shop else plan_name
            pending.append((rec, alerts, core_hit, core_text, plan_key))
        if not pending:
            self.log("本轮无告警")
            return

        title = "TikTok 监控告警"
        # v10.14.3：旧式 alert_metric/alert_operator 已删除——核心告警固定 ROI 低于阈值
        # （preset_roi_low 语义），由规则编辑器预设参数收编；threshold 保留为预设参数承载键
        metric = "roi"
        op = "lt"
        threshold = cfg.get("alert_threshold", 0.6)
        metric_label = _metric_label(metric)
        op_label = _op_label(op)
        # v10.21 合主：操作层自动关判定已与告警层解耦——对 pending 全部行按品龄规则表
        # 判定（12.4），不再依赖"核心 ROI 告警 + roi_alert_cost_min 门槛"（已废除）。
        # 逐行渲染告警正文并合并（核心告警行按 alert_template，其它规则行按 pushplus_template）
        content_parts = []
        toast_lines = []      # 任务C：本机 toast 正文（含计划名+ROI+成本+收入）
        auto_close_rows = []  # [(plan_name, row_no)]
        for rec, alerts, core_hit, core_text, plan_key in pending:
            roi = rec.get("roi") or 0
            cost = rec.get("cumulative_cost") or 0
            revenue = rec.get("cumulative_revenue") or 0
            plan_name = _plan_label(rec)
            row_no = rec.get("row_no") or 0
            content_default = (
                f"时间：{rec['time']}\n"
                f"店铺：{shop if shop else '-'}\n"
                f"计划：{plan_name}\n"
                f"ROI：{roi:.2f} | 累计消耗：${cost:.2f}\n"
                f"累计订单：{rec['cumulative_orders']} | 累计收入：${revenue:.2f}\n"
                f"-----\n" + "\n".join(alerts)
            )
            if core_hit:
                # 任务C：核心告警主体按 alert_template 渲染（用户可完全掌控文案）；
                # 其它规则告警（成本上限/空烧等）作为补充段落追加。
                # v10.2 推送模板增强：全部业务变量注入（含 message=核心建议、orders、aov、上一轮对比值等）
                prev_rec = records[-1] if records else {}
                content = _render_template(
                    cfg, "alert_template", content_default,
                    time=rec['time'],
                    shop=shop or "",
                    plan_name=plan_name,
                    roi=roi,
                    cost=cost,
                    revenue=revenue,
                    orders=rec.get("cumulative_orders") or 0,
                    threshold=threshold,
                    metric=metric,
                    operator=op,
                    metric_label=metric_label,
                    op_label=op_label,
                    message=core_text or "",
                    aov=cfg.get("avg_order_value") or 0.0,
                    cost_prev=prev_rec.get("cumulative_cost") or 0,
                    revenue_prev=prev_rec.get("cumulative_revenue") or 0,
                    burn_redline=cfg.get("burn_redline") or 5.0,
                    cost_x=cfg.get("cost_x_aov") or 2.0,
                )
                extra = [a for a in alerts if a != core_text]
                if extra:
                    content += "\n\n其他告警：\n" + "\n".join(extra)
                toast_lines.append(
                    f"计划[{plan_name}] ROI={roi:.2f} 成本=${cost:.2f} 收入=${revenue:.2f}"
                )
            else:
                # 仅其它规则告警：维持原 pushplus_template 渲染（变量已全开放）
                prev_rec = records[-1] if records else {}
                content = _render_template(
                    cfg, "pushplus_template", content_default,
                    time=rec['time'],
                    shop=shop or "",
                    plan_name=plan_name,
                    roi=roi,
                    cost=cost,
                    orders=rec.get("cumulative_orders") or 0,
                    revenue=revenue,
                    alerts="\n".join(alerts),
                    message="\n".join(alerts),
                    threshold=threshold,
                    metric=metric,
                    operator=op,
                    metric_label=metric_label,
                    op_label=op_label,
                    aov=cfg.get("avg_order_value") or 0.0,
                    cost_prev=prev_rec.get("cumulative_cost") or 0,
                    revenue_prev=prev_rec.get("cumulative_revenue") or 0,
                    burn_redline=cfg.get("burn_redline") or 5.0,
                    cost_x=cfg.get("cost_x_aov") or 2.0,
                )
            # v10.21 合主：操作层自动关判定（品龄规则表 12.4，与告警层命中与否解耦）
            # tkm_10.23：判定"不关"也落 debug 原因日志（修"判定不关静默"——每轮每条计划
            # 一条 [自动关·不关原因]，回扫日志可核对各计划品龄归属与未关理由）
            act, reason = ops_decision_close(cfg, rec)
            if act == "close":
                auto_close_rows.append((plan_name, row_no, reason))
            else:
                self.log(f"[自动关·不关原因] 计划[{plan_name}]（第{row_no}行）{reason}", level="debug")
            content_parts.append(content)
        content = "\n\n".join(content_parts)
        if auto_close_rows:
            content += "\n\n自动关闭候选（待无人值守闸门）：\n" + "\n".join(
                f"计划[{pn}]（第{rn}行）{reason}" for pn, rn, reason in auto_close_rows)
        # v10.5：推送全量快照——多行列表模式（len(rows)>1）时把本轮全部计划一并推送，
        # 满足"10 条一并显示"；命中告警且进入 pending 的计划行尾标 ⚠（按 plan_name 判断）
        if len(rows) > 1:
            pending_names = set()
            for _r, _a, _c, _t, pk in pending:
                pn = str(pk).split(":", 1)[-1] if ":" in str(pk) else str(pk)
                pending_names.add(pn.strip())
            snap_lines = []
            for i, r in enumerate(rows, 1):
                pn = str(r.get("plan_name") or r.get("name") or "未命名计划").strip()
                snap_roi = r.get("roi") if r.get("roi") is not None else 0
                snap_cost = r.get("cumulative_cost") if r.get("cumulative_cost") is not None else r.get("cost") or 0
                snap_rev = r.get("cumulative_revenue") if r.get("cumulative_revenue") is not None else r.get("revenue") or 0
                snap_ord = r.get("orders") if r.get("orders") is not None else r.get("cumulative_orders") or 0
                snap_status = r.get("status") or "-"
                mark = " ⚠" if pn in pending_names else ""
                snap_lines.append(
                    f"{i}. [{pn}] ROI={snap_roi:.2f} 成本=${snap_cost:.2f} 收入=${snap_rev:.2f} "
                    f"订单={snap_ord} 状态={snap_status}{mark}")
            content += "\n\n===== 本轮全部计划 =====\n" + "\n".join(snap_lines)
        # v10.9：手动识别/快捷键刷新（push=False）——只记录不推送（用户在电脑前，
        # 推送仅作为离席告警方式，不消耗推送额度）；告警已在上方以 [告警] 日志记录
        if not push:
            if force_close:
                # v10.24 ③.5「立即检测」强制完整轮：仍按手动原则不推送不响（人在电脑前），
                # 但自动关候选不走"只报不动"——直接入值守队列并 force=True 立即真关
                # （跳过无人值守闸门，不等下轮、工位有人也执行）
                self.log(f"[手动识别·立即检测] 命中 {len(hit_rows)} 条告警，强制执行自动关判定（跳过无人值守闸门）", level="result")
                if auto_close_rows:
                    self._auto_close_store_pending(auto_close_rows)
                    self.log(f"[自动关·立即检测] {len(auto_close_rows)} 个候选已入值守队列，立即强制尝试执行", level="debug")
                try:
                    self._auto_close_watchdog(source="立即检测", force=True)
                except Exception as _e:
                    self.log(f"[自动关] 值守步进异常: {_e}", level="debug")
                return
            self.log(f"[手动识别] 命中 {len(hit_rows)} 条告警，人在电脑前，跳过远程推送（告警已记录入库）", level="result")
            # v10.21 合主（13.1 原则2）：手动路径一律只报不动——记录候选供人工处理，不执行自动关
            for plan_name, row_no, reason in auto_close_rows:
                self.log(f"[自动关候选·手动路径不执行] 计划[{plan_name}]（第{row_no}行）{reason}", level="debug")
            return
        # 任务C：本机 toast 通知（低调标题「数据看板提醒」，正文含计划名/ROI/成本/收入）
        # v10.10：toast_enabled 开关——上班时间可关闭防被同事看到；关闭仅影响本机气泡，推送/声音不受影响
        if toast_lines and self.cfg.get("toast_enabled", False):
            notify_toast("数据看板提醒", "；".join(toast_lines))
        # v10.9.1：告警轻滴声；v10.11 深化为按告警类型区分：
        # 有不好情况（负面告警：ROI低于底线/空烧/超上限/止损等）→ 低沉长音 Beep(220,500)；
        # 加预算信号（ROI 达投放目标 preset_roi_good 命中，含"可加预算/加量"）→ 明亮双连音 Beep(1300,120)×2；
        # 其它/混合（无法归类的告警）→ 保留现有轻滴声 Beep(880,120)。
        # 仅在自动监控命中告警（push=True）且开启声音开关时响；手动识别/快捷键刷新已 return 不响
        if self.cfg.get("sound_alarm", True) and hit_rows:
            try:
                _has_bad = any(
                    "建议暂停" in _a or "建议停投" in _a or "止损" in _a
                    or "控制预算" in _a or "低于阈值" in _a or "未增长" in _a
                    or "低于走量底线" in _a or "超客单" in _a
                    for _r2, _al2, _c2, _t2 in hit_rows for _a in _al2
                )
                _has_good = any(
                    "可加预算" in _a or "加量" in _a or "达投放目标" in _a
                    for _r2, _al2, _c2, _t2 in hit_rows for _a in _al2
                )
                if _has_bad:
                    winsound.Beep(220, 500)
                elif _has_good:
                    winsound.Beep(1300, 120)
                    winsound.Beep(1300, 120)
                else:
                    winsound.Beep(880, 120)
            except Exception:
                pass
        # v10.9：打包推送——告警先入合并缓冲，合并窗口到点统一发一条，省每日推送额度
        self._enqueue_push(title, content)
        # tkm_10.23 守夜版（替代 v10.21 原“逐候选过闸门”段）：
        # 候选先入值守队列（同一计划只留最新），随后立即走值守步进——闸门通过当场执行；
        # 闸门不过（有人值守/总开关关）的候选留在队列，由值守步进在键鼠空闲达到阈值后
        # 第一时间执行，不再等下一个识别轮（解决“识别间隔独立运行、干等轮询”冲突）。
        if auto_close_rows:
            self._auto_close_store_pending(auto_close_rows)
            self.log(f"[自动关] {len(auto_close_rows)} 个候选已入值守队列，立即尝试执行", level="debug")
        try:
            self._auto_close_watchdog(source="识别轮")
        except Exception as _e:
            self.log(f"[自动关] 值守步进异常: {_e}", level="debug")

    # ---------- tkm_10.23 守夜版：自动关值守队列（解耦识别轮间隔与自动关时机） ----------
    def _auto_close_store_pending(self, rows):
        """自动监控识别出的 close 候选入值守队列（仅 push=True 自动路径调用）。

        同一计划只保留最新一条（覆盖 row_no/reason/时间戳），避免队列无限膨胀与重复关闭。
        手动识别路径不调用本方法——手动只报不动（13.1），不产生守夜待命。
        """
        try:
            _now = time.time()
            with self._auto_close_watch_lock:
                for plan_name, row_no, reason in (rows or []):
                    if not plan_name:
                        continue
                    hit = None
                    for it in self._auto_close_pending:
                        if it.get("plan_name") == plan_name:
                            hit = it
                            break
                    if hit is not None:
                        hit["row_no"] = row_no
                        hit["reason"] = reason
                        hit["ts"] = _now
                    else:
                        self._auto_close_pending.append({
                            "plan_name": plan_name, "row_no": row_no,
                            "reason": reason, "ts": _now,
                            "attempts": 0, "last_try": 0.0,
                        })
        except Exception:
            pass

    def _auto_close_watchdog(self, source="值守", force=False):
        """守夜值守步进：只要无人值守闸门通过，立即执行队列中的待关候选。

        调用点：① run_alerts 识别轮结束立即调一次（闸门过当场关）；② _monitor_loop
        倒计时等待中每秒调一次（闸门由“有人”翻“无人”后 1 秒内接手，不再等下个识别轮）；
        ③ v10.24 ③.5「立即检测」按钮：force=True 手动强制路径——跳过 monitor_running
        检查与 is_unattended 无人值守闸门、忽略 60 秒冷却，对队列中候选立即真关
        （工位有人也执行，不等下个识别轮）。
        返回本轮实际执行的候选数（含成功/失败尝试）。

        失败策略（防反复点关）：
        - 结果串含确定性结束词（已暂停/未找到/无操作等）→ 出队，不再重试；
        - 其它失败 → 保留，attempts+1，60 秒冷却后由后续值守重试；达 3 次上限出队并记录。
        执行期间置 _auto_close_watching 防重入（执行耗时可达数秒~十几秒）。
        """
        if not force and not getattr(self, "monitor_running", False):
            return 0
        if getattr(self, "_auto_close_watching", False):
            return 0
        if force:
            # v10.24 立即检测（手动强制）：跳过无人值守闸门，用户主动点击即视为授权真关
            gate_ok, gate_why = True, "立即检测（手动强制，跳过无人值守闸门）"
        else:
            # 总开关/空闲闸门——由值守统一把关（走空闲判断机 idle_gate，仅自动路径候选会入队）
            gate_ok, gate_why = idle_gate.is_unattended(self.cfg)
            if not gate_ok:
                # 修复（2026-09-09）：闸门不过不再静默 return——此前自动轮入队后无任何
                # 闸门/执行/异常日志，造成“全天候选入队零消费”的静默断链假象，无法定位。
                # 节流打印原因（≥30s 至多一条，避免守夜值守每秒轮询刷屏），候选保留待下次值守。
                try:
                    # 打日志条件：①配置性拒绝（总开关关闭）必打——这是静默断链的元凶；
                    # ②识别轮刚入队后打一条（说明候选在等闸门，非漏检）；守夜值守仅空闲
                    # 不足的“正常等待”不打（避免用户在场时每秒轮询刷屏），30s 节流兜底。
                    _need_log = ("总开关关闭" in str(gate_why)) or (source == "识别轮")
                    if _need_log:
                        _rej_now = time.time()
                        _rej_last = getattr(self, "_auto_close_gate_reject_ts", 0.0)
                        if _rej_last == 0.0 or (_rej_now - _rej_last) >= 30:
                            self._auto_close_gate_reject_ts = _rej_now
                            with self._auto_close_watch_lock:
                                _rej_pend = len(self._auto_close_pending)
                            self.log(f"[自动关·{source}] 值守闸门未通过（{gate_why}），"
                                     f"{_rej_pend} 个候选保留待值守，暂不执行关闭", level="debug")
                except Exception:
                    pass
                return 0
        try:
            self._auto_close_watching = True
            _now = time.time()
            with self._auto_close_watch_lock:
                if force:
                    # v10.24 立即检测：忽略 60s 冷却，队列中全部候选立即尝试（保留 3 次上限）
                    todo = [dict(it) for it in self._auto_close_pending
                            if it.get("attempts", 0) < 3]
                else:
                    todo = [dict(it) for it in self._auto_close_pending
                            if it.get("attempts", 0) < 3
                            and (_now - it.get("last_try", 0.0)) >= 60]
            if not todo:
                return 0
            done = 0
            _final_hints = ("已暂停", "暂停成功", "未找到", "找不到", "不存在", "无操作",
                            "无需", "已是", "未开启", "开关不可用", "灰钮")
            for it in todo:
                if not force and not self.monitor_running:
                    break
                plan_name, row_no, reason = it["plan_name"], it["row_no"], it["reason"]
                self.log(f"[自动关·{source}] 闸门通过（{gate_why}）→ 执行 计划[{plan_name}]（第{row_no}行）：{reason}", level="result")
                try:
                    _res = auto_close_ad(self.cfg, plan_name=plan_name, row_no=row_no,
                                         reason=reason,
                                         logger=lambda m: self.log(m, level="debug"))
                except Exception as _e:
                    _res = f"执行异常: {_e}"
                self.log(f"[自动关·{source}] 执行结果：{_res}", level="result")
                # v10.21 修复轮：自动关事件记入内存列表，供 ③.5 面板状态只读区展示
                self._record_auto_close_event(plan_name, f"{reason} → {_res}")
                _final = any(_h in str(_res) for _h in _final_hints)
                with self._auto_close_watch_lock:
                    for _p in self._auto_close_pending:
                        if _p.get("plan_name") == plan_name:
                            if _final:
                                self._auto_close_pending.remove(_p)
                                self.log(f"[自动关·{source}] 计划[{plan_name}] 已结束（{_res}），出队", level="debug")
                            else:
                                _p["attempts"] = _p.get("attempts", 0) + 1
                                _p["last_try"] = time.time()
                                if _p["attempts"] >= 3:
                                    self.log(f"[自动关·{source}] 计划[{plan_name}] 重试达上限，出队放弃", level="debug")
                                    self._auto_close_pending.remove(_p)
                            break
                done += 1
            return done
        finally:
            try:
                self._auto_close_watching = False
            except Exception:
                pass

    # ---------- v10.14 打包推送：轮次缓冲，每轮识别完统一发送 ----------
    def _enqueue_push(self, title, content):
        """告警推送入轮次缓冲。v10.14：不再按 push_batch_minutes 启动 Timer 等待，
        自动监控一轮（所有启用店）识别完成后由 _monitor_loop 统一 _flush_push_batch
        合并发送；stop_monitor 兜底 flush 防丢。0/旧配置值不再参与推送时机。"""
        lock = getattr(self, "_push_lock", None)
        if lock is None:
            lock = self._push_lock = threading.Lock()
        with lock:
            if not getattr(self, "_push_buffer", None):
                self._push_buffer = []
            self._push_buffer.append((datetime.now().strftime("%H:%M:%S"), title, content))
        self.log(f"[推送] 告警已入轮次缓冲（{len(self._push_buffer)} 条），本轮识别完成后统一打包发送", level="result")

    def _enqueue_round_alert(self, shop, reason):
        """v10.14：识别失败也打包告警——自动监控时某店识别失败（结构对不上/未解析到
        ROI/成本/收入/识别异常）生成"识别失败"条目入轮次缓冲，随本轮正常告警一起
        推送，便于用户判断是网络不好或浏览器页面未刷新。"""
        try:
            _sync = self._sync_push_ui_to_cfg
            if callable(_sync):
                _sync()
        except Exception:
            pass
        content = (f"店铺：{shop}\n"
                   f"识别失败：{reason}\n"
                   f"（可能是网络不好或浏览器页面未刷新，请检查）")
        self._enqueue_push("TikTok 监控告警", content)

    def _flush_push_batch(self, _cfg=None):
        """轮次缓冲到点/轮末：缓冲内所有告警合并为一条推送发送（线程安全，可被 stop_monitor 兜底调用）。"""
        try:
            lock = getattr(self, "_push_lock", None)
            if lock is not None:
                with lock:
                    if not getattr(self, "_push_buffer", None):
                        return
                    items = self._push_buffer
                    self._push_buffer = []
            else:
                items = getattr(self, "_push_buffer", [])
                self._push_buffer = []
            if not items:
                return
            # v10.14.2 修复（v10.14.1 该修复实际未落盘）：发送前先同步 UI 开关到内存配置，
            # 直接用 self.cfg，不再 load_config() 读磁盘旧值（用户取消勾选未保存时磁盘仍 true 导致照推）
            try:
                self._sync_push_ui_to_cfg()
            except Exception:
                pass
            cfg = self.cfg
            parts = [f"—— {ts} ——\n{body}" for ts, _t, body in items]
            merged = "\n\n".join(parts)
            title = f"TikTok 监控告警（合并 {len(items)} 条）"
            for name, ok, msg in send_alerts(cfg, title, merged):
                if ok and name == "pushplus":
                    bump_push_count(cfg)
                self.log(f"[推送-{name}] {msg}", level="result")
        except Exception as e:
            self.log(f"[推送] 合并推送失败: {e}")

    # ---------- 自动监控线程 ----------
    def start_monitor(self):
        if self.monitor_running:
            self.log("监控已在运行")
            return
        cur = self._current_target()
        if not cur.get("roi"):
            # 错误17补充：未设置 ROI 区域也允许启动，识别时自动使用全窗口兜底
            self.log("未设置 ROI 区域，将使用全窗口识别（建议框选 ROI 区域以提升精度）")
        self.monitor_running = True
        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="normal")
        interval = max(1, self.cfg.get("interval_minutes", 60))
        self.log(f"自动监控已启动，每 {interval} 分钟识别一次，共 {len([t for t in self._targets() if t.get('enabled', True)])} 个启用目标")
        self.monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.monitor_thread.start()

    def stop_monitor(self):
        self.monitor_running = False
        self.btn_start.config(state="normal")
        self.btn_stop.config(state="disabled")
        self.log("自动监控已停止（等待当前轮次结束）")
        # v10.19：监控停止时清零刷新异常统计（进程内跨轮累计口径随监控生命周期）
        try:
            _rst = self._uia_action("refresh_stats_reset")
            if _rst:
                _rst(self)
        except Exception:
            pass
        # v10.9：停止时兜底发送合并缓冲内未发出的告警，避免告警丢失（异步不卡 UI）
        # v10.14：缓冲语义已改为"轮次缓冲"（每轮识别完统一发），停止兜底 flush 逻辑不变
        if getattr(self, "_push_buffer", None):
            threading.Thread(target=self._flush_push_batch, daemon=True).start()

    def _refresh_all_targets_parallel(self, targets):
        """v10.19：多目标并行刷新阶段（test_parallel_refresh v0.3 验证方案并入主程序）。

        各店走 _refresh_once_guarded -> mod _uia_reload：点击段在 mod 内全局点击锁
        _CLICK_LOCK 内执行（收集树→置前→Invoke→还原焦点），互不抢前台/互不打断；
        锁释放后各店就绪轮询并行（mod _poll_ready_after_action 锁外轮询）。
        阶段完成返回 True，由调用方以 do_refresh=False 进入识别，不再逐店二次刷新。
        单店刷新失败不影响其他店；整体异常向上抛出由调用方降级逐店刷新。"""
        if not targets:
            return False
        self.log(f"[刷新] v10.19 并行刷新阶段开始（{len(targets)} 个目标：点击段全局锁串行 + 就绪轮询并行）")

        def _one(t):
            try:
                name = t.get("name") or f"目标{t.get('id')}"
                self._refresh_once_guarded(t, name)
            except Exception as e:
                self.log(f"[目标 {t.get('name', t.get('id'))}] 并行刷新异常: {e}", level="debug")

        _ths = []
        for t in targets:
            if not self.monitor_running:
                break
            _th = threading.Thread(target=_one, args=(t,), daemon=True,
                                   name=f"refresh-{t.get('id')}")
            _ths.append(_th)
            _th.start()
        for _th in _ths:
            _th.join(180)
        self.log("[刷新] v10.19 并行刷新阶段完成（点击已提交，识别轮不再二次刷新）")
        return True

    def _log_refresh_stats_summary(self):
        """v10.19：刷新异常分类统计摘要（只读快照，随主日志输出；详情/首次细节在 ops 日志）。
        同类错误记触发次数；同分类细节不同——首次细节由 mod 写入 ops 日志，累计次数在此汇总。
        无失败（cats 空）时不输出，避免每轮刷屏。"""
        try:
            _fn = self._uia_action("refresh_stats")
            if not _fn:
                return
            st = _fn(self) or {}
            cats = st.get("cats") or {}
            if not cats:
                return
            self.log(f"[刷新-统计] 自统计清零以来：刷新调用 {st.get('count', 0)} 次，成功 {st.get('ok', 0)} 次，失败 {st.get('failed', 0)} 次（分类明细如下）：")
            for _cat, _info in cats.items():
                _notes = _info.get("notes") or {}
                if len(_notes) <= 1:
                    self.log(f"[刷新-统计]   - {_cat}：{_info.get('n', 0)} 次")
                else:
                    self.log(f"[刷新-统计]   - {_cat}：{_info.get('n', 0)} 次（细节 {len(_notes)} 种，首次细节见 ops 日志，最近几条：")
                    for _d, _c in list(_notes.items())[-3:]:
                        self.log(f"[刷新-统计]       · {_d}（{_c} 次）")
                    self.log("[刷新-统计]     ）")
        except Exception as e:
            self.log(f"[刷新-统计] 统计摘要输出失败: {e}", level="debug")

    def _monitor_loop(self):
        interval_sec = max(1, self.cfg.get("interval_minutes", 60)) * 60
        # v10.26：首轮标记——启动后第一轮由用户主动触发（点「开始监控」/ 无人自启拉起），
        # 不属于「间隔到点」，故刷新门控直通，避免启动后要等一个轮询间隔才首次刷新。
        _first_round = True
        while self.monitor_running:
            self.cfg = load_config()  # 重新读取，保证面板改动即时生效
            interval_sec = max(1, self.cfg.get("interval_minutes", 60)) * 60
            targets = [t for t in self._targets() if t.get("enabled", True)]
            if not targets:
                self.log("没有启用的监控目标，请在目标栏添加或启用目标")
            else:
                self.log("=" * 50)
                self.log(f"[{datetime.now().strftime('%H:%M:%S')}] 开始识别 {len(targets)} 个目标")
                # ---- v10.26：间隔到点刷新门控（统一「空闲判断机」idle_gate）----
                # 语义：判定「有人」→ 本轮不刷新浏览器（不抢前台、不打扰用户），记一条日志
                # 并挂起「待刷」；空闲达标（无人）后由下方逐秒等待循环立即补偿刷一轮。
                # 手动路径（热键刷新 / 面板立即识别 / 立即检测）不经此处，天然 force 直通
                # （见 _refresh_gate_force_direct）；首轮直通见 _first_round；门控总闸配置键
                # refresh_idle_gate_enabled（默认 True=门控生效）。
                _refresh_allowed = True
                try:
                    _rg_allow, _rg_why, _rg_pending = self._refresh_gate.request(
                        self.cfg, force=False, first_round=_first_round)
                    _refresh_allowed = bool(_rg_allow)
                    if _rg_allow:
                        self.log(f"[刷新门控] 允许本轮刷新：{_rg_why}")
                    else:
                        self.log(f"[刷新门控] {_rg_why}", level="result")
                        self.ops_log(f"[刷新门控] {_rg_why}")
                except Exception as _e:
                    _refresh_allowed = True   # 判定失败按直通处理，绝不因门控异常而不刷新
                    self.log(f"[刷新门控] 判定异常，按直通处理（本轮照常刷新）: {_e}", level="debug")
                _first_round = False
                # v10.19：多目标并行刷新阶段（test_parallel_refresh v0.3 融合进主程序）——
                # 点击段全局锁串行 + 就绪轮询锁外并行；阶段完成后再逐店识别，识别轮不再刷新。
                # 单目标/刷新关闭/uia_recog reload 未加载时不阶段化，_monitor_round 内逐店刷新。
                _parallel_refresh_used = False
                if (_refresh_allowed
                        and bool(self.cfg.get("refresh_enabled", True)) and len(targets) > 1
                        and self._uia_action("reload")):
                    try:
                        _parallel_refresh_used = self._refresh_all_targets_parallel(targets)
                    except Exception as e:
                        self.log(f"[刷新] 并行刷新阶段异常，降级逐店刷新: {e}", level="debug")
                        _parallel_refresh_used = False
                for t in targets:
                    if not self.monitor_running:
                        break
                    try:
                        self._monitor_round(t, do_refresh=(_refresh_allowed and not _parallel_refresh_used))
                    except Exception as e:
                        # 任务A：单目标识别失败只记该目标日志，不影响其他目标
                        self.log(f"[目标 {t.get('name', t.get('id'))}] 监控异常: {e}")
                        # v10.14.5 逆向纠错：窗口未定位类异常（句柄失效/关键词无匹配）
                        # 只记本地日志，不生成推送告警——没有定位到窗口时机械推送毫无意义
                        _err_str = str(e)
                        if any(_k in _err_str for _k in ("未定位到目标窗口", "未取得目标窗口句柄", "未定位窗口", "句柄失效")):
                            self.log(f"[目标 {t.get('name', t.get('id'))}] 窗口未定位类异常，不推送（仅本地记录）", level="debug")
                        else:
                            # v10.14：识别异常（重试后仍抛）也生成识别失败告警，随本轮一起推送
                            try:
                                self._enqueue_round_alert(
                                    t.get("name") or f"目标{t.get('id')}",
                                    f"监控异常: {e}")
                            except Exception:
                                pass
                # v10.19：本轮刷新异常分类统计摘要（同类记次数；仅失败时输出）
                self._log_refresh_stats_summary()
                # v10.14：一轮（所有启用店）识别完成，统一打包发送本轮缓冲内的全部告警
                self._flush_push_batch()
            # tkm_10.23 守夜版：等待循环由“整段干睡”改为逐秒值守——
            # ① 键鼠空闲达到阈值后，值守步进立即执行队列中待关候选（不再等下个识别轮）；
            # ② 面板轮询间隔变更即时生效（保存后重新计时，不再等本轮倒计时走完）；
            # ③ 手动完整轮完成后仍重置定时器（v10.12 原语义保留）。
            _waited = 0
            while _waited < interval_sec:
                if not self.monitor_running:
                    break
                # v10.12：手动完整轮完成后置位，跳出倒计时重新计时
                if getattr(self, "_reset_monitor_timer", False):
                    self._reset_monitor_timer = False
                    self.log("自动监控定时器已重置（手动完整轮完成，重新计时）")
                    break
                _new_interval = max(1, self.cfg.get("interval_minutes", 60)) * 60
                if _new_interval != interval_sec:
                    self.log(f"轮询间隔已变更为 {_new_interval // 60} 分钟，重新计时（原 {interval_sec // 60} 分钟）")
                    interval_sec = _new_interval
                    _waited = 0
                    continue
                # 守夜值守：闸门一通过即执行待关候选
                try:
                    self._auto_close_watchdog(source="守夜值守")
                except Exception:
                    pass
                # v10.26：挂起「待刷」补偿——空闲达标（无人）立即跳出倒计时，下一轮即刻补刷
                try:
                    _comp, _comp_why = self._refresh_gate.poll_compensation(self.cfg)
                    if _comp:
                        self.log(f"[刷新门控] {_comp_why}，跳出倒计时立即补刷一轮")
                        self.ops_log(f"[刷新门控] {_comp_why}，立即补刷一轮")
                        break
                except Exception:
                    pass
                time.sleep(1)
                _waited += 1
        self.log("自动监控已停止")

    def _collect_rows_from_detail(self, detail, max_rows=None):
        """任务A：从识别 detail 提取所有候选数据行（最多 max_rows_to_check 行，默认 10）。

        词坐标路径 candidates 携带 plan_name/triplet；文本路径 candidates 仅有
        triplet（无品名），计划名用「第N行」占位并附文本摘要便于人工识别。
        返回 list[rec]，每项含 plan_name/roi/cost/revenue/row_no，供逐行告警判断。
        v10.5：结构化行完整字段（status/budget/target_roi/net_cost/schedule/avg_cost/
        roi_protection）从 c.get("raw") 或 c 透传到 rec，供逐行入库；source 按
        detail.mode=="structured" 记为 "uia"，否则 "ocr"。
        """
        rows = []
        if not detail or not isinstance(detail, dict):
            return rows
        structured_mode = str(detail.get("mode") or "") == "structured"
        src = "uia" if structured_mode else "ocr"
        try:
            max_rows = max(1, int(max_rows if max_rows is not None
                                  else self.cfg.get("max_rows_to_check", 10)))
        except (ValueError, TypeError):
            max_rows = 10
        for c in (detail.get("candidates") or [])[:max_rows]:
            triplet = c.get("triplet")
            if not triplet or len(triplet) != 3:
                continue
            roi, cost, revenue = triplet
            if roi is None or cost is None or revenue is None:
                continue
            plan_name = (c.get("plan_name") or "").strip()
            if not plan_name:
                snippet = (c.get("text") or "").strip().replace("\n", " ")
                plan_name = f"第{c.get('row', '?')}行" + (f"·{snippet[:24]}" if snippet else "")
            # v10.3 任务2：结构化行自带 orders（SKU 订单数），优先使用；文本路径仍用 current_orders
            try:
                orders = int(c.get("orders") or self.cfg.get("current_orders", 0) or 0)
            except (ValueError, TypeError):
                orders = int(self.cfg.get("current_orders", 0) or 0)
            r = self.make_record(roi, cost, revenue, orders, source=src)
            r["plan_name"] = plan_name
            r["row_no"] = c.get("row") or 0
            # v10.5：完整行字段透传——优先取结构化 raw（UIA 原行），缺失时回退候选自身
            raw = c.get("raw") if isinstance(c.get("raw"), dict) else {}
            for fld in ("status", "budget", "target_roi", "net_cost",
                        "schedule", "avg_cost", "roi_protection"):
                if raw.get(fld) is not None:
                    r[fld] = raw.get(fld)
                elif c.get(fld) is not None:
                    r[fld] = c.get(fld)
                else:
                    r[fld] = None
            rows.append(r)
        return rows

    def _recog_is_failed(self, detail, roi, cost, revenue):
        """v10.8.4：识别失败判定——结构对不上（no_header/no_anchor 等）或未解析到 ROI/成本/收入。"""
        if detail and (detail.get("fail_reason") or detail.get("mode") in ("fail_no_header", "fail_no_anchor")):
            return True
        if roi is None or cost is None or revenue is None:
            return True
        return False

    def _recog_fail_reason(self, detail, roi, cost, revenue):
        """v10.8.4：识别失败原因描述（供自动重试日志）。"""
        if detail and detail.get("fail_reason"):
            return detail.get("fail_reason")
        if detail and detail.get("mode") in ("fail_no_header", "fail_no_anchor"):
            return detail.get("mode")
        return "未解析到 ROI/成本/收入"

    def _monitor_round(self, t, push=True, do_refresh=True, force_close=False):
        """单目标一轮识别：锁定窗口→截图→识别→入库→告警（含 ROI）。
        v10.14：push=False（手动完整轮，人在电脑前）时正常告警与识别失败告警
        均只记录不推送；自动监控（默认 True）识别失败也生成告警随本轮打包推送。
        v10.24：force_close=True（③.5「立即检测」按钮）——手动触发但强制完整执行
        自动关链路，等同 push=True 的"识别→判定→应关即真关"，仅保留手动不推送特性。"""
        tid = t.get("id")
        name = t.get("name") or f"目标{tid}"
        self.log("-" * 40)
        self.log(f"[{name}] 开始识别")
        # v10.14.5 逆向纠错：识别前先校验目标窗口是否定位成功——句柄失效/关键词无匹配
        # 时本轮直接跳过，不进入识别、不产生识别失败告警、不推送，
        # 杜绝"没有指定窗口却机械推送消息"（此前窗口未定位被当作识别异常推送）
        try:
            _pre_hwnd = self._resolve_hwnd(t)
        except Exception:
            _pre_hwnd = None
        if not _pre_hwnd:
            try:
                _kw = str(t.get("window_keyword") or "").strip() or self.e_window.get().strip()
            except Exception:
                _kw = str(t.get("window_keyword") or "").strip()
            self.log(f"[{name}] 未定位到目标窗口（锁定句柄失效或标题关键词[{_kw}]无匹配），本轮跳过识别，不推送；请先打开后台并『列出窗口』锁定", level="result")
            return
        # v10.7.2：定期刷新（路线B）——在识别之前。
        # v10.19：多目标轮首已由并行刷新阶段统一刷新（_monitor_loop 传 do_refresh=False），
        # 本店不再二次刷新（结构性消灭双刷）；单目标/降级路径保持 do_refresh=True 原样。
        if do_refresh:
            self._maybe_refresh_target(t)
        retried = False
        try:
            roi, cost, revenue, text, detail = self.recognize_once(t)
        except Exception as e:
            # v10.8.4：识别异常也自动重试一次（等待 5 秒），重试仍异常则抛给上层记录
            self.log(f"[{name}] 第1次识别异常（{e}），等待 5 秒后自动重试识别（v10.8.4）")
            time.sleep(5)
            roi, cost, revenue, text, detail = self.recognize_once(t)
            retried = True
        else:
            if self._recog_is_failed(detail, roi, cost, revenue):
                _reason = self._recog_fail_reason(detail, roi, cost, revenue)
                # v10.26 修复：页面状态非『全部已生效』（全非已生效被滤 / 结构化无有效数据行）时，
                # 原地重试同一视图救不回来——先强制一次约束性刷新（mod 已保证导航 URL 恒带
                # 当天 + campaign_status=delivery_ok），再重试识别；其它失败维持原重试语义。
                _need_correct_view = (
                    "非已生效" in _reason
                    or bool(detail and detail.get("mode") == "structured"
                            and (detail.get("fail_reason") or "") and "有效数据行" in str(detail.get("fail_reason")))
                )
                if _need_correct_view:
                    self.log(f"[{name}] 第1次识别失败（原因:{_reason}），视图疑似非『全部已生效』，"
                             f"先强制约束性刷新（当天+delivery_ok）再重试识别（v10.26）")
                    try:
                        self._send_refresh_to_target(t)
                    except Exception as e:
                        self.log(f"[{name}] 约束性刷新异常（{e}），直接重试识别", level="debug")
                    time.sleep(5)
                else:
                    self.log(f"[{name}] 第1次识别失败（原因:{_reason}），等待 5 秒后自动重试识别（v10.8.4）")
                    time.sleep(5)
                roi, cost, revenue, text, detail = self.recognize_once(t)
                retried = True
        # v10.6.2：识别成功日志尽量带首行计划名（A419 多计划区分不开）
        def _first_plan_name(detail):
            try:
                _r0 = self._collect_rows_from_detail(detail)
                if _r0 and _r0[0].get("plan_name"):
                    return _r0[0]["plan_name"]
            except Exception:
                pass
            return None
        if t.get("id") == self.current_target_id:
            # v10.7：成功分支收集全行明细供简略模式小窗展示（与下方入库共用 _collect_rows_from_detail）
            _rows_now = []
            try:
                _rows_now = self._collect_rows_from_detail(detail)
            except Exception:
                _rows_now = []
            self._store_last_ocr_result(roi, cost, revenue, detail, rows=_rows_now, target=t)
            _fp = _first_plan_name(detail)
            msg = "识别成功" + ("（重试成功）" if retried else "")
            if _fp:
                msg += f" 计划[{_fp}]"
            msg += f"  ROI={roi if roi is not None else '-'}  成本={cost if cost is not None else '-'}  收入={revenue if revenue is not None else '-'}"
            self.log(msg, level="result")
            self.set_last_result(self._last_result_text(roi, cost, revenue, detail))
            self._log_pick_detail(detail)
        else:
            _fp = _first_plan_name(detail)
            _msg = f"[{name}] 识别成功" + ("（重试成功）" if retried else "")
            if _fp:
                _msg += f" 计划[{_fp}]"
            _msg += f"  ROI={roi if roi is not None else '-'}  成本={cost if cost is not None else '-'}  收入={revenue if revenue is not None else '-'}"
            self.log(_msg, level="result")
        # 错误20：结构对不上（找不到表头/列对不上/数据异常）必须明确报
        # "没有找到广告计划"并跳过入库，禁止静默兜底硬读假数据（错误16防脏数据保留）
        if (detail and detail.get("fail_reason")) or (detail and detail.get("mode") in ("fail_no_header", "fail_no_anchor")):
            reason = detail.get("fail_reason") or detail.get("mode")
            _retry_tag = "（重试后仍失败）" if retried else ""
            self.log(f"【没有找到广告计划】识别结构对不上（原因:{reason}）{_retry_tag}，本轮跳过入库，请重新框选（须包含表头+品名+ROI/成本/收入三列）", level="result")
            # v10.14：识别失败也告警——自动监控时随本轮打包推送，提示可能是网络/浏览器问题
            if push:
                self._enqueue_round_alert(name, f"识别结构对不上（原因:{reason}）")
            # v10.6.2 错误42落地：UIA 模式结构化未找到广告计划表格（页面不对，如 GMV Max 概览页）时，
            # 追加 result 级日志并弹本机 toast，引导切回广告计划列表页（A419 店实测停在概览页识别失败）
            _rmode = str(self.cfg.get("recog_mode", "uia") or "uia").strip() or "uia"
            _rs = str(reason or "")
            _uia_missing_table = (
                _rmode == "uia"
                and (
                    _rs in ("no_header", "no_anchor", "no_header_cols", "no_rows")
                    or _rs.startswith("结构化读取")
                    or "core-table-tr" in _rs
                    or detail.get("mode") in ("fail_no_header", "fail_no_anchor")
                )
            )
            if _uia_missing_table:
                _hint = "当前窗口可能不是广告计划列表页（如 GMV Max 概览页），请切换到广告计划列表页后重试"
                self.log(_hint, level="result")
                # v10.14.7：toast 统一受 toast_enabled 管控（§8.4-4，关闭后一律不弹）
                if self.cfg.get("toast_enabled", False):
                    try:
                        notify_toast("数据看板提醒", _hint)
                    except Exception:
                        pass
            self.log("OCR 原文（完整）:\n" + (text or ""), level="debug")
        elif roi is None or cost is None or revenue is None:
            _retry_tag = "（重试后仍失败）" if retried else ""
            self.log(f"【没有找到广告计划】未解析到 ROI/成本/收入{_retry_tag}，本轮跳过入库，请检查 ROI 区域或手动录入", level="result")
            # v10.14：识别失败也告警——自动监控时随本轮打包推送，提示可能是网络/浏览器问题
            if push:
                self._enqueue_round_alert(name, "未解析到 ROI/成本/收入")
        else:
            # v10.5：逐行入库——识别成功后对 rows 全部 add_record（每行补 shop 字段），
            # 不再只入首行；首行仍用于上方"最近一次识别结果"展示
            rows = self._collect_rows_from_detail(detail)
            if rows:
                for r in rows:
                    r["shop"] = name
                    add_record(r)
                # v10.6.2：逐行打印 result 级日志，每条广告计划一行（plan_name 取不到用 '-'）
                for r in rows:
                    pn = r.get("plan_name") or "-"
                    self.log(f"[{name}] 计划[{pn}] ROI={r['roi']:.2f} 成本=${r['cumulative_cost']:.2f} "
                             f"收入=${r['cumulative_revenue']:.2f} 订单={r['cumulative_orders']}", level="result")
                self.log(f"[{name}] 已自动入库 {len(rows)} 行", level="result")
                # 任务A：逐行告警——提取识别到的所有数据行（业务上限 max_rows_to_check=10），
                # 每行独立比较 ROI 与阈值；多条同时触发由 run_alerts 合并推送（避免刷屏）。
                # v10.14：push 透传——自动监控 True；手动完整轮 False 只记录不推送
                self.run_alerts(rows, shop=name, push=push, force_close=force_close)
            else:
                orders = int(self.cfg.get("current_orders", 0) or 0)
                rec = self.make_record(roi, cost, revenue, orders, source="ocr")
                rec["shop"] = name
                add_record(rec)
                self.log(f"[{name}] 已自动入库: ROI={rec['roi']:.2f} 成本=${rec['cumulative_cost']:.2f} "
                         f"订单={rec['cumulative_orders']} 收入=${rec['cumulative_revenue']:.2f}", level="result")
                self.run_alerts(rec, shop=name, push=push, force_close=force_close)

    # ---------- 历史记录 ----------
    def show_history(self):
        data = load_history()
        records = data["records"]
        win = tk.Toplevel(self.root)
        win.title(f"历史记录（共 {len(records)} 条）")
        win.geometry("760x480")
        txt = tk.Text(win, font=("Consolas", 9))
        txt.pack(fill="both", expand=True, padx=5, pady=5)
        header = f"{'时间':<18}{'ROI':>8}{'消耗$':>10}{'订单':>6}{'收入$':>10}{'客单':>8}{'CVR%':>8}  来源\n"
        txt.insert(tk.END, header + "-" * 80 + "\n")
        for r in reversed(records[-500:]):
            cvr = f"{r['conversion_rate']:.2f}" if r.get("conversion_rate") is not None else "-"
            txt.insert(tk.END, f"{r['time']:<18}{r['roi']:>8.2f}{r['cumulative_cost']:>10.2f}"
                               f"{r['cumulative_orders']:>6}{r['cumulative_revenue']:>10.2f}"
                               f"{r['avg_order_value']:>8.2f}{cvr:>8}  {r.get('source', '')}\n")
        txt.config(state="disabled")

    # ---------- 推送测试 ----------
    def test_wecom(self):
        # 逻辑闭环：先检查启用开关，未启用禁止发送，禁止绕过条件直接执行
        if not bool(self.var_wecom.get()):
            self.log("该通道未启用，请先勾选启用后再测试")
            self.lbl_test_wecom.config(text="✗ 未启用，请先勾选启用", fg="#cc0000")
            self.update_push_status()
            return
        self.cfg["wecom_key"] = self.e_wecom.get().strip()
        self.cfg["wecom_enabled"] = True
        save_config(self.cfg)
        ok, msg = push_wecom(self.cfg, "TikTok 监控测试", "测试消息：推送通道正常")
        self.log(f"[测试-企业微信] {msg}")
        self.lbl_test_wecom.config(text=("✓ " if ok else "✗ ") + msg, fg=("#006600" if ok else "#cc0000"))

    def test_ding(self):
        # 逻辑闭环：先检查启用开关，未启用禁止发送，禁止绕过条件直接执行
        if not bool(self.var_ding.get()):
            self.log("该通道未启用，请先勾选启用后再测试")
            self.lbl_test_ding.config(text="✗ 未启用，请先勾选启用", fg="#cc0000")
            self.update_push_status()
            return
        self.cfg["dingtalk_url"] = self.e_ding.get().strip()
        self.cfg["dingtalk_enabled"] = True
        save_config(self.cfg)
        ok, msg = push_dingtalk(self.cfg, "测试消息", "测试消息：推送通道正常")
        self.log(f"[测试-钉钉] {msg}")
        self.lbl_test_ding.config(text=("✓ " if ok else "✗ ") + msg, fg=("#006600" if ok else "#cc0000"))

    def test_pushplus(self):
        # 逻辑闭环：先检查启用开关，未启用禁止发送，禁止绕过条件直接执行
        if not bool(self.var_pushplus.get()):
            self.log("该通道未启用，请先勾选启用后再测试")
            self.lbl_test_pushplus.config(text="✗ 未启用，请先勾选启用", fg="#cc0000")
            self.update_push_status()
            return
        self.cfg["pushplus_token"] = self.e_pushplus.get().strip()
        self.cfg["pushplus_enabled"] = True
        save_config(self.cfg)
        ok, msg = push_pushplus(self.cfg, "TikTok 监控测试", "测试消息：推送通道正常")
        self.log(f"[测试-PushPlus] {msg}")
        self.lbl_test_pushplus.config(text=("✓ " if ok else "✗ ") + msg, fg=("#006600" if ok else "#cc0000"))


# ==================== Mod 体系骨架 ====================
# 约定：exe 同级 mods/ 目录，每个 mod 一个子文件夹 mods/<mod_id>/，
# 内含 manifest.json（{id, name, version, entry, enabled, description}）与入口 mod.py。
# 核心只负责：扫描 -> 读 manifest -> 导入入口 -> 调用 setup(api)。
# 单个 mod 读取/导入/注册全程 try/except 隔离，出错只记日志，绝不影响核心和其他 mod。

class ModAPI:
    """核心暴露给 mod 的最小接口。

    - register_panel(title, build_fn)  注册一个新板块（build_fn(container) 填充控件，挂载到主面板滚动容器）
    - register_menu(text, callback)    注册一个按钮到主界面（可选）
    - log(msg)                         mod 日志（带 mod id 前缀输出到主日志区）
    - get_config(key=None, default=None) / set_config(key, value)  读写 mod 命名空间配置（config.json 的 mod_<mod_id>）
    """

    def __init__(self, app, mod_id):
        self._app = app
        self._mod_id = mod_id

    def get_core(self):
        """返回核心应用实例（高级 mod 使用）：可调用核心方法 / 读取核心配置。

        注意：返回的 core 持有 tkinter 控件与状态，面板类操作请优先使用本 API
        封装方法；跨线程（如 mod 自建 HTTP 服务线程）访问 tkinter 控件有风险，
        务必按核心现有模式（worker 线程 + root.after 回调）使用。
        """
        return self._app

    def register_panel(self, title, build_fn):
        """mod 注册一个新板块，build_fn(container) 负责在 container 里填充控件"""
        try:
            container = tk.LabelFrame(self._app.main_frame, text=title, padx=8, pady=5)
            build_fn(container)
            container.pack(fill="x", **{"padx": 8, "pady": 3})
            self._app._bind_panel_wheel_recursive(container)
            self.log(f"面板注册成功: {title}")
        except Exception as e:
            self.log(f"面板注册失败: {title} -> {e}")

    def register_menu(self, text, callback):
        """mod 注册一个按钮到主界面（追加到日志区上方的 mod 按钮行，可选功能）"""
        try:
            if self._app._mod_menu_frame is None:
                # v10.7：mod 按钮行挂主滚动区 main_frame 顶部（原 info_container/_layout_host 已移除）
                self._app._mod_menu_frame = tk.Frame(self._app.main_frame)
                self._app._mod_menu_frame.pack(fill="x", padx=8, pady=2)
            tk.Button(self._app._mod_menu_frame, text=text, command=callback).pack(side="left", padx=2)
            self.log(f"按钮注册成功: {text}")
        except Exception as e:
            self.log(f"按钮注册失败: {text} -> {e}")

    def log(self, msg):
        """mod 日志：带 mod id 前缀输出到主日志区"""
        self._app.log(f"[mod:{self._mod_id}] {msg}")

    def get_config(self, key=None, default=None):
        """读取 mod 命名空间配置 mod_<mod_id>；key 为空返回整个命名空间 dict"""
        ns = self._app.cfg.get(f"mod_{self._mod_id}", {})
        if not isinstance(ns, dict):
            ns = {}
        if key is None:
            return dict(ns)
        return ns.get(key, default)

    def set_config(self, key, value):
        """写入 mod 命名空间配置并保存 config.json（mod 自治存储）"""
        ns = dict(self._app.cfg.get(f"mod_{self._mod_id}", {}))
        if not isinstance(ns, dict):
            ns = {}
        ns[key] = value
        self._app.cfg[f"mod_{self._mod_id}"] = ns
        save_config(self._app.cfg)

    def register_recog_mode(self, mode_id, name, recognize_fn, config_keys=None, expand_fn=None,
                            requires_sections=None):
        """注册识别器（架构健康化A：识别器外置 mod 的统一注册入口）。

        - mode_id: 识别器唯一 id（如 "ocr"/"uia"），写入 config.recog_mode 保存，识别时按此分发
        - name: 识别方式下拉显示的友好名称（如 "截图OCR" / "控件树UIA"）
        - recognize_fn(core, target) -> (text, words)：识别主入口。core 为核心实例，
          可调用 core.get_target_window / core.capture_roi / core._capture_full_window /
          core._resolve_hwnd / core.cfg / core.log 等共享能力；target 为当前目标 dict（含 id/roi/window_keyword）。
          返回 (text, words)，words=[{text,x,y,w,h},...] 与核心解析管线 _parse_table_by_words 兼容。
        - config_keys: 该识别器使用的 config 键列表（供识别规则摘要展示等）
        - expand_fn(core, target) -> (text, words)：可选扩框兜底（如 OCR 全窗口再识别一次）
        - requires_sections: v10.5 能力声明——识别器依赖的 UI 板块列表（如 OCR 依赖 ["roi"]），
          核心按声明做条件渲染（_apply_section_capabilities）；UIA 不需要 ROI 板块可传 []。
        """
        return self._app.register_recog_mode(mode_id, name, recognize_fn,
                                             config_keys=config_keys, expand_fn=expand_fn,
                                             requires_sections=requires_sections,
                                             mod_id=self._mod_id)


def load_mods(app):
    """启动时扫描 mods/ 目录并加载启用中的 mod，返回 {"loaded": [...], "failed": [...]}
    v10.7.4：frozen 下优先加载 PyInstaller 内嵌 mods（_MEIPASS/mods，与 exe 同包发布的
    mod 版本），内嵌不存在时回退 exe 同级外部 mods（兼容旧部署包/自定义覆盖）。"""
    results = {"loaded": [], "failed": []}
    if getattr(sys, "frozen", False):
        embedded = Path(getattr(sys, "_MEIPASS", BASE_DIR)) / "mods"
        if embedded.is_dir():
            mods_dir = embedded
            app.log(f"Mod 目录：exe 内嵌（{embedded}）")
        else:
            mods_dir = BASE_DIR / "mods"
            app.log(f"Mod 目录：exe 同级（{mods_dir}）")
    else:
        mods_dir = BASE_DIR / "mods"
    if mods_dir is None or not mods_dir.is_dir():
        app.log("未找到 mods 目录（exe 内嵌/同级），跳过 Mod 加载")
        return results
    # v10.2 任务3：mod 管理——config.disabled_mods 中禁用的 mod 直接跳过（识别方式下拉只显示已启用识别器）
    disabled = set()
    try:
        disabled = set(app.cfg.get("disabled_mods") or []) if getattr(app, "cfg", None) else set()
    except Exception:
        disabled = set()
    for child in sorted(mods_dir.iterdir()):
        if not child.is_dir():
            continue
        mod_id = child.name
        if mod_id in disabled:
            app.log(f"[mod:{mod_id}] 已在 config.disabled_mods 中禁用，跳过")
            continue
        try:
            manifest_path = child / "manifest.json"
            if not manifest_path.is_file():
                app.log(f"[mod:{mod_id}] 缺少 manifest.json，跳过")
                results["failed"].append({"id": mod_id, "reason": "缺少 manifest.json"})
                continue
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
            if not isinstance(manifest, dict):
                raise ValueError("manifest.json 不是 JSON 对象")
            mod_id = str(manifest.get("id") or mod_id)
            name = str(manifest.get("name") or mod_id)
            version = str(manifest.get("version") or "")
            entry = str(manifest.get("entry") or "mod.py")
            enabled = bool(manifest.get("enabled", True))
            if not enabled:
                app.log(f"[mod:{mod_id}] 已在 manifest 中禁用，跳过")
                continue
            entry_path = child / entry
            if not entry_path.is_file():
                raise FileNotFoundError(f"入口文件缺失: {entry}")
            spec = importlib.util.spec_from_file_location(f"tiktok_mod_{mod_id}", entry_path)
            if spec is None or spec.loader is None:
                raise ImportError("无法创建模块加载器")
            module = importlib.util.module_from_spec(spec)
            import sys as _sys
            _sys.modules[f"tiktok_mod_{mod_id}"] = module  # 注册到 sys.modules，支持 mod 内部自引用
            spec.loader.exec_module(module)
            setup = getattr(module, "setup", None)
            if not callable(setup):
                raise AttributeError("入口缺少 setup(api) 函数")
            api = ModAPI(app, mod_id)
            setup(api)
            results["loaded"].append({"id": mod_id, "name": name, "version": version})
            app.log(f"[mod:{mod_id}] 加载成功（{name} v{version}）")
        except Exception as e:
            app.log(f"[mod:{mod_id}] 加载失败: {e}")
            results["failed"].append({"id": mod_id, "reason": str(e)})
    app.log(f"Mod 加载完成：成功 {len(results['loaded'])} 个 / 失败 {len(results['failed'])} 个")
    return results


# ===== v10.8.0 单实例运行 =====
# 优先命名 Mutex 检测（避免仅靠进程名误判）；兼容旧版本实例（v10.7.9 及以前无 Mutex）
# 按主窗口标题兜底检测。检测必须早于创建窗口/注册热键/加载 Mod。
SINGLE_INSTANCE_MUTEX = r"Local\tkm_v10_SingleInstance"
SINGLE_INSTANCE_EVENT = r"Local\tkm_v10_Activate"
_single_instance_mutex_handle = None


def _pre_log(msg):
    """App 实例尚未创建时写 monitor 日志（v10.12 起按天轮转 monitor_YYYYMMDD.log）。
    v10.14.4：统一带版本前缀 [tkm_10.14.5]，msg 内自带模块标签。"""
    _append_day_log("monitor", f"[{datetime.now():%H:%M:%S}][info][tkm_10.26] {msg}\n")


def _find_instance_window():
    """按主窗口标题（含『TikTok 广告监控』）查找已有实例窗口，返回 hwnd（找不到返回 0）。

    兜底检测用：兼容旧版本实例（无 Mutex/事件机制）。withdrawn（隐藏）的窗口句柄仍存在，可命中。
    """
    import ctypes

    def _cb(hwnd, lparam):
        try:
            n = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            ctypes.windll.user32.GetWindowTextW(hwnd, buf, n + 1)
            if ("tkm_10.26" in buf.value) or ("工作台" in buf.value) or ("TikTok 广告监控" in buf.value):
                handles = ctypes.cast(lparam, ctypes.POINTER(ctypes.c_void_p))
                handles.contents.value = hwnd
                return False  # 找到即停止枚举
        except Exception:
            pass
        return True

    found = ctypes.c_void_p(0)
    try:
        ctypes.windll.user32.EnumWindows(
            ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)(_cb),
            ctypes.byref(found))
    except Exception:
        return 0
    return found.value or 0


def _signal_existing_instance(hwnd):
    """激活已有实例：优先 v10.8+ 命名事件（旧实例监听线程收到后呼出主窗口，
    即使隐藏/mini 也会恢复显示置前）；事件不可用时按 hwnd 用 Win32 兜底唤起（兼容旧版本）。

    v10.8.1：SetEvent 后短暂延时再执行一次 Win32 兜底唤起（ShowWindow SW_RESTORE +
    BringWindowToTop + SetForegroundWindow），事件路径与窗口路径双保险，避免事件信号
    因旧实例监听线程短暂未在等待（auto-reset 丢失）而导致唤起完全失效。
    """
    import ctypes
    try:
        kernel32 = ctypes.windll.kernel32
        h_evt = kernel32.OpenEventW(0x001F0003, False, SINGLE_INSTANCE_EVENT)  # EVENT_MODIFY_STATE|SYNCHRONIZE
        if h_evt:
            try:
                kernel32.SetEvent(h_evt)
            finally:
                kernel32.CloseHandle(h_evt)
            # 给旧实例监听线程留出处理时间（manual-reset 事件不丢信号，此处等待仅为兜底窗口路径）
            try:
                time.sleep(0.8)
            except Exception:
                pass
    except Exception:
        pass
    if hwnd:
        try:
            user32 = ctypes.windll.user32
            user32.ShowWindow(hwnd, 9)   # SW_RESTORE：恢复显示（withdrawn/minimized）
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
        except Exception:
            pass


def check_single_instance():
    """单实例检测（main() 最早调用）：已有实例返回 True（本进程应立即退出），否则 False。

    流程：
    1. 命名 Mutex：创建成功且 ERROR_ALREADY_EXISTS → v10.8+ 实例在运行 → 唤出并退出；
    2. Mutex 创建成功（本进程为唯一）→ 持有句柄，并创建"唤出"命名事件供后续新实例触发；
    3. 兜底：仍按主窗口标题找旧版本实例（v10.7.9 及以前）→ 唤出并退出。
    """
    global _single_instance_mutex_handle
    import ctypes
    try:
        kernel32 = ctypes.windll.kernel32
        ERROR_ALREADY_EXISTS = 183
        h_mutex = kernel32.CreateMutexW(None, False, SINGLE_INSTANCE_MUTEX)
        if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
            # v10.8+ 实例已运行：命名事件唤起（同时按标题找 hwnd 作为事件失败时的兜底）
            _pre_log("检测到已有实例，发送唤起")
            hwnd = _find_instance_window()
            _signal_existing_instance(hwnd)
            return True
        _single_instance_mutex_handle = h_mutex  # 持有句柄直至进程退出，保证 Mutex 生效
        _pre_log("单实例检测通过：无其他实例运行，本进程为唯一实例")
        # 创建"唤出"事件（供后续重复打开的新实例 SetEvent 唤起本实例）
        # v10.8.1：bManualReset=True（manual-reset 事件），即使监听线程短暂未在等待，
        # 信号也保持触发态不丢失；监听线程收到后显式 ResetEvent 再处理。
        try:
            kernel32.CreateEventW(None, True, False, SINGLE_INSTANCE_EVENT)
        except Exception:
            pass
        # 兜底：兼容旧版本实例（无 Mutex），按主窗口标题检测
        hwnd = _find_instance_window()
        if hwnd:
            _pre_log("检测到已有实例（旧版本，无 Mutex），发送唤起")
            _signal_existing_instance(hwnd)
            return True
    except Exception:
        pass
    return False


def _detect_legacy_instances():
    """v10.14.4：检测是否有其他版本的 tkm 监控进程在运行（旧版 exe 进程名含 tkm_）。

    从进程文件名解析版本号（tkm_10.14.3.exe → 10.14.3），用于日志/弹窗精确提示版本，
    不再笼统说"旧版本"；返回 [(进程名, 版本号, PID)]，供 main() 启动时提示用户
    先退出旧版实例（防止旧版继续推送/混淆）。排除当前版本自身（tkm_10.14.5）。
    """
    import re
    import subprocess
    results = []
    cur_pid = os.getpid()
    # v10.16.1 修复：排除版本号不再写死，改为动态解析当前 exe 自身版本号
    # （tkm_10.16.1.exe → 10.16；PyInstaller onefile 双进程中 bootloader 父进程
    #   与主程序子进程同名同版本，按"版本==当前版本"统一排除，避免误判自身为旧版弹窗）。
    # 同时兼容非 frozen 源码运行（sys.executable 是 python.exe，无 tkm_ 前缀时回落当前版本）。
    cur_ver = ""
    try:
        m0 = re.search(r"tkm_([\d.]+)", os.path.basename(sys.executable).lower())
        cur_ver = m0.group(1).rstrip('.') if m0 else ""
    except Exception:
        cur_ver = ""
    if not cur_ver:
        cur_ver = "10.21"  # 源码运行兜底：以文件头当前版本为准
    try:
        out = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return results
    for line in out.splitlines():
        try:
            parts = line.strip().strip('"').split('","')
            name = parts[0].strip('"')
            pid = int(parts[1].strip('"'))
        except Exception:
            continue
        low = name.lower()
        # 命中 tkm_ 命名空间进程：tkm_10.14.3.exe 等
        if low.startswith("tkm_") and low.endswith(".exe"):
            # v10.14.4.1 修复：[\d.]+ 贪婪匹配会把 ".exe" 的点吞进去，
            # 解析出 "10.14.4."（带尾点）导致排除自身失败、误判旧版弹窗。
            # 解析后 rstrip('.') 去掉尾点；并额外用当前 PID 排除自身（双保险）。
            m = re.search(r"tkm_([\d.]+)", low)
            version = m.group(1).rstrip('.') if m else ""
            if version == cur_ver or pid == cur_pid:
                continue
            results.append((name, version, pid))
    return results


def main():
    # v10.8.0 单实例：检测早于创建窗口/注册热键/加载 Mod；已有实例则唤起旧实例并立即退出
    if check_single_instance():
        sys.exit(0)
    # v10.14.4：旧版实例检测——若发现旧版 tkm_10.14.x.exe 等进程仍在运行，
    # 提示用户先退出旧版再启动（旧版可能继续推送且与新版混淆；同时排查"多个同名进程"问题）
    try:
        legacy = _detect_legacy_instances()
        if legacy:
            names = "、".join(f"{n} 版本 {v}(PID {p})" for n, v, p in legacy)
            _pre_log(f"[旧版检测] 发现旧版进程 {names}，请先退出旧版再启动新版")
            try:
                import ctypes
                ctypes.windll.user32.MessageBoxW(
                    None,
                    "检测到旧版监控进程仍在运行：\n\n" + names +
                    "\n\n请先在任务管理器中结束旧版进程，再启动新版本，"
                    "否则旧版可能继续发送推送、且任务管理器会出现多个同名进程。",
                    "tkm_10.26 提示", 0x40 | 0x1000)  # MB_ICONINFORMATION | MB_TOPMOST
            except Exception:
                pass
            sys.exit(0)
    except Exception as e:
        _pre_log(f"[旧版检测] 检测异常（不影响启动）: {e}")
    # 低负载优化：进程优先级降到"低于正常"，即使 OCR 吃 CPU，也不抢其它程序资源
    try:
        import ctypes
        ctypes.windll.kernel32.SetPriorityClass(
            ctypes.windll.kernel32.GetCurrentProcess(), 0x00004000)  # BELOW_NORMAL_PRIORITY_CLASS
    except Exception:
        pass
    root = tk.Tk()
    app = MonitorApp(root)
    # v10.16：微云日志自动上传——后台线程检查并上传昨日及更早的 log（不阻塞启动）
    _maybe_upload_logs_async()
    root.mainloop()


if __name__ == "__main__":
    main()
