# -*- coding: utf-8 -*-
"""
mini_sampling_check.py —— auto_close_probe v1.2 采样链路的本机复现检查器
========================================================================
目的：工作机 v1.1 采样段"静默失踪"（基础报告落盘后无任何采样结果）。
 在工作机复现代价高，先用本机任意 Chromium 窗口（Edge/Chrome）验证
 采样线程模型本身是否健康：
   - 本机 Chromium 也超时/卡 → uiautomation 线程/COM 层通用问题，本机定位；
   - 本机 Chromium 秒回命中 → 问题锁定紫鸟页面特有 DOM/provider，才需工作机。

用法：
  python mini_sampling_check.py                 # 自动挑第一个可见 Chromium 窗口
  python mini_sampling_check.py --keyword Edge  # 指定标题关键词
  python mini_sampling_check.py --points 12     # 点数（默认 8），单点 3s 超时

只读：ControlFromPoint 仅查命中控件属性，不点击不滚动。输出仅控制台。
"""
import argparse
import ctypes
import ctypes.wintypes
import datetime
import sys
import threading
import time

# ---------------- Win32 窗口枚举（只读，不依赖 uiautomation） ----------------
user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)


def _title(hwnd):
    buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, buf, 512)
    return buf.value


def _pid(hwnd):
    pid = ctypes.c_ulong()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def _exe(pid):
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(512)
        n = ctypes.c_ulong(512)
        ok = kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n))
        return buf.value if ok else ""
    finally:
        kernel32.CloseHandle(h)


def _client_rect_screen(hwnd):
    r = ctypes.wintypes.RECT()
    pt = ctypes.wintypes.POINT()
    user32.GetClientRect(hwnd, ctypes.byref(r))
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    return (pt.x, pt.y, pt.x + r.right - r.left, pt.y + r.bottom - r.top)


def list_chrome_windows():
    wins = []

    def cb(hwnd, _):
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            cls = ctypes.create_unicode_buffer(128)
            user32.GetClassNameW(hwnd, cls, 128)
            if cls.value != "Chrome_WidgetWin_1":
                return True
            title = _title(hwnd)
            if not title:
                return True
            rect = ctypes.wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            if rect.right - rect.left < 200 or rect.bottom - rect.top < 120:
                return True
            wins.append({"hwnd": hwnd, "title": title, "exe": _exe(_pid(hwnd)),
                         "rect": (rect.left, rect.top, rect.right, rect.bottom)})
        except Exception:
            pass
        return True

    user32.EnumWindows(EnumWindowsProc(cb), 0)
    return wins


def now():
    return datetime.datetime.now().strftime("%H:%M:%S")


# ---------------- 采样（与 probe v1.2 point_probe 同构） ----------------
def sample_one(p, per_point_timeout):
    """独立线程内做 ControlFromPoint；返回 rec。"""
    out = {}

    def work():
        rec = {"point": p, "hit": None}
        try:
            import uiautomation as autoX
            with autoX.UIAutomationInitializerInThread():
                c = autoX.ControlFromPoint(p["x"], p["y"])
                if c is not None:
                    hit = {}
                    for k, fn in (
                        ("ct", lambda: c.ControlTypeName),
                        ("name", lambda: c.Name),
                        ("cls", lambda: c.ClassName),
                    ):
                        try:
                            hit[k] = fn()
                        except Exception:
                            hit[k] = None
                    try:
                        rr = c.BoundingRectangle
                        hit["rect"] = (int(rr.left), int(rr.top),
                                       int(rr.right), int(rr.bottom))
                    except Exception:
                        hit["rect"] = None
                    try:
                        hit["offscreen"] = bool(c.IsOffscreen)
                    except Exception:
                        hit["offscreen"] = None
                    rec["hit"] = hit
        except Exception as e:
            rec["err"] = f"{type(e).__name__}: {e}"
        out.update(rec)

    t = threading.Thread(target=work, daemon=True)
    t0 = time.time()
    t.start()
    t.join(timeout=per_point_timeout)
    if t.is_alive():
        return {"point": p, "hit": None, "err": "timeout",
                "wait": round(time.time() - t0, 2)}
    out["wait"] = round(time.time() - t0, 2)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keyword", default=None)
    ap.add_argument("--points", type=int, default=8)
    ap.add_argument("--timeout", type=float, default=3.0)
    ap.add_argument("--max-timeouts", type=int, default=6)
    args = ap.parse_args()

    print(f"[{now()}] mini_sampling_check 启动（点数={args.points} "
          f"单点超时={args.timeout}s 最大超时容忍={args.max_timeouts}）")
    wins = list_chrome_windows()
    if not wins:
        print("!! 本机未发现可见 Chromium 窗口（Chrome_WidgetWin_1）。")
        print("   请打开 Edge/Chrome 任意页面后重跑。")
        return 2
    print(f"[{now()}] 可见 Chromium 窗口 {len(wins)} 个：")
    for i, w in enumerate(wins, 1):
        print(f"   {i}. 0x{w['hwnd'] & 0xFFFFFFFFFFFFFFFF:X} "
              f"exe={w['exe'].split(chr(92))[-1] or '?'} 标题={w['title'][:60]!r}")

    pick = None
    if args.keyword:
        for w in wins:
            if args.keyword.lower() in (w["title"] + w["exe"]).lower():
                pick = w
                break
        if not pick:
            print(f"!! 未找到标题/exe 含 {args.keyword!r} 的窗口，取第一个。")
    if pick is None:
        pick = wins[0]
    print(f"[{now()}] 选定窗口 0x{pick['hwnd'] & 0xFFFFFFFFFFFFFFFF:X} "
          f"标题={pick['title'][:60]!r} rect={pick['rect']}")

    cr = _client_rect_screen(pick["hwnd"])
    cw, ch = cr[2] - cr[0], cr[3] - cr[1]
    print(f"[{now()}] 客户区(屏幕) = {cr}  宽={cw} 高={ch}")

    pts = [
        {"tag": "client-center", "x": (cr[0] + cr[2]) // 2, "y": (cr[1] + cr[3]) // 2},
        {"tag": "client-20%", "x": cr[2] - max(60, cw // 4), "y": cr[1] + int(ch * 0.2)},
        {"tag": "client-40%", "x": cr[2] - max(60, cw // 4), "y": cr[1] + int(ch * 0.4)},
        {"tag": "client-60%", "x": cr[2] - max(60, cw // 4), "y": cr[1] + int(ch * 0.6)},
        {"tag": "client-80%", "x": cr[2] - max(60, cw // 4), "y": cr[1] + int(ch * 0.8)},
        {"tag": "clamp-x-right", "x": cr[2] - 2, "y": (cr[1] + cr[3]) // 2},
        {"tag": "top-left", "x": cr[0] + 30, "y": cr[1] + 30},
        {"tag": "bottom-right", "x": cr[2] - 30, "y": cr[3] - 30},
    ][:args.points]

    timeout_cnt = 0
    results = []
    print(f"[{now()}] 采样 {len(pts)} 点 ...")
    for i, p in enumerate(pts, 1):
        if timeout_cnt >= args.max_timeouts:
            print(f"   [{now()}] 采样 {i}/{len(pts)} ({p['tag']}): 跳过（累计超时达上限）")
            results.append({"point": p, "err": "skipped"})
            continue
        rec = sample_one(p, args.timeout)
        results.append(rec)
        if rec.get("err") == "timeout":
            timeout_cnt += 1
            print(f"   [{now()}] 采样 {i}/{len(pts)} ({p['tag']}): 超时 >{args.timeout}s")
        elif rec.get("err"):
            print(f"   [{now()}] 采样 {i}/{len(pts)} ({p['tag']}): 错误 {rec['err']}")
        else:
            h = rec.get("hit") or {}
            print(f"   [{now()}] 采样 {i}/{len(pts)} ({p['tag']}): 命中 {h.get('ct')} "
                  f"cls={str(h.get('cls'))[:40]!r} off={h.get('offscreen')} "
                  f"rect={h.get('rect')} 耗时={rec.get('wait')}s")
        sys.stdout.flush()

    hit = sum(1 for r in results if r.get("hit"))
    to = sum(1 for r in results if r.get("err") == "timeout")
    err = sum(1 for r in results if r.get("err") and r.get("err") != "timeout"
              and r.get("err") != "skipped")
    print(f"[{now()}] 汇总: 命中 {hit}/{len(results)}，超时 {to}，错误 {err}，跳过 "
          f"{sum(1 for r in results if r.get('err')=='skipped')}")
    if to:
        print("=> 本机采样存在超时：疑似 uiautomation 线程/COM 层通用问题，继续深挖。")
    else:
        print("=> 本机采样全部秒回：线程模型健康，问题锁定紫鸟页面特有 DOM/provider，"
              "需要工作机跑 v1.2 定位具体卡点。")
    return 0


if __name__ == "__main__":
    try:
        rc = main()
    except Exception:
        import traceback
        traceback.print_exc()
        rc = 1
    sys.exit(rc)
