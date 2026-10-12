# -*- coding: utf-8 -*-
"""
TK 投流网页监控程序（无人值守 + 命中才告警）
==============================================
- 采集层：截图 + Windows 原生 OCR（与真人看屏幕无异，零封店风险）
- 规则层：止损线 4 条（CVR 观察线 / 成本硬线 / 个人红线 / 空烧红灯）
- 通道层：方糖 Server酱 -> 微信；Windows 通知本地兜底

用法：python main.py  （或双击 START.bat）
"""
import datetime
import json
import re
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import mss
import requests
import win32gui
import winocr
from PIL import Image, ImageTk
from winotify import Notification
try:
    import pystray
    from pystray import MenuItem as TrayItem
    HAVE_TRAY = True
except Exception:
    HAVE_TRAY = False

BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "config.json"
DATA_FILE = BASE_DIR / "data.json"
STATE_FILE = BASE_DIR / "state.json"
OCR_DEBUG_DIR = BASE_DIR / "ocr_debug"
REPORT_DIR = BASE_DIR / "reports"

DEFAULT_CONFIG = {
    "sendkey": "",                       # 方糖 Server酱 SendKey，留空则不推微信
    "window_title_keyword": "TikTok",    # 后台窗口标题关键字
    "interval_minutes": 60,              # 采集间隔（分钟），默认 60，最小 5
    "fields": {                          # 各字段识别区域（相对窗口坐标）
        "cost": None,                    #   [left, top, width, height]
        "revenue": None,
        "orders": None,
        "uv": None
    },
    "rules": {
        "unit_price": 15.0,              # 客单价（元，与成本同单位）
        "cost_line_multiplier": 2.0,     # 成本线 = 客单价 x 倍数
        "red_line_usd": 5.0,             # 个人红线：空烧 >= 5 立即停
        "cvr_low": 0.5,                  # CVR 观察线（%）
        "roi_warn_max": 10.0,            # ROI 异常上限
        "roi_warn_min": 0.0,             # ROI 异常下限
        "high_rate_usd_per_min": 0.5     # 空烧红灯：每分钟消耗阈值
    },
    "alert_cooldown_min": 30             # 同规则告警冷却（分钟）
}

FIELDS_CN = {"cost": "成本", "revenue": "收入", "orders": "订单", "uv": "UV"}


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def find_window(keyword):
    """按标题关键字找可见窗口，返回句柄列表"""
    found = []

    def cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and keyword.lower() in win32gui.GetWindowText(hwnd).lower():
            found.append(hwnd)
    win32gui.EnumWindows(cb, None)
    return found


def grab_region(region):
    """region=(left, top, width, height)，返回 PIL Image"""
    with mss.mss() as sct:
        mon = {"left": region[0], "top": region[1], "width": region[2], "height": region[3]}
        img = sct.grab(mon)
        return Image.frombytes("RGB", img.size, img.rgb)


def ocr_image(img):
    rgba = img.convert("RGBA")
    try:
        return winocr.recognize_pil(rgba).text or ""
    except Exception:
        return ""


def parse_number(text, kind):
    """从 OCR 文本提取字段数值：cost/revenue 取金额，orders/uv 取整数"""
    if not text:
        return None
    if kind in ("cost", "revenue"):
        m = re.search(r"\$\s?([\d,]+(?:\.\d{1,2})?)", text)
        if m:
            return float(m.group(1).replace(",", ""))
        m = re.search(r"([\d,]+\.\d{1,2})", text)
        if m:
            return float(m.group(1).replace(",", ""))
        m = re.search(r"([\d,]+)", text)
        if m:
            return float(m.group(1).replace(",", ""))
    else:
        m = re.search(r"([\d,]+)", text)
        if m:
            return int(m.group(1).replace(",", ""))
    return None


class MonitorApp:
    def __init__(self, root):
        self.root = root
        root.title("TK 投流监控 - 无人值守")
        root.geometry("720x600")
        self.cfg = load_json(CONFIG_FILE, DEFAULT_CONFIG)
        self.data = load_json(DATA_FILE, {"records": []})
        self.state = load_json(STATE_FILE, {})
        self.running = False
        self.thread = None
        self.muted = bool(self.state.get("muted", False))
        self.stage = "待机"
        self.last_collect_ts = None
        self.next_run_ts = None
        self._build_ui()
        self._setup_tray()
        root.protocol("WM_DELETE_WINDOW", self.hide_to_tray)
        self.tick()
        self.log("程序启动。若未标定区域，请先点击「标定识别区域」；方糖 SendKey 可后填。")
        self.log("提示：关闭窗口=最小化到托盘，托盘菜单可恢复/退出；告警可一键静音。")

    # ---------- UI ----------
    def _build_ui(self):
        top = ttk.Frame(self.root, padding=8)
        top.pack(fill="x")
        self.btn_start = ttk.Button(top, text="开始监控", command=self.start_monitor)
        self.btn_start.pack(side="left", padx=4)
        self.btn_stop = ttk.Button(top, text="■ 停止监控", command=self.stop_monitor, state="disabled")
        self.btn_stop.pack(side="left", padx=4)
        ttk.Button(top, text="立即采集一次", command=lambda: self.collect_once(from_gui=True)).pack(side="left", padx=4)
        ttk.Button(top, text="手动录入", command=self.manual_input).pack(side="left", padx=4)
        ttk.Button(top, text="标定识别区域", command=self.calibrate).pack(side="left", padx=4)
        ttk.Button(top, text="生成报告", command=self.gen_report).pack(side="left", padx=4)
        ttk.Button(top, text="快速清理", command=self.quick_clean).pack(side="left", padx=4)
        self.btn_mute = ttk.Button(top, text="静音告警" if not self.muted else "恢复告警", command=self.toggle_mute)
        self.btn_mute.pack(side="right", padx=4)

        # 状态机可视化（常驻可见）
        stf = ttk.LabelFrame(self.root, text="运行状态（可感知、可控）", padding=6)
        stf.pack(fill="x", padx=8)
        self.var_stage = tk.StringVar(value="阶段：待机")
        self.var_last = tk.StringVar(value="上次采集：无")
        self.var_countdown = tk.StringVar(value="下次采集：--")
        self.var_records = tk.StringVar(value="累计记录：0")
        self.var_mute = tk.StringVar(value="告警：开启" if not self.muted else "告警：已静音")
        ttk.Label(stf, textvariable=self.var_stage, foreground="blue", font=("微软雅黑", 10, "bold")).grid(row=0, column=0, sticky="w", padx=8)
        ttk.Label(stf, textvariable=self.var_last).grid(row=0, column=1, sticky="w", padx=8)
        ttk.Label(stf, textvariable=self.var_countdown).grid(row=0, column=2, sticky="w", padx=8)
        ttk.Label(stf, textvariable=self.var_records).grid(row=1, column=0, sticky="w", padx=8)
        ttk.Label(stf, textvariable=self.var_mute, foreground="red" if self.muted else "green").grid(row=1, column=1, sticky="w", padx=8)

        cfg = ttk.LabelFrame(self.root, text="配置", padding=8)
        cfg.pack(fill="x", padx=8)
        ttk.Label(cfg, text="方糖 SendKey:").grid(row=0, column=0, sticky="e")
        self.var_sendkey = tk.StringVar(value=self.cfg.get("sendkey", ""))
        ttk.Entry(cfg, textvariable=self.var_sendkey, width=40).grid(row=0, column=1, sticky="w", padx=4)
        ttk.Label(cfg, text="窗口关键字:").grid(row=0, column=2, sticky="e")
        self.var_win = tk.StringVar(value=self.cfg.get("window_title_keyword", "TikTok"))
        ttk.Entry(cfg, textvariable=self.var_win, width=16).grid(row=0, column=3, sticky="w", padx=4)
        ttk.Label(cfg, text="间隔(分钟,>=5):").grid(row=1, column=0, sticky="e")
        self.var_interval = tk.StringVar(value=str(self.cfg.get("interval_minutes", 60)))
        ttk.Entry(cfg, textvariable=self.var_interval, width=8).grid(row=1, column=1, sticky="w", padx=4)
        ttk.Label(cfg, text="客单价(与成本同单位):").grid(row=1, column=2, sticky="e")
        self.var_price = tk.StringVar(value=str(self.cfg["rules"].get("unit_price", 15)))
        ttk.Entry(cfg, textvariable=self.var_price, width=8).grid(row=1, column=3, sticky="w", padx=4)
        ttk.Button(cfg, text="保存配置", command=self.save_cfg).grid(row=1, column=4, padx=8)

        logf = ttk.LabelFrame(self.root, text="日志", padding=4)
        logf.pack(fill="both", expand=True, padx=8, pady=8)
        self.log_text = tk.Text(logf, height=14)
        self.log_text.pack(fill="both", expand=True)

    def log(self, msg):
        line = "[%s] %s" % (datetime.datetime.now().strftime("%H:%M:%S"), msg)
        self.log_text.insert("end", line + "\n")
        self.log_text.see("end")

    def save_cfg(self):
        self.cfg["sendkey"] = self.var_sendkey.get().strip()
        self.cfg["window_title_keyword"] = self.var_win.get().strip() or "TikTok"
        try:
            iv = int(float(self.var_interval.get()))
            iv = max(self.cfg.get("min_interval_minutes", 5), iv)
            self.cfg["interval_minutes"] = iv
        except Exception:
            self.log("间隔格式错误，保持原值")
        try:
            self.cfg["rules"]["unit_price"] = float(self.var_price.get())
        except Exception:
            self.log("客单价格式错误，保持原值")
        save_json(CONFIG_FILE, self.cfg)
        self.log("配置已保存")

    # ---------- 可见性 & 可控性（§4.3） ----------
    def set_stage(self, stage):
        self.stage = stage
        self.var_stage.set("阶段：" + stage)

    def tick(self):
        """每秒刷新：状态机、上次采集、倒计时、累计记录数"""
        self.var_records.set("累计记录：%d" % len(self.data.get("records", [])))
        if self.last_collect_ts:
            self.var_last.set("上次采集：" + self.last_collect_ts)
        if self.running and self.next_run_ts:
            remain = max(0, self.next_run_ts - time.time())
            m, s = int(remain // 60), int(remain % 60)
            self.var_countdown.set("下次采集：%02d:%02d" % (m, s))
        else:
            self.var_countdown.set("下次采集：--")
        self.root.after(1000, self.tick)

    def toggle_mute(self):
        self.muted = not self.muted
        self.state["muted"] = self.muted
        save_json(STATE_FILE, self.state)
        self.btn_mute.config(text="恢复告警" if self.muted else "静音告警")
        self.var_mute.set("告警：已静音" if self.muted else "告警：开启")
        self.log("告警已静音，推送将暂停" if self.muted else "告警已恢复")
        if self.tray_icon:
            try:
                self.tray_icon.menu = self._tray_menu()
            except Exception:
                pass

    def hide_to_tray(self):
        """关闭窗口 = 最小化到托盘（程序仍在跑，托盘常驻可找回）"""
        self.log("窗口已最小化到托盘，双击托盘图标可恢复。")
        self.root.withdraw()
        if HAVE_TRAY and self.tray_icon:
            try:
                self.tray_icon.visible = True
            except Exception:
                pass

    def show_window(self, icon=None, item=None):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def quit_app(self, icon=None, item=None):
        """干净退出：停线程、存状态、销毁窗口、无残留"""
        self.running = False
        if self.tray_icon:
            try:
                self.tray_icon.stop()
            except Exception:
                pass
        self.root.destroy()

    def _tray_menu(self):
        mute_label = "恢复告警" if self.muted else "静音告警"
        items = [
            TrayItem("显示主窗口", self.show_window, default=True),
            TrayItem("停止监控" if self.running else "监控已停止", self._tray_toggle_run),
            TrayItem(mute_label, lambda i, it: self.toggle_mute()),
            TrayItem("退出程序", self.quit_app),
        ]
        return pystray.Menu(*items)

    def _tray_toggle_run(self, icon=None, item=None):
        if self.running:
            self.stop_monitor()
        else:
            self.start_monitor()

    def _setup_tray(self):
        self.tray_icon = None
        if not HAVE_TRAY:
            self.log("未安装 pystray，托盘不可用（pip install pystray）；窗口常驻不受影响。")
            return
        try:
            img = Image.new("RGB", (64, 64), "#1a73e8")
            from PIL import ImageDraw
            d = ImageDraw.Draw(img)
            d.ellipse((8, 8, 56, 56), fill="#ffffff")
            d.text((20, 16), "TK", fill="#1a73e8")
            self.tray_icon = pystray.Icon("tk_monitor", img, "TK 投流监控", menu=self._tray_menu())
            threading.Thread(target=self.tray_icon.run, daemon=True).start()
            self.log("托盘图标已启动。")
        except Exception as e:
            self.tray_icon = None
            self.log("托盘启动失败：" + str(e))

    # ---------- 采集 ----------
    def start_monitor(self):
        if self.running:
            return
        self.save_cfg()
        if not self.cfg.get("sendkey"):
            self.log("警告：SendKey 为空，告警将只走 Windows 通知，不发微信。")
        if not any(self.cfg.get("fields", {}).values()):
            if not messagebox.askyesno("未标定区域", "识别区域尚未标定，自动采集会失败。是否现在标定？"):
                self.log("未标定区域，启动取消。")
                return
            self.calibrate()
            if not any(self.cfg.get("fields", {}).values()):
                self.log("仍无识别区域，启动取消。")
                return
        self.running = True
        self.next_run_ts = time.time() + 2
        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="normal")
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()
        self.set_stage("采集中")
        self.log("监控已启动，间隔 %d 分钟" % self.cfg["interval_minutes"])

    def stop_monitor(self):
        self.running = False
        self.next_run_ts = None
        self.btn_start.config(state="normal")
        self.btn_stop.config(state="disabled")
        self.set_stage("已停止")
        self.log("监控已停止")

    def _loop(self):
        while self.running:
            self.collect_once(from_gui=False)
            iv = max(5, int(self.cfg.get("interval_minutes", 60)))
            self.next_run_ts = time.time() + iv * 60
            self.set_stage("等待中")
            for _ in range(iv * 60 // 2):
                if not self.running:
                    return
                time.sleep(2)

    def collect_once(self, from_gui=True):
        """采集一次：定位窗口 -> 逐字段截图 OCR -> 计算入库 -> 告警"""
        self.set_stage("采集中")
        try:
            keyword = self.cfg.get("window_title_keyword", "TikTok")
            hwnds = find_window(keyword)
            if not hwnds:
                msg = "找不到标题含「%s」的窗口，请确认后台已打开且窗口标题匹配。" % keyword
                self._alert("找不到窗口", msg, modal=True)
                return
            hwnd = hwnds[0]
            if win32gui.IsIconic(hwnd):
                self._alert("窗口最小化", "后台窗口处于最小化状态，请还原后再采集。", modal=True)
                return
            rect = win32gui.GetWindowRect(hwnd)  # (left, top, right, bottom)
            win_left, win_top = rect[0], rect[1]

            fields = self.cfg.get("fields", {})
            values = {}
            texts = {}
            for fname, region in fields.items():
                if not region:
                    continue
                abs_region = (win_left + region[0], win_top + region[1], region[2], region[3])
                img = grab_region(abs_region)
                OCR_DEBUG_DIR.mkdir(exist_ok=True)
                img.save(OCR_DEBUG_DIR / ("%s_%s.png" % (fname, datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))))
                text = ocr_image(img)
                texts[fname] = text
                values[fname] = parse_number(text, fname)

            # 校验：至少 cost+revenue 或 cost+orders 之一
            ok_fields = [f for f in ("cost", "orders", "revenue") if values.get(f) is not None]
            if "cost" not in ok_fields or len(ok_fields) < 2:
                self.log("OCR 识别不足：cost=%s orders=%s revenue=%s uv=%s" % (
                    values.get("cost"), values.get("orders"), values.get("revenue"), values.get("uv")))
                self._alert("OCR 识别不足", "本次识别不到足够的数字（原始文本见日志）。\n"
                            "请检查区域标定，或点击「手动录入」兜底。", modal=from_gui)
                return

            rec = self._build_record(values, texts)
            if rec is None:
                return  # ROI 异常走手动确认
            self.set_stage("比对中")
            self._store(rec)
            self._evaluate(rec)
        except Exception as e:
            self.log("采集异常：" + str(e))
            self._alert("采集异常", str(e), modal=False)

    def _build_record(self, values, texts):
        cost = values.get("cost")
        revenue = values.get("revenue")
        orders = values.get("orders")
        uv = values.get("uv")
        unit_price = float(self.cfg["rules"].get("unit_price", 15))
        rec = {"ts": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
               "cost": cost, "revenue": revenue, "orders": orders, "uv": uv,
               "roi": None, "cvr": None, "unit_price": unit_price, "rate": None}

        if cost and revenue:
            rec["roi"] = round(revenue / cost, 3)
        elif cost and orders:
            rec["roi"] = round((orders * unit_price) / cost, 3)
        if uv and orders:
            rec["cvr"] = round(orders * 100.0 / uv, 2)
        elif revenue and cost and unit_price:
            est_orders = revenue / unit_price if unit_price else 0
            rec["cvr"] = None

        # ROI 异常校验：>10 或 <0 不入库，要求手动确认
        if rec["roi"] is not None:
            mx = float(self.cfg["rules"].get("roi_warn_max", 10))
            mn = float(self.cfg["rules"].get("roi_warn_min", 0))
            if rec["roi"] > mx or rec["roi"] < mn:
                self.log("ROI 异常（%.2f）不自动入库，转手动确认" % rec["roi"])
                if messagebox.askyesno("ROI 异常确认",
                                       "识别 ROI=%.2f 超出正常范围(%.1f~%.1f)。\n"
                                       "点击「是」强制入库，点击「否」打开手动录入。" % (rec["roi"], mn, mx)):
                    self._store(rec)
                else:
                    self.manual_input()
                return None
        # 消耗速率（相对上一条记录）
        prev = self.data["records"][-1] if self.data["records"] else None
        if prev and prev.get("cost") is not None and cost is not None:
            dt = self._ts_delta(prev["ts"], rec["ts"])
            if dt > 0:
                rec["rate"] = round((cost - prev["cost"]) / dt, 3)
        return rec

    @staticmethod
    def _ts_delta(ts1, ts2):
        try:
            d1 = datetime.datetime.strptime(ts1, "%Y-%m-%d %H:%M:%S")
            d2 = datetime.datetime.strptime(ts2, "%Y-%m-%d %H:%M:%S")
            return (d2 - d1).total_seconds() / 60.0
        except Exception:
            return 0

    def _store(self, rec):
        self.data["records"].append(rec)
        save_json(DATA_FILE, self.data)
        self.last_collect_ts = rec["ts"]
        self.log("已入库: 成本=%s 收入=%s 订单=%s UV=%s ROI=%s CVR=%s%% 速率=%s/分" % (
            rec.get("cost"), rec.get("revenue"), rec.get("orders"), rec.get("uv"),
            rec.get("roi"), rec.get("cvr"), rec.get("rate")))
        self.var_last.set("上次采集：" + rec["ts"])
        self.var_records.set("累计记录：%d" % len(self.data.get("records", [])))

    # ---------- 告警（止损线 4 条） ----------
    def _evaluate(self, rec):
        rules = self.cfg["rules"]
        now = datetime.datetime.now()
        alerts = []

        cost = rec.get("cost") or 0
        orders = rec.get("orders") or 0
        revenue = rec.get("revenue") or 0
        cvr = rec.get("cvr")
        rate = rec.get("rate")
        roi = rec.get("roi")
        unit_price = float(rules.get("unit_price", 15))

        # 1. 转化率线：CVR < 0.5，第一天观察不推，第二天仍低才推
        if cvr is not None and cvr < float(rules.get("cvr_low", 0.5)):
            today = now.date().isoformat()
            watch_date = self.state.get("cvr_watch_date")
            if watch_date != today:
                if watch_date is None:
                    self.state["cvr_watch_date"] = today
                    save_json(STATE_FILE, self.state)
                    self.log("CVR=%.2f%% < 0.5%%，第1天观察中，不推送" % cvr)
                else:
                    self.state["cvr_watch_date"] = today
                    save_json(STATE_FILE, self.state)
                    alerts.append(("CVR观察线", "CVR=%.2f%% 连续第2天低于0.5%%，建议换品/换视频" % cvr))

        # 2. 成本线：成本 > 客单价 x 2
        if cost > unit_price * float(rules.get("cost_line_multiplier", 2.0)):
            alerts.append(("成本止损线", "广告成本 $%.2f 超过客单价$%.2f 的%d倍($%.2f)，立即止损；"
                          "消耗过多建议关闭并重建计划" % (cost, unit_price, int(rules.get("cost_line_multiplier", 2)),
                                                     unit_price * float(rules.get("cost_line_multiplier", 2)))))

        # 3. 个人红线：空烧 >= 5 美元（有消耗且无订单）
        if cost >= float(rules.get("red_line_usd", 5.0)) and orders == 0 and revenue == 0:
            alerts.append(("个人红线", "空烧 $%.2f 无产出，立即停投！" % cost))

        # 4. 空烧红灯：速率异常高 + 无转化/无订单
        if rate is not None and rate > float(rules.get("high_rate_usd_per_min", 0.5)) and orders == 0 and revenue == 0:
            alerts.append(("空烧红灯", "消耗速率 %.2f$/分 异常高且无订单，疑似空烧，立即检查！" % rate))

        if alerts:
            self.set_stage("告警中")
            for rule, msg in alerts:
                self._push(rule, msg)
                self.log("告警[%s]: %s" % (rule, msg))
        else:
            self.set_stage("比对中")
            self.log("本次无告警命中")

    def _push(self, title, msg):
        if self.muted:
            self.log("告警[%s] 已静音，跳过推送：%s" % (title, msg))
            return
        # 冷却去重
        cool = float(self.cfg.get("alert_cooldown_min", 30)) * 60
        last = self.state.get("last_alert_ts", {}).get(title, 0)
        if time.time() - last < cool:
            self.log("告警[%s] 在冷却期内，跳过推送" % title)
            return
        self.state.setdefault("last_alert_ts", {})[title] = time.time()
        save_json(STATE_FILE, self.state)
        # Windows 通知（兜底）
        try:
            Notification(app_id="TK投流监控", title=title, msg=msg).show()
        except Exception:
            pass
        # 方糖 -> 微信
        sendkey = self.cfg.get("sendkey", "").strip()
        if sendkey:
            try:
                requests.get("https://sctapi.ftqq.com/%s.send" % sendkey,
                             params={"title": "[TK监控] " + title, "desp": msg}, timeout=10)
            except Exception as e:
                self.log("方糖推送失败：" + str(e))

    def _alert(self, title, msg, modal=False):
        self.log("提示[%s]: %s" % (title, msg))
        if modal:
            messagebox.showwarning(title, msg)
        else:
            try:
                Notification(app_id="TK投流监控", title=title, msg=msg).show()
            except Exception:
                pass

    # ---------- 手动录入兜底 ----------
    def manual_input(self):
        win = tk.Toplevel(self.root)
        win.title("手动录入")
        win.geometry("360x260")
        vals = {}
        for i, fname in enumerate(("cost", "revenue", "orders", "uv")):
            ttk.Label(win, text=FIELDS_CN[fname] + ":").grid(row=i, column=0, sticky="e", padx=8, pady=6)
            v = tk.StringVar()
            ttk.Entry(win, textvariable=v, width=16).grid(row=i, column=1, sticky="w")
            vals[fname] = v
        ttk.Label(win, text="留空表示未知").grid(row=4, column=0, columnspan=2)

        def submit():
            values = {}
            for fname, v in vals.items():
                s = v.get().strip().replace(",", "")
                try:
                    values[fname] = float(s) if s else None
                except Exception:
                    values[fname] = None
            rec = self._build_record(values, {})
            if rec is not None:
                self._store(rec)
                self._evaluate(rec)
            win.destroy()

        ttk.Button(win, text="入库并检查", command=submit).grid(row=5, column=0, columnspan=2, pady=10)

    # ---------- 区域标定 ----------
    def calibrate(self):
        hwnds = find_window(self.cfg.get("window_title_keyword", "TikTok"))
        if not hwnds:
            messagebox.showwarning("标定失败", "找不到后台窗口，请先打开后台并确认窗口标题含「%s」" % self.cfg.get("window_title_keyword"))
            return
        if win32gui.IsIconic(hwnds[0]):
            messagebox.showwarning("标定失败", "后台窗口处于最小化，请还原后再标定")
            return
        rect = win32gui.GetWindowRect(hwnds[0])
        win_left, win_top = rect[0], rect[1]

        win = tk.Toplevel(self.root)
        win.title("标定识别区域 - 拖拽框选后点保存")
        win.geometry("900x700")

        # 截取窗口区域（含边框，够用）
        w = rect[2] - rect[0]
        h = rect[3] - rect[1]
        img = grab_region((rect[0], rect[1], max(w, 50), max(h, 50)))
        scale = min(1.0, 860.0 / max(w, 1), 640.0 / max(h, 1))
        disp = img.resize((int(w * scale), int(h * scale)))
        self._tk_img = ImageTk.PhotoImage(disp)

        frm = ttk.Frame(win)
        frm.pack(fill="x", padx=8, pady=4)
        ttk.Label(frm, text="字段:").pack(side="left")
        field_var = tk.StringVar(value="cost")
        ttk.Combobox(frm, textvariable=field_var, values=list(FIELDS_CN.values()), width=6, state="readonly").pack(side="left", padx=4)
        canvas = tk.Canvas(win, width=int(w * scale), height=int(h * scale), bg="gray")
        canvas.pack()
        canvas.create_image(0, 0, anchor="nw", image=self._tk_img)
        self._sel = {"start": None, "rect": None}
        canvas.bind("<ButtonPress-1>", lambda e: self._sel_update("press", e))
        canvas.bind("<B1-Motion>", lambda e: self._sel_update("move", e))
        canvas.bind("<ButtonRelease-1>", lambda e: self._sel_update("release", e))

        def save():
            if not self._sel.get("end"):
                messagebox.showwarning("未框选", "请先拖拽框选一个区域")
                return
            x1, y1 = self._sel["start"]
            x2, y2 = self._sel["end"]
            l = int(min(x1, x2) / scale)
            t = int(min(y1, y2) / scale)
            r = int(max(x1, x2) / scale)
            b = int(max(y1, y2) / scale)
            region = [l, t, max(r - l, 10), max(b - t, 10)]
            fname_map = {v: k for k, v in FIELDS_CN.items()}
            fname = fname_map.get(field_var.get(), "cost")
            self.cfg.setdefault("fields", {})[fname] = region
            save_json(CONFIG_FILE, self.cfg)
            self.log("已保存 %s 识别区域(相对窗口): %s" % (FIELDS_CN[fname], region))
            if messagebox.askyesno("继续", "%s 区域已保存。继续标定下一个字段？" % FIELDS_CN[fname]):
                return
            win.destroy()

        ttk.Button(win, text="保存该区域", command=save).pack(pady=6)

    def _sel_update(self, kind, event):
        c = event.widget
        if kind == "press":
            self._sel = {"start": (event.x, event.y), "end": (event.x, event.y), "rect": None, "canvas": c}
        else:
            self._sel["end"] = (event.x, event.y)
            if self._sel.get("rect"):
                c.delete(self._sel["rect"])
            x1, y1 = self._sel["start"]
            x2, y2 = event.x, event.y
            self._sel["rect"] = c.create_rectangle(x1, y1, x2, y2, outline="red", width=2)

    # ---------- 报告 ----------
    def gen_report(self):
        records = self.data.get("records", [])
        if not records:
            messagebox.showinfo("无数据", "还没有数据，先采集或手动录入")
            return
        now = datetime.datetime.now()
        since = now - datetime.timedelta(hours=24)
        recs24 = [r for r in records if datetime.datetime.strptime(r["ts"], "%Y-%m-%d %H:%M:%S") >= since]
        REPORT_DIR.mkdir(exist_ok=True)
        ts = now.strftime("%Y%m%d_%H%M%S")

        # 文本报告
        txt = ["TK 投流监控报告 %s" % now.strftime("%Y-%m-%d %H:%M:%S"), "=" * 40, ""]
        if recs24:
            last = recs24[-1]
            txt.append("最近一次采集: %s" % last.get("ts"))
            txt.append("成本: %s | 收入: %s | 订单: %s | UV: %s" % (
                last.get("cost"), last.get("revenue"), last.get("orders"), last.get("uv")))
            txt.append("ROI: %s | CVR: %s%% | 消耗速率: %s/分" % (
                last.get("roi"), last.get("cvr"), last.get("rate")))
            total_cost = sum(r.get("cost") or 0 for r in recs24)
            total_rev = sum(r.get("revenue") or 0 for r in recs24)
            total_orders = sum(r.get("orders") or 0 for r in recs24)
            txt.append("")
            txt.append("24h 累计: 成本 $%.2f | 收入 $%.2f | 订单 %d" % (total_cost, total_rev, total_orders))
            txt.append("24h ROI: %s" % (round(total_rev / total_cost, 3) if total_cost else "N/A"))
        else:
            txt.append("24h 内无记录")
        txt_path = REPORT_DIR / ("report_%s.txt" % ts)
        txt_path.write_text("\n".join(txt), encoding="utf-8")

        # 四宫格图
        if recs24:
            xs = [datetime.datetime.strptime(r["ts"], "%Y-%m-%d %H:%M:%S") for r in recs24]
            fig, axs = plt.subplots(2, 2, figsize=(12, 8))
            axs[0, 0].plot(xs, [r.get("roi") or 0 for r in recs24], marker="o")
            axs[0, 0].set_title("ROI")
            axs[0, 1].plot(xs, [r.get("cost") or 0 for r in recs24], marker="o", color="r")
            axs[0, 1].set_title("Cost")
            axs[1, 0].plot(xs, [r.get("orders") or 0 for r in recs24], marker="o", color="g")
            axs[1, 0].set_title("Orders")
            axs[1, 1].plot(xs, [r.get("revenue") or 0 for r in recs24], marker="o", color="orange")
            axs[1, 1].set_title("Revenue")
            for ax in axs.flat:
                ax.tick_params(axis="x", rotation=30, labelsize=8)
            fig.tight_layout()
            png_path = REPORT_DIR / ("chart_%s.png" % ts)
            fig.savefig(png_path)
            plt.close(fig)
            self.log("报告已生成: %s / %s" % (txt_path, png_path))
            messagebox.showinfo("报告完成", "文本报告和曲线图已生成到 reports 目录")
        else:
            self.log("24h 无数据，仅生成文本报告")

    # ---------- 清理 ----------
    def quick_clean(self):
        if not messagebox.askyesno("快速清理", "将删除截图、数据、配置和报告（程序本身保留）。确认？"):
            return
        import shutil
        for d in (OCR_DEBUG_DIR, REPORT_DIR):
            if d.exists():
                shutil.rmtree(d, ignore_errors=True)
        for f in (DATA_FILE, CONFIG_FILE, STATE_FILE):
            try:
                f.unlink()
            except Exception:
                pass
        self.cfg = dict(DEFAULT_CONFIG)
        self.data = {"records": []}
        self.state = {}
        self.log("快速清理完成：截图/数据/配置/报告已删除")
        messagebox.showinfo("完成", "快速清理完成")


def main():
    root = tk.Tk()
    MonitorApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
