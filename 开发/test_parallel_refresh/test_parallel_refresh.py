# -*- coding: utf-8 -*-
"""
test_parallel_refresh.py —— TK监控器「多店并行刷新」独立验证程序（v0.3 测试版）

背景：主程序 _monitor_loop 目前串行遍历 targets 逐店刷新（_uia_reload 含 sleep +
控件树收集 + 轮询等待 5~15s），店铺多时单轮耗时长。拟改为并行触发刷新——
本程序在独立环境验证并行刷新的可行性：
  1. 多店同时触发『重新加载』是否都能成功（不会被互相打断）
  2. 并行 vs 串行总耗时对比
  3. 日志按店铺整块输出（演示主程序将来『按店 buffer + 串行写日志』方案）

复用：test_refresh_filter.py 已验证的窗口定位 / 控件树收集 / 按钮查找 / 就绪轮询 /
UIA 子线程安全包装。

v0.3 变更（v0.2 方案A验证失败后的方案调整）：
  - 放弃方案A（点击前最小化窗口）：v0.2 实测最小化后 Invoke reload 在紫鸟 Chromium
    不触发刷新（串行全程 0 行、并行 2~3s 假就绪 11 行是刷新前旧表格、控件树仍
    217/221 壳层），说明方案A本身存疑，回到『置前点击 + 还原焦点』链路（v0.1 链路）。
  - 新增全局点击锁：把『清场 → 收集控件树 → 点击前快照 → 置前窗口 → Invoke →
    还原焦点』整体放入全局锁临界区，同一时刻仅一个店执行点击链路，消除 v0.1
    并行互抢前台（A244 点击前前台=A419）；就绪轮询在锁外并行执行，互不干扰。
  - 就绪轮询固定 with_render=True（同 v0.2），新增『刷新前后快照对比』防假就绪：
    点击前记录 URL/行数/表头集合，点击后基础就绪需伴随特征变化（URL/行数/表头任一）
    或曾观测到加载中间态（页面重载表格清空），才判定真刷新；仅基础就绪而无刷新
    特征视为疑似旧表格，继续轮询到超时，不再误报成功。
  - 置前动作加校验：SW_RESTORE+SW_SHOW+SetForegroundWindow 后检查 IsWindowVisible/
    IsIconic，防止 ShowWindow 静默失败（v0.2 恢复窗口日志写成功但肉眼仍最小化）。

用法：
  python test_parallel_refresh.py                    # 默认 both：先串行后并行，自动检测 Axxx 店铺窗口
  python test_parallel_refresh.py --mode serial      # 只跑串行
  python test_parallel_refresh.py --mode parallel    # 只跑并行
  python test_parallel_refresh.py --keyword A419 --keyword A244  # 显式指定店铺关键词
  python test_parallel_refresh.py --wait 15          # 就绪等待秒数（默认 15）
  python test_parallel_refresh.py --no-clean         # 不做遮挡窗口清场
  python test_parallel_refresh.py --render           # 开启渲染子窗口（页面 DOM）收集（默认关）
  python test_parallel_refresh.py --no-pause         # 结束后不暂停
"""
import argparse
import ctypes
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

if getattr(sys, "frozen", False):
    # PyInstaller onefile 打包后：脚本在临时解压目录运行，__file__ 指向 _MEIPASS；
    # 日志必须写到 exe 同级目录（用户可见）。test_refresh_filter 已由 spec
    # （pathex + hiddenimports）内嵌进 PYZ，无需兄弟目录路径，运行期不再 insert。
    BASE_DIR = os.path.dirname(sys.executable)
else:
    # 源码直跑：本目录 + 复用兄弟目录 test_refresh_filter 的成熟函数
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, BASE_DIR)
    _TRF_DIR = os.path.join(os.path.dirname(BASE_DIR), "test_refresh_filter")
    sys.path.insert(0, _TRF_DIR)

import test_refresh_filter as trf  # noqa: E402

# Windows 控制台默认 GBK：窗口标题/日志可能含 emoji（如 ❤），print 会抛
# UnicodeEncodeError 直接崩溃；保持原编码只把无法编码字符替换为 ?（避免乱码）。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors="replace")
    except Exception:
        pass

LOG_PATH = os.path.join(BASE_DIR, "test_parallel_refresh.log")

# ---------------- 日志：按店铺 buffer 整块输出 ----------------
# 并行时各店工作线程把日志写入自己的 buffer（thread-local），不直接 print，
# 全部完成后由主线程按店铺顺序整块打印/落盘，避免 A 店两行 B 店两行交错。
_tls = threading.local()
_fallback_buf = []  # 主线程 / 无 buffer 上下文（如渲染子线程）的杂项日志


def _shop_log(msg):
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    buf = getattr(_tls, "buf", None)
    if buf is not None:
        buf.append(line)
    else:
        _fallback_buf.append(line)


# 替换 test_refresh_filter 模块级 log：内部函数（_collect_tree / _minimize_blocking_windows 等）
# 的日志会写入当前工作线程的店铺 buffer
trf.log = _shop_log


def _out(msg, stream=None):
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    (stream or print)(line, flush=True)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ---------------- 刷新单店（v0.3 置前点击 + 点击链路全局锁串行化） ----------------
# 全局点击锁：并行验证核心。v0.3 放弃方案A（最小化点击在紫鸟 Chromium 不触发 reload，
# v0.2 实测假OK），回到『置前点击 + 还原焦点』链路；为消除 v0.1 并行互抢前台
# （A244 点击前前台被记录成 A419 窗口），用全局锁把『清场 → 收集控件树 → 点击前
# 快照 → 置前 → Invoke → 还原焦点』整体串行化——同一时刻仅一个店执行点击链路，
# 其余线程阻塞在锁上；就绪轮询在锁外并行进行，互不干扰。
_CLICK_LOCK = threading.Lock()


def refresh_shop(kw, hwnd, title, wait_sec, with_render, do_clean):
    """执行单店刷新（v0.3 置前点击 + 点击链路全局锁串行化）：
    锁内：清场 → 收集控件树 → 点击前快照 → 置前窗口 → Invoke → 还原焦点；
    锁外：并行轮询表格就绪（with_render=True），就绪需与点击前快照对比发生
    特征变化（URL / 行数 / 表头集合）或曾观测到加载中间态，防并行期旧表格假就绪。
    日志写入 thread-local buffer。
    返回 dict(ok, clicked, ready, refreshed, elapsed, steps)。"""
    buf = []
    _tls.buf = buf
    t0 = time.time()
    steps = []
    try:
        _shop_log(f"===== 店铺 {kw} 刷新开始 (hwnd=0x{hwnd & 0xFFFFFFFFFFFFFFFF:X}) =====")

        # --- 点击链路临界区（全局锁）：同一时刻仅一个店可执行置前/点击/还原，
        #     并行时其余线程阻塞在锁上，消除 v0.1 互抢前台（A244 点击前前台=A419） ---
        with _CLICK_LOCK:
            # 1. 抗干扰清场（可选）：最小化遮挡窗口；店铺窗口 Axxx 不会被处理
            if do_clean:
                trf._minimize_blocking_windows(hwnd)

            # 2. 收集控件树（锁内独占 UIA 遍历，避免并行并发遍历只暴露壳层 217/221）
            infos = trf._collect_tree(hwnd, trf._AUTO, with_render=with_render)
            btn, cands = trf._find_reload_button(infos)
            if btn is None:
                _shop_log(f"未找到『重新加载』按钮（Button 候选 {len(cands)} 个）")
                return {"ok": False, "clicked": False, "ready": False, "refreshed": False,
                        "elapsed": time.time() - t0, "steps": buf, "reason": "no_reload_btn"}
            _shop_log(f"找到重载按钮 aid={btn['aid']} cls={btn['cls']}")

            # 3. 点击前快照（URL/行数/表头集合）：用于点击后对比，防旧表格假就绪
            try:
                url_before, rows_before, headers_before = trf._probe_table_ready(
                    hwnd, trf._AUTO, with_render=True)
                _shop_log(f"点击前快照: url={'有' if url_before else '无'} 行数={rows_before} "
                          f"表头={sorted(headers_before) if headers_before else []}")
            except Exception as e:
                url_before, rows_before, headers_before = "", -1, []
                _shop_log(f"点击前快照读取失败（忽略，继续刷新）: {e}")

            # 4. 置前目标窗口（SW_RESTORE+SW_SHOW+SetForegroundWindow，v0.1 链路），
            #    并校验置前结果。放弃方案A最小化点击：v0.2 实测最小化后 Invoke reload
            #    在紫鸟 Chromium 不触发刷新（串行全程 0 行、并行假OK），故回到置前点击。
            user32 = ctypes.windll.user32
            try:
                user32.ShowWindow(hwnd, 9)   # SW_RESTORE
                user32.ShowWindow(hwnd, 5)   # SW_SHOW
                user32.SetForegroundWindow(hwnd)
                time.sleep(0.3)
                if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
                    _shop_log("置前校验失败：窗口仍不可见或处于最小化")
                else:
                    _shop_log("目标窗口已置前（SW_RESTORE+SW_SHOW+SetForegroundWindow）")
            except Exception as e:
                _shop_log(f"置前目标窗口失败（忽略，继续刷新）: {e}")

            # 5. 记录点击前前台窗口，点击成功后还原焦点（AttachThreadInput 链路）
            prev_fg = None
            try:
                prev_fg = user32.GetForegroundWindow()
                _shop_log(f"点击前前台窗口 hwnd=0x{prev_fg & 0xFFFFFFFFFFFFFFFF:X}")
            except Exception:
                pass

            # 6. Invoke 点击（LegacyIAccessible 兜底；_ui_call_safe 子线程超时保护）
            clicked = False
            try:
                def _do_invoke():
                    ip = btn["ctrl"].GetInvokePattern()
                    if ip is not None:
                        ip.Invoke()
                        return True
                    lp = btn["ctrl"].GetLegacyIAccessiblePattern()
                    if lp is not None:
                        lp.DoDefaultAction()
                        return True
                    return False
                ok, err = trf._ui_call_safe(_do_invoke, timeout=8.0, desc="Invoke")
                if ok:
                    clicked = True
                    _shop_log("Invoke 点击成功")
                else:
                    _shop_log(f"Invoke 失败: {err}")
                    return {"ok": False, "clicked": False, "ready": False, "refreshed": False,
                            "elapsed": time.time() - t0, "steps": buf, "reason": "click_fail"}
            except Exception as e:
                _shop_log(f"点击异常: {e}")
                return {"ok": False, "clicked": False, "ready": False, "refreshed": False,
                        "elapsed": time.time() - t0, "steps": buf, "reason": f"click_exc:{e}"}

            # 7. 还原前台焦点（失败静默，仅记录日志）
            if prev_fg:
                try:
                    cur_tid = ctypes.windll.kernel32.GetCurrentThreadId()
                    fg_tid = user32.GetWindowThreadProcessId(prev_fg, ctypes.byref(ctypes.c_ulong()))
                    if fg_tid:
                        user32.AttachThreadInput(cur_tid, fg_tid, True)
                        try:
                            user32.BringWindowToTop(prev_fg)
                            user32.SetForegroundWindow(prev_fg)
                        finally:
                            user32.AttachThreadInput(cur_tid, fg_tid, False)
                    _shop_log("前台焦点已还原")
                except Exception:
                    _shop_log("焦点还原失败（忽略，不影响刷新流程）")
        # --- 点击链路临界区结束（锁外开始并行就绪轮询） ---

        # 8. 就绪轮询（每 2s 探测，sleep 累计达 wait_sec 为止；固定 with_render=True）
        #    判定『真就绪』= 基础就绪（行数>0+表头齐）且：
        #      - 相对点击前快照发生特征变化（URL / 行数 / 表头集合任一），或
        #      - 轮询中曾观测到加载中间态（基础未就绪：页面重载表格清空/未渲染）
        #    两者皆无则视为疑似旧表格（v0.2 并行 2~3s 就绪 11 行实为刷新前旧表），继续等。
        elapsed_wait = 0.0
        ready = False
        refreshed = False
        saw_dip = False
        poll_round = 0
        while elapsed_wait < wait_sec:
            poll_round += 1
            t_round = time.time()
            time.sleep(2.0)
            elapsed_wait += 2.0
            url_now, rows_now, headers_now = trf._probe_table_ready(
                hwnd, trf._AUTO, with_render=True)
            base = trf._ready_by_table(rows_now, headers_now)
            if not base:
                saw_dip = True  # 出现过加载中间态（表格清空重载 / 尚未渲染）
            try:
                headers_now_set = set(headers_now) if headers_now else set()
                headers_before_set = set(headers_before) if headers_before else set()
            except Exception:
                headers_now_set, headers_before_set = set(), set()
            changed = False
            if base and rows_before >= 0:
                changed = (url_now != url_before) or (rows_now != rows_before) or \
                          (headers_now_set != headers_before_set)
            _shop_log(f"[轮询#{poll_round}] 行数={rows_now} 表头={sorted(headers_now_set)} "
                      f"url={'有' if url_now else '无'} 基础就绪={base} 特征变化={changed} "
                      f"曾见中间态={saw_dip}（sleep累计{elapsed_wait:.0f}s，本轮{time.time()-t_round:.1f}s）")
            if base and (changed or saw_dip):
                ready = True
                refreshed = True
                _shop_log(f"表格真就绪（行数={rows_now}，刷新特征已确认）sleep累计{elapsed_wait:.0f}s")
                break
            if base and not changed and not saw_dip:
                _shop_log("基础就绪但无任何刷新特征（疑似旧表格），继续轮询等待刷新特征")
        if not ready:
            _shop_log(f"等待{wait_sec:.0f}s 表格未真就绪（sleep累计{elapsed_wait:.0f}s）")

        _shop_log(f"===== 店铺 {kw} 刷新结束：clicked={clicked} ready={ready} "
                  f"refreshed={refreshed} 耗时{time.time()-t0:.1f}s =====")
        return {"ok": clicked and refreshed, "clicked": clicked, "ready": ready,
                "refreshed": refreshed, "elapsed": time.time() - t0, "steps": buf, "reason": ""}
    except Exception as e:
        _shop_log(f"刷新异常: {e}")
        return {"ok": False, "clicked": False, "ready": False, "refreshed": False,
                "elapsed": time.time() - t0, "steps": buf, "reason": f"exc:{e}"}
    finally:
        _tls.buf = None


# ---------------- 串行 / 并行执行 ----------------
def run_serial(shops, wait_sec, with_render, do_clean):
    _out(f"--- 串行模式：{len(shops)} 店逐个刷新 ---")
    t_start = time.time()
    results = []
    for kw, hwnd, title in shops:
        r = refresh_shop(kw, hwnd, title, wait_sec, with_render, do_clean)
        results.append((kw, hwnd, title, r))
    wall = time.time() - t_start
    _out(f"--- 串行模式完成：总耗时 {wall:.1f}s ---")
    return results, wall


def run_parallel(shops, wait_sec, with_render, do_clean):
    _out(f"--- 并行模式：{len(shops)} 店同时触发刷新 ---")
    t_start = time.time()
    results = [None] * len(shops)  # 按店铺顺序占位，避免 as_completed 乱序
    with ThreadPoolExecutor(max_workers=len(shops)) as ex:
        fut_map = {}
        for idx, (kw, hwnd, title) in enumerate(shops):
            fut = ex.submit(refresh_shop, kw, hwnd, title, wait_sec, with_render, do_clean)
            fut_map[fut] = idx
        for fut in as_completed(fut_map):
            idx = fut_map[fut]
            kw, hwnd, title = shops[idx]
            try:
                r = fut.result()
            except Exception as e:
                r = {"ok": False, "clicked": False, "ready": False, "elapsed": 0,
                     "steps": [f"[{datetime.now().strftime('%H:%M:%S')}] 线程异常: {e}"], "reason": f"thread_exc:{e}"}
            results[idx] = (kw, hwnd, title, r)
    wall = time.time() - t_start
    _out(f"--- 并行模式完成：总耗时（墙钟）{wall:.1f}s ---")
    return results, wall


# ---------------- 汇总输出 ----------------
def _print_results(title, results, wall):
    _out("=" * 70)
    _out(title)
    # 按店铺顺序整块打印日志（演示『日志按店打包、按序输出』）
    for kw, hwnd, _t, r in results:
        for line in r["steps"]:
            _out(f"  {line}")
    # 汇总表
    _out("")
    _out(f"{'店铺':<10}{'结果':<6}{'点击':<6}{'就绪':<6}{'真刷新':<8}{'耗时(s)':<10}原因")
    _out("-" * 66)
    for kw, _h, _t, r in results:
        _out(f"{kw:<10}{'OK' if r['ok'] else 'FAIL':<6}"
             f"{'Y' if r['clicked'] else 'N':<6}{'Y' if r['ready'] else 'N':<6}"
             f"{'Y' if r.get('refreshed') else 'N':<8}"
             f"{r['elapsed']:<10.1f}{r['reason']}")
    _out(f"总耗时（墙钟）：{wall:.1f}s")
    _out("=" * 70)


def main():
    ap = argparse.ArgumentParser(description="TK监控器 多店并行刷新 验证程序")
    ap.add_argument("--mode", choices=["serial", "parallel", "both"], default="both",
                    help="执行模式：serial=只串行 / parallel=只并行 / both=两者都跑（默认）")
    ap.add_argument("--keyword", action="append", default=None,
                    help="店铺窗口关键词（可多次传入，如 --keyword A419 --keyword A244；默认自动扫描 Axxx）")
    ap.add_argument("--wait", type=int, default=15, help="就绪等待秒数（默认 15）")
    ap.add_argument("--no-clean", action="store_true", help="不做遮挡窗口清场")
    ap.add_argument("--render", action="store_true", help="开启渲染子窗口（页面 DOM）收集（默认关闭）")
    ap.add_argument("--no-pause", action="store_true", help="结束后不暂停等待回车")
    args = ap.parse_args()

    with_render = args.render
    do_clean = not args.no_clean

    # 店铺关键词：--keyword > 自动扫描 Axxx > 默认 A419
    keywords = list(args.keyword or [])
    if not keywords:
        auto_kws = trf.auto_detect_shop_keywords(max_n=8)
        if auto_kws:
            keywords = auto_kws
            _out(f"自动检测到店铺窗口关键词: {keywords}")
        else:
            keywords = ["A419"]
            _out("未检测到 Axxx 店铺窗口，使用默认关键词 A419")

    _out("=" * 70)
    _out(f"TK监控器 多店并行刷新 验证 v0.3  mode={args.mode} wait={args.wait}s "
         f"clean={do_clean} render={with_render}")

    if args.mode == "dry":
        return 0

    # 定位每个店铺窗口
    shops = []
    seen = set()
    for kw in keywords:
        kw = kw.strip().lstrip("=")
        hwnd, title, hit = trf.find_window_by_keywords_multi([kw])
        if not hwnd:
            _out(f"未找到标题含 {kw} 的可见窗口，跳过该店")
            continue
        if hwnd in seen:
            _out(f"店铺 {kw} 与已定位窗口重复（hwnd=0x{hwnd & 0xFFFFFFFFFFFFFFFF:X}），跳过")
            continue
        seen.add(hwnd)
        shops.append((kw, hwnd, title))
        _out(f"定位店铺 {kw}: hwnd=0x{hwnd & 0xFFFFFFFFFFFFFFFF:X} 标题={title}")

    if not shops:
        _out("未定位到任何店铺窗口，无法验证。请确认紫鸟已打开 TK 商户中心页面。")
        _out("--- 当前可见窗口标题（前25个） ---")
        for i, (wt, _h) in enumerate(trf.list_windows()[:25], 1):
            _out(f"  {i:2d}. {wt[:80]}")
        return 2

    _out(f"共定位 {len(shops)} 个店铺窗口")

    # import uiautomation（与 test_refresh_filter 一致的延迟导入；挂到模块供各店线程使用）
    t0 = time.time()
    try:
        import uiautomation as auto
        trf._AUTO = auto
        _out(f"uiautomation import 成功，耗时 {time.time() - t0:.2f}s")
    except Exception as e:
        _out(f"uiautomation 导入失败: {e}")
        return 2

    # 执行
    results = None
    wall = 0.0
    if args.mode in ("serial", "both"):
        results, wall = run_serial(shops, args.wait, with_render, do_clean)
        _print_results(f"串行结果", results, wall)
        time.sleep(2.0)  # 两轮之间稍歇，避免上轮轮询与下轮清场交叠

    if args.mode in ("parallel", "both"):
        results, wall = run_parallel(shops, args.wait, with_render, do_clean)
        _print_results(f"并行结果", results, wall)

    # 对比（both 模式）
    _out("")
    _out(f"日志已落盘: {LOG_PATH}")
    if not args.no_pause:
        try:
            input("按回车退出...")
        except EOFError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
