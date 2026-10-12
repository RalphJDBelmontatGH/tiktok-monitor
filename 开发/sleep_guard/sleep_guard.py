# -*- coding: utf-8 -*-
"""
sleep_guard.py —— TK监控器「睡觉驻守」独立程序（v0.3）

场景：老板睡前担心半夜广告空烧，挂一个无人值守小卫兵：
  只要主程序 monitor 日志出现【新写入行】（主程序每轮识别完成后写日志），
  驻守就重新扫描当前页面全部投放中计划，按睡觉规则自动暂停命中计划。

v0.2 双触发（老板拍板：tkm 识别间隔已调 5 分钟，驻守兜底间隔 7 分钟）：
  触发A【log 增量即时触发】主程序 monitor 日志每出现一批新行（=完成一轮识别），
          等待日志静默稳定后立即扫描一次 —— 通常对齐 tkm 5 分钟轮次；
  触发B【定时强制兜底】距上次扫描满 7 分钟（默认 --scan-interval-min 7）即使
          log 没有新行也强制全店扫描一轮 —— 防 tkm 挂掉/日志不写导致的整夜漏检。
  两者任一命中即执行；同一轮内不会重复扫描（7 分钟内 log 增量触发了就顺延兜底计时）。

睡觉规则（本程序内置，v0.1 老板口头口径，非正式两层规则）：
  1) 有订单（订单数 >= 1）且 ROI < 0.6          → 暂停
  2) 无订单（订单数 == 0）且 消耗 > $5.00        → 暂停
  其余一律不动；指标解析失败的行不处理（宁缺毋滥，防误关）。

安全设计（对齐红线）：
  * 自动点击仅无人值守：程序检测到命中候选后，先等用户键鼠空闲满
    unattended_sec（默认 600 秒 = 10 分钟，睡前启动→人离开后才动手）；
    执行期间每次点击前还有 test_auto_close 自带的 2s 空闲二道闸。
  * 工位有人绝不点：轮询期间一旦发现用户键鼠在活动，本轮挂起等待，不点。
  * 点前重收树 + 计划名校验 + 开关状态校验（灰钮=已暂停则跳过，天然幂等）。
  * 弹窗确认自动点（无人值守，没人来点【确认】）；弹窗双按钮校验不过不点。

v0.3 二次复核（防识别时页面刷新误判）：
  首扫命中候选【不直接点】：先等 confirm_delay 秒（默认5，页面刷新落定期），
  重新收树按计划名定位 → 重新解析指标 → 规则二次判定 + 开关仍白钮，
  全部通过才交给 dumb_close_one 走关闭链路；二次不命中/解析失败/找不到计划
  一律放弃（页面数据已变化，防把已回升的计划误关）。配合 dumb_close_one 点前
  重收树+计划名校验，形成：首扫 → 二次复核 → 点前校验 三道防线。

触发机制（v0.2 双触发，老板拍板：tkm 识别间隔 5 分钟 + 驻守兜底 7 分钟）：
  触发A【log 增量即时触发】TailReader 增量检测 monitor 日志新行（UTF-8），
    主程序每轮识别完成后写日志，通常对齐 tkm 5 分钟轮次（config interval_minutes）；
    看到新行后等 stabilize 秒静默（防一轮没写完），再扫描对应店铺。
  触发B【定时强制兜底】距上次实际扫描满 scan_interval_min（默认 7）分钟，
    即使日志没有新行也强制全店扫描一轮 —— 防 tkm 挂掉/日志不写导致的整夜漏检。
  * 默认监控 exe 同级目录下 monitor_YYYYMMDD.log（今天）；今天文件不存在则自动
    跟随目录内最新修改的 monitor_*.log；跨天自动切换新文件。
  * 也可用 --log-file 显式指定。

用法：
  双击运行 / python sleep_guard.py                    # 默认无人值守驻守
  python sleep_guard.py --dry-run                     # 只扫描报告候选，不点（先验证规则用）
  python sleep_guard.py --log-file D:\\.A\\10.19\\monitor_20260906.log
  python sleep_guard.py --keyword A419 --keyword A244  # 指定店铺窗口（默认从日志/自动探测）
  python sleep_guard.py --unattended-sec 120          # 改无人值守门槛（测试用短些）
  python sleep_guard.py --scan-interval-min 3         # 改定时兜底间隔（默认7分钟）

退出：关闭控制台窗口 / Ctrl+C。
"""
import argparse
import ctypes
import os
import re
import sys
import time
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
# 源码运行时依赖位于父目录下（开发\test_auto_close、开发\test_refresh_filter）；
# 打包版由 spec 的 hiddenimports/pathex 收集，本段仅源码调试兜底。
_PARENT = os.path.dirname(BASE_DIR)
for _dep in ("test_auto_close", "test_refresh_filter"):
    _p = os.path.join(_PARENT, _dep)
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors="replace")
    except Exception:
        pass

if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    APP_DIR = BASE_DIR

# 关闭链路复用 test_auto_close 已验证的完整函数（不执行其 main）
import test_auto_close as tac  # noqa: E402

# ---------- 内置参数 ----------
BUILTIN_CFG = {
    "roi_close": 0.6,        # 有单且 ROI < 此值 → 暂停
    "cost_zero_order": 5.0,  # 无单且消耗 > 此值 → 暂停（美元）
}

LOG_PATH = os.path.join(APP_DIR, "sleep_guard_%s.log" % datetime.now().strftime("%Y%m%d"))


def log(msg):
    line = "[%s] %s" % (datetime.now().strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def idle_seconds():
    """用户键鼠空闲秒数（GetLastInputInfo）。"""
    try:
        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]
        lii = LASTINPUTINFO()
        lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
            return (ctypes.windll.kernel32.GetTickCount() - lii.dwTime) / 1000.0
    except Exception:
        pass
    return 1e9  # 探测失败视为已空闲（保守方向：无人值守判定通过，点击前还有二道闸）


def monitor_log_candidates():
    """返回 exe 同级目录下 monitor_*.log 列表，按 mtime 降序。"""
    try:
        files = [os.path.join(APP_DIR, f) for f in os.listdir(APP_DIR)
                 if re.match(r"^monitor_\d{8}\.log$", f)]
        files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        return files
    except Exception:
        return []


def resolve_monitor_log(explicit=None):
    """确定监控的日志文件：--log-file > 今天文件 > 最新 monitor_*.log。"""
    if explicit:
        return explicit if os.path.isfile(explicit) else None
    today = os.path.join(APP_DIR, "monitor_%s.log" % datetime.now().strftime("%Y%m%d"))
    if os.path.isfile(today):
        return today
    cands = monitor_log_candidates()
    return cands[0] if cands else None


SHOP_RE = re.compile(r"\[(?:结果|告警)\] \[([A-Za-z0-9]+)\]")


def extract_shops(lines):
    """从日志新增行中提取店铺标记（[A244] 等）。无标记返回空集合。"""
    shops = []
    for ln in lines:
        m = SHOP_RE.search(ln)
        if m and m.group(1) not in shops:
            shops.append(m.group(1))
    return shops


def rule_should_close(m):
    """睡觉规则判定：m = parse_plan_metrics 结果（roi/cost/orders）。"""
    if not m:
        return False
    roi, cost, orders = m["roi"], m["cost"], m["orders"]
    if orders >= 1 and roi < BUILTIN_CFG["roi_close"]:
        return True
    if orders == 0 and cost > BUILTIN_CFG["cost_zero_order"]:
        return True
    return False


class TailReader:
    """UTF-8 增量行读取器：维护文件 offset，只吐新增完整行。"""

    def __init__(self, path):
        self.path = path
        self.offset = os.path.getsize(path) if os.path.isfile(path) else 0
        self.buf = ""

    def poll(self):
        """返回新增完整行列表；无则 []。文件轮转/重建自动归零。"""
        if not os.path.isfile(self.path):
            return []
        size = os.path.getsize(self.path)
        if size < self.offset:
            # 文件被轮转/重写：从头开始（新文件内容较少，安全）
            self.offset = 0
            self.buf = ""
        if size == self.offset:
            return []
        try:
            with open(self.path, "rb") as f:
                f.seek(self.offset)
                data = f.read(size - self.offset)
            self.offset = size
        except Exception:
            return []
        self.buf += data.decode("utf-8", errors="replace")
        if "\n" not in self.buf:
            return []
        parts = self.buf.split("\n")
        complete, self.buf = parts[:-1], parts[-1]
        return [ln.rstrip("\r") for ln in complete if ln.strip()]


def locate_shop_window(shop):
    """定位店铺窗口。返回 (hwnd, title) 或 (None, "")。"""
    kw = shop or ""
    if kw:
        hwnd, title, hit = tac.trf.find_window_by_keywords_multi([kw])
        if hwnd:
            return hwnd, title
    # 自动探测 Axxx 兜底
    autos = tac.trf.auto_detect_shop_keywords()
    if autos:
        for a in autos:
            hwnd, title, hit = tac.trf.find_window_by_keywords_multi([a])
            if hwnd:
                return hwnd, title
    return None, ""


def process_shops(shops, args):
    """对每个店铺：收树 → 按睡觉规则扫候选 → 逐条走安全关闭链路。"""
    log("=" * 66)
    log("[触发] 开始扫描 %d 个店铺: %s"
        % (len(shops), ", ".join(s if s else "(自动探测)" for s in shops)))
    import uiautomation as auto
    ns = argparse.Namespace(render=False, no_wait_idle=args.no_wait_idle)
    total_cand = total_ok = total_skip = 0

    for shop in shops or [None]:
        hwnd, title = locate_shop_window(shop)
        if not hwnd:
            log("  店铺 %s：未找到对应窗口，跳过（稍后主程序下轮增量会再触发）" % (shop or "默认"))
            continue
        log("  店铺 %s：窗口=0x%X %s，开始收树..." % (shop or "?", hwnd & 0xFFFFFFFFFFFFFFFF, title[:60]))
        try:
            infos = tac.collect_all(auto, hwnd, with_render=False)
            rows, _header = tac.scan_rows(infos)
        except Exception as e:
            log("  店铺 %s：收树/扫描失败: %s" % (shop or "?", e))
            continue
        log("  店铺 %s：数据行 %d 个" % (shop or "?", len(rows)))
        cands = []
        for r in rows:
            try:
                sw = tac.find_row_switch(infos, r["rect"])
                checked = sw is not None and tac.switch_is_checked(sw)
            except Exception:
                checked = False
            if not checked:
                continue  # 只处理投放中(白钮)
            m = tac.parse_plan_metrics(r.get("text") or "")
            if m is None:
                log("    行#%d [计划] %s 指标解析失败，跳过" % (r["no"], r.get("plan") or "?"))
                continue
            flag = rule_should_close(m)
            log("    行#%d [计划] %s ROI=%.2f 成本=$%.2f 订单=%d → %s"
                % (r["no"], r.get("plan") or "?", m["roi"], m["cost"], m["orders"],
                   "命中睡觉规则" if flag else "不关"))
            if flag:
                cands.append((r["no"], r.get("plan") or ""))
        total_cand += len(cands)
        if not cands:
            log("  店铺 %s：无命中，本次不动。" % (shop or "?"))
            continue
        log("  店铺 %s：命中 %d 条，执行二次复核（防识别时页面刷新读到瞬时数据误判）..."
            % (shop or "?", len(cands)))
        if args.dry_run:
            log("  [dry-run] 只报告不点击，以上候选不会暂停。")
            continue
        # ---- v0.3 二次复核：等页面刷新落定后重收树，按计划名重新定位+重新判定 ----
        cd = getattr(args, "confirm_delay", 5.0)
        log("  等待 %.1f 秒页面刷新落定，然后二次收树复核..." % cd)
        time.sleep(cd)
        try:
            infos_c = tac.collect_all(auto, hwnd, with_render=False)
            rows_c, _hdr_c = tac.scan_rows(infos_c)
        except Exception as e:
            log("  二次收树失败: %s —— 保守跳过本轮全部候选（不执行关闭）。" % e)
            total_skip += len(cands)
            continue
        log("  二次收树完成：数据行 %d 个，逐候选复核..." % len(rows_c))
        confirmed = 0
        for row_no, plan in cands:
            # 按计划名在二次树中定位（重排/滚动后行号不可靠）
            r2 = None
            if plan:
                for rr in rows_c:
                    if (rr.get("plan") or "").strip() == (plan or "").strip():
                        r2 = rr
                        break
                if r2 is None:
                    log("  复核: [计划] %s 二次树中已不存在（已被暂停/下架/列表变化），跳过。"
                        % plan)
                    total_skip += 1
                    continue
            else:
                # 无计划名退化为行号匹配（与 dumb_close_one 原行为一致）
                if 1 <= row_no <= len(rows_c):
                    r2 = rows_c[row_no - 1]
            if r2 is None:
                total_skip += 1
                continue
            pname = r2.get("plan") or plan or "?"
            # 复核开关仍为投放中（白钮）
            try:
                sw2 = tac.find_row_switch(infos_c, r2["rect"])
                checked2 = sw2 is not None and tac.switch_is_checked(sw2)
            except Exception:
                checked2 = False
            if not checked2:
                log("  复核: [计划] %s 开关已非投放中（灰钮/未定位），跳过。" % pname)
                total_skip += 1
                continue
            m2 = tac.parse_plan_metrics(r2.get("text") or "")
            if m2 is None:
                log("  复核: [计划] %s 二次解析失败，跳过（宁缺毋滥）。" % pname)
                total_skip += 1
                continue
            if not rule_should_close(m2):
                log("  复核: [计划] %s 二次判定不再命中（ROI=%.2f 成本=$%.2f 订单=%d）"
                    "—— 页面数据已变化，放弃（防瞬时数据误关）。"
                    % (pname, m2["roi"], m2["cost"], m2["orders"]))
                total_skip += 1
                continue
            log("  复核通过: [计划] %s 二次判定仍命中，走关闭链路..." % pname)
            try:
                ok, msg = tac.dumb_close_one(auto, hwnd, ns, r2["no"], r2.get("plan") or "")
            except Exception as e:
                ok, msg = False, "异常: %s" % e
            if ok:
                confirmed += 1
                total_ok += 1
                log("  ✓ 已暂停 行#%d [计划] %s（%s）" % (r2["no"], pname, msg))
            else:
                total_skip += 1
                log("  ✗ 未暂停 行#%d [计划] %s（%s）" % (r2["no"], pname, msg))
            time.sleep(1.0)
        log("  店铺 %s：二次复核通过 %d/%d 条并执行。" % (shop or "?", confirmed, len(cands)))
        # 处理完截图留底
        try:
            shot = os.path.join(APP_DIR, "sleep_guard_after_%s.png"
                                % datetime.now().strftime("%Y%m%d_%H%M%S"))
            tac.trf.save_screenshot(hwnd, shot)
            log("  截图已保存: %s" % shot)
        except Exception:
            pass
    log("[汇总] 候选 %d / 成功暂停 %d / 失败跳过 %d" % (total_cand, total_ok, total_skip))
    log("=" * 66)


def main():
    ap = argparse.ArgumentParser(description="TK监控器 睡觉驻守（自动暂停空烧计划）")
    ap.add_argument("--log-file", default=None, help="监控的 monitor 日志文件（默认自动探测 exe 同级）")
    ap.add_argument("--keyword", action="append", default=None,
                    help="店铺窗口关键词（可多次；默认从日志/自动探测）")
    ap.add_argument("--unattended-sec", type=int, default=600,
                    help="无人值守门槛秒数：用户空闲满该值才执行自动暂停（默认600=10分钟）")
    ap.add_argument("--poll-sec", type=float, default=5.0, help="日志轮询间隔秒数（默认5）")
    ap.add_argument("--stabilize-sec", type=float, default=8.0,
                    help="看到日志新行后等待静默秒数再执行（默认8，等主程序一轮写完）")
    ap.add_argument("--confirm-delay", type=float, default=5.0,
                    help="首扫命中候选后等待秒数再做二次复核（防页面刷新瞬时数据误判，默认5）")
    ap.add_argument("--dry-run", action="store_true", help="只扫描报告候选，不点击")
    ap.add_argument("--no-wait-idle", action="store_true", help="点击前跳过空闲检测（调试用，慎用）")
    ap.add_argument("--scan-interval-min", type=float, default=7.0,
                    help="定时兜底扫描间隔分钟数：距上次实际扫描满该值即使日志无新行也强制全店扫一轮（默认7）")
    args = ap.parse_args()

    log("=" * 66)
    log("sleep_guard v0.3 睡觉驻守启动（无人值守防空烧 · 双触发 · 二次复核）")
    log("规则: 有单(>=1)且ROI<%.2f → 暂停 | 无单且消耗>$%.2f → 暂停"
        % (BUILTIN_CFG["roi_close"], BUILTIN_CFG["cost_zero_order"]))
    log("无人值守门槛: 用户空闲满 %d 秒后自动点击（dry_run=%s）" % (args.unattended_sec, args.dry_run))
    log("触发: log增量即时(对齐tkm轮次) + 每 %.1f 分钟强制兜底全扫" % args.scan_interval_min)
    log("日志: %s" % LOG_PATH)

    monitor = resolve_monitor_log(args.log_file)
    if not monitor:
        log("未找到 monitor 日志文件（exe 同级 monitor_YYYYMMDD.log）。")
        log("可用 --log-file 指定，例如 --log-file D:\\.A\\10.19\\monitor_20260906.log")
        log("无法驻守，退出。")
        return 2
    log("监控日志: %s" % monitor)
    log("说明: 触发A对齐主程序识别轮次（老板已调 5 分钟）；触发B每 %.1f 分钟兜底。"
        % args.scan_interval_min)
    log("=" * 66)

    if args.dry_run:
        log("[dry-run] 模式：只扫描报告，不会点击暂停任何计划。")

    tail = TailReader(monitor)
    pending_shops = []
    last_new_ts = None
    last_scan_ts = time.time()  # 触发B计时基准：距上次实际扫描满 scan_interval_min 才强制扫
    idle_wait_logged = False
    unattended_ok = args.dry_run or args.no_wait_idle  # dry-run 无需等无人值守

    try:
        while True:
            # 跨天/新文件自动切换
            cur = resolve_monitor_log(args.log_file)
            if cur and cur != tail.path:
                log("monitor 日志切换: %s -> %s" % (tail.path, cur))
                tail = TailReader(cur)

            lines = tail.poll()
            if lines:
                log("日志新行 %d 条（尾行: %s）" % (len(lines), lines[-1][:100]))
                shops = extract_shops(lines)
                if args.keyword:
                    shops = list(args.keyword)  # 用户显式指定优先
                if not shops:
                    shops = [None]  # 日志无店铺标记 → 自动探测窗口扫描一次
                for s in shops:
                    if s not in pending_shops:
                        pending_shops.append(s)
                last_new_ts = time.time()
                idle_wait_logged = False

            # 无人值守门槛：非 dry-run 时须用户空闲满 unattended_sec
            if not unattended_ok and not args.dry_run:
                idle = idle_seconds()
                if idle >= args.unattended_sec:
                    unattended_ok = True
                    log("用户已空闲 %.0f 秒，达到无人值守门槛，允许自动暂停。" % idle)
                else:
                    if not idle_wait_logged:
                        log("等待无人值守：当前用户空闲 %.0f 秒 < %d 秒，期间不点击。"
                            % (idle, args.unattended_sec))
                        idle_wait_logged = True
                    time.sleep(args.poll_sec)
                    continue

            # 触发执行（双触发，v0.2）：
            #   触发A：有待处理店铺且日志增量已静默 stabilize 秒（主程序一轮写完）
            #   触发B：距上次实际扫描满 scan_interval_min 分钟 → 强制全店兜底扫描
            #     （B 跳过条件：日志增量刚到、还在 stabilize 静默期 → 等 A 处理完再计时）
            do_scan = False
            scan_shops = None
            if pending_shops and last_new_ts is not None \
                    and (time.time() - last_new_ts) >= args.stabilize_sec:
                do_scan = True
                scan_shops = pending_shops
            elif (time.time() - last_scan_ts) >= args.scan_interval_min * 60 \
                    and (last_new_ts is None
                         or (time.time() - last_new_ts) >= args.stabilize_sec):
                do_scan = True
                if pending_shops:
                    scan_shops = pending_shops  # 顺手把待处理增量店铺一起扫了
                else:
                    scan_shops = args.keyword or [None]
                log("触发B【定时兜底】距上次扫描已满 %.1f 分钟，即使日志无新行也强制全店扫描。"
                    % args.scan_interval_min)

            if do_scan:
                process_shops(scan_shops, args)
                pending_shops = []
                last_new_ts = None
                last_scan_ts = time.time()
                idle_wait_logged = False

            time.sleep(args.poll_sec)
    except KeyboardInterrupt:
        log("收到 Ctrl+C，驻守退出。")
    except Exception as e:
        import traceback
        log("[致命异常]\n" + traceback.format_exc())
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        import traceback
        tb = traceback.format_exc()
        try:
            print(tb, flush=True)
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write("\n[%s] [致命异常]\n%s\n" % (datetime.now().strftime("%H:%M:%S"), tb))
        except Exception:
            pass
        sys.exit(99)
