# -*- coding: utf-8 -*-
"""UIA 控件树识别器 mod（架构健康化A：模式二 UIA 外置）

职责：目标窗口控件树读取（BFS 遍历 + ValuePattern 兜底 + Chromium 渲染子窗口
下钻 + 滚动收集合并去重）→ 产出与 OCR 兼容的 (text, words)；CDP 端口可用时
以页面 DOM 文本兜底。核心只通过 api.register_recog_mode 注册本识别器，解析
管线（_parse_table_by_words/parse_fields）留在核心，本 mod 只含 UIA 专属逻辑。

依赖：uiautomation / websocket 由主 exe 打包（--collect-all uiautomation
--collect-all websocket），本 mod 运行时从 exe 环境 import。

v10.8.4 修复（2026-08-26）：
- 修复：uia_reload 刷新后页面未加载完就识别（no_header / 未解析到 ROI/成本/收入），
  多店（A419/A244）识别不稳定。根因：就绪轮询以地址栏 URL 变化为就绪信号（URL 变化
  只代表刷新开始，此时表格行=0、表头未渲染），等待仅 1.5~4.5s 就接续识别导致失败。
  修复：就绪信号改为页面表格就绪为主——轮询检测表格行数>0 且表头关键词（广告计划
  名称/ROI/成本/总收入 等）出现，最长 30s、每 2s 探测一次；URL 变化仅作辅助日志不再
  作为就绪信号；超时输出详细快照（表格行数、表头候选、控件数）后按失败处理（调用方
  继续识别，由主程序 v10.8.4 识别失败自动重试兜底）。
- 版本号 v10.8.4（主程序文件头/启动日志/窗口标题/spec 同步升级）。

v10.7.5 修复（2026-08-25）：
- 修复：路线C「状态筛选框」误匹配浏览器 UI（如「标签页重新变为活动状态」AutomationId=view_1011）。
  根因：_find_status_ctrl 仅按 Name 含『状态』+ControlType 匹配，未排除浏览器 chrome UI。
  修复：新增浏览器 UI 特征识别（AutomationId 以 view_ 开头 / Name 为浏览器功能名），
  页面控件定位限定在页面内容区——优先从渲染子窗口（Chrome_RenderWidgetHostHWND）子树收集，
  主窗口外壳兜底，过滤浏览器 UI。
- 修复：勾选『已生效』失败仍报「流程全部完成」。根因：DataItemControl 无 Toggle/SelectionItem/
  Invoke Pattern 时 _select_option 返回失败，但流程未中止继续点『应用』且 missing 未记录。
  修复：勾选/选中增加 LegacyIAccessiblePattern.DoDefaultAction() 兜底；任一步骤失败立即返回
  失败并中止后续步骤，日志标注「流程失败于第X步」；全部 Pattern 不可用时可配置
  mouse_click_fallback=true 启用鼠标点击降级（SetCursorPos+mouse_event，默认 false）。
- 修复：控件探测不完整（345 个控件主要是浏览器 chrome UI，页面内控件未覆盖）。根因：
  _uia_ctrl_probe 仅从主窗口 root 遍历，未下钻渲染子窗口。修复：遍历主窗口+渲染子窗口
  合并去重；每条记录标注 zone（page_content / browser_chrome）；完整清单写入
  temp 目录 uia_probe_YYYYMMDD_HHMM.txt，日志只显示统计摘要。
"""

import ctypes
import ctypes.wintypes
import json
import os
import re
import tempfile
import time
import urllib.request
import threading


# ==================== UIA 专属辅助（原主程序模块级函数迁移） ====================

def _setup_user32_types(user32):
    """显式声明 user32 API 的 64 位句柄类型，避免 hwnd 被按 32 位截断导致误判失效。

    本 mod 仅需 EnumChildWindows/GetClassNameW 两个 API（渲染子窗口定位用），
    与主程序同名单函数职责一致（原由主程序提供，拆分后随 UIA 逻辑迁入 mod）。
    """
    user32.EnumChildWindows.argtypes = [ctypes.wintypes.HWND, ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM), ctypes.wintypes.LPARAM]
    user32.EnumChildWindows.restype = ctypes.wintypes.BOOL
    user32.GetClassNameW.argtypes = [ctypes.wintypes.HWND, ctypes.c_wchar_p, ctypes.c_int]
    user32.GetClassNameW.restype = ctypes.c_int


def _uia_scroll_down(hwnd):
    """向目标窗口发送 WM_MOUSEWHEEL 向下滚动一屏（SendMessage 程序内消息，非模拟鼠标键盘）。

    返回 True/False；某些窗口（如未聚焦的浏览器）可能忽略该消息，
    懒加载场景建议开启滚动收集并实测滚屏是否触发渲染。"""
    try:
        user32 = ctypes.windll.user32
        user32.SendMessageW.argtypes = [ctypes.wintypes.HWND, ctypes.wintypes.UINT,
                                        ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM]
        user32.SendMessageW.restype = ctypes.wintypes.LPARAM
        # WM_MOUSEWHEEL=0x020A，wParam 高 16 位为有符号 delta，-120 表示向下滚动一档
        user32.SendMessageW(hwnd, 0x020A, ctypes.wintypes.WPARAM((0 - 120) << 16), 0)
        return True
    except Exception:
        return False


def _find_render_sub_hwnd(hwnd, max_depth=12):
    """递归 EnumChildWindows 定位 Chromium 内核浏览器页面渲染子窗口。

    返回优先级最高的子窗口 hwnd：Chrome_RenderWidgetHostHWND（0）>
    Chrome_WidgetWin_*（1）> 其他含 Render 的类（2）；找不到返回 None。
    页面内容（DOM 渲染结果）通常位于渲染子窗口的 UIA 子树下，从主窗口
    hwnd 遍历会因深度/节点预算截断而只读到浏览器外壳。"""
    user32 = ctypes.windll.user32
    _setup_user32_types(user32)

    def _class_of(h):
        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(h, buf, 256)
        return buf.value or ""

    best = [99, None]  # [优先级, hwnd]，越小越优先

    def _children(h):
        out = []

        def cb(hc, _):
            out.append(hc)
            return True

        user32.EnumChildWindows(h, ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)(cb), 0)
        return out

    def walk(cur, depth):
        nonlocal best
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

    # 从主窗口的子窗口开始找（主窗口自身即外壳，无意义）
    for c in _children(hwnd):
        if best[0] == 0:
            break
        walk(c, 1)
    return best[1] if best[0] < 99 else None


def _find_render_sub_hwnd_safe(hwnd, timeout=8.0):
    """_find_render_sub_hwnd 带超时保护：递归 EnumChildWindows 在复杂窗口树
    可能极慢/卡死，放子线程执行，超时返回 None 不拖死主监控线程（v10.18 合入，
    源自 test_refresh_filter 实测：连续三版卡在渲染子窗口裸调）。"""
    import threading
    result = {"hwnd": None}

    def _run():
        try:
            result["hwnd"] = _find_render_sub_hwnd(hwnd)
        except Exception:
            result["hwnd"] = None

    th = threading.Thread(target=_run, daemon=True)
    th.start()
    th.join(timeout)
    if th.is_alive():
        return None
    return result["hwnd"]


# ==================== 控件树读取核心 ====================

def _uia_read_text(core, target=None):
    """模式二核心：读取目标窗口控件树文本，组装与 OCR 兼容的 (text, words)。

    words=[{text,x,y,w,h},...]，坐标统一为相对 ROI 左上角（无 ROI 时相对窗口左上角），
    与模式一 OCR 词坐标基准一致，供 _parse_table_by_words/parse_fields 复用。
    懒加载滚动收集：开启时 SendMessage WM_MOUSEWHEEL 向下滚动一屏→读一次控件树→
    合并去重，连续两次行数不增长或达上限（uia_max_scroll，默认20）停止。
    失败抛 RuntimeError（带明确原因），不影响模式一。"""
    t = target if target is not None else core._current_target()
    win = core.get_target_window(t)
    if not win or win.get("width", 0) <= 0 or win.get("height", 0) <= 0:
        raise RuntimeError("控件树读取失败：未定位到目标窗口（句柄失效或关键词匹配失败），请点『列出窗口』重新锁定")
    hwnd = core._resolve_hwnd(t)
    if not hwnd:
        raise RuntimeError("控件树读取失败：未取得目标窗口句柄，请先『列出窗口』双击锁定窗口")
    try:
        wait = float(core.cfg.get("uia_wait_sec", 1.5) or 0)
    except (ValueError, TypeError):
        wait = 1.5
    if wait > 0:
        time.sleep(min(wait, 5.0))  # accessibility tree 构建延迟，读取前短暂等待
    scroll = bool(core.cfg.get("uia_scroll_collect", False))
    try:
        max_scroll = int(core.cfg.get("uia_max_scroll", 20) or 20)
    except (ValueError, TypeError):
        max_scroll = 20
    try:
        max_nodes = int(core.cfg.get("uia_max_nodes", 5000) or 5000)
    except (ValueError, TypeError):
        max_nodes = 5000
    try:
        timeout_sec = float(core.cfg.get("uia_timeout_sec", 20.0) or 20.0)
    except (ValueError, TypeError):
        timeout_sec = 20.0
    try:
        max_depth = int(core.cfg.get("uia_max_depth", 40) or 40)
    except (ValueError, TypeError):
        max_depth = 40
    try:
        import uiautomation as auto
        root = auto.ControlFromHandle(hwnd)
    except Exception as e:
        raise RuntimeError(f"控件树读取失败：uiautomation 初始化异常（hwnd={hwnd}）：{e}")
    roi = t.get("roi")
    has_roi = isinstance(roi, list) and len(roi) == 4
    base_x = win["left"] + (roi[0] if has_roi else 0)
    base_y = win["top"] + (roi[1] if has_roi else 0)
    rl, rt, rw, rh = (roi[0], roi[1], roi[2], roi[3]) if has_roi else (0, 0, win["width"], win["height"])

    def _walk_collect(root_ctrl):
        """BFS 收集一轮控件（Name+BoundingRectangle），返回 [(x,y,w,h,name)]（相对读取区域）。
        带节点上限/时间/深度预算（config 可调，默认 5000 节点/20 秒/深度 40）：
        Chromium 页面内容在深层渲染子树，原 1500 节点/深度14 易截断只读到外壳；
        预算兜底仍保留，避免主监控线程被拖死。"""
        items = []
        deadline = time.time() + timeout_sec
        try:
            stack = []
            try:
                for ch in root_ctrl.GetChildren():
                    stack.append((ch, 1))
            except Exception:
                pass
            visited = 0
            while stack and visited < max_nodes and time.time() < deadline:
                ctrl, depth = stack.pop()
                visited += 1
                if depth < max_depth:
                    try:
                        for ch in ctrl.GetChildren():
                            stack.append((ch, depth + 1))
                    except Exception:
                        pass
                try:
                    name = ctrl.Name
                except Exception:
                    name = None
                if not name or not isinstance(name, str) or not name.strip():
                    # 表格单元格常无 Name，尝试 ValuePattern（文本型控件值）
                    try:
                        vp = ctrl.GetValuePattern()
                        v = vp.Value
                    except Exception:
                        v = None
                    if v and isinstance(v, str) and v.strip():
                        name = v.strip()
                    else:
                        continue
                try:
                    rect = ctrl.BoundingRectangle
                except Exception:
                    continue
                if rect is None:
                    continue
                try:
                    cx, cy = rect.left, rect.top
                    cw, chh = rect.right - rect.left, rect.bottom - rect.top
                except Exception:
                    continue
                if cw <= 0 or chh <= 0:
                    continue
                # 与读取区域（ROI 或全窗口）有交集才保留，坐标转为相对读取区域左上角
                if (cx + cw <= base_x + rl or cx >= base_x + rl + rw
                        or cy + chh <= base_y + rt or cy >= base_y + rt + rh):
                    continue
                items.append((cx - base_x - rl, cy - base_y - rt, cw, chh, name.strip()))
        except Exception:
            pass
        return items

    def _assemble(items):
        """收集结果组装 (text, words)：按 y 聚类成行、行内按 x 排序；name+近似y 去重"""
        seen = set()
        uniq = []
        for x, y, w, h, name in items:
            key = (name, round(y / 10))
            if key in seen:
                continue
            seen.add(key)
            uniq.append({"text": name, "x": int(x), "y": int(y), "w": int(w), "h": int(h)})
        uniq.sort(key=lambda it: (it["y"], it["x"]))
        rows = []
        for it in uniq:
            cy = it["y"] + it["h"] / 2.0
            placed = False
            for r in rows:
                if abs(r["cy"] - cy) <= 10:
                    r["items"].append(it)
                    r["cy"] = (r["cy"] + cy) / 2.0
                    placed = True
                    break
            if not placed:
                rows.append({"cy": cy, "items": [it]})
        for r in rows:
            r["items"].sort(key=lambda it: it["x"])
            r["text"] = " ".join(it["text"] for it in r["items"])
        rows.sort(key=lambda r: r["cy"])
        text = "\n".join(r["text"] for r in rows)
        return text, uniq

    try:
        items = _walk_collect(root)
        total = len(items)
        # v10.8.5：主窗口可能只读到浏览器外壳（页面内容在渲染子窗口），
        # 总是尝试定位 Chromium 渲染子窗口合并读取——仅当子窗口带来新增内容时采用合并，
        # 解决『主窗口控件较多(>10)不切子窗口 → 页面表格缺失 → no_header』问题
        try:
            sub_hwnd = _find_render_sub_hwnd(hwnd)
            if sub_hwnd and sub_hwnd != hwnd:
                sub_root = auto.ControlFromHandle(sub_hwnd)
                sub_items = _walk_collect(sub_root)
                if sub_items:
                    seen = {(it[4], round(it[1] / 10)) for it in items}
                    added = 0
                    for it in sub_items:
                        if (it[4], round(it[1] / 10)) not in seen:
                            items.append(it)
                            seen.add((it[4], round(it[1] / 10)))
                            added += 1
                    if added:
                        core.log(f"[模式二] 渲染子窗口 0x{sub_hwnd:X} 补充 {added} 个控件（主窗口 {len(items) - added} 个 → 合并 {len(items)} 个）")
        except Exception:
            pass
        if scroll:
            rounds = 0
            stable = 0
            while rounds < max_scroll:
                if not _uia_scroll_down(hwnd):
                    break
                rounds += 1
                time.sleep(0.6)
                more = _walk_collect(root)
                before = len(items)
                seen = {(it[4], round(it[1] / 10)) for it in items}
                for it in more:
                    if (it[4], round(it[1] / 10)) not in seen:
                        items.append(it)
                        seen.add((it[4], round(it[1] / 10)))
                if len(items) <= before:
                    stable += 1
                    if stable >= 2:  # 连续两次无新增即判定滚到底
                        break
                else:
                    stable = 0
            core.log(f"[模式二] 滚动收集完成：滚动 {rounds} 轮，控件 {total} → {len(items)} 个")
        text, words = _assemble(items)
        if not text:
            raise RuntimeError("控件树未读取到任何文本：窗口可能为空页面、accessibility tree 未暴露文本，"
                               "或控件不在 ROI 区域内（请检查 ROI/窗口，或切换回模式一截图OCR）")
        core.log(f"[模式二] 控件树读取成功：控件 {len(words)} 个，文本行 {len(text.splitlines())} 行"
                 f"{'（含滚动收集）' if scroll else ''}")
        return text, words
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"控件树读取失败：{e}")


def _cdp_read_text(core, target=None):
    """CDP 兜底：经浏览器本地调试端口读当前页 DOM 文本（模式二 UIA 无结果的补充通道）。

    仅读取浏览器本地渲染出的 DOM 文本，不访问 TikTok 服务器，符合
    『不从服务器获取数据』原则。需浏览器以 --remote-debugging-port=<port>
    启动（cdp_port 配置 >0 才启用）；端口未开/连接失败返回 None 不抛异常，
    调用方自行决定回退到 UIA 异常上报。返回 (text, words)：text=去重后按行
    拼接的页面可见文本，words=[]（DOM 文本无坐标，交由 parse_fields 宽松解析）。"""
    try:
        port = int(core.cfg.get("cdp_port", 0) or 0)
    except (ValueError, TypeError):
        port = 0
    if port <= 0:
        return None
    try:
        # 1. 枚举调试 target，选当前活动页面
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=3) as resp:
            targets = json.loads(resp.read().decode("utf-8", "replace"))
        page = None
        for tg in targets:
            if tg.get("type") == "page" and str(tg.get("url", "")).startswith(("http", "https")):
                page = tg
                break
        if not page:
            core.log(f"[模式二] CDP 兜底：端口 {port} 无活动页面 target")
            return None
        ws_url = page.get("webSocketDebuggerUrl")
        if not ws_url:
            return None
        # 2. WebSocket 执行 Runtime.evaluate 取 body.innerText
        import websocket
        ws = websocket.create_connection(ws_url, timeout=5)
        try:
            ws.send(json.dumps({
                "id": 1, "method": "Runtime.evaluate",
                "params": {"expression": "document.body ? document.body.innerText : ''",
                           "returnByValue": True}}))
            time.sleep(0.8)
            resp = json.loads(ws.recv())
            result = (resp.get("result") or {}).get("result") or {}
            txt = result.get("value") or ""
        finally:
            try:
                ws.close()
            except Exception:
                pass
        if not txt or not str(txt).strip():
            return None
        lines = []
        for ln in str(txt).splitlines():
            s = " ".join(ln.split())
            if s and s not in lines:
                lines.append(s)
        text = "\n".join(lines)
        core.log(f"[模式二] CDP 兜底读取成功：{len(lines)} 行文本（端口 {port}）")
        return text, []
    except Exception as e:
        core.log(f"[模式二] CDP 兜底不可用（端口 {port}）：{e}")
        return None


# ==================== v10.3 任务2：UIA 结构化表格读取（新模式主力） ====================
# 依据真实 UIA dump 实测结论（工作电脑 uia_dump.txt）：
# - 紫鸟一个店铺一个顶层窗口，窗口标题=店铺名（class=Chrome_WidgetWin_1，实测 'A445'/'A419'）
# - 广告计划表格是 DOM 级结构化控件：行 class='core-table-tr'、单元格 class='core-table-td'，
#   值在 DataItemControl name 直接给全（如 name='0.73'、name='25.84 USD'、
#   name='copy of Lilin pemulihan D 1.2 0821 2026 1341'）；表头也是 DataItemControl
#   name='ROI'/'成本'/'总收入'/'SKU 订单数'/'状态'/'广告计划预算'/'目标 ROI'/'排期时间' 等
# - 实测一行完整列：广告计划名称/ROI/成本/总收入/状态/预算(含日预算)/目标ROI/SKU订单数/
#   净成本/平均下单成本/ROI保护/排期时间

# 表头名 → 结构化字段映射（不写死列顺序，按表头 name 动态列映射）
_STRUCT_FIELDS = {
    "广告计划名称": "plan_name", "计划名称": "plan_name", "广告计划": "plan_name",
    "roi": "roi", "ROI": "roi",
    "目标 roi": "target_roi", "目标ROI": "target_roi", "目标 ROI": "target_roi",
    "成本": "cost", "总成本": "cost",
    "总收入": "revenue", "收入": "revenue",
    "状态": "status",
    "广告计划预算": "budget", "日预算": "budget", "预算": "budget",
    "sku 订单数": "orders", "sku订单数": "orders", "订单数": "orders",
    "排期时间": "schedule", "平均下单成本": "avg_cost",
    "roi 保护": "roi_protection", "roi保护": "roi_protection", "净成本": "net_cost",
}
# 表头行识别提示词（命中这些词的行视为表头行候选）
_HEADER_HINTS = ("roi", "成本", "总收入", "sku 订单数", "广告计划名称", "状态", "预算", "目标")


_CELL_NUM_RE = re.compile(r"[-+]?\d+(?:\.\d+)?")


def _to_float_cell(v):
    """单元格文本转 float：去货币符号/逗号/USD/百分比后缀；整体解析失败时
    提取文本中第一个十进制数（容忍 'ROI:1.1' / 'e/ROI:1.1' / 'p/:18.91' 等
    带标签前缀的 UIA cell 文本，v10.7.8）；仍无法解析返回 None。"""
    if v is None:
        return None
    s = str(v).strip().replace(",", "").replace("$", "").replace("USD", "")
    s = s.replace("￥", "").replace("¥", "").replace("%", "").strip()
    if not s or s in ("-", "--", "—", "null", "None", "N/A", "na"):
        return None
    try:
        return float(s)
    except ValueError:
        pass
    m = _CELL_NUM_RE.search(s)
    if m:
        try:
            return float(m.group(0))
        except ValueError:
            return None
    return None


def _int_cell(v):
    f = _to_float_cell(v)
    return int(round(f)) if f is not None else None


def _find_hwnd_by_title(title, class_hint="Chrome_WidgetWin_1"):
    """按顶层窗口标题精确匹配店铺窗口（紫鸟每店一窗口，标题=店铺名）。

    店铺名取自窗口标题，不再依赖手动窗口关键词配置；找不到返回 None。"""
    if not title:
        return None
    user32 = ctypes.windll.user32
    found = [None]

    def _title_of(h):
        buf = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(h, buf, 512)
        return buf.value or ""

    def _class_of(h):
        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(h, buf, 256)
        return buf.value or ""

    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        cls = _class_of(hwnd)
        if class_hint and class_hint not in cls:
            return True
        if _title_of(hwnd).strip() == title:
            found[0] = hwnd
            return False
        return True

    user32.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND,
                                          ctypes.wintypes.LPARAM)(cb), 0)
    return found[0]


def _collect_ctrl_items(root, max_nodes=30000, timeout_sec=25.0):
    """BFS 收集控件树，返回 [(ctrl, depth)]；带节点/时间预算防拖死主监控线程。"""
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


def _ctrl_class(ctrl):
    try:
        return str(ctrl.ClassName or "")
    except Exception:
        return ""


def _ctrl_name(ctrl):
    """控件文本：优先 Name，Name 空时尝试 ValuePattern（表格单元格常见）。"""
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


def _uia_structured_read(core, target=None):
    """v10.3 任务2：UIA 结构化表格读取（新模式主力，替代文本解析为主力）。

    流程：按顶层窗口标题匹配目标店铺 → 收集 core-table-tr 行 → 识别表头行
    （DataItemControl name 命中表头词集合）建立"表头名→列"映射 → 每行按列
    中心 x 就近取值 → 产出结构化字段列表。

    返回 list[dict]：{plan_name, roi, cost, revenue, orders, status, budget,
    target_roi, net_cost, schedule, avg_cost, roi_protection, row_no, source};
    找不到表格/解析失败返回 []（调用方回退文本解析路径）。
    """
    t = target if target is not None else core._current_target()
    # 1. 窗口定位：店铺名=顶层窗口标题（紫鸟实测）；锁定/关键词为兜底
    shop = str(t.get("shop") or t.get("name") or "").strip()
    hwnd = _find_hwnd_by_title(shop) if shop else None
    if not hwnd:
        hwnd = core._resolve_hwnd(t)
    if not hwnd:
        return []
    # v10.4 修复：日志店铺优先显示实际窗口标题（匹配/解析到的 hwnd 标题），取不到再回退配置
    try:
        shop_disp = _title_of(hwnd).strip() or shop
    except Exception:
        shop_disp = shop
    try:
        wait = float(core.cfg.get("uia_wait_sec", 1.5) or 0)
    except (ValueError, TypeError):
        wait = 1.5
    if wait > 0:
        time.sleep(min(wait, 5.0))  # accessibility tree 构建延迟
    try:
        import uiautomation as auto
        root = auto.ControlFromHandle(hwnd)
    except Exception as e:
        core.log(f"[模式二-结构化] uiautomation 初始化异常（hwnd={hwnd}）：{e}")
        return []
    try:
        max_nodes = int(core.cfg.get("uia_max_nodes", 5000) or 5000) * 4
    except (ValueError, TypeError):
        max_nodes = 20000
    ctrls = _collect_ctrl_items(root, max_nodes=max_nodes)
    rows_ctrls = [c for c, _d in ctrls if "core-table-tr" in _ctrl_class(c).lower()]
    # v10.8.5：主窗口树未读到表格时，尝试渲染子窗口（页面内容常在其子树）再找一次，
    # 避免主窗口只暴露浏览器外壳（如内存压力/后台标签）时误判为"非列表页"
    if not rows_ctrls:
        try:
            sub_hwnd = _find_render_sub_hwnd(hwnd)
            if sub_hwnd and sub_hwnd != hwnd:
                sub_root = auto.ControlFromHandle(sub_hwnd)
                sub_ctrls = _collect_ctrl_items(sub_root, max_nodes=max_nodes)
                sub_rows = [c for c, _d in sub_ctrls if "core-table-tr" in _ctrl_class(c).lower()]
                if sub_rows:
                    core.log(f"[模式二-结构化] 主窗口未找到表格，切换到渲染子窗口 0x{sub_hwnd:X} 读取：{len(sub_rows)} 行")
                    ctrls = sub_ctrls
                    rows_ctrls = sub_rows
        except Exception:
            pass
    if not rows_ctrls:
        # 标签定位诊断：非广告计划列表页时提示（TabItem URL / 页面 tab 特征），调用方回退文本解析
        tabs = []
        for c, _d in ctrls:
            cls = _ctrl_class(c).lower()
            if cls in ("tab",) or "arco-tabs-header-title" in cls:
                nm = _ctrl_name(c)
                if nm:
                    tabs.append(nm)
        if tabs:
            core.log("[模式二-结构化] 当前窗口未找到广告计划表格（core-table-tr）。检测到标签："
                     + " | ".join(tabs[:6]) + "（若当前非广告计划列表页，请切换页面后重试）")
        else:
            core.log("[模式二-结构化] 当前窗口未找到广告计划表格（core-table-tr），回退文本解析")
        return []

    def row_values(row_ctrl):
        """行内所有单元格值：取后代控件的 name，按 (x, y) 排序返回 [(x,y,rx,ry,nm)]。"""
        vals = []
        stack = [(row_ctrl, 0)]
        while stack:
            c, d = stack.pop()
            nm = _ctrl_name(c)
            rect = None
            try:
                rect = c.BoundingRectangle
            except Exception:
                rect = None
            if nm and rect is not None:
                try:
                    vals.append((rect.left, rect.top, rect.right, rect.bottom, nm))
                except Exception:
                    pass
            if d < 8:
                try:
                    for ch in c.GetChildren():
                        stack.append((ch, d + 1))
                except Exception:
                    pass
        # 去重：父子同名/同列重复只保留一个（避免表头行/滚动重复）
        # v10.4 修复：容差从 /8 加大到 /24，滤掉表头单元格父子控件（DataItemControl 与
        # 子文本控件同值同列）重复，避免列映射出现同一字段两个 x（plan_name@632,plan_name@559）
        uniq = []
        seen = set()
        for x, y, rx, ry, nm in sorted(vals, key=lambda v: (v[0], v[1])):
            key = (round(x / 24), nm)
            if key in seen:
                continue
            seen.add(key)
            uniq.append((x, y, rx, ry, nm))
        return uniq

    def normalize_header(name):
        """表头名 → 字段名；非表头返回 None。

        精确匹配优先，再按 key 长度降序做 startswith，避免前缀吞并
        （'广告计划' 吞 '广告计划预算'、'ROI' 吞 'ROI 保护'）。"""
        s = str(name).strip().lower()
        for k in sorted(_STRUCT_FIELDS, key=len, reverse=True):
            fld = _STRUCT_FIELDS[k]
            if k.lower() == s or s.startswith(k.lower()):
                return fld
        return None

    # 2. 表头行识别与列映射（命中表头词最多的行视为表头行）
    # v10.4 修复：header_cols 按字段去重（同一 field 只保留第一个 x_center），
    # 配合 row_values 容差加大，确保列映射每字段唯一（实机出现 plan_name@632,plan_name@559 双 x）
    header_cols = []   # [(x_center, field)]
    best_hits = 0
    header_row = None
    for rc in rows_ctrls:
        mapping = []
        seen_fields = set()
        for x, y, rx, ry, nm in row_values(rc):
            fld = normalize_header(nm)
            if fld and fld not in seen_fields:
                seen_fields.add(fld)
                mapping.append(((x + rx) / 2.0, fld))
        if len(mapping) > best_hits:
            best_hits = len(mapping)
            header_cols = mapping
            header_row = rc
    if best_hits < 2:
        core.log(f"[模式二-结构化] 表头行未识别（命中 {best_hits} 列 < 2），回退文本解析")
        return []

    def cell_for(vals, cx):
        """按列中心 x 就近匹配单元格文本（容差 60px）。

        取 x 最近候选：父级 DataItemControl name 通常信息最完整
        （预算列 '300.00 USD 日预算：300.00 USD'、计划名列含按钮拼接）。"""
        cands = []
        for x, y, rx, ry, nm in vals:
            ccx = (x + rx) / 2.0
            if abs(ccx - cx) < 60:
                cands.append((abs(ccx - cx), nm))
        if not cands:
            return ""
        return min(cands, key=lambda t: (t[0], len(t[1])))[1]

    def clean_plan_name(s):
        """剔除计划名尾部拼接的按钮词（数据分析/修改/优化，来自父级 DataItemControl name）。"""
        s = str(s or "").strip()
        while True:
            m = re.search(r"\s*(?:数据分析|修改|优化)\s*$", s)
            if not m:
                break
            s = s[:m.start()].strip()
        return s

    # 3. 数据行解析：逐行按列映射取值，按 plan_name 去重
    rows_out = []
    seen_plans = set()
    for i, rc in enumerate(rows_ctrls):
        if rc is header_row:
            continue
        vals = row_values(rc)
        if not vals:
            continue
        rec = {"source": "structured"}
        for cx, fld in header_cols:
            v = cell_for(vals, cx)
            if fld == "plan_name":
                rec["plan_name"] = clean_plan_name(v)
            elif fld == "roi":
                rec["roi"] = _to_float_cell(v)
                rec["roi_raw"] = v
            elif fld == "cost":
                rec["cost"] = _to_float_cell(v)
                rec["cost_raw"] = v
            elif fld == "revenue":
                rec["revenue"] = _to_float_cell(v)
                rec["revenue_raw"] = v
            elif fld == "orders":
                rec["orders"] = _int_cell(v)
            elif fld == "status":
                rec["status"] = v
            elif fld == "budget":
                rec["budget"] = v
            elif fld == "target_roi":
                rec["target_roi"] = _to_float_cell(v)
            elif fld == "net_cost":
                rec["net_cost"] = _to_float_cell(v)
            elif fld == "schedule":
                rec["schedule"] = v
            elif fld == "avg_cost":
                rec["avg_cost"] = _to_float_cell(v)
            elif fld == "roi_protection":
                rec["roi_protection"] = v
        plan = str(rec.get("plan_name") or "").strip()
        if plan:
            if plan in seen_plans:
                continue
            seen_plans.add(plan)
        if rec.get("roi") is None and rec.get("cost") is None and rec.get("revenue") is None:
            continue
        rec["row_no"] = i + 1
        rows_out.append(rec)
    if not rows_out:
        core.log("[模式二-结构化] 已定位表格但无有效数据行，回退文本解析")
        return []
    core.log(f"[模式二-结构化] 读取成功：店铺={shop_disp or '-'}，{len(rows_out)} 行，列映射="
             + ",".join(f"{fld}@{int(cx)}" for cx, fld in header_cols))
    return rows_out


def _structured_summary(rows):
    """结构化行 → 可读摘要文本（供日志/兜底展示）。"""
    lines = []
    for i, r in enumerate(rows):
        lines.append(
            f"第{i + 1}行 | {r.get('plan_name') or '-'} | ROI={r.get('roi')} "
            f"| 成本={r.get('cost')} | 收入={r.get('revenue')} | 订单={r.get('orders')} "
            f"| 状态={r.get('status')} | 预算={r.get('budget')}"
        )
    return "\n".join(lines)


# ==================== v10.7.3 路线C：UIA 控件操作（控件探测 / 筛选刷新） ====================
# 背景：路线B（PostMessage 直发 F5/Ctrl+R）实测失败（错误47）——消息投递成功但 Chromium 过滤
# 后台伪造按键、页面不刷新。路线C 改为纯控件级 UIA 操作模拟手动刷新流程：
#   点『状态』筛选框 → 勾选『已生效』 → 点『应用』
# 全程只调用 UIA Pattern（ExpandCollapse/Invoke/Toggle/SelectionItem/LegacyIAccessible），
# 不模拟键盘鼠标（不用 SendInput/Click）、不抢焦点、不依赖前台窗口。
# 控件定位策略：优先按 Name 匹配（"状态"/"已生效"/"应用"）+ ControlType 过滤；
# 下拉展开后的选项可能在同一控件树内（arco 组件）也可能在新顶层窗口（弹层），
# 先遍历目标窗口子树，未命中再扩展枚举 Chromium 顶层窗口。控件找不到时返回明确失败原因。

# 控件探测关注的可操作控件类型（ControlTypeName 子串）
_INTEREST_CTRL_TYPES = ("Button", "ComboBox", "CheckBox", "Text", "Edit", "ListItem",
                        "DataItem", "MenuItem", "TreeItem", "TabItem", "RadioButton", "Hyperlink")


def _ctrl_type(ctrl):
    try:
        return str(ctrl.ControlTypeName or "")
    except Exception:
        return ""


def _ctrl_desc(ctrl):
    """控件摘要：ControlType|Name|AutomationId（供日志展示定位依据）。"""
    parts = [_ctrl_type(ctrl) or "?"]
    nm = _ctrl_name(ctrl)
    if nm:
        parts.append(f"Name='{nm}'")
    try:
        aid = str(ctrl.AutomationId or "")
        if aid:
            parts.append(f"AutomationId='{aid}'")
    except Exception:
        pass
    return "|".join(parts)


# v10.7.5：浏览器 chrome UI 特征——定位页面控件（筛选框/表格/按钮）时必须排除浏览器自身 UI，
# 避免把「标签页重新变为活动状态」（ButtonControl，AutomationId=view_1011 附近）误当页面筛选框。
_BROWSER_UI_AID_PREFIXES = ("view_",)  # Chromium 外壳控件 AutomationId 前缀
_BROWSER_UI_NAMES = (
    "地址和搜索栏", "标签页分组", "跨境导航", "标签页重新变为活动状态",
    "标签页", "刷新", "书签", "扩展", "菜单", "最小化", "最大化", "关闭",
    "重新加载", "新建标签页", "窗口控件", "页面操作",
)


def _is_browser_chrome_ui(ctrl):
    """判断控件是否属于浏览器 chrome UI（非页面内容）。

    命中任一特征即判定为浏览器 UI：AutomationId 以 view_ 开头；Name 命中浏览器
    功能名集合（地址栏/标签页/导航/窗口控制等）。页面内容控件不受影响。"""
    try:
        aid = str(ctrl.AutomationId or "")
    except Exception:
        aid = ""
    if aid.startswith(_BROWSER_UI_AID_PREFIXES):
        return True
    nm = _ctrl_name(ctrl)
    if nm:
        for kw in _BROWSER_UI_NAMES:
            if kw in nm:
                return True
    return False


def _collect_page_ctrls(hwnd, auto, exclude_browser_ui=True):
    """收集目标窗口页面内容控件（v10.7.5）。

    优先从渲染子窗口（Chrome_RenderWidgetHostHWND）子树收集（页面 DOM 渲染结果所在），
    主窗口外壳兜底合并；按 (类型, Name, AutomationId, 坐标粗格) 去重，避免同一控件
    在主子窗口树重复出现。默认过滤浏览器 chrome UI。返回 [(ctrl, depth)]。"""
    items = []
    seen_keys = set()

    def _add(ctrl_items):
        for c, d in ctrl_items:
            if exclude_browser_ui and _is_browser_chrome_ui(c):
                continue
            key = (_ctrl_type(c), _ctrl_name(c))
            try:
                aid = str(c.AutomationId or "")
            except Exception:
                aid = ""
            rect = None
            try:
                rect = c.BoundingRectangle
            except Exception:
                rect = None
            if rect is not None:
                try:
                    key += (aid, int(rect.left // 8), int(rect.top // 8))
                except Exception:
                    key += (aid, 0, 0)
            else:
                key += (aid, 0, 0)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            items.append((c, d))

    try:
        sub = _find_render_sub_hwnd(hwnd)
        if sub and sub != hwnd:
            _add(_collect_ctrl_items(auto.ControlFromHandle(sub)))
    except Exception:
        pass
    try:
        _add(_collect_ctrl_items(auto.ControlFromHandle(hwnd)))
    except Exception:
        pass
    return items


def _mouse_click_ctrl(ctrl):
    """鼠标点击控件中心（SetCursorPos + mouse_event 左键单击）。

    仅作为 Pattern 全部不可用时的降级手段（配置 mouse_click_fallback=true 才启用），
    会短暂移动鼠标并可能抢焦点，故默认关闭。返回 (ok, 说明)。"""
    try:
        rect = ctrl.BoundingRectangle
        cx = int((rect.left + rect.right) / 2)
        cy = int((rect.top + rect.bottom) / 2)
    except Exception:
        return False
    try:
        user32 = ctypes.windll.user32
        user32.SetCursorPos(cx, cy)
        time.sleep(0.05)
        user32.mouse_event(0x0002, 0, 0, 0, 0)  # MOUSEEVENTF_LEFTDOWN
        time.sleep(0.03)
        user32.mouse_event(0x0004, 0, 0, 0, 0)  # MOUSEEVENTF_LEFTUP
        return True
    except Exception:
        return False


def _resolve_probe_hwnd(core, t):
    """定位控件操作目标窗口：按店铺名（顶层窗口标题）精确匹配优先（紫鸟每店一窗口），
    兜底 _resolve_hwnd（锁定句柄→关键词匹配）。返回 (hwnd, 店铺显示名)。"""
    shop = str(t.get("shop") or t.get("name") or "").strip()
    hwnd = _find_hwnd_by_title(shop) if shop else None
    shop_disp = shop
    if not hwnd:
        hwnd = core._resolve_hwnd(t)
    if hwnd:
        try:
            buf = ctypes.create_unicode_buffer(512)
            ctypes.windll.user32.GetWindowTextW(hwnd, buf, 512)
            shop_disp = buf.value.strip() or shop
        except Exception:
            pass
    return hwnd, shop_disp


def _iter_top_chromium_windows():
    """枚举可见顶层 Chromium 窗口（class 含 Chrome），返回 [(hwnd, title, class)]。
    弹层（下拉浮层）可能开在新顶层窗口，控件树遍历需覆盖。"""
    user32 = ctypes.windll.user32
    out = []

    def _title_of(h):
        buf = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(h, buf, 512)
        return buf.value or ""

    def _class_of(h):
        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(h, buf, 256)
        return buf.value or ""

    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        cls = _class_of(hwnd)
        if "Chrome" in cls:
            out.append((hwnd, _title_of(hwnd).strip(), cls))
        return True

    user32.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND,
                                          ctypes.wintypes.LPARAM)(cb), 0)
    return out


def _find_status_ctrl(ctrls, exclude_browser_ui=True):
    """找『状态』筛选框：Name 含『状态』，ControlType 优先级 ComboBox>Button>Edit>Text>DataItem>其他。

    v10.7.5：默认排除浏览器 chrome UI（AutomationId 以 view_ 开头 / Name 为浏览器功能名），
    避免误把浏览器「标签页重新变为活动状态」等外壳按钮当页面筛选框。"""
    pref = ["ComboBox", "Button", "Edit", "Text", "DataItem"]
    best, best_rank, best_how = None, 99, ""
    for c, _d in ctrls:
        if exclude_browser_ui and _is_browser_chrome_ui(c):
            continue
        nm = _ctrl_name(c)
        if not nm or "状态" not in nm:
            continue
        ct = _ctrl_type(c)
        rank = next((i for i, p in enumerate(pref) if p in ct), len(pref))
        if rank < best_rank:
            best_rank, best, best_how = rank, c, f"Name含『状态』+{ct}"
    return best, best_how


def _ctrl_rect_of(c):
    """控件矩形 (left, top, right, bottom)，异常返回 None。"""
    try:
        r = c.BoundingRectangle
        return (r.left, r.top, r.right, r.bottom)
    except Exception:
        return None


def _option_in_dropdown_zone(c, anchor_rect, tol_x=250.0, tol_y=500.0):
    """粗略判定候选是否位于筛选框下拉弹层区域：选项中心在 anchor 下方且水平偏差较小。

    下拉弹层通常紧贴筛选框展开（y 略大于 anchor.bottom），表格整列状态文本虽同列但遍布
    更大范围——本约束用于优先候选排序，仅排除明显越界的候选（如弹层上方/远端表格行），
    不绝对过滤，避免误杀真实选项。anchor_rect=None 时不约束。"""
    if anchor_rect is None:
        return True
    cc = _ctrl_rect_of(c)
    if cc is None:
        return True
    cx, cy = (cc[0] + cc[2]) / 2.0, (cc[1] + cc[3]) / 2.0
    ax = (anchor_rect[0] + anchor_rect[2]) / 2.0
    ay = anchor_rect[3]
    return cy >= ay - 20 and abs(cx - ax) <= tol_x and cy <= ay + tol_y


def _dump_candidates(ctrls, keyword, exclude_browser_ui=True, limit=8):
    """收集 Name 含 keyword 的控件诊断描述（type|Name|AutomationId|矩形），供失败日志输出候选列表。

    v10.7.9：筛选刷新任一步骤找不到目标控件时，输出该关键词命中的候选清单，
    让下次实测能一眼看出『页面无此控件』还是『控件存在但被过滤/类型不符』。"""
    out = []
    for c, _d in ctrls:
        if exclude_browser_ui and _is_browser_chrome_ui(c):
            continue
        nm = _ctrl_name(c)
        if not nm or keyword not in nm:
            continue
        r = _ctrl_rect_of(c)
        rect_s = f"({int(r[0])},{int(r[1])} {int(r[2] - r[0])}x{int(r[3] - r[1])})" if r else "(无矩形)"
        out.append(f"{_ctrl_desc(c)} | {rect_s}")
        if len(out) >= limit:
            break
    return out


def _find_option_ctrl(ctrls, keyword, exclude_browser_ui=True, anchor_rect=None):
    """找可勾选/选中选项（如『已生效』）：Name 含 keyword，ControlType 优先级
    CheckBox>ListItem>DataItem>MenuItem>TreeItem>Text>Button。
    v10.7.5：默认排除浏览器 chrome UI。
    v10.7.9：①精确名优先——Name==keyword 的候选排在包含匹配之前，避免表格状态列
    文本『已生效』（DataItem/Text）被优先当作下拉选项；②anchor_rect 传入状态筛选框
    矩形时仅保留下拉弹层区域候选（见 _option_in_dropdown_zone），规避整列状态文本。"""
    pref = ["CheckBox", "ListItem", "DataItem", "MenuItem", "TreeItem", "Text", "Button"]
    best, best_rank, best_how = None, 99, ""
    for c, _d in ctrls:
        if exclude_browser_ui and _is_browser_chrome_ui(c):
            continue
        nm = _ctrl_name(c)
        if not nm:
            continue
        ct = _ctrl_type(c)
        rank = next((i for i, p in enumerate(pref) if p in ct), len(pref))
        exact = (nm == keyword)
        if exact:
            rank -= 0.5  # 精确名优先于包含匹配（v10.7.9）
        elif keyword not in nm:
            continue
        if anchor_rect is not None and not _option_in_dropdown_zone(c, anchor_rect):
            continue
        if rank < best_rank:
            best_rank, best, best_how = rank, c, f"Name{'精确' if exact else '含'}『{keyword}』+{ct}"
    return best, best_how


def _find_apply_ctrl(ctrls, exclude_browser_ui=True):
    """找『应用』按钮：Name 精确『应用』优先，其次『应用』前缀且类型 Button/Text。
    v10.7.5：默认排除浏览器 chrome UI。"""
    best, best_how = None, ""
    for c, _d in ctrls:
        if exclude_browser_ui and _is_browser_chrome_ui(c):
            continue
        nm = _ctrl_name(c).strip()
        if nm == "应用":
            ct = _ctrl_type(c)
            if best is None or "Button" in ct:
                best, best_how = c, f"Name精确『应用』+{ct}"
        elif nm.startswith("应用"):
            ct = _ctrl_type(c)
            if "Button" in ct and best is None:
                best, best_how = c, f"Name前缀『应用』+{ct}"
    return best, best_how


def _find_option_in_top_windows(keyword, exclude_hwnd=None, exclude_titles=()):
    """在 Chromium 顶层窗口（含子树）中找含 keyword 的可勾选控件（弹层可能开在新顶层窗口）。
    v10.7.5：同样排除浏览器 chrome UI。
    v10.7.9：增加 exclude_hwnd（排除目标主窗口本身，避免把主窗口内表格行状态文本当选项）
    与 exclude_titles（排除其他店铺标题窗口，避免跨窗口误选）。"""
    for hwnd, ttl, cls in _iter_top_chromium_windows():
        if exclude_hwnd is not None and hwnd == exclude_hwnd:
            continue
        if ttl and any(t and t in ttl for t in exclude_titles):
            continue
        try:
            import uiautomation as auto
            root = auto.ControlFromHandle(hwnd)
            opt, how = _find_option_ctrl(_collect_ctrl_items(root), keyword)
            if opt is not None:
                return opt, f"顶层窗口0x{hwnd & 0xFFFFFFFFFFFFFFFF:X}({cls}, 标题='{ttl}'): {how}"
        except Exception:
            continue
    return None, ""


def _find_apply_in_top_windows():
    """在 Chromium 顶层窗口（含子树）中找『应用』按钮（弹层可能开在新顶层窗口）。
    v10.7.5：同样排除浏览器 chrome UI。"""
    for hwnd, _t, cls in _iter_top_chromium_windows():
        try:
            import uiautomation as auto
            root = auto.ControlFromHandle(hwnd)
            btn, how = _find_apply_ctrl(_collect_ctrl_items(root))
            if btn is not None:
                return btn, f"顶层窗口0x{hwnd & 0xFFFFFFFFFFFFFFFF:X}({cls}): {how}"
        except Exception:
            continue
    return None, ""


def _expand_ctrl(ctrl):
    """展开控件（下拉筛选框）：优先 ExpandCollapsePattern.Expand()，其次 InvokePattern.Invoke()，
    再 LegacyIAccessiblePattern.DoDefaultAction()。返回 (ok, 说明)。纯 Pattern 操作不抢焦点。"""
    try:
        ctrl.GetExpandCollapsePattern().Expand()
        return True, "ExpandCollapsePattern.Expand()"
    except Exception:
        pass
    try:
        ctrl.GetInvokePattern().Invoke()
        return True, "InvokePattern.Invoke()"
    except Exception:
        pass
    try:
        ctrl.GetLegacyIAccessiblePattern().DoDefaultAction()
        return True, "LegacyIAccessiblePattern.DoDefaultAction()"
    except Exception:
        pass
    return False, "无可用展开 Pattern（ExpandCollapse/Invoke/LegacyIAccessible 均不可用）"


def _select_option(ctrl, use_mouse=False):
    """勾选/选中选项（『已生效』）：CheckBox→TogglePattern（Off 才 Toggle）；ListItem/DataItem/
    MenuItem/TreeItem→SelectionItemPattern.Select()（未选中才 Select）；
    v10.7.5：DataItem 等再补 LegacyIAccessiblePattern.DoDefaultAction() 兜底（DataItem 常见可选项）；
    全部 Pattern 不可用且 use_mouse=True 时鼠标点击控件中心降级（会移动鼠标/抢焦点，须配置开关）。
    返回 (ok, 说明)。"""
    ct = _ctrl_type(ctrl)
    if "CheckBox" in ct:
        try:
            tp = ctrl.GetTogglePattern()
            state = int(tp.ToggleState)
            if state == 1:
                return True, f"CheckBox 已处于勾选状态（ToggleState=On），跳过"
            tp.Toggle()
            return True, f"CheckBox TogglePattern.Toggle()（原 ToggleState={state}）"
        except Exception:
            pass
    if any(p in ct for p in ("ListItem", "DataItem", "MenuItem", "TreeItem")):
        try:
            sip = ctrl.GetSelectionItemPattern()
            if sip.IsSelected:
                return True, f"{ct} 已处于选中状态，跳过"
            sip.Select()
            return True, f"{ct} SelectionItemPattern.Select()"
        except Exception:
            pass
        # v10.7.5：DataItem 等常见支持 LegacyIAccessible 默认动作（等价点击/选中）
        try:
            la = ctrl.GetLegacyIAccessiblePattern()
            try:
                la.DoDefaultAction()
                return True, f"{ct} LegacyIAccessiblePattern.DoDefaultAction()（v10.7.5 兜底）"
            except Exception:
                pass
            try:
                if hasattr(la, "Select"):
                    la.Select()
                    return True, f"{ct} LegacyIAccessiblePattern.Select()（v10.7.5 兜底）"
            except Exception:
                pass
        except Exception:
            pass
    try:
        ctrl.GetInvokePattern().Invoke()
        return True, f"{ct} InvokePattern.Invoke()（兜底）"
    except Exception:
        pass
    if use_mouse:
        ok = _mouse_click_ctrl(ctrl)
        return ok, f"{ct} 鼠标点击降级（mouse_click_fallback 开启，已移动鼠标点击控件中心）"
    return False, f"{ct} 无可用勾选/选中 Pattern（Toggle/SelectionItem/Invoke/LegacyIAccessible 均不可用）"


def _invoke_ctrl(ctrl, use_mouse=False):
    """点击控件（『应用』按钮）：优先 InvokePattern.Invoke()，其次 LegacyIAccessiblePattern.
    DoDefaultAction()；v10.7.5：全部 Pattern 不可用且 use_mouse=True 时鼠标点击降级。
    返回 (ok, 说明)。"""
    try:
        ctrl.GetInvokePattern().Invoke()
        return True, "InvokePattern.Invoke()"
    except Exception:
        pass
    try:
        ctrl.GetLegacyIAccessiblePattern().DoDefaultAction()
        return True, "LegacyIAccessiblePattern.DoDefaultAction()"
    except Exception:
        pass
    if use_mouse:
        ok = _mouse_click_ctrl(ctrl)
        return ok, "鼠标点击降级（mouse_click_fallback 开启，已移动鼠标点击控件中心）"
    return False, "无可用点击 Pattern（Invoke/LegacyIAccessible 均不可用）"


def _uia_ctrl_probe(core, target=None):
    """v10.7.3 路线C 能力①：控件探测——遍历目标窗口 accessibility tree，dump 所有可操作控件
    （Button/ComboBox/CheckBox/Text/Edit/ListItem 等）的 ControlType+Name+AutomationId+矩形。

    v10.7.5：遍历范围覆盖主窗口 + 渲染子窗口（Chrome_RenderWidgetHostHWND，页面 DOM 所在子树，
    对照 _uia_read_text 能读到 core-table-tr 的下钻方式），合并去重；每条记录标注 zone
    （page_content / browser_chrome，依渲染子树来源 + AutomationId view_ 前缀粗分）；完整清单
    写入 temp 目录 uia_probe_YYYYMMDD_HHMM.txt 方便回传分析，日志只显示统计摘要。

    返回 [{"type","name","automation_id","left","top","width","height","zone"}]；
    未定位窗口/初始化失败返回 None（原因写日志）。"""
    t = target if target is not None else core._current_target()
    name = t.get("name") or f"目标{t.get('id')}"
    hwnd, shop_disp = _resolve_probe_hwnd(core, t)
    if not hwnd:
        core.log(f"[{name}] [控件探测] 未定位到目标窗口，探测失败（请检查窗口锁定/关键词匹配）", level="debug")
        return None
    core.log(f"[{name}] [控件探测] 目标窗口 hwnd=0x{hwnd & 0xFFFFFFFFFFFFFFFF:X} 店铺={shop_disp or '-'} 开始遍历控件树（主窗口+渲染子窗口）")
    try:
        wait = float(core.cfg.get("uia_wait_sec", 1.5) or 0)
    except (ValueError, TypeError):
        wait = 1.5
    if wait > 0:
        time.sleep(min(wait, 5.0))  # accessibility tree 构建延迟
    try:
        import uiautomation as auto
    except Exception as e:
        core.log(f"[{name}] [控件探测] uiautomation 初始化异常：{e}", level="debug")
        return None

    # v10.7.5：渲染子窗口（页面内容）优先，主窗口外壳兜底；zone 按来源粗分
    roots = []
    try:
        sub = _find_render_sub_hwnd(hwnd)
        if sub and sub != hwnd:
            try:
                roots.append((auto.ControlFromHandle(sub), "page_content"))
            except Exception:
                pass
    except Exception:
        pass
    try:
        roots.append((auto.ControlFromHandle(hwnd), "browser_chrome"))
    except Exception as e:
        core.log(f"[{name}] [控件探测] uiautomation 初始化异常（hwnd={hwnd}）：{e}", level="debug")
        return None

    hits = []
    seen_keys = set()
    for root_ctrl, root_zone in roots:
        for c, _d in _collect_ctrl_items(root_ctrl):
            ct = _ctrl_type(c)
            if not any(p in ct for p in _INTEREST_CTRL_TYPES):
                continue
            nm = _ctrl_name(c)
            aid = ""
            try:
                aid = str(c.AutomationId or "")
            except Exception:
                aid = ""
            rect = None
            try:
                rect = c.BoundingRectangle
            except Exception:
                rect = None
            if rect is None:
                continue
            try:
                l, tp = rect.left, rect.top
                w, h = rect.right - rect.left, rect.bottom - rect.top
            except Exception:
                continue
            if w <= 0 or h <= 0:
                continue
            # v10.7.5：zone 判定——渲染子树来源优先，AutomationId view_ 前缀强制归浏览器 UI
            if root_zone == "page_content" and not _is_browser_chrome_ui(c):
                zone = "page_content"
            else:
                zone = "browser_chrome"
            key = (zone, ct, nm, aid, int(l // 8), int(tp // 8))
            if key in seen_keys:
                continue
            seen_keys.add(key)
            hits.append({"type": ct, "name": nm, "automation_id": aid, "zone": zone,
                         "left": int(l), "top": int(tp), "width": int(w), "height": int(h)})
    hits.sort(key=lambda it: (it["zone"] != "page_content", it["top"], it["left"]))

    # v10.7.5：完整清单写入 temp 目录（文件名含时间戳），日志只显示统计摘要
    try:
        ts = time.strftime("%Y%m%d_%H%M")
        dump_path = os.path.join(tempfile.gettempdir(), f"uia_probe_{ts}.txt")
        with open(dump_path, "w", encoding="utf-8") as f:
            f.write(f"# uia_probe {ts} hwnd=0x{hwnd & 0xFFFFFFFFFFFFFFFF:X} shop={shop_disp or '-'}\n")
            f.write("# zone: page_content=页面内容（渲染子窗口子树） browser_chrome=浏览器UI\n")
            for i, it in enumerate(hits):
                rect_s = f"({it['left']},{it['top']} {it['width']}x{it['height']})"
                f.write(f"{i + 1:03d}. [{it['zone']}] {it['type']} | Name='{it['name']}' | "
                        f"AutomationId='{it['automation_id']}' | {rect_s}\n")
        page_n = sum(1 for it in hits if it["zone"] == "page_content")
        chrome_n = len(hits) - page_n
        core.log(f"[{name}] [控件探测] 共 {len(hits)} 个可操作控件（页面内容 {page_n} / 浏览器UI {chrome_n}），"
                 f"完整清单已写入：{dump_path}")
        core.log(f"[{name}] [控件探测] 页面内容控件 {page_n} 个（{dump_path} 中 zone=page_content）：")
        shown = 0
        for it in hits:
            if it["zone"] != "page_content":
                continue
            shown += 1
            if shown > 50:
                core.log(f"[控件探测] ... 其余 {page_n - 50} 个页面控件见文件")
                break
            rect_s = f"({it['left']},{it['top']} {it['width']}x{it['height']})"
            core.log(f"[控件探测] {shown:03d}. {it['type']} | Name='{it['name']}' | "
                     f"AutomationId='{it['automation_id']}' | {rect_s}")
    except Exception as e:
        core.log(f"[{name}] [控件探测] 清单写入失败：{e}", level="debug")
        for i, it in enumerate(hits):
            rect_s = f"({it['left']},{it['top']} {it['width']}x{it['height']})"
            core.log(f"[控件探测] {i + 1:03d}. [{it['zone']}] {it['type']} | Name='{it['name']}' | "
                     f"AutomationId='{it['automation_id']}' | {rect_s}")
    return hits


def _uia_filter_refresh(core, target=None):
    """v10.7.3 路线C 能力②：筛选刷新——执行"点『状态』筛选框 → 勾选『已生效』 → 点『应用』"
    流程（UIA 纯控件级操作，不抢焦点、不模拟键盘鼠标）。既刷新数据又只显示已生效计划。

    v10.7.5：①控件定位限定页面内容区（优先渲染子窗口子树 + 过滤浏览器 chrome UI，避免误匹配
    浏览器「标签页重新变为活动状态」等外壳按钮）；②任一步骤失败立即返回失败并中止后续步骤，
    日志标注「流程失败于第X步」（如勾选失败不再点『应用』）；③勾选增加
    LegacyIAccessiblePattern 兜底，全部 Pattern 不可用时按配置 mouse_click_fallback
    （默认 false）启用鼠标点击降级。

    返回 {"ok": bool, "steps": [每步结果字符串], "missing": [未找到/失败控件列表]}。
    控件找不到时返回明确失败原因，不抛异常（调用方优雅降级，跳过刷新继续识别）。"""
    steps = []
    missing = []
    t = target if target is not None else core._current_target()
    name = t.get("name") or f"目标{t.get('id')}"
    hwnd, shop_disp = _resolve_probe_hwnd(core, t)
    if not hwnd:
        steps.append("定位目标窗口失败（句柄失效或关键词匹配失败）")
        return {"ok": False, "steps": steps, "missing": ["目标窗口"]}
    core.log(f"[{name}] [筛选刷新] 目标窗口 hwnd=0x{hwnd & 0xFFFFFFFFFFFFFFFF:X} 店铺={shop_disp or '-'} "
             f"开始 UIA 筛选刷新（路线C：状态→已生效→应用，纯控件操作不抢焦点）")
    try:
        wait = float(core.cfg.get("uia_wait_sec", 1.5) or 0)
    except (ValueError, TypeError):
        wait = 1.5
    if wait > 0:
        time.sleep(min(wait, 5.0))
    try:
        import uiautomation as auto
    except Exception as e:
        steps.append(f"uiautomation 初始化异常（hwnd={hwnd}）：{e}")
        return {"ok": False, "steps": steps, "missing": ["uiautomation 初始化"]}
    try:
        use_mouse = bool(core.cfg.get("mouse_click_fallback", False))
    except Exception:
        use_mouse = False

    def _fail(step_no, reason):
        """任一步骤失败立即返回失败并中止后续步骤，日志标注『流程失败于第X步』。"""
        missing.append(reason)
        steps.append(f"流程失败于第{step_no}步：{reason}")
        for s in steps:
            core.log(f"[{name}] [筛选刷新] {s}")
        core.log(f"[{name}] [筛选刷新] 流程失败于第{step_no}步，中止后续步骤（缺失/失败项：{reason}）")
        return {"ok": False, "steps": steps, "missing": missing}

    # v10.7.5：页面内容区控件（渲染子窗口优先 + 过滤浏览器 UI），避免误匹配浏览器外壳
    ctrls = _collect_page_ctrls(hwnd, auto)

    # 第1步：找『状态』筛选框并展开
    status_ctrl, how = _find_status_ctrl(ctrls)
    if status_ctrl is None:
        cands_st = _dump_candidates(ctrls, "状态")
        steps.append("未找到『状态』筛选框（页面内容区遍历 Button/ComboBox/Edit/Text 控件，Name 含『状态』，已排除浏览器 UI）")
        if cands_st:
            steps.append(f"候选控件（Name 含『状态』，前 {len(cands_st)} 个，均因类型不符/被过滤未选用）：")
            steps.extend("  " + s for s in cands_st)
        else:
            steps.append("候选：页面内容区无任何 Name 含『状态』的控件（可能筛选框 Name 不含『状态』，或控件树未暴露/渲染子窗口定位失败）")
        return _fail(1, "状态筛选框未找到")
    status_rect = _ctrl_rect_of(status_ctrl)
    steps.append(f"找到状态筛选框：{_ctrl_desc(status_ctrl)}（匹配：{how}）")
    ok_expand, desc_expand = _expand_ctrl(status_ctrl)
    steps.append(f"展开状态筛选框：{'成功' if ok_expand else '失败'}（{desc_expand}）")
    if not ok_expand:
        steps.append(f"控件详情：{_ctrl_desc(status_ctrl)}（类型={_ctrl_type(status_ctrl)}，矩形={status_rect}）")
        return _fail(1, f"状态筛选框展开失败（{desc_expand}）")

    time.sleep(1.0)  # 等下拉开/选项渲染（arco 组件 accessibility tree 有延迟）
    # 第2步：勾选『已生效』（先重新收集页面内容区：弹层可能在同一控件树内或新顶层窗口）
    try:
        ctrls2 = _collect_page_ctrls(hwnd, auto)
    except Exception:
        ctrls2 = ctrls
    opt, opt_how = _find_option_ctrl(ctrls2, "已生效", anchor_rect=status_rect)
    if opt is None:
        # v10.7.9：顶层窗口扩展排除目标主窗口本身与其他店铺标题窗口（避免跨窗口/表格行误选）
        opt, opt_how = _find_option_in_top_windows("已生效",
                                                   exclude_hwnd=hwnd,
                                                   exclude_titles=(shop_disp,))
    if opt is None:
        cands_op = _dump_candidates(ctrls2, "已生效")
        steps.append("未找到『已生效』选项（展开后重遍历页面内容区[精确名优先+下拉区域约束] + Chromium 顶层窗口扩展[排除目标窗口/店铺窗口]均未命中）")
        if cands_op:
            steps.append(f"页面内容区候选（Name 含『已生效』，前 {len(cands_op)} 个，均不满足选项条件）：")
            steps.extend("  " + s for s in cands_op)
        else:
            steps.append("页面内容区候选：无任何 Name 含『已生效』的控件（可能下拉弹层未渲染/渲染在新顶层窗口/选项文本不同）")
        return _fail(2, "已生效选项未找到")
    steps.append(f"找到『已生效』选项：{_ctrl_desc(opt)}（匹配：{opt_how}）")
    ok_sel, desc_sel = _select_option(opt, use_mouse=use_mouse)
    steps.append(f"勾选『已生效』：{'成功' if ok_sel else '失败'}（{desc_sel}）")
    if not ok_sel:
        return _fail(2, f"勾选已生效失败（{desc_sel}）")
    if "DataItem" in _ctrl_type(opt) or "Text" in _ctrl_type(opt):
        # v10.7.9：DataItem/Text 类『已生效』可能命中表格状态列文本而非下拉选项，无法回读勾选状态
        steps.append("提示：UIA 无法回读勾选后的页面状态；若实测页面无变化，可能匹配到表格行状态文本而非下拉选项，"
                     "请回传控件探测清单（uia_probe_*.txt）或尝试 mouse_click_fallback=true")

    time.sleep(0.5)
    # 第3步：点『应用』
    btn, btn_how = _find_apply_ctrl(ctrls2)
    if btn is None:
        try:
            ctrls3 = _collect_page_ctrls(hwnd, auto)
            btn, btn_how = _find_apply_ctrl(ctrls3)
        except Exception:
            pass
    if btn is None:
        btn, btn_how = _find_apply_in_top_windows()
    if btn is None:
        cands_ap = _dump_candidates(ctrls2, "应用")
        steps.append("未找到『应用』按钮（重遍历页面内容区 + Chromium 顶层窗口扩展均未命中）")
        if cands_ap:
            steps.append(f"页面内容区候选（Name 含『应用』，前 {len(cands_ap)} 个，均未作为应用按钮选用）：")
            steps.extend("  " + s for s in cands_ap)
        else:
            steps.append("页面内容区候选：无任何 Name 含『应用』的控件（可能按钮文本为『确定』/图标按钮，或控件树未暴露）")
        return _fail(3, "应用按钮未找到")
    steps.append(f"找到应用按钮：{_ctrl_desc(btn)}（匹配：{btn_how}）")
    ok_apply, desc_apply = _invoke_ctrl(btn, use_mouse=use_mouse)
    steps.append(f"点击『应用』：{'成功' if ok_apply else '失败'}（{desc_apply}）")
    if not ok_apply:
        return _fail(3, f"应用按钮点击失败（{desc_apply}）")

    for s in steps:
        core.log(f"[{name}] [筛选刷新] {s}")
    core.log(f"[{name}] [筛选刷新] 流程全部完成：状态→已生效→应用 执行成功")
    return {"ok": True, "steps": steps, "missing": []}

# ==================== 识别器入口（注册给核心） ====================

def recognize_fn(core, target):
    """模式二识别入口（v10.3）：结构化表格读取优先 → 控件树文本 →（CDP 兜底）。

    返回 (text, words) 或 (text, words, structured_rows)：
    - structured_rows 非空（list[dict]）时，核心走结构化管线（detail.candidates
      直接带 plan_name/roi/cost/revenue/orders/status/budget 等真实字段），
      完全跳过文本解析，下游告警/推送零改动；
    - 否则按旧路径返回 (text, words)，由核心 _parse_recognized 统一解析。
    结构化读取失败时自动回退文本路径，不中断识别。"""
    t = target if target is not None else core._current_target()
    # v10.3：结构化表格读取默认启用（uia_structured=false 可关闭）
    try:
        structured_enabled = bool(core.cfg.get("uia_structured", True))
    except Exception:
        structured_enabled = True
    if structured_enabled:
        try:
            rows = _uia_structured_read(core, t)
        except Exception as e:
            core.log(f"[模式二-结构化] 读取异常，回退文本解析：{e}")
            rows = []
        if rows:
            return _structured_summary(rows), [], rows
    try:
        text, words = _uia_read_text(core, t)
    except RuntimeError:
        # UIA 无结果且配置了 CDP 端口时，尝试 CDP 读页面 DOM 文本兜底
        cdp = _cdp_read_text(core, t)
        if not cdp:
            raise
        text, words = cdp
    return text, words


# ==================== v10.8.2 路线D：UIA 点击浏览器『重新加载』按钮（uia_reload） ====================
# 背景：路线C 筛选刷新（uia_filter）在 TikTok 广告面板实测不可行（『状态』筛选下拉弹层不在
# 控件树）。探测报告确认浏览器工具栏存在 [ButtonControl] name="重新加载" aid="view_1003"
# class="ReloadButton" patterns=Invoke。v10.8.2 改为 UIA Invoke 点击该按钮触发页面重新加载，
# 方案已由独立工具 ReloadTest（输出\探测工具\uia_reload_test.py）在工作电脑实测确认可刷新页面
# 后合并进主程序。全程纯控件级 Pattern 调用：不抢焦点、不模拟键盘鼠标、只作用于目标浏览器窗口。
# 定位：目标窗口（复用 _resolve_probe_hwnd）→ 主窗口整树 + 渲染子窗口下钻收集 →
#      优先 aid=view_1003 且 class=ReloadButton 精确匹配 → aid 匹配 → Name 含『重新加载』。
# 点击：InvokePattern.Invoke()，失败用 LegacyIAccessiblePattern.DoDefaultAction() 兜底；
#      找不到重载按钮时输出候选清单（类型|Name|AutomationId|rect|Patterns）并失败返回，不谎报成功。
# 就绪：点击后轮询（间隔1.5s，最多 refresh_wait_sec 秒[clamp 5~15]）检测地址栏 URL 变化或
#      列表表格行数就绪（core-table-tr），到点固定延时兜底；返回 waited 秒数供主程序日志。

_RELOAD_AID = "view_1003"
_RELOAD_NAME = "重新加载"
_OMNIBOX_AID = "view_1012"
_OMNIBOX_NAME = "地址和搜索栏"
_TABLE_CONTAINER_CLASS = "core-table-container"
_TABLE_ROW_CLASS = "core-table-tr"
_RENDER_WIDGET_HINT = "Chrome_RenderWidgetHostHWND"

# 候选清单展示的 Pattern 列表（ReloadTest 同款）
_RELOAD_PATTERN_IDS = None


def _reload_pattern_list():
    global _RELOAD_PATTERN_IDS
    if _RELOAD_PATTERN_IDS is not None:
        return _RELOAD_PATTERN_IDS
    try:
        from uiautomation import PatternId
        _RELOAD_PATTERN_IDS = [
            (PatternId.InvokePattern, "Invoke"),
            (PatternId.SelectionItemPattern, "SelectionItem"),
            (PatternId.ExpandCollapsePattern, "ExpandCollapse"),
            (PatternId.TogglePattern, "Toggle"),
            (PatternId.LegacyIAccessiblePattern, "LegacyIAccessible"),
            (PatternId.ValuePattern, "Value"),
            (PatternId.ScrollPattern, "Scroll"),
            (PatternId.WindowPattern, "Window"),
            (PatternId.SelectionPattern, "Selection"),
            (PatternId.TextPattern, "Text"),
            (PatternId.ScrollItemPattern, "ScrollItem"),
            (PatternId.TableItemPattern, "TableItem"),
            (PatternId.TablePattern, "Table"),
            (PatternId.GridPattern, "Grid"),
            (PatternId.RangeValuePattern, "RangeValue"),
        ]
    except Exception:
        _RELOAD_PATTERN_IDS = []
    return _RELOAD_PATTERN_IDS


def _ctrl_patterns(ctrl):
    """取控件支持的 UIA Pattern 名称列表（供候选清单）。"""
    names = []
    for pid, pname in _reload_pattern_list():
        try:
            if ctrl.GetPattern(pid) is not None:
                names.append(pname)
        except Exception:
            pass
    return names


def _control_from_handle_safe(auto, hwnd, timeout=10.0):
    """ControlFromHandle 带超时保护：uiautomation 对复杂 Chromium 窗口可能无限阻塞，
    放子线程执行，超时返回 None 不拖死主监控线程（v10.18 合入，源自 test_refresh_filter）。
    子线程必须先用 UIAutomationInitializerInThread 初始化 COM（CoInitialize），
    否则报 OSError(22, '尚未调用 CoInitialize。')。返回 (ctrl, err_info)。"""
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


def _collect_reload_tree(hwnd, auto, max_nodes=4000, max_depth=24):
    """uia_reload 专用控件树收集：主窗口整树（不过滤浏览器 UI——ReloadButton 在浏览器
    工具栏，属 chrome UI）+ 渲染子窗口下钻（页面表格/地址栏快照用）。
    返回 (infos, truncated)；infos 每条为 dict：{ctrl, ct, name, aid, cls, rect, patterns, source}"""
    infos = []
    truncated = False

    def _walk(root_ctrl, source):
        nonlocal truncated
        if truncated:
            return
        try:
            for c, _depth in _collect_ctrl_items(root_ctrl, max_nodes=max_nodes, timeout_sec=15.0):
                if len(infos) >= max_nodes:
                    truncated = True
                    return
                try:
                    ct = _ctrl_type(c)
                    name = _ctrl_name(c)
                    aid = ""
                    cls = ""
                    try:
                        aid = str(c.AutomationId or "")
                    except Exception:
                        aid = ""
                    try:
                        cls = str(c.ClassName or "")
                    except Exception:
                        cls = ""
                    rect = ""
                    try:
                        r = c.BoundingRectangle
                        rect = "rect=(%d,%d)-(%d,%d)" % (
                            int(r.left), int(r.top), int(r.right), int(r.bottom))
                    except Exception:
                        rect = "rect=?"
                    pats = _ctrl_patterns(c) if "Button" in ct else []
                except Exception:
                    continue
                infos.append({"ctrl": c, "ct": ct, "name": name, "aid": aid, "cls": cls,
                              "rect": rect, "patterns": pats, "source": source})
        except Exception:
            pass

    root = None
    root, err = _control_from_handle_safe(auto, hwnd)
    if root is None:
        return infos, True
    _walk(root, "root")
    # 渲染子窗口下钻（页面 DOM 所在子树）：定位与收集均带超时保护（v10.18），
    # 复杂 Chromium 窗口树单次调用可能无限阻塞，放子线程+join 超时，超时跳过不拖死。
    try:
        sub = _find_render_sub_hwnd_safe(hwnd)
        if sub and sub != hwnd:
            import threading
            done = {"ok": False}

            def _walk_sub():
                sub_root, sub_err = _control_from_handle_safe(auto, sub, timeout=8.0)
                if sub_root is None:
                    return
                _walk(sub_root, "render")
                done["ok"] = True

            th = threading.Thread(target=_walk_sub, daemon=True)
            th.start()
            th.join(25.0)
            if not done["ok"] and th.is_alive():
                pass  # 渲染子窗口收集超时，保留已收集部分继续
    except Exception:
        pass
    return infos, truncated


def _find_reload_button(infos):
    """优先 aid=view_1003 且 class=ReloadButton 精确匹配；其次 aid 匹配；再其次 Name 含『重新加载』。
    返回 (匹配info|None, Button候选列表)。"""
    candidates = []
    for info in infos:
        if "Button" not in info["ct"]:
            continue
        if info["aid"] == _RELOAD_AID and info["cls"] == "ReloadButton":
            return info, candidates
        candidates.append(info)
    for info in candidates:
        if info["aid"] == _RELOAD_AID:
            return info, candidates
    for info in candidates:
        if _RELOAD_NAME in info["name"]:
            return info, candidates
    return None, candidates


def _find_omnibox(infos):
    for info in infos:
        if info["aid"] == _OMNIBOX_AID and "Edit" in info["ct"]:
            return info
    for info in infos:
        if info["name"] == _OMNIBOX_NAME:
            return info
    return None


def _omnibox_url(info):
    if not info:
        return ""
    try:
        vp = info["ctrl"].GetValuePattern()
        if vp is not None:
            return (vp.Value or "").strip()
    except Exception:
        pass
    return ""


def _count_table_rows(infos):
    return sum(1 for i in infos if _TABLE_ROW_CLASS in i["cls"])


def _reload_candidate_line(info):
    pat = ",".join(info["patterns"]) if info["patterns"] else "-"
    return (f"[{info['ct']}] name=\"{info['name']}\" aid=\"{info['aid']}\" "
            f"{info['rect']} patterns={pat}")


# v10.8.4：表格就绪表头关键词——就绪主信号为「表格行数>0 且表头关键词出现」
_READY_HEADER_STRONG = ("广告计划名称", "计划名称", "广告计划", "总收入", "总成本", "sku 订单数", "状态")
_READY_HEADER_WEAK = ("ROI", "成本", "预算", "目标", "收入")


def _ready_by_table(row_count, header_hits):
    """表格就绪判定（v10.8.4 主信号）：行数>0 且命中强表头词≥1 或弱表头词≥2。"""
    if row_count <= 0:
        return False
    if not header_hits:
        return False
    strong = [h for h in header_hits if h in _READY_HEADER_STRONG]
    if strong:
        return True
    return len(header_hits) >= 2


def _probe_table_ready(hwnd, auto):
    """轻量就绪探测（v10.8.4/v10.19）：主窗口树 + 渲染子窗口子树合并统计表格行数、
    表头关键词命中、控件总数与地址栏 URL。返回 (url, row_count, header_hits, ctrl_count)。

    v10.19：渲染子窗口收集可能超时（9/2 就绪全天失败根因——收集超时时控件数=0），
    主窗口树同步统计行/表头/控件兜底；渲染子收集 join 提升到 22s（与点击前收集同口径）。"""
    url = ""
    row_count = 0
    header_hits = []
    ctrl_count = 0
    try:
        root, _err = _control_from_handle_safe(auto, hwnd, timeout=6.0)
        if root is not None:
            for c, _d in _collect_ctrl_items(root, max_nodes=2000, timeout_sec=6.0):
                try:
                    aid = str(c.AutomationId or "")
                except Exception:
                    aid = ""
                ct = _ctrl_type(c)
                ctrl_count += 1  # v10.19：主窗口树同步计数（渲染子收集超时不漏判）
                try:
                    if _TABLE_ROW_CLASS in str(c.ClassName or ""):
                        row_count += 1
                except Exception:
                    pass
                if not url and aid == _OMNIBOX_AID and "Edit" in ct:
                    try:
                        vp = c.GetValuePattern()
                        if vp is not None:
                            url = (vp.Value or "").strip()
                    except Exception:
                        pass
                if len(header_hits) < 8:
                    try:
                        nm = str(c.Name or "").strip()
                    except Exception:
                        nm = ""
                    for h in _READY_HEADER_STRONG + _READY_HEADER_WEAK:
                        if h and h in nm and h not in header_hits:
                            header_hits.append(h)
    except Exception:
        pass
    try:
        sub = _find_render_sub_hwnd_safe(hwnd)
        if sub and sub != hwnd:
            # 渲染子窗口收集整体放子线程+join 超时（v10.18），超时不拖死主监控线程
            import threading
            done = {"ok": False}

            def _probe_sub():
                sub_root, _err2 = _control_from_handle_safe(auto, sub, timeout=6.0)
                if sub_root is None:
                    return
                for c, _d in _collect_ctrl_items(sub_root, max_nodes=3000, timeout_sec=20.0):
                    ctrl_count += 1
                    try:
                        if _TABLE_ROW_CLASS in str(c.ClassName or ""):
                            row_count += 1
                    except Exception:
                        pass
                    if len(header_hits) < 8:
                        try:
                            nm = str(c.Name or "").strip()
                        except Exception:
                            nm = ""
                        for h in _READY_HEADER_STRONG + _READY_HEADER_WEAK:
                            if h and h in nm and h not in header_hits:
                                header_hits.append(h)
                done["ok"] = True

            th = threading.Thread(target=_probe_sub, daemon=True)
            th.start()
            th.join(22.0)  # v10.19：与点击前 _collect_reload_tree 口径一致，渲染子收集慢时不再漏判
            if not done["ok"] and th.is_alive():
                pass  # 渲染子窗口探测超时，用已收集数据继续
    except Exception:
        pass
    return url, row_count, header_hits, ctrl_count


def _ops_log_line(core, name, s):
    """v10.12：操作级日志写入（UIA 控件操作细节→ops_YYYYMMDD.log）。

    找不到 core.ops_log 时降级主日志 debug（兼容 ReloadTest 等独立调用场景）。
    """
    fn = getattr(core, "ops_log", None)
    if fn:
        try:
            fn(f"[{name}] [重载刷新] {s}")
            return
        except Exception:
            pass
    try:
        core.log(f"[{name}] [重载刷新] {s}", level="debug")
    except Exception:
        pass


# ==================== v10.19 并行刷新融合（test_parallel_refresh v0.3 并入主程序） ====================
# 背景：9/1、9/2 ops 日志分析确认主程序刷新存在『每店每轮双刷』与『就绪判定全天失败空转』
#（渲染子窗口收集超时 -> 控件数=0 -> 刷新失败 -> v10.14.7 自动重试再点一次，双刷叠加空转）。
# 融合设计：
#   1. 点击段临界区串行 + 就绪轮询锁外并行（原 test_parallel_refresh v0.3 验证方案）；
#   2. 就绪判定升级——表格就绪 + 确认刷新真的发生（URL/行数/表头变化或见过空白中间态），
#      不再拿旧表格误判成功，也不再『点击已发生但探测失败』时由主程序自动重试（双刷来源）；
#   3. 刷新异常分类统计：同类记触发次数；同分类细节不同记首次细节（ops 日志），不另留文件。
_CLICK_LOCK = threading.RLock()
_STATS_LOCK = threading.Lock()
_REFRESH_STATS = {"count": 0, "ok": 0, "failed": 0, "cats": {}}  # cats: {cat: {"n": n, "notes": {detail: n}}}


def _refresh_stats_bump(core, cat):
    """刷新异常分类计数（线程安全；detail 不区分时用）。"""
    with _STATS_LOCK:
        _REFRESH_STATS["failed"] += 1
        c = _REFRESH_STATS["cats"].setdefault(cat, {"n": 0, "notes": {}})
        c["n"] += 1


def _refresh_stats_note(core, name, cat, detail):
    """v10.19：同类记触发次数；同分类细节不同——首次细节写 ops 日志并保留计数。

    detail 应包含出错特征但不含易变句柄/时间戳，避免同类错误因句柄不同刷屏。
    """
    first = False
    with _STATS_LOCK:
        _REFRESH_STATS["failed"] += 1
        c = _REFRESH_STATS["cats"].setdefault(cat, {"n": 0, "notes": {}})
        c["n"] += 1
        c["notes"][detail] = c["notes"].get(detail, 0) + 1
        if c["notes"][detail] == 1:
            first = True
    if first:
        _ops_log_line(core, name, f"[统计-{cat}] 首次出现细节：{detail}（累计同分类 {c['n']} 次）")


def _refresh_stats_snapshot(_core=None):
    """返回统计快照 dict（主程序轮末/停止时读取输出摘要）。"""
    with _STATS_LOCK:
        return {
            "count": _REFRESH_STATS["count"],
            "ok": _REFRESH_STATS["ok"],
            "failed": _REFRESH_STATS["failed"],
            "cats": {k: {"n": v["n"], "notes": dict(v["notes"])} for k, v in _REFRESH_STATS["cats"].items()},
        }


def _refresh_stats_reset(_core=None):
    """清零统计（监控停止时由主程序调用；监控进程内跨轮累计）。"""
    with _STATS_LOCK:
        _REFRESH_STATS["count"] = 0
        _REFRESH_STATS["ok"] = 0
        _REFRESH_STATS["failed"] = 0
        _REFRESH_STATS["cats"] = {}


def _with_click_lock(fn):
    """整函数持全局点击锁（低频地址栏读取/导航用；RLock 可重入）。"""
    def _wrapped(*args, **kwargs):
        with _CLICK_LOCK:
            return fn(*args, **kwargs)
    return _wrapped


def _poll_ready_after_action(core, name, hwnd, auto, url_before, rows_before=0,
                             headers_before=None, steps=None, poll_sec=None, tag="就绪轮询"):
    """v10.19：点击/导航动作完成后的锁外就绪轮询（多店并行执行，不占全局点击锁）。

    就绪判定 = _ready_by_table（行数>0 且表头命中）且确认『刷新真的发生了』：URL 变化，
    或行数/表头较点击前快照有变化，或轮询期间见过空白中间态（行/表头为 0 的加载中态）。
    旧表格 + 全程无任何变化 -> 『假就绪』，按失败返回并写明原因，防止识别到过期数据。
    渲染子窗口收集慢（join 最长 ~22s，与点击前 _collect_reload_tree 口径一致）时，
    以硬上限与最多 2 次探测约束总等待，不让单店刷新无限拖长。
    返回 (ready, elapsed, reason, snapshot)。
    """
    steps = steps if steps is not None else []
    headers_before = list(headers_before) if headers_before else []
    try:
        _poll_sec = float(poll_sec) if poll_sec is not None else max(5, float(core.cfg.get("refresh_wait_sec", 8) or 8))
    except (ValueError, TypeError):
        _poll_sec = 8.0
    _poll_interval = 1.0
    _t0 = time.monotonic()
    _hard = _t0 + max(_poll_sec * 3, 20.0)
    _probe_n = 0
    _url_changed = ""
    _saw_blank = False
    _last_rows = rows_before
    _last_headers = list(headers_before)
    _ready_reason = ""
    while _probe_n < 2 and time.monotonic() < _hard:
        if _probe_n > 0:
            time.sleep(_poll_interval)
        _probe_n += 1
        url_now, rows_now, header_hits, ctrl_count = _probe_table_ready(hwnd, auto)
        if url_now and url_now != url_before and not _url_changed:
            _url_changed = url_now  # URL 变化 = 页面开始重载（辅助信号）
        if rows_now == 0 or not header_hits:
            _saw_blank = True  # 见过中间态：页面确实在加载
        _last_rows = rows_now
        _last_headers = header_hits
        if _ready_by_table(rows_now, header_hits):
            _changed = bool(_url_changed) or rows_now != rows_before or set(header_hits) != set(headers_before)
            if _changed or _saw_blank:
                _ready_reason = f"表格已就绪（行数={rows_now}，表头命中={header_hits}"
                if _url_changed:
                    _ready_reason += f"；URL 变化={_url_changed[:80]}"
                elif _saw_blank:
                    _ready_reason += "；轮询期间见过空白中间态"
                else:
                    _ready_reason += "；快照较点击前有变化"
                _ready_reason += "）"
                break
    _elapsed = time.monotonic() - _t0
    if not _ready_reason:
        _snapshot = (f"轮询 {_elapsed:.1f}s 未确认就绪：末次快照 行数={_last_rows} 表头命中={_last_headers}，"
                     f"点击前快照 行数={rows_before} 表头命中={headers_before}，"
                     f"URL变化={(_url_changed[:60]) if _url_changed else '未变化'}")
        steps.append(_snapshot)
        steps.append(f"{tag}：等待 {_elapsed:.1f}s 表格未就绪或未见刷新变化（旧表格不判成功，避免识别到过期数据）")
        return False, _elapsed, _snapshot, _snapshot
    steps.append(f"{tag}：{_ready_reason}（等待 {_elapsed:.1f}s）")
    return True, _elapsed, _ready_reason, _ready_reason




# ==================== v10.17 当天日期自动校正（跨天自动追踪当天数据） ====================
# 背景：自动监控每轮走 _uia_reload 点击『重新加载』，但刷新只重载当前页，URL 中的
# list_start_date/list_end_date 停留在上次日期——跨天（用户睡觉）后识别到的仍是旧日期
# 数据，无法自动追踪当天。方案：刷新前先检查 URL 日期是否为当天（UTC），非当天则自动
# 地址栏导航到「当天+已生效」URL（test_refresh_filter 已验证方案），当天则走原重载。
# 主程序无需改动：仍经 _uia_mod_actions["reload"] 调用，返回结构 ok/steps/missing/waited 不变。
# v10.26 修复（视图约束保障）：campaign_status in ['delivery_ok']（已生效）筛选参数作为单一来源常量，
# 供模板 URL 与 _ensure_active_filter 共同引用，避免两处维护漂移。
_ACTIVE_FILTER_PGM = (
    "%5B%28%27field%21%27campaign_status%27%7Efilter_type%210"
    "%7Ein_field_values%21%5B%27delivery_ok%27%5D%29%5D_"
)
_TARGET_URL_TEMPLATE_TODAY = (
    "https://seller.tiktokshopglobalselling.com/ads-creation/dashboard"
    "?mpa=1&list_overview_metric=3&list_overview_metric=0"
    "&campaign_overview_metric=3&campaign_overview_metric=0"
    "&list_order_field=cost&list_order_type=descend&activated_tab_id=0"
    "&type=product&list_start_date={ms}&list_end_date={ms}"
    "&product_campaign_page=1"
    "&sp_campaign_list_pgm=" + _ACTIVE_FILTER_PGM
)


def _is_today_utc_ms(ms_str):
    """list_start_date 毫秒串是否属于今天（UTC）。返回 bool。"""
    try:
        ms = int(ms_str)
    except Exception:
        return False
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    today_start = int(datetime(now.year, now.month, now.day, tzinfo=timezone.utc).timestamp() * 1000)
    return today_start <= ms < today_start + 86400000


def _extract_url_param(url, key):
    """从 URL 提取指定参数值（未命中返回 None）。"""
    if not url:
        return None
    m = re.search(r"[?&]" + re.escape(key) + r"=([^&#]+)", url)
    return m.group(1) if m else None


def _url_has_active_filter(url):
    """v10.26：判断 URL 是否已含 campaign_status in ['delivery_ok']（全部已生效）视图约束。

    判据为 URL 中 sp_campaign_list_pgm 参数包含 delivery_ok 值片段（编码后为 %27delivery_ok%27）。
    返回 True 表示当前地址栏已是『已生效』筛选视图；False 表示可能是全部/已暂停/其它状态视图。
    """
    if not url:
        return False
    return "delivery_ok" in url and "%27delivery_ok%27" in url.replace("'", "%27")


def _ensure_active_filter(url):
    """v10.26：确保 URL 携带『已生效』筛选约束，缺失则补写 sp_campaign_list_pgm 片段。

    入参 url 为已含正确日期参数的目标地址；若已含 delivery_ok 约束则原样返回，
    否则用单一来源常量 _ACTIVE_FILTER_PGM 追加/替换 sp_campaign_list_pgm 参数。
    返回恒带 campaign_status=delivery_ok 视图约束的 URL。
    """
    if _url_has_active_filter(url):
        return url
    # 去掉可能残留的旧 sp_campaign_list_pgm 参数（含已暂停/全部等旧筛选），再补写已生效筛选
    url = re.sub(r"[?&]sp_campaign_list_pgm=[^&#]*", "", url)
    sep = "&" if "?" in url else "?"
    return url + sep + "sp_campaign_list_pgm=" + _ACTIVE_FILTER_PGM


def _uia_reload_today(core, target=None):
    """v10.18/v10.19：『当天重载刷新』——刷新前自动校正日期到当天，跨天自动追踪当天数据。
    v10.19：入口统一计一次刷新请求（当天重载/跨天新标签导航同属一次刷新尝试）。

    读目标窗口当前 URL：
      - URL 含 ads-creation/dashboard 且 list_start_date 已是今天（UTC）→ 走原
        _uia_reload 点击『重新加载』（重载保留当天筛选）；
      - 非当天 / 无日期参数 / 不在广告列表页 → 构造「当天+已生效」URL 并走
        _uia_goto_url_new_tab 新增标签页后地址栏导航（不在旧标签覆盖地址，
        原页面状态保留，过程尽量快；test_refresh_filter 已验证地址栏导航方案）。
    返回 {"ok": bool, "steps": [...], "missing": [...], "waited": 秒}，与 _uia_reload 一致。
    """
    from datetime import datetime, timezone
    with _STATS_LOCK:
        _REFRESH_STATS["count"] += 1  # v10.19：刷新请求统一计数
    url, _hwnd, _infos = _uia_current_url(core, target)
    if not url:
        # 读不到地址栏 URL：不阻塞，走原重载（下轮继续尝试校正）
        return _uia_reload(core, target)
    on_dashboard = "ads-creation/dashboard" in url
    start_ms = _extract_url_param(url, "list_start_date")
    if on_dashboard and start_ms and _is_today_utc_ms(start_ms) and _url_has_active_filter(url):
        core.log(f"[{_current_target_name(core, target)}] [当天重载] URL 日期已是当天且已含『全部已生效』筛选（list_start_date={start_ms}），走原重载刷新")
        return _uia_reload(core, target)
    # 非当天 / 缺日期 / 不在广告列表页 / 日期当天但视图未约束为已生效（如被切到已暂停/全部视图）
    # → 新增标签页导航到「当天+delivery_ok 已生效」URL（_ensure_active_filter 保证约束恒在）
    ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    if on_dashboard:
        new_url = _ensure_active_filter(_replace_date_params(url, ms, ms))
    else:
        new_url = _ensure_active_filter(_TARGET_URL_TEMPLATE_TODAY.format(ms=ms))
    if not new_url:
        return _uia_reload(core, target)
    try:
        core.log(f"[{_current_target_name(core, target)}] [当天重载] 日期非当天或视图无『全部已生效』约束，新增标签页导航到当天+已生效 URL（list_start_date={ms}）")
        return _uia_goto_url_new_tab(core, new_url, target)
    except Exception as e:
        core.log(f"[{_current_target_name(core, target)}] [当天重载] 新标签页导航异常: {e}", level="debug")
        return _uia_reload(core, target)


def _current_target_name(core, target):
    try:
        t = target if target is not None else core._current_target()
        return t.get("name") or f"目标{t.get('id')}"
    except Exception:
        return "目标"


def _uia_reload(core, target=None):
    """v10.8.2 路线D：UIA 点击浏览器工具栏『重新加载』按钮刷新页面（ReloadTest 已验证方案）。

    流程：定位目标窗口 → 收集主窗口整树+渲染子窗口 → 查找 ReloadButton
    （aid=view_1003 / Name 含『重新加载』，ButtonControl）→ Invoke 点击
    （LegacyIAccessiblePattern.DoDefaultAction() 兜底）→ 就绪轮询（点击段结束后进行）。

    v10.19（test_parallel_refresh v0.3 融合）：点击段与就绪轮询分离——
    * 点击段（收集树→点击前快照→置前→Invoke→还原焦点）在全局点击锁 _CLICK_LOCK 内执行，
      多店同时刷新时互不抢前台、互不打断（原 test_parallel_refresh 临界区方案）；
    * 点击完成后释放锁，就绪轮询在锁外执行（_poll_ready_after_action），多店并行轮询，
      不再占全局锁——并行收益保留；
    * 就绪判定升级：表格就绪 + 确认刷新真的发生（URL/行数/表头变化或见过空白中间态），
      不再拿旧表格误判成功；『表格就绪未确认』不再重试点击（主程序 v10.19 同步），
      结构性消灭 9/1-9/2『每店每轮双刷』；
    * 失败分类统计：目标窗口定位/uiautomation 初始化/未找到重载按钮/按钮点击失败/
      表格就绪未确认，同类记触发次数、同分类细节不同记首次细节（ops 日志）。

    返回 {"ok": bool, "steps": [...], "missing": [...], "waited": 秒}；
    找不到重载按钮时输出候选清单日志（类型|Name|AutomationId|rect|Patterns）并失败返回，
    不谎报成功；未定位窗口/初始化失败同样返回失败（调用方优雅降级，跳过刷新继续识别）。"""
    steps = []
    missing = []
    t = target if target is not None else core._current_target()
    name = t.get("name") or f"目标{t.get('id')}"
    hwnd, shop_disp = _resolve_probe_hwnd(core, t)
    if not hwnd:
        steps.append("定位目标窗口失败（句柄失效或关键词匹配失败）")
        _refresh_stats_note(core, name, "目标窗口定位失败", "句柄失效或关键词匹配失败")
        return {"ok": False, "steps": steps, "missing": ["目标窗口"], "waited": 0}
    core.log(f"[{name}] [重载刷新] 目标窗口 hwnd=0x{hwnd & 0xFFFFFFFFFFFFFFFF:X} 店铺={shop_disp or '-'} "
             f"开始 UIA 重载刷新（路线D：点击『重新加载』按钮，纯控件操作不抢焦点）")
    try:
        wait = float(core.cfg.get("uia_wait_sec", 1.5) or 0)
    except (ValueError, TypeError):
        wait = 1.5
    if wait > 0:
        time.sleep(min(wait, 5.0))  # accessibility tree 构建延迟
    try:
        import uiautomation as auto
    except Exception as e:
        steps.append(f"uiautomation 初始化异常（hwnd={hwnd}）：{e}")
        _refresh_stats_note(core, name, "uiautomation 初始化", f"{e}")
        return {"ok": False, "steps": steps, "missing": ["uiautomation 初始化"], "waited": 0}

    # ---- v10.19：点击段持全局点击锁（临界区串行，多店不互抢前台）----
    with _CLICK_LOCK:
        # ---- 1. 收集控件树 + 查找 ReloadButton ----
        infos, truncated = _collect_reload_tree(hwnd, auto)
        steps.append(f"控件树收集完成：{len(infos)} 个控件{'（已达上限截断）' if truncated else ''}")
        btn, candidates = _find_reload_button(infos)
        if btn is None:
            steps.append(f"未找到『重新加载』按钮（控件树中无 aid={_RELOAD_AID} / Name 含『{_RELOAD_NAME}』的 Button）")
            if candidates:
                steps.append(f"Button 候选清单（共 {len(candidates)} 个，类型|Name|AutomationId|rect|Patterns）：")
                shown = candidates[:40]
                for i, info in enumerate(shown, 1):
                    steps.append(f"  {i:02d}. {_reload_candidate_line(info)}")
                if len(candidates) > 40:
                    steps.append(f"  ... 其余 {len(candidates) - 40} 个略")
            else:
                steps.append("候选：控件树中无任何 Button 控件（可能窗口不支持 UIA 或控件树未暴露）")
            for s in steps:
                _ops_log_line(core, name, s)
            core.log(f"[{name}] [重载刷新] 未找到『重新加载』按钮，刷新失败（候选清单见上，不谎报成功）")
            _refresh_stats_note(core, name, "未找到重载按钮", f"候选按钮 {len(candidates)} 个，控件树 {len(infos)} 个控件")
            return {"ok": False, "steps": steps, "missing": ["重新加载按钮"], "waited": 0}
        steps.append(f"找到『重新加载』按钮：{_reload_candidate_line(btn)}（匹配：aid+class 精确/aid/Name 含『重新加载』）")

        # ---- 2. 点击前快照 ----
        omnibox = _find_omnibox(infos)
        url_before = _omnibox_url(omnibox)
        rows_before = _count_table_rows(infos)
        header_before = []
        for _ri in infos:
            _rn = _ri.get("name") or ""
            for _h in _READY_HEADER_STRONG + _READY_HEADER_WEAK:
                if _h and _h in _rn and _h not in header_before:
                    header_before.append(_h)
        steps.append(f"点击前快照：URL={url_before or '(空)'} 表格行={rows_before} 表头命中={header_before}")

        # ---- 3. Invoke 点击（LegacyIAccessible 兜底）----
        # v10.14.4：彻底删除 v10.10 方案A（点击前最小化后刷新）逻辑——最小化导致
        # Chromium 暂停渲染、UIA 读不到表格报 no_header（v10.14.3 声称删除实际未删净）。
        # 刷新点击前显式置前：若窗口处于最小化则 SW_RESTORE 恢复（写 [窗口] ops 日志），
        # 再 SW_SHOW + SetForegroundWindow 置前；窗口保持原状不最小化；点击后还原前台焦点。
        try:
            _u32 = ctypes.windll.user32
            if _u32.IsIconic(hwnd):
                _u32.ShowWindow(hwnd, 9)  # SW_RESTORE：恢复最小化窗口
                steps.append("目标窗口处于最小化，已恢复（SW_RESTORE）后置前")
                try:
                    core.ops_log(f"[窗口] 恢复窗口 hwnd=0x{hwnd & 0xFFFFFFFFFFFFFFFF:X}（点击前 SW_RESTORE 恢复最小化窗口）")
                except Exception:
                    pass
            if _u32.IsWindowVisible(hwnd):
                _u32.ShowWindow(hwnd, 5)  # SW_SHOW：确保可见（已可见时幂等）
            _u32.SetForegroundWindow(hwnd)
            steps.append("点击前已显式置前（SW_RESTORE + SW_SHOW + SetForegroundWindow），窗口保持原状不最小化")
            try:
                core.ops_log(f"[窗口] 置前窗口 hwnd=0x{hwnd & 0xFFFFFFFFFFFFFFFF:X}（SW_SHOW + SetForegroundWindow，点击刷新前不最小化）")
            except Exception:
                pass
        except Exception as e:
            steps.append(f"置前目标窗口失败（忽略，继续刷新）：{e}")
        # v10.8.6 待办C：点击前记录前台句柄，点击后还原焦点，避免刷新抢前台阻断用户
        prev_fg = None
        try:
            prev_fg = ctypes.windll.user32.GetForegroundWindow()
        except Exception:
            prev_fg = None
        clicked = False
        try:
            ip = btn["ctrl"].GetInvokePattern()
            if ip is not None:
                ip.Invoke()
                clicked = True
                steps.append("InvokePattern.Invoke() 调用成功")
        except Exception as e:
            steps.append(f"InvokePattern 调用失败: {e}")
        if not clicked:
            try:
                lp = btn["ctrl"].GetLegacyIAccessiblePattern()
                if lp is not None:
                    lp.DoDefaultAction()
                    clicked = True
                    steps.append("LegacyIAccessiblePattern.DoDefaultAction() 兜底调用成功")
            except Exception as e:
                steps.append(f"LegacyIAccessible 兜底调用失败: {e}")
        if not clicked:
            steps.append("点击失败（Invoke 与 LegacyIAccessible 均不可用）")
            for s in steps:
                _ops_log_line(core, name, s)
            _refresh_stats_note(core, name, "重载按钮点击失败", "Invoke 与 LegacyIAccessible 均不可用")
            return {"ok": False, "steps": steps, "missing": ["重载按钮点击"], "waited": 0}
        # 点击成功：还原前台焦点（AttachThreadInput + BringWindowToTop + SetForegroundWindow，失败静默）
        if prev_fg:
            try:
                user32 = ctypes.windll.user32
                cur_tid = ctypes.windll.kernel32.GetCurrentThreadId()
                fg_tid = user32.GetWindowThreadProcessId(prev_fg, ctypes.byref(ctypes.c_ulong()))
                if fg_tid:
                    user32.AttachThreadInput(cur_tid, fg_tid, True)
                    try:
                        user32.BringWindowToTop(prev_fg)
                        user32.SetForegroundWindow(prev_fg)
                    finally:
                        user32.AttachThreadInput(cur_tid, fg_tid, False)
            except Exception:
                core.log(f"[{name}] [重载刷新] 焦点还原失败（忽略，不影响刷新流程）", level="debug")


    # ---- 4. v10.19：锁外就绪轮询（点击已提交、锁已释放；多店并行轮询）----
    # 点击段持锁期间其他店串行等待；锁释放后各店并行 _probe_table_ready 轮询。
    # v10.19 判定升级：表格就绪且确认刷新真的发生（URL/行数/表头变化或见过空白中间态），
    # 旧表格不判成功（防止识别到过期数据）；未确认时不阻塞、不自动重试点击（防双刷）。
    try:
        _poll_sec = max(5, int(float(core.cfg.get("refresh_wait_sec", 8) or 8)))
    except (ValueError, TypeError):
        _poll_sec = 8
    _ready, _elapsed, _reason, _snapshot = _poll_ready_after_action(
        core, name, hwnd, auto, url_before, rows_before=rows_before,
        headers_before=header_before, steps=steps,
        poll_sec=_poll_sec, tag="就绪轮询")
    if not _ready:
        for s in steps:
            _ops_log_line(core, name, s)
        _refresh_stats_note(core, name, "表格就绪未确认", _snapshot)
        core.log(f"[{name}] [重载刷新] 刷新后表格未确认就绪，本轮不阻塞；点击已发生不再自动重试"
                 f"（v10.19 防双刷；如需再刷请手动触发一轮识别）")
        return {"ok": False, "steps": steps, "missing": ["表格就绪"], "waited": _elapsed}
    with _STATS_LOCK:
        _REFRESH_STATS["ok"] += 1
    for s in steps:
        _ops_log_line(core, name, s)
    core.log(f"[{name}] [重载刷新] 重载刷新完成（点击成功 + 确认就绪等待 {_elapsed:.1f}s），接续识别轮询")
    return {"ok": True, "steps": steps, "missing": missing, "waited": _elapsed}


def _utc_ms(date_str):
    """日期字符串 YYYY-MM-DD → UTC 毫秒时间戳（TikTok 后台 list_start_date/list_end_date 格式）。"""
    from datetime import datetime, timezone
    try:
        dt = datetime.strptime(date_str.strip(), "%Y-%m-%d")
    except Exception:
        return None
    return int(dt.replace(tzinfo=timezone.utc).timestamp() * 1000)


def _replace_date_params(url, start_ms, end_ms):
    """替换/追加 URL 中的 list_start_date / list_end_date 参数，其余参数保留。"""
    import re
    if not url:
        return None
    if re.search(r"[?&]list_start_date=\d+", url):
        url = re.sub(r"list_start_date=\d+", f"list_start_date={start_ms}", url)
        url = re.sub(r"list_end_date=\d+", f"list_end_date={end_ms}", url)
    else:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}list_start_date={start_ms}&list_end_date={end_ms}"
    return url


def _uia_current_url(core, target=None):
    """v10.12：读取目标窗口当前地址栏 URL（Omnibox aid=view_1012）。返回 (url, hwnd, infos)。"""
    t = target if target is not None else core._current_target()
    hwnd, _shop_disp = _resolve_probe_hwnd(core, t)
    if not hwnd:
        return None, 0, None
    try:
        import uiautomation as auto
    except Exception:
        return None, hwnd, None
    infos, _trunc = _collect_reload_tree(hwnd, auto)
    om = _find_omnibox(infos)
    url = _omnibox_url(om) if om else ""
    return url, hwnd, infos


def _uia_goto_url(core, url, target=None):
    """v10.12：向地址栏（Omnibox aid=view_1012）输入 URL 并回车导航（按日期过滤查看）。

    前台模式：用户主动触发的过滤查看允许临时恢复窗口并聚焦（区别于后台自动刷新的
    最小化不抢前台方案），UIA SetFocus + ValuePattern.SetValue 写入 URL，再键盘 Enter
    触发导航，随后表格就绪轮询（与 uia_reload 同一就绪判定）。
    返回 {"ok": bool, "steps": [...], "missing": [...], "waited": 秒, "url": 目标URL}。
    """
    import ctypes
    steps = []
    missing = []
    t = target if target is not None else core._current_target()
    name = t.get("name") or f"目标{t.get('id')}"
    hwnd, _shop_disp = _resolve_probe_hwnd(core, t)
    if not hwnd:
        steps.append("定位目标窗口失败（句柄失效或关键词匹配失败）")
        return {"ok": False, "steps": steps, "missing": ["目标窗口"], "waited": 0, "url": url}
    core.log(f"[{name}] [按日期过滤] 目标窗口 hwnd=0x{hwnd & 0xFFFFFFFFFFFFFFFF:X}，准备导航 URL 到地址栏")
    try:
        import uiautomation as auto
    except Exception as e:
        steps.append(f"uiautomation 初始化异常：{e}")
        return {"ok": False, "steps": steps, "missing": ["uiautomation 初始化"], "waited": 0, "url": url}
    try:
        wait = float(core.cfg.get("uia_wait_sec", 1.5) or 0)
    except (ValueError, TypeError):
        wait = 1.5
    if wait > 0:
        time.sleep(min(wait, 5.0))

    # ---- 1. 收集控件树 + 查找 Omnibox ----
    infos, truncated = _collect_reload_tree(hwnd, auto)
    steps.append(f"控件树收集完成：{len(infos)} 个控件{'（已达上限截断）' if truncated else ''}")
    om = _find_omnibox(infos)
    if om is None:
        steps.append(f"未找到地址栏（控件树中无 aid={_OMNIBOX_AID} / Name 含『{_OMNIBOX_NAME}』的 Edit）")
        for s in steps:
            _ops_log_line(core, name, s)
        return {"ok": False, "steps": steps, "missing": ["地址栏"], "waited": 0, "url": url}
    steps.append(f"找到地址栏：{_reload_candidate_line(om)}")
    url_before = _omnibox_url(om)
    steps.append(f"导航前快照：URL={url_before or '(空)'}")

    # ---- 2. 前台模式：临时恢复窗口并聚焦（用户主动操作，允许抢前台） ----
    try:
        user32 = ctypes.windll.user32
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
        try:
            core.ops_log(f"[窗口] 恢复窗口 hwnd=0x{hwnd & 0xFFFFFFFFFFFFFFFF:X}（前台模式 SW_RESTORE + SetForegroundWindow）")
        except Exception:
            pass
    except Exception as e:
        steps.append(f"恢复/聚焦窗口失败（忽略，继续尝试写入）：{e}")

    # ---- 3. UIA 写入地址栏 + 回车导航 ----
    entered = False
    try:
        om_ctrl = om["ctrl"]
        try:
            sp = om_ctrl.GetSetFocusPattern() if hasattr(om_ctrl, "GetSetFocusPattern") else None
        except Exception:
            sp = None
        try:
            vp = om_ctrl.GetValuePattern()
        except Exception:
            vp = None
        if vp is None:
            steps.append("地址栏无 ValuePattern，无法写入 URL")
            for s in steps:
                _ops_log_line(core, name, s)
            return {"ok": False, "steps": steps, "missing": ["地址栏 ValuePattern"], "waited": 0, "url": url}
        vp.SetValue(url)
        steps.append("ValuePattern.SetValue(url) 写入成功")
        # 焦点加固：keybd_event 是全局按键，发给当前焦点窗口；SetValue 后若用户
        # 有其他操作（切窗/点击）焦点会丢，回车就到别的窗口。重新 SetFocus 地址栏
        # 把输入焦点抢回来，再发回车。
        try:
            if sp is not None:
                sp.SetFocus()
                steps.append("SetFocus 地址栏成功")
            else:
                om_ctrl.SetFocus()
                steps.append("SetFocus 地址栏成功（控件级）")
        except Exception as e:
            steps.append(f"SetFocus 地址栏失败（继续尝试回车）: {e}")
        # 回车触发导航：优先 SendInput（窗口已聚焦可靠），失败时 PostMessage 兜底
        try:
            time.sleep(0.1)
            user32 = ctypes.windll.user32
            user32.keybd_event(0x0D, 0, 0, 0)
            user32.keybd_event(0x0D, 0, 2, 0)  # KEYEVENTF_KEYUP
            entered = True
            steps.append("SendInput 回车已发送（keybd_event VK_RETURN）")
        except Exception as e:
            steps.append(f"SendInput 回车失败: {e}，改用 PostMessage")
            try:
                user32 = ctypes.windll.user32
                user32.PostMessageW(hwnd, 0x0100, 0x0D, 0)
                user32.PostMessageW(hwnd, 0x0101, 0x0D, 0)
                entered = True
                steps.append("PostMessage 回车已投递")
            except Exception as e2:
                steps.append(f"PostMessage 回车也失败: {e2}")
    except Exception as e:
        steps.append(f"地址栏写入/导航异常: {e}")
        for s in steps:
            _ops_log_line(core, name, s)
        return {"ok": False, "steps": steps, "missing": ["地址栏写入"], "waited": 0, "url": url}
    if not entered:
        for s in steps:
            _ops_log_line(core, name, s)
        return {"ok": False, "steps": steps, "missing": ["回车导航"], "waited": 0, "url": url}

    # ---- 4. 就绪轮询（表格就绪为主信号，每 2s 探测一次；URL 变化仅辅助日志）----
    # v10.14.3：poll_sec = max(5, refresh_wait_sec)，删除 10 秒下限钳制
    try:
        poll_sec = max(5, int(float(core.cfg.get("refresh_wait_sec", 8) or 8)))
    except (ValueError, TypeError):
        poll_sec = 8
    poll_interval = 2.0
    elapsed = 0.0
    ready = False
    ready_reason = ""
    url_changed = ""
    while elapsed < poll_sec:
        time.sleep(poll_interval)
        elapsed += poll_interval
        url_now, rows_now, header_hits, ctrl_count = _probe_table_ready(hwnd, auto)
        if url_now and url_now != url_before and not url_changed:
            url_changed = url_now
        if _ready_by_table(rows_now, header_hits):
            ready = True
            ready_reason = f"表格已就绪（行数={rows_now}，表头命中={header_hits}"
            if url_changed:
                ready_reason += f"；URL 变化辅助记录={url_changed[:80]}"
            ready_reason += "）"
            break
    if not ready:
        url_now, rows_now, header_hits, ctrl_count = _probe_table_ready(hwnd, auto)
        if url_now and url_now != url_before and not url_changed:
            url_changed = url_now
        steps.append(
            f"就绪超时快照：表格行数={rows_now}，表头命中={header_hits}，控件数={ctrl_count}，"
            f"URL变化={'已变化' if url_changed else '未变化'}（轮询{poll_sec:.0f}s）"
        )
        ready_reason = f"等待 {poll_sec:.0f}s 表格仍未就绪（行数={rows_now}、表头未出现），按失败处理"
    steps.append(f"就绪轮询：{ready_reason}（等待 {elapsed:.1f}s）")
    for s in steps:
        _ops_log_line(core, name, s)
    core.log(f"[{name}] [按日期过滤] 导航完成，{ready_reason}")
    return {"ok": ready, "steps": steps, "missing": missing, "waited": elapsed, "url": url}


def _uia_goto_url_new_tab(core, url, target=None):
    """v10.18：『新标签页导航』——Ctrl+T 新增标签页后向新标签页地址栏输入 URL 并回车。

    与 _uia_goto_url（当前标签页覆盖地址）区别：跨天自动刷新时不在旧标签覆盖，而是
    新增标签页再导航，原页面状态保留；要求过程尽量快（Ctrl+T 后 Chromium 自动聚焦
    新标签页地址栏，短暂等待即收集控件树写入）。前台模式：临时恢复窗口聚焦（keybd_event
    是全局按键，发给当前焦点窗口），完成后还原前台焦点。
    返回 {"ok": bool, "steps": [...], "missing": [...], "waited": 秒, "url": 目标URL}。
    """
    import ctypes
    steps = []
    missing = []
    t = target if target is not None else core._current_target()
    name = t.get("name") or f"目标{t.get('id')}"
    hwnd, _shop_disp = _resolve_probe_hwnd(core, t)
    if not hwnd:
        steps.append("定位目标窗口失败（句柄失效或关键词匹配失败）")
        return {"ok": False, "steps": steps, "missing": ["目标窗口"], "waited": 0, "url": url}
    core.log(f"[{name}] [新标签页导航] 目标窗口 hwnd=0x{hwnd & 0xFFFFFFFFFFFFFFFF:X}，Ctrl+T 新增标签页后导航")
    try:
        import uiautomation as auto
    except Exception as e:
        steps.append(f"uiautomation 初始化异常：{e}")
        return {"ok": False, "steps": steps, "missing": ["uiautomation 初始化"], "waited": 0, "url": url}

    # ---- 1. 前台模式：临时恢复窗口并聚焦（新增标签页需要键盘事件送达窗口） ----
    try:
        user32 = ctypes.windll.user32
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
        try:
            core.ops_log(f"[窗口] 恢复窗口 hwnd=0x{hwnd & 0xFFFFFFFFFFFFFFFF:X}（新标签页导航 SW_RESTORE + SetForegroundWindow）")
        except Exception:
            pass
    except Exception as e:
        steps.append(f"恢复/聚焦窗口失败（忽略，继续尝试）：{e}")
    prev_fg = None
    try:
        prev_fg = ctypes.windll.user32.GetForegroundWindow()
    except Exception:
        prev_fg = None

    # ---- 2. Ctrl+T 新增标签页（keybd_event 模拟，Chromium 新标签页地址栏自动聚焦） ----
    ctrl_pressed = False
    try:
        user32 = ctypes.windll.user32
        user32.keybd_event(0x11, 0, 0, 0)  # VK_CONTROL down
        ctrl_pressed = True
        user32.keybd_event(0x54, 0, 0, 0)  # 'T' down
        user32.keybd_event(0x54, 0, 2, 0)  # 'T' up
        user32.keybd_event(0x11, 0, 2, 0)  # VK_CONTROL up
        ctrl_pressed = False
        steps.append("Ctrl+T 已发送（keybd_event）")
    except Exception as e:
        if ctrl_pressed:
            try:
                ctypes.windll.user32.keybd_event(0x11, 0, 2, 0)
            except Exception:
                pass
        steps.append(f"Ctrl+T 发送失败: {e}")
        for s in steps:
            _ops_log_line(core, name, s)
        return {"ok": False, "steps": steps, "missing": ["Ctrl+T"], "waited": 0, "url": url}
    # 新标签页打开：等待地址栏可用（约 0.3~0.5s，保持"尽快"）
    time.sleep(0.5)

    # ---- 3. 收集控件树 + 查找 Omnibox（新标签页地址栏） ----
    infos, truncated = _collect_reload_tree(hwnd, auto)
    steps.append(f"控件树收集完成：{len(infos)} 个控件{'（已达上限截断）' if truncated else ''}")
    om = _find_omnibox(infos)
    if om is None:
        steps.append(f"未找到地址栏（控件树中无 aid={_OMNIBOX_AID} / Name 含『{_OMNIBOX_NAME}』的 Edit）")
        for s in steps:
            _ops_log_line(core, name, s)
        return {"ok": False, "steps": steps, "missing": ["地址栏"], "waited": 0, "url": url}
    steps.append(f"找到地址栏：{_reload_candidate_line(om)}")
    url_before = _omnibox_url(om)
    steps.append(f"新标签页导航前快照：URL={url_before or '(空)'}")

    # ---- 4. UIA 写入地址栏 + 回车导航（焦点加固同 _uia_goto_url） ----
    entered = False
    try:
        om_ctrl = om["ctrl"]
        try:
            sp = om_ctrl.GetSetFocusPattern() if hasattr(om_ctrl, "GetSetFocusPattern") else None
        except Exception:
            sp = None
        try:
            vp = om_ctrl.GetValuePattern()
        except Exception:
            vp = None
        if vp is None:
            steps.append("地址栏无 ValuePattern，无法写入 URL")
            for s in steps:
                _ops_log_line(core, name, s)
            return {"ok": False, "steps": steps, "missing": ["地址栏 ValuePattern"], "waited": 0, "url": url}
        vp.SetValue(url)
        steps.append("ValuePattern.SetValue(url) 写入成功")
        try:
            if sp is not None:
                sp.SetFocus()
                steps.append("SetFocus 地址栏成功")
            else:
                om_ctrl.SetFocus()
                steps.append("SetFocus 地址栏成功（控件级）")
        except Exception as e:
            steps.append(f"SetFocus 地址栏失败（继续尝试回车）: {e}")
        try:
            time.sleep(0.1)
            user32 = ctypes.windll.user32
            user32.keybd_event(0x0D, 0, 0, 0)
            user32.keybd_event(0x0D, 0, 2, 0)  # KEYEVENTF_KEYUP
            entered = True
            steps.append("SendInput 回车已发送（keybd_event VK_RETURN）")
        except Exception as e:
            steps.append(f"SendInput 回车失败: {e}，改用 PostMessage")
            try:
                user32 = ctypes.windll.user32
                user32.PostMessageW(hwnd, 0x0100, 0x0D, 0)
                user32.PostMessageW(hwnd, 0x0101, 0x0D, 0)
                entered = True
                steps.append("PostMessage 回车已投递")
            except Exception as e2:
                steps.append(f"PostMessage 回车也失败: {e2}")
    except Exception as e:
        steps.append(f"地址栏写入/导航异常: {e}")
        for s in steps:
            _ops_log_line(core, name, s)
        return {"ok": False, "steps": steps, "missing": ["地址栏写入"], "waited": 0, "url": url}
    if not entered:
        for s in steps:
            _ops_log_line(core, name, s)
        return {"ok": False, "steps": steps, "missing": ["回车导航"], "waited": 0, "url": url}
    # 还原前台焦点（AttachThreadInput + BringWindowToTop + SetForegroundWindow，失败静默）
    if prev_fg:
        try:
            user32 = ctypes.windll.user32
            cur_tid = ctypes.windll.kernel32.GetCurrentThreadId()
            fg_tid = user32.GetWindowThreadProcessId(prev_fg, ctypes.byref(ctypes.c_ulong()))
            if fg_tid:
                user32.AttachThreadInput(cur_tid, fg_tid, True)
                try:
                    user32.BringWindowToTop(prev_fg)
                    user32.SetForegroundWindow(prev_fg)
                finally:
                    user32.AttachThreadInput(cur_tid, fg_tid, False)
        except Exception:
            core.log(f"[{name}] [新标签页导航] 焦点还原失败（忽略）", level="debug")

    # ---- 5. 就绪轮询（表格就绪为主信号，每 2s 探测一次；URL 变化仅辅助日志）----
    try:
        poll_sec = max(5, int(float(core.cfg.get("refresh_wait_sec", 8) or 8)))
    except (ValueError, TypeError):
        poll_sec = 8
    poll_interval = 2.0
    elapsed = 0.0
    ready = False
    ready_reason = ""
    url_changed = ""
    while elapsed < poll_sec:
        time.sleep(poll_interval)
        elapsed += poll_interval
        url_now, rows_now, header_hits, ctrl_count = _probe_table_ready(hwnd, auto)
        if url_now and url_now != url_before and not url_changed:
            url_changed = url_now
        if _ready_by_table(rows_now, header_hits):
            ready = True
            ready_reason = f"表格已就绪（行数={rows_now}，表头命中={header_hits}"
            if url_changed:
                ready_reason += f"；URL 变化辅助记录={url_changed[:80]}"
            ready_reason += "）"
            break
    if not ready:
        url_now, rows_now, header_hits, ctrl_count = _probe_table_ready(hwnd, auto)
        if url_now and url_now != url_before and not url_changed:
            url_changed = url_now
        steps.append(
            f"就绪超时快照：表格行数={rows_now}，表头命中={header_hits}，控件数={ctrl_count}，"
            f"URL变化={'已变化' if url_changed else '未变化'}（轮询{poll_sec:.0f}s）"
        )
        ready_reason = f"等待 {poll_sec:.0f}s 表格仍未就绪（行数={rows_now}、表头未出现），按失败处理"
    steps.append(f"就绪轮询：{ready_reason}（等待 {elapsed:.1f}s）")
    for s in steps:
        _ops_log_line(core, name, s)
    core.log(f"[{name}] [新标签页导航] 导航完成，{ready_reason}")
    return {"ok": ready, "steps": steps, "missing": missing, "waited": elapsed, "url": url}


def _date_filter_menu(api):
    """v10.12：『按日期过滤』按钮回调——输入起止日期→构造 URL（替换 list_start_date/end_date）
    →地址栏输入→回车导航。页面过滤显示效果需工作电脑实测。"""
    import threading as _th
    from tkinter import simpledialog
    core = api.get_core()
    try:
        t = core._current_target()
    except Exception:
        t = None
    if not t:
        core.log("按日期过滤：未定位到当前目标，请先设置监控目标", level="debug")
        return
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    start = simpledialog.askstring("按日期过滤查看", "起始日期（YYYY-MM-DD，留空=今天）", parent=core.root)
    if start is None:
        return
    end = simpledialog.askstring("按日期过滤查看", "结束日期（YYYY-MM-DD，留空=今天）", parent=core.root)
    if end is None:
        return
    start = (start or "").strip() or today
    end = (end or "").strip() or today
    start_ms = _utc_ms(start)
    end_ms = _utc_ms(end)
    if start_ms is None or end_ms is None:
        core.log("按日期过滤：日期格式错误，请使用 YYYY-MM-DD", level="debug")
        return
    if start_ms > end_ms:
        core.log("按日期过滤：起始日期不能晚于结束日期", level="debug")
        return
    end_ms += 86399999  # 结束日含当天 23:59:59.999 UTC
    core.log(f"按日期过滤：输入 {start} ~ {end}（UTC 毫秒 {start_ms} ~ {end_ms}）")
    try:
        _tid = t.get("id")
        core.ops_log(f"按日期过滤：目标={t.get('name') or f'目标{_tid}'}，起止 {start} ~ {end}")
    except Exception:
        pass

    def _worker():
        try:
            url_before, hwnd, _infos = _uia_current_url(core, t)
            new_url = _replace_date_params(url_before, start_ms, end_ms)
            if not new_url:
                core.log("按日期过滤：无法读取当前地址栏 URL，无法构造日期参数 URL（需先打开广告计划列表页）", level="debug")
                return
            try:
                core.ops_log(f"按日期过滤：原URL={url_before}，新URL={new_url}")
            except Exception:
                pass
            res = _uia_goto_url(core, new_url, t)
            core.log(f"按日期过滤：导航结果={'成功' if res.get('ok') else '失败'}（就绪等待 {res.get('waited', 0):.1f}s）")
        except Exception as e:
            core.log(f"按日期过滤：异常 {e}", level="debug")

    _th.Thread(target=_worker, daemon=True, name="date-filter").start()


# v10.19：地址栏读取/按日期过滤导航/新标签页导航整段持全局点击锁（RLock 可重入）。
# 低频操作与 reload 点击段互斥，避免并行刷新期间互抢前台/键盘焦点/控件树；
# reload_today 在锁外先读 URL 时自身持锁收集树，与并发点击段天然串行。
_uia_current_url = _with_click_lock(_uia_current_url)
_uia_goto_url = _with_click_lock(_uia_goto_url)
_uia_goto_url_new_tab = _with_click_lock(_uia_goto_url_new_tab)


def setup(api):
    """mod 入口：向核心注册 uia 识别器"""
    api.register_recog_mode(
        mode_id="uia",
        name="控件树UIA",
        recognize_fn=recognize_fn,
        config_keys=["uia_scroll_collect", "uia_wait_sec", "uia_max_scroll",
                     "uia_max_nodes", "uia_timeout_sec", "uia_max_depth", "cdp_port",
                     "uia_structured",
                     "mouse_click_fallback"],
        # v10.5：能力声明——UIA 按控件树读取，不依赖 ROI 截图区域，声明空依赖
        requires_sections=[],
    )
    # v10.7.3 路线C：注册控件操作能力到核心（主程序按钮/定期刷新经 core._uia_mod_actions 调用）。
    # 控件探测 = _uia_ctrl_probe(core, target)；筛选刷新 = _uia_filter_refresh(core, target)。
    # 主程序侧无 mod 时优雅降级：_uia_action() 取不到则提示 mod 未加载，不影响其他功能。
    try:
        core = api.get_core()
        core._uia_mod_actions = {
            "probe": _uia_ctrl_probe,
            "filter_refresh": _uia_filter_refresh,
            # v10.8.2：UIA 点击浏览器『重新加载』按钮（uia_reload）
            # v10.17：升级为『当天重载』——刷新前自动校正日期到当天（跨天自动追踪当天数据）
            "reload": _uia_reload_today,
            # v10.12：地址栏输入日期参数 URL 并回车导航（按日期过滤查看）
            "goto_url": _uia_goto_url,
            # v10.19：刷新异常分类统计（进程内跨轮累计；主程序轮末读取摘要/停止时清零）
            "refresh_stats": _refresh_stats_snapshot,
            "refresh_stats_reset": _refresh_stats_reset,
        }
        # v10.12：注册『按日期过滤』按钮（点击后输入起止日期→地址栏导航）
        if hasattr(api, "register_menu"):
            try:
                api.register_menu("按日期过滤", _date_filter_menu)
            except Exception as e:
                api.log(f"按日期过滤菜单注册失败: {e}")
        api.log("控件操作能力已注册：probe(控件探测) / filter_refresh(筛选刷新) / reload(重载刷新) / goto_url(按日期过滤导航)")
    except Exception as e:
        api.log(f"控件操作能力注册失败: {e}")