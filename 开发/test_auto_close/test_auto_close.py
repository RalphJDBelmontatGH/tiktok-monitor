# -*- coding: utf-8 -*-
"""
test_auto_close.py —— TK监控器「自动启停广告计划」独立验证程序（v0.2 open 优先版）

背景：用户诉求「帮我关闭」= 自动暂停空烧/低 ROI 广告计划（最终全自动、全店适用）。
主程序 auto_close_ad() 目前仅占位打日志，真实操作未开工。按老规矩先用独立小型测试程序验证
UIA 控件级点击链路，稳定后再合入主程序（不用 pyautogui 坐标，控件级方案见 Roadmap）。

v0.2 策略反转（用户 2026-09-04 提议）：当时手里只剩一个店，计划金贵，直接拿「暂停」验证
风险高（误停正在投的计划）。改为先验证「打开/启用」链路——对已暂停计划执行启用操作，若 UIA
能点开一个计划，说明行内操作按钮点击链路成立，再把方向切回 --mode close 做真正的自动暂停。
启用点错了最多重新暂停，代价远低于误停空烧计划。

--mode 双方向（默认 open，安全优先）：
  open  ：针对状态=已暂停的行，找「启用/开启/恢复投放」类按钮并点击（验证链路）
  close ：针对状态=投放中/已生效的行，找「暂停」类按钮并点击（v0.2 最终目标）

本程序目标（分阶段，先验证再点）：
  1. 定位店铺窗口（复用 trf 窗口定位，自动 Axxx / --keyword 指定）
  2. 收集页面控件树，识别广告计划表格数据行（core-table-tr，同主程序）+ 每行状态
     （状态列文本含 已暂停/投放中/已生效/已结束）
  3. 逐行按 --mode 扫描目标动作按钮，分三档报告 UIA 可达路径：
     L1 行内 name 精确为目标词（如「启用」/「暂停」）的 Button（最理想：直接 Invoke 即可）
     L2 行内任何控件 name 精确为目标词
     L3 行内 name 拼接/包含目标词（如计划名列父 DataItem 拼接，需 hover/展开验证）
     同时 dump 行内其它操作按钮（数据分析/编辑/删除/复制/推广/查看报告/更多⋯）作对照。
  4. 默认 dry-run：只探测 + 截图，绝不点击。
     --go --row N：对第 N 行执行「点目标按钮」验证 —— 点击后探测弹窗/确认层并 dump 按钮；
     弹窗按钮默认不点；再加 --confirm <按钮词> 才会点确认（两段式授权，防误伤）。
  5. 全程日志落盘 + 截图留存。

用法：
  python test_auto_close.py                        # 默认 open+dry-run 探测，自动识别店铺
  python test_auto_close.py --keyword A244         # 指定店铺窗口
  python test_auto_close.py --mode close           # 切到关闭方向探测（找暂停按钮）
  python test_auto_close.py --go --row 3           # 对第3行执行目标按钮点击验证（弹窗只 dump）
  python test_auto_close.py --go --row 3 --confirm 启用   # open 模式连弹窗确认一起点（真启用！）
  python test_auto_close.py --go --row 3 --confirm 暂停   # close 模式：优先词匹配，未命中时弹窗被证实且有"确认"按钮则兜底点确认（真暂停！）
  python test_auto_close.py --go --row 3 --confirm 确认   # v0.8 close 推荐写法：直接命中弹窗"确认"按钮（真暂停！）
  python test_auto_close.py --render               # 收集渲染子窗口树（默认关闭，先主窗口试）
  python test_auto_close.py --no-pause             # 结束不暂停（默认暂停便于看输出）

安全提示：本程序点的是真实广告计划列表里的按钮。open 默认方向点启用=恢复投放，close 方向
点确认=计划真实暂停。请先用 --row 确认行，再决定是否加 --confirm。

内置 config（v0.2.1）：测试程序所需的运营参数直接内置在文件头 BUILTIN_CFG 常量区
（窗口关键词/ROI 止损线/消耗门槛/观察额度），与部署 config.json 对齐但独立维护——
跑测试不用另带 config.json，也不会出现复制多份改错副本的问题。

日志与截图（v0.2.2）：全部输出到程序同级目录（打包版=exe 所在目录；源码版=脚本目录），
日志按天轮转 test_auto_close_YYYYMMDD.log 追加不覆盖、行带时间戳，方便追溯对照；
控制台只同步显示（信息不再只进 _MEI 临时目录）。

v0.5 修复（2026-09-04）：uiautomation 控件类型名带 Control 后缀（TextControl/ButtonControl），
旧代码用无后缀短名比较全部失效 → 统一 _ct_short()/_ct_in() 截短再判断，Text/行文本/按钮扫描恢复。
诊断截图(22:53)确认：已暂停筛选下 10 行 paused 状态全部正确识别，但行内无"启用"按钮候选。

v0.6 开关区侦查（2026-09-04）：人工 hover 截图发现每行最左侧有一个灰色圆形滑动开关
（关闭=已暂停，打开=投放中），这才是启停真入口；三点菜单里只有重命名/删除。
但 UIA 行内扫描从未见过该开关——怀疑开关无 name（被 row_ctrls_in_rect 的 not name 过滤丢弃）
或不在 CTRL_TYPES_CLICKABLE 白名单（如 ToggleButton/Pane/Image/Custom）。
v0.6 对数据行最左侧 160px 竖带做全量 dump（不限 name/类型），定位开关控件的真实 UIA 身份；
若该带内无任何控件，则证明开关是纯 CSS/aria-hidden，需改走其它入口（勾选批量/编辑页）。

v0.7 开关直点（2026-09-04）：v0.6 两轮 dump 已确认启停开关真实身份 = 行左带内无 name 的
ButtonControl，cls 稳定含 theme-arco-switch-type-circle；勾选态（白钮/运行中）cls 额外含
theme-arco-switch-checked，未勾选态（灰钮/已暂停）无该字样；附加业务类 theme-m4b-switch /
switch-column-r-oH 时有时无，不作识别依据。用户拍板：程序只需 UIA 找到开关元素直接作用，
不依赖 OCR/颜色/截图判态。v0.7 主逻辑 = 逐行定位开关 → 状态以 cls 含 checked 为准 →
dry-run 只报不点 → --go --row N 坐标点击真实切换 → 点击后重收树验证开关翻转。
（判定模式沿用 open=启用灰钮计划 / close=暂停白钮计划，点前校验开关状态防误点。）

v0.8 close 弹窗确认修复（2026-09-05）：v0.7 exe 三次 close --go 实测，点白钮后肉眼未见关闭。
人工验证：手动关闭会弹"是否暂停此推广系列？"窗口，按钮为「确认/取消」，Tab+Enter 可确认。
根因：CONFIRM_CLOSE 词表缺"确认"二字（只有暂停/停用/停止…），弹窗"确认"按钮匹配失败不点击；
且点击后固定等 3s 只收一次树，after 截图常错过弹窗出现窗口。v0.8 改动：
  1. CONFIRM_CLOSE 词表补"确认"（弹窗按钮文字即"确认"）；
  2. 点击后改为轮询（最多 6 次/约 12s）：每轮检测 ①目标行开关翻转(直接生效) ②弹窗出现
     （确认按钮候选 + "取消"按钮探针——行内无"取消"字样，弹窗必有取消钮，作为强存在信号）；
  3. 弹窗处理：未传 --confirm 只提示（close 建议 --confirm 确认）；传了则先按词匹配，
     若词未命中但弹窗被"取消"探针证实且存在 name 含"确认"的按钮 → 兜底点击该确认钮；
  4. 翻转成功视为直接生效无弹窗，跳过无谓弹窗扫描，直接收尾截图。
（键盘兜底：人工 Tab+Enter 仍可用；程序侧不以全局 SendKeys 乱发 Tab，避免焦点错位误点。）

v0.9.5 用户并行防干扰 + 防误点加固（2026-09-05，合入主程序前评审确定）：
  1. A-空闲检测：前台独占动作（置前/坐标点开关/点确认）前用 GetLastInputInfo 探测用户键鼠空闲
     满 2s 才执行；用户一直忙超过 60s 本轮放弃（--no-wait-idle 可关，调试用）；
  2. F3-点前重收树：--go/--ask 真点前重新收树并按行号重定位目标行，plan_name 与 dry-run 快照
     双重校验不一致即中止，严禁拿旧 rect 坐标直接点（防页面被用户/网络切走时误点错行）；
  3. F3-offscren 不硬点：目标开关在视口外先 ScrollItemPattern 滚动进可视区并重定位，滚动不了中止；
  4. F6-轮询加长：点后最多 10 轮（约 20s+收树耗时），照顾 TK 网络慢导致翻转/弹窗延迟；
  5. F6-弹窗双按钮校验：暂停确认弹窗须「确认类按钮 + 取消按钮」同现且两者 rect 均在表格行外
     （网络报错层/其它浮层不满足 → 不自动点，只提示人工看截图）。

v0.11 内置规则自动选靶（2026-09-05，老板拍板测试阶段）：
  1. config 彻底内置：删除外部 test_auto_close_cfg.json 加载与回退交互秒退问题，
     双击 exe 直接读文件头 BUILTIN_CFG['dumb_close'] 规则执行，无任何外部文件依赖；
  2. 双击流程：扫描投放中(白钮)行 → 解析每行 ROI/成本/订单数 → 命中空烧规则
     （ROI=0 且 SKU订单数=0 且 成本≥$5）即候选 → 无候选：弹提示并退出，不做任何操作；
     有候选：弹 Windows「是/否」确认框逐条列出计划名+三指标，点【是】才逐条关闭；
  3. 逐条关闭复用 v0.9.5 完整安全链路（点前重收树/空闲检测/offscreen 滚动/轮询/弹窗双按钮校验）；
     前一条关闭导致列表重排时，按计划名全表重定位再继续，防止误点与漏点。

v0.14 视口外自动滚动修复（2026-09-06）：v0.13 工作机实测滚轮无效，根因为悬停点 x 取行
  rect 中心飞出客户区（Chromium 虚拟布局坐标）、无可见行时 y 锚错。v0.14 将悬停点 clamp
  进客户区安全带、无可见行悬停 y 偏客户区下半区、滚轮前 WindowFromPoint 命中校验。
  详见文件内 v0.14 视口外自动滚动区块。engine 版（auto_close_engine.py）已同步合入。
"""
import argparse
import os
import re
import sys
import time
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
_TRF_DIR = os.path.join(os.path.dirname(BASE_DIR), "test_refresh_filter")
sys.path.insert(0, _TRF_DIR)

import test_refresh_filter as trf  # noqa: E402

# Windows 控制台默认 GBK：日志可能含 emoji/特殊字符，print 可能抛 UnicodeEncodeError；
# 保持原编码只把无法编码字符替换为 ?（与 test_parallel_refresh 一致）。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors="replace")
    except Exception:
        pass

# 输出目录：打包版=exe 同级目录（__file__ 在 PyInstaller 单文件下指向 _MEI 临时解压目录，
# 绝不能拿来存日志/截图——临时目录每次运行变化且会被清）；源码版=脚本所在目录。
if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    APP_DIR = BASE_DIR

# 日志每次启动按秒新建（不再按天追加，避免多次运行混在当天文件里难追溯）；行内带 [HH:MM:SS]
LOG_PATH = os.path.join(APP_DIR, f"test_auto_close_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")

# v0.11：config 内置（老板拍板：测试阶段不靠外部配置文件，双击 exe 直接用内置规则自动选靶）
# 无外部 cfg、无交互输入——一切参数都改文件头 BUILTIN_CFG，改完重新打包即可。
TABLE_ROW_CLASS = trf.TABLE_ROW_CLASS
HEADER_WORDS = trf.READY_HEADER_STRONG + trf.READY_HEADER_WEAK
# 行内操作按钮词集合（与主程序 OCR 路径观察一致；另有更多按钮 ⋯ 多为无 name/图标型）
ROW_ACTION_WORDS = ("暂停", "数据分析", "修改", "编辑", "删除", "复制", "推广", "查看报告",
                    "更多", "操作", "⋯", "···")
# 行状态词（状态列文本；unknown 表示未识别状态，点击前需人工确认）
STATUS_PAUSED_WORDS = ("已暂停", "暂停中")
STATUS_RUNNING_WORDS = ("投放中", "已生效", "启用中", "投放")

# 双方向动作词表
OPEN_WORDS = ("启用", "开启", "恢复投放", "恢复", "重新启用", "重新开启", "激活", "启动", "开始投放")
CLOSE_WORDS = ("暂停", "停用", "停止投放", "停止")

# 弹窗确认按钮候选词（按 mode 两套）
CONFIRM_OPEN = ("启用", "确认", "确定", "开启", "恢复投放", "重新启用")
CONFIRM_CLOSE = ("暂停", "停用", "停止", "停止投放", "确认暂停", "确定暂停", "确认")  # v0.8: 补"确认"——关闭弹窗按钮文字为"确认/取消"，缺词导致匹配失败不点击

# v0.12.1 弹窗按钮样式特征（根因A修复）：TK 暂停确认弹窗按钮 UIA ClassName 带 ARCO 主/次按钮
# 样式（theme-arco-btn-primary=确认、theme-arco-btn-secondary=取消）；行内普通操作按钮
# （数据分析/编辑/删除/开关等）不带此样式。find_confirm_dialog 行 rect 区域排除时据此豁免
# 弹窗按钮，避免"弹窗按钮 rect 落入数据行条带被误剔除"导致自动确认不点。
DIALOG_BTN_STYLE_MARKS = ("theme-arco-btn-primary", "theme-arco-btn-secondary")

# ================= 内置参数区（唯一维护处，勿外置复制 config.json） =================
# 背景：老板踩过"复制外部 config 改错"的坑，测试程序参数直接内置成常量——
# 跑测试不用再带/拷一份部署 config.json，也不存在多副本改串的问题。
# 对齐基准：部署\10.20\config.json（2026-09-04 运行版，单店运营配置）。
# 若主程序 config 调整，只需同步改这里一处，其余逻辑自动跟随。
BUILTIN_CFG = {
    # 店铺窗口关键词（= 部署 config.targets[0].window_keyword；现只剩一个店：商户中心）
    "window_keyword": "商户中心",
    # ROI 止损告警线（config.alert_threshold）：ROI 低于此值视为亏损走量
    "alert_threshold": 0.6,
    # ROI 告警前置消耗门槛（config.roi_alert_cost_min）：累计消耗须超过才进入自动关闭候选
    "roi_alert_cost_min": 20.0,
    # 新计划宽松观察额度（config.grace_cost）：累计消耗未达前只提示不自动关
    "grace_cost": 20.0,
    # v0.11 双击傻瓜模式规则（老板拍板 2026-09-05）：
    #   空烧判定：ROI=0 且 SKU订单数=0 且 成本≥cost_min USD → 自动关闭（暂停）；
    #   无候选 → 直接提示，不做任何操作；有候选 → 弹 Windows 确认框逐条列出，点是才真关。
    "dumb_close": {
        "enabled": True,          # 双击 exe 时启用（无任何命令行参数即触发）
        "roi_max": 0.0,           # ROI 小于等于此值视为空烧（0 或负）
        "order_max": 0,           # SKU 订单数小于等于此值视为零单
        "cost_min": 5.0,          # 成本 >= 此值(USD) 才算烧够、值得关
    },
}

CTRL_TYPES_CLICKABLE = ("Button", "DataItem", "ListItem", "MenuItem", "Hyperlink",
                        "TabItem", "CheckBox", "RadioButton", "ComboBox", "TreeItem")


def _ct_short(ct):
    """uiautomation ControlTypeName 实际返回 'TextControl'/'DataItemControl' 等带
    'Control' 后缀，历史代码用无后缀短名比较全部失效 → 统一截短再判断。"""
    try:
        if ct.endswith("Control"):
            return ct[: -len("Control")]
    except Exception:
        pass
    return ct


def _ct_in(ct, names):
    return _ct_short(ct) in names


def log(msg):
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as e:
        # v0.9.3：写盘失败不再静默吞——控制台明确提示，便于定位"无log"根因
        try:
            print(f"[log写盘失败] {e} -> {LOG_PATH}", flush=True)
        except Exception:
            pass


# ---------------- 控件收集（带坐标/越屏标记，子线程防卡死） ----------------
def _collect_infos_with_rect(auto, root, max_nodes=12000, timeout_sec=20.0):
    """收集整棵子树控件信息，含 rect/offscreen/depth。放子线程防复杂 Chromium 树卡死主流程。"""
    import threading
    result = {"infos": []}

    def _safe_str(fn):
        try:
            v = fn()
            return str(v or "")
        except Exception:
            return ""

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
            if depth > 0:  # root 本身跳过，省一点噪音
                info = {
                    "ctrl": ctrl,
                    "ct": _safe_str(lambda: ctrl.ControlTypeName),
                    "name": trf._ctrl_name(ctrl),
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
        log("[收集] 子树收集超时，放弃该子树（可能返回部分数据）")
    return result["infos"]


def collect_all(auto, hwnd, with_render=True):
    """主窗口 + 可选渲染子窗口整树收集，返回 info 列表。"""
    out = []
    root, err = trf._control_from_handle_safe(auto, hwnd, timeout=10.0)
    if root is None:
        log(f"[收集] ControlFromHandle(0x{hwnd & 0xFFFFFFFFFFFFFFFF:X}) 失败: {err or 'None'}")
    else:
        t0 = time.time()
        infos = _collect_infos_with_rect(auto, root)
        out.extend(infos)
        log(f"[收集] 主窗口控件 {len(infos)} 个，耗时 {time.time() - t0:.1f}s")
    if with_render:
        try:
            sub = trf._find_render_sub_hwnd_safe(hwnd)
            if sub and sub != hwnd:
                t0 = time.time()
                sroot, err2 = trf._control_from_handle_safe(auto, sub, timeout=10.0)
                if sroot is not None:
                    infos = _collect_infos_with_rect(auto, sroot)
                    out.extend(infos)
                    log(f"[收集] 渲染子窗口(0x{sub & 0xFFFFFFFFFFFFFFFF:X}) 控件 {len(infos)} 个，耗时 {time.time() - t0:.1f}s")
        except Exception as e:
            log(f"[收集] 渲染子窗口收集异常: {e}")
    return out


# ---------------- 表格行扫描 ----------------
def _rect_key(r):
    try:
        return r[1] * 100000 + r[0]
    except Exception:
        return 0


def _inside(inner, outer, pad=4):
    """inner rect 是否落在 outer rect 内（含少量容差）。"""
    if not inner or not outer:
        return False
    x1, y1, x2, y2 = inner
    X1, Y1, X2, Y2 = outer
    return (x1 >= X1 - pad and y1 >= Y1 - pad and x2 <= X2 + pad and y2 <= Y2 + pad)


def _is_header_row(row_info, words):
    """表头行：行名命中表头词数最多；数据行一般只命中 0~1 个弱词。"""
    hits = sum(1 for w in HEADER_WORDS if w in row_info["text"])
    return hits >= 2


# v0.9.4：行文本/计划名修复（2026-09-05 工作机 dry-run 实测读串）
# 现象：计划行左侧固定列（计划名 DataItemControl）未并入"行文本"，行文本全是右半报表数字列，
#       行号无法对照计划（log 里计划名只出现在"其它[数据分析]"DataItem 内）。
# 修复：行 rect 内文本控件按 x 从左到右聚合（不依赖 DFS 顺序）；显式提取 plan_name 挂到每行。
ROW_TEXT_TYPES = ("Text", "Button", "DataItem", "Edit", "Hyperlink", "Custom", "Group", "ListItem")


def extract_plan_name(raw):
    """从 DataItem/Text name 剔除行内动作按钮词，保留计划名前缀。
    例：'黑金刚喷头A 1重建 数据分析 修改 优化' -> '黑金刚喷头A 1重建'。"""
    if not raw:
        return ""
    out = raw
    # DataItem name 尾部常带"数据分析 修改 优化"等按钮词；"优化"不在全局 ROW_ACTION_WORDS（避免影响动作候选统计），仅在此局部剔除
    for w in tuple(ROW_ACTION_WORDS) + ("优化",):
        out = out.replace(w, " ")
    out = " ".join(out.split())
    return out.strip(" |⋯·-")


def _collect_row_text(infos, rc):
    """行 rect 内全部文本/DataItem 控件按从左到右聚合；返回 (text, plan_name)。"""
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
    for c in parts:  # 计划名 = 最左 DataItem/Text 剔除按钮词后的前缀
        if not _ct_in(c["ct"], ("DataItem", "Text")):
            continue
        cand = extract_plan_name(c["name"])
        if cand:
            plan = cand
            break
    return " | ".join(text_parts), plan


def scan_rows(infos):
    """返回 (rows, header_row)：rows=[{'no':1,'text':...,'plan':...,'rect':...,'ctrls':[行内info]}] 数据行（不含表头）。"""
    row_ctrls = [i for i in infos if TABLE_ROW_CLASS in i["cls"] and i["rect"]]
    if not row_ctrls:
        return [], None
    rows_raw = []
    for rc in row_ctrls:
        text, plan = _collect_row_text(infos, rc)
        rows_raw.append({"ctrl": rc, "rect": rc["rect"], "text": text, "plan": plan})
    rows_raw.sort(key=lambda r: _rect_key(r["rect"]))
    # 表头识别：文字命中表头词数 >=2 的第一行
    header_row = None
    for r in rows_raw:
        if _is_header_row(r, HEADER_WORDS):
            header_row = r
            break
    data_rows = [r for r in rows_raw if r is not header_row]
    for i, r in enumerate(data_rows, 1):
        r["no"] = i
    return data_rows, header_row


def row_ctrls_in_rect(infos, rect, clickable_only=True):
    """行 rect 内的控件（可点击优先）。"""
    got = []
    for i in infos:
        if i["rect"] is None or not i["name"]:
            continue
        if not _inside(i["rect"], rect, pad=2):
            continue
        if clickable_only and not _ct_in(i["ct"], CTRL_TYPES_CLICKABLE):
            continue
        got.append(i)
    return got


def find_action_candidates(row_ctrls, action_words):
    """在行控件里找目标动作按钮（如启用/暂停），分 L1/L2/L3。
    L1: Button 且 name 精确等于任一动作词；L2: 其它控件 name 精确等于；L3: name 包含任一动作词。"""
    l1, l2, l3 = [], [], []
    for c in row_ctrls:
        nm = c["name"]
        ct = c["ct"]
        if _ct_in(ct, ("Button",)) and nm in action_words:
            l1.append(c)
        elif nm in action_words:
            l2.append(c)
        elif any(w in nm for w in action_words):
            l3.append(c)
    return l1, l2, l3


def row_status(text):
    """根据行文本粗判状态（仅展示辅助，决策以启停开关为准）：
    paused=已暂停/暂停中；nobudget=未投放（开关开着但无余额，不烧钱）；
    running=投放中/已生效/启用中；unknown=未识别。"""
    if not text:
        return "unknown"
    if any(w in text for w in STATUS_PAUSED_WORDS):
        return "paused"
    if "未投放" in text:  # 必须先于 running 判断（"未投放"含子串"投放"）
        return "nobudget"
    if any(w in text for w in STATUS_RUNNING_WORDS):
        return "running"
    return "unknown"


def _diag_dump(infos, data_rows):
    """诊断模式：定位页面文字到底在哪些控件里（行文本为空时的关键证据）。"""
    from collections import Counter
    # 0) 行容器自身原始信息（确认是不是真的表格行/有没有找错层级）
    log(f"[诊断] 数据行容器共 {len(data_rows)} 个，前3个原始信息:")
    for r in data_rows[:3]:
        rc = r.get("ctrl")
        if rc is not None:
            log(f"        {_fmt_ctrl(rc)}")
    # 1) 全部有 name 的 Text 控件（前60）
    texts = [i for i in infos if i["name"] and _ct_in(i["ct"], ("Text",))]
    log(f"[诊断] 树中共有带 name 的 Text 控件 {len(texts)} 个")
    for c in texts[:60]:
        log(f"    [Text] {_fmt_ctrl(c)}")
    # 2) 首行 rect 内全部控件类型分布 + 有 name 的控件
    if data_rows:
        r0 = data_rows[0]
        inner = [i for i in infos if i["rect"] and _inside(i["rect"], r0["rect"], pad=2)]
        log(f"[诊断] 行#1 rect={r0['rect']} 内控件共 {len(inner)} 个，类型分布: "
            + ", ".join(f"{k}x{v}" for k, v in Counter(i['ct'] for i in inner).most_common(15)))
        named = [i for i in inner if i["name"]]
        log(f"[诊断] 行#1 rect 内有 name 控件 {len(named)} 个:")
        for c in named[:40]:
            log(f"        {_fmt_ctrl(c)}")
    # 3) 行 rect 正上方区域（表头/列名可能不在 core-table-tr 里）
    if data_rows:
        r0 = data_rows[0]
        above = [i for i in infos if i["name"] and i["rect"]
                 and i["rect"][1] < r0["rect"][1] and i["rect"][3] > r0["rect"][1] - 200]
        log(f"[诊断] 首行上方 200px 内有 name 的控件 {len(above)} 个:")
        for c in above[:40]:
            log(f"        {_fmt_ctrl(c)}")
    # 4) 无 name 控件里有没有 Button 类型（可能按钮图标无文字）
    btns = [i for i in infos if _ct_in(i["ct"], ("Button",))]
    log(f"[诊断] 树中 Button 控件共 {len(btns)} 个（含无 name）:")
    for c in btns[:40]:
        log(f"        {_fmt_ctrl(c)}")


SWITCH_ZONE_PX = 160  # 行左边界向右的开关探测带宽度（TikTok 计划行启停开关在最左侧）


def dump_switch_zone(infos, data_rows, max_rows=5):
    """行最左侧开关带全量 dump（不限 name/类型）——定位启停开关控件的真实 UIA 身份。

    背景：人工 hover 截图确认每行最左侧有圆形滑动开关（关闭=已暂停/打开=投放中），
    但 row_ctrls_in_rect 按 not name 丢弃 + clickable 白名单过滤后从未见到它。
    本函数对行 rect 左边界向右 SWITCH_ZONE_PX 竖带内的控件做无过滤输出：
     - 若带内有 Button/ToggleButton/CheckBox/Pane/Image/Custom 等控件 → 开关真实身份在此；
     - 若带内无任何控件 → 开关不在 UIA 树（纯 CSS/aria-hidden），需改走其它入口。
    """
    from collections import Counter
    for r in data_rows[:max_rows]:
        x1, y1, _x2, y2 = r["rect"]
        zone = (x1, y1, x1 + SWITCH_ZONE_PX, y2)
        inner = [i for i in infos if i["rect"] and _inside(i["rect"], zone, pad=8)]
        log(f"[开关区] 行#{r['no']} rect={r['rect']} 左带(x<{x1 + SWITCH_ZONE_PX}) 状态={r.get('status')} "
            f"内控件 {len(inner)} 个，类型: "
            + ", ".join(f"{k}x{v}" for k, v in Counter(i['ct'] for i in inner).most_common(10)))
        if not inner:
            log("          <空> —— 该带内无任何 UIA 控件，开关疑似纯 CSS/aria-hidden，不在可访问性树")
            continue
        for c in inner[:12]:
            log(f"          {_fmt_ctrl(c)}")


def _fmt_ctrl(c):
    r = c["rect"]
    rs = f"({r[0]},{r[1]},{r[2]},{r[3]})" if r else "无rect"
    return (f"[{c['ct']}] name={c['name']!r} aid={c['aid']!r} cls={c['cls']!r} "
            f"rect={rs} off={c['offscreen']} d={c['depth']}")


# ================= v0.7 启停开关识别（UIA 直连，不依赖 OCR/颜色） =================
# v0.6 dump 实证：启停开关 = 行左带内无 name 的 ButtonControl（非 ToggleButton/CheckBox）。
# - 本体 cls 稳定含 theme-arco-switch-type-circle
# - 勾选态（白钮/运行中）额外含 theme-arco-switch-checked；未勾选（灰钮/已暂停）无
# - theme-m4b-switch / switch-column-r-oH 等附加业务类时有时无，禁止用作识别/判态依据
SWITCH_CLS_MARK = "arco-switch-type-circle"
SWITCH_CHECKED_MARK = "theme-arco-switch-checked"


def _area(rect):
    try:
        return (rect[2] - rect[0]) * (rect[3] - rect[1])
    except Exception:
        return 0


def find_row_switch(infos, row_rect):
    """在行左带（SWITCH_ZONE_PX）内定位启停开关本体。

    返回行内最里层（面积最小）命中 Button/ToggleButton 且 cls 含 arco-switch-type-circle
    的控件；找不到返回 None。调用方用 switch_is_checked() 判定当前勾选状态。"""
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
    """开关是否勾选/运行中（白钮）：cls 含 theme-arco-switch-checked。"""
    if not sw:
        return False
    return SWITCH_CHECKED_MARK in sw["cls"]


def switch_desc(sw):
    """开关状态的人类可读描述，供日志展示。"""
    if sw is None:
        return "未找到"
    return "白钮(checked=运行中)" if switch_is_checked(sw) else "灰钮(off=已暂停)"


# ---------------- 点击链路 ----------------
def do_invoke(ctrl_info):
    """InvokePattern 点击，返回 (ok, msg)。"""
    try:
        def _do():
            ip = ctrl_info["ctrl"].GetInvokePattern()
            if ip is None:
                raise RuntimeError("无 InvokePattern")
            ip.Invoke()
        return trf._ui_call_safe(_do, timeout=8.0, desc="Invoke")
    except Exception as e:
        return False, f"异常: {e}"


def click_by_coord(ctrl_info, hwnd=None):
    """坐标点击（替代兜底）：先激活目标窗口再鼠标左键点控件中心（若需要）。"""
    rect = ctrl_info["rect"]
    if not rect:
        return False, "无 rect，无法坐标点击"
    x = (rect[0] + rect[2]) // 2
    y = (rect[1] + rect[3]) // 2
    try:
        import ctypes
        if hwnd:
            # v0.9.2 修复：点击前必须激活目标窗口。此前未置前，
            # 输入行号后焦点在控制台，点击可能被前台窗口吞掉导致 6 轮无翻转无弹窗。
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


def dump_dialogs(infos, confirm_words):
    """扫描可能出现的弹窗/确认层按钮（供人工判断），返回候选按钮 info 列表。"""
    cands = []
    for i in infos:
        if not i["name"] or not _ct_in(i["ct"], CTRL_TYPES_CLICKABLE):
            continue
        nm = i["name"]
        for w in confirm_words:
            if w in nm and w != "数据分析":  # 排除表格行常规词避免海量噪音
                if _ct_in(i["ct"], ("Button", "MenuItem", "ListItem")):
                    cands.append(i)
                    break
    return cands


def dump_dialog_buttons(auto, hwnd, before_count, with_render=True, confirm_words=None):
    """点击后重新收集控件树，找出新增/变化的确认按钮候选。"""
    infos2 = collect_all(auto, hwnd, with_render=with_render)
    cands = dump_dialogs(infos2, confirm_words or CLOSE_WORDS)
    # 简单去重（同按钮可能多控件层次都有词）
    seen = set()
    out = []
    for c in cands:
        key = (c["name"], c["rect"])
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return infos2, out


# ---------------- v0.9.5 helpers：用户并行防干扰 + 弹窗双按钮校验 ----------------
def user_idle_seconds():
    """距用户上次键鼠输入的秒数（GetLastInputInfo）；失败返回 None。"""
    try:
        import ctypes

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

        lii = LASTINPUTINFO()
        lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
            return None
        tick = ctypes.windll.kernel32.GetTickCount()
        return max(0.0, (tick - lii.dwTime) / 1000.0)
    except Exception:
        return None


def wait_user_idle(min_idle=2.0, max_wait=60.0, poll=2.0):
    """等待用户空闲满 min_idle 秒 → True；超过 max_wait 秒用户仍在忙 → False（调用方放弃动作）。
    空闲检测不可用时直接放行（返回 True），不阻塞主流程。"""
    waited = 0.0
    while waited < max_wait:
        idle = user_idle_seconds()
        if idle is None:
            return True
        if idle >= min_idle:
            return True
        time.sleep(poll)
        waited += poll
    return False


def scroll_into_view(ctrl_info, hwnd=None):
    """尽力把控件滚动进可视区（ScrollItemPattern）。返回是否成功。"""
    try:
        ctrl = ctrl_info.get("ctrl")
        if ctrl is None:
            return False
        pat = ctrl.GetScrollItemPattern()
        if pat is None:
            return False
        pat.ScrollIntoView()
        time.sleep(0.8)
        return True
    except Exception:
        return False


# ---------------- v0.14 视口外自动滚动（2026-09-06，v0.13 实测失败修复版） ----------------
# 背景：15:37 工作机实测——目标空烧计划行在视口外（offscreen=True）时 dumb_close_one
#      仅靠 scroll_into_view(ScrollItemPattern) 滚动，Chromium 渲染行不暴露可用实现，
#      失败即跳过（成功 0 条）；人工滚动进视口后同链路成功（1/1）。
# v0.13 实测（auto_close_probe v1.0~v1.3 情报）：A244 页面 13 行全视口外且行 rect 为
#      Chromium 虚拟布局坐标（远超真实客户区）；ScrollItemPattern 对渲染行无效、
#      全树 44 个 ScrollPattern 垂直可滚=0，仅剩模拟滚轮路线。v0.13 滚轮失败根因：
#      ①悬停 x 取行 rect 中心飞出客户区；②无可见行时 y 锚客户区中心非表格滚动区。
# v0.14 修复：悬停点取行中心 clamp 进客户区（rect∩客户区交集），行整行在屏外时退回
#      客户区水平中心；无可见行时悬停 y 偏客户区下半区（0.62 处）；滚轮前
#      WindowFromPoint 校验鼠标已落在目标窗口内（被遮挡则改悬停客户区中心重试一次）。
# 方案（决策日志_20260906_自动滚动开发.md）：ScrollItemPattern 保留为前置白嫖；
#      失败后改用「视口 rect 比对行 rect 推算方向 + SetCursorPos 悬停表格 + 模拟滚轮」
#      循环逼近，每轮重收树按行号+计划名重定位，直至目标开关进入可视区或达到尝试上限。
SCROLL_WHEEL_DELTA_UNIT = 120       # 单格滚轮 delta（正=向上/负=向下）
SCROLL_MAX_WHEEL_ATTEMPTS = 12      # 单目标滚轮尝试上限（每轮含重收树）
SCROLL_REFRESH_SEC = 0.8            # 滚轮后静置（等页面滚动画与重排稳定）
SCROLL_CLAMP_PAD = 40               # v0.14: 悬停点距客户区边缘最小内缩


def _client_rect_screen(hwnd):
    """窗口客户区在屏幕坐标下的 (left, top, right, bottom)；失败返回 None。"""
    import ctypes
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


def _top_window_at(x, y):
    """返回屏幕点 (x, y) 处顶层窗口 hwnd（WindowFromPoint 再取根）；失败返回 0。"""
    import ctypes
    import ctypes.wintypes
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
        # 0x0800 = MOUSEEVENTF_WHEEL；滚轮作用于鼠标悬停的表格滚动容器
        ctypes.windll.user32.mouse_event(0x0800, 0, 0, int(delta), 0)
        time.sleep(SCROLL_REFRESH_SEC)
        return True, "ok"
    except Exception:
        return False, "wheel_exc"


def _clamp_inside(x, y, cr, pad=SCROLL_CLAMP_PAD):
    """把点 (x, y) clamp 进客户区 (left, top, right, bottom) 内缩 pad 的安全带。"""
    if cr is None:
        return int(x), int(y)
    l, t, r, b = cr
    cx = max(l + pad, min(int(x), r - pad))
    cy = max(t + pad, min(int(y), b - pad))
    return cx, cy


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


def auto_scroll_to_visible(auto, hwnd, args, row_no, plan_expected, sw):
    """v0.14 视口外自动滚动主封装：目标开关 IsOffscreen=True 时把计划行滚进可视区。

    流程：先试 scroll_into_view（ScrollItemPattern 白嫖一次）；
    失败 → 模拟滚轮循环逼近：视口=可见行 y 带（退化=窗口客户区），目标行中心在视口
    下方滚负、上方滚正，超出越远步数越多（1~5 格/轮），每轮重收树 + 行号/计划名重定位
    后重判 offscreen，最多 SCROLL_MAX_WHEEL_ATTEMPTS 轮。
    v0.14：悬停点 clamp 进客户区安全带（防虚拟布局坐标飞出窗口）、无可见行时悬停 y
    偏客户区下半区、滚轮前 WindowFromPoint 命中校验（被遮挡改客户区中心重试一次）。
    返回 (ok, r, sw2, msg)：ok=True 时 r/sw2 为滚动后重定位的行与开关（可继续点击链路）。
    """
    if not sw:
        return False, None, None, "开关为空，无法滚动"
    # 1) 前置白嫖 ScrollItemPattern（部分环境有效，成本低）
    if scroll_into_view(sw):
        log("[自动滚动] ScrollItemPattern 滚动成功，重收树校验目标行...")
    else:
        log("[自动滚动] ScrollItemPattern 不可用，改用模拟滚轮滚动（目标行滚进可视区）...")
        moved = 0
        for attempt in range(1, SCROLL_MAX_WHEEL_ATTEMPTS + 1):
            infos_cur = collect_all(auto, hwnd, with_render=args.render)
            rows_cur, _h2 = scan_rows(infos_cur)
            r_cur, no_cur = _find_row_by_plan(rows_cur, row_no, plan_expected)
            if r_cur is None:
                return False, None, None, "滚动过程中目标计划丢失，中止防误滚"
            sw_cur = find_row_switch(infos_cur, r_cur["rect"])
            if sw_cur is None:
                return False, None, None, "滚动后目标开关丢失，中止防误滚"
            if not sw_cur["offscreen"]:
                log(f"[自动滚动] 第{attempt}次滚动后目标进入可视区（行#{no_cur} rect={r_cur['rect']} 开关可见）。")
                return True, r_cur, sw_cur, "模拟滚轮滚动成功"
            # 目标仍不可见：算方向与步数
            cy = (r_cur["rect"][1] + r_cur["rect"][3]) // 2
            vp = _visible_table_viewport(infos_cur)
            cr = _client_rect_screen(hwnd)
            if vp:
                vp_top, vp_bottom = vp
                if cy < vp_top - 15:
                    delta_dir = SCROLL_WHEEL_DELTA_UNIT          # 目标在视口上方 → 向上滚
                    dist = vp_top - cy
                elif cy > vp_bottom + 15:
                    delta_dir = -SCROLL_WHEEL_DELTA_UNIT         # 目标在视口下方 → 向下滚
                    dist = cy - vp_bottom
                else:
                    # 推断视口内仍 offscreen（视口估算偏差/浮层遮挡）→ 小量向下试滚
                    delta_dir = -SCROLL_WHEEL_DELTA_UNIT
                    dist = 60
                y_hover = (vp_top + vp_bottom) // 2
            else:
                if cr is None:
                    return False, None, None, "无可见行且取窗口客户区失败，无法判滚动方向"
                mid = (cr[1] + cr[3]) // 2
                delta_dir = SCROLL_WHEEL_DELTA_UNIT if cy < mid else -SCROLL_WHEEL_DELTA_UNIT
                dist = abs(cy - mid)
                # v0.14: 无可见行时悬停 y 偏客户区下半区（更靠近表格滚动区）
                y_hover = int(cr[1] + (cr[3] - cr[1]) * 0.62)
            steps = max(1, min(5, int(abs(dist) // 100) + 1))
            # v0.14: 悬停点 clamp 进客户区（行 rect 为虚拟布局坐标时防飞出窗口）
            x_hover = (r_cur["rect"][0] + r_cur["rect"][2]) // 2   # 行中部，避开固定左列
            if cr is not None:
                x_hover, y_hover = _clamp_inside(x_hover, y_hover, cr)
                # 行 rect 完全在屏外导致 x 被钳到边界时，退回客户区水平中心更稳
                if x_hover <= cr[0] + SCROLL_CLAMP_PAD + 2 or x_hover >= cr[2] - SCROLL_CLAMP_PAD - 2:
                    x_hover = (cr[0] + cr[2]) // 2
            log(f"[自动滚动] 第{attempt}次: 目标行中心 y={cy} 视口={vp or cr} → 滚 {'向上' if delta_dir > 0 else '向下'} {steps} 格 悬停=({x_hover},{y_hover})")
            ok_w, tag_w = _send_wheel(hwnd, delta_dir * steps, x_hover, y_hover)
            if not ok_w:
                if tag_w == "wrong_window" and cr is not None:
                    log(f"[自动滚动] 第{attempt}次悬停点被其它窗口遮挡，改悬停客户区中心重试...")
                    ok_w2, tag_w2 = _send_wheel(hwnd, delta_dir * steps,
                                                (cr[0] + cr[2]) // 2, (cr[1] + cr[3]) // 2)
                    if not ok_w2:
                        return False, None, None, f"悬停点被遮挡且中心重试失败({tag_w2})，中止"
                else:
                    return False, None, None, f"滚轮事件发送失败({tag_w})，中止"
            moved += 1
        return False, None, None, f"模拟滚轮 {SCROLL_MAX_WHEEL_ATTEMPTS} 次仍未进入可视区，中止"
    # 2) ScrollItemPattern 成功后的标准重定位（校验行号/计划名/开关可见）
    infos_f2 = collect_all(auto, hwnd, with_render=args.render)
    rows_f2, _h2 = scan_rows(infos_f2)
    r2, no2 = _find_row_by_plan(rows_f2, row_no, plan_expected)
    if r2 is None:
        return False, None, None, "滚动后计划名不一致，中止"
    sw2 = find_row_switch(infos_f2, r2["rect"])
    if sw2 is None or sw2["offscreen"]:
        return False, None, None, "滚动后开关校验失败，中止"
    log(f"[自动滚动] 滚动后重定位 行#{no2} rect={r2['rect']} 开关 {switch_desc(sw2)}")
    return True, r2, sw2, "滚动成功"


def find_confirm_dialog(infos, confirm_words, exclude_rects, diag=False):
    """v0.9.5 F6：弹窗确认按钮候选 = 词命中 且 rect 不落在表格行内（区域过滤，防把行内词当弹窗）；
    弹窗有效 = 确认候选与取消按钮都存在（双按钮）。返回 (dlg_buttons, cancel_btn, valid)。
    v0.12.1 修复：弹窗按钮浮于表格上方，rect 可能落入数据行条带内被区域排除误伤（根因A）——
    凡按钮带弹窗按钮样式（DIALOG_BTN_STYLE_MARKS，ARCO primary/secondary）即豁免行内排除；
    行内普通按钮无该样式，仍被排除。
    diag=True 时打诊断：①词命中但被行 rect 排除（普通行内按钮）；②带弹窗样式被豁免加入候选。"""
    def _hit(words):
        out = []
        for c in infos:
            nm = c["name"] or ""
            if not nm or not c["rect"]:
                continue
            if not _ct_in(c["ct"], ("Button", "MenuItem", "ListItem")):
                continue
            if not any(w in nm for w in words):
                continue
            hit_row = None
            for rr in exclude_rects:
                if rr and _inside(c["rect"], rr, pad=2):
                    hit_row = rr
                    break
            if hit_row is not None:
                # v0.12.1 根因A豁免：词命中且 rect 落行内，若按钮带弹窗按钮样式（ARCO
                # primary/secondary，如弹窗「确认/取消」）→ 视为浮层按钮而非行内按钮，加入候选；
                # 行内普通按钮（暂停/数据分析/编辑…）无该样式，仍走排除。
                cls_mark = next((m for m in DIALOG_BTN_STYLE_MARKS if m in (c["cls"] or "")), None)
                if cls_mark is not None:
                    if diag:
                        log(f"[弹窗诊断] 词命中且落行内，但按钮带弹窗按钮样式({cls_mark}) → "
                            f"豁免行内排除，按弹窗候选处理（根因A修复生效）: {_fmt_ctrl(c)} in 行 rect={hit_row}")
                    out.append(c)
                    continue
                if diag:
                    log(f"[弹窗诊断] 词命中但被行 rect 排除（行内普通按钮，非弹窗按钮?）: {_fmt_ctrl(c)} in 行 rect={hit_row}")
                continue  # 词在表格行内 → 不是弹窗按钮
            out.append(c)
        return out

    dlg_buttons = _hit(confirm_words)
    cancel_btn = _hit(("取消",))
    valid = bool(dlg_buttons and cancel_btn)
    return dlg_buttons, cancel_btn, valid


# ---------------- 主流程 ----------------

# ================= v0.11 内置规则：行指标解析 + 自动关闭执行 =================
# 行文本样例（工作机 17:41 实测）：按 ' | ' 拆分后，计划名/按钮等 5 段前缀后依次是
#   ROI(裸小数) → 成本('X.XX USD') → SKU 订单数(纯整数) → 排期/收入/状态…。
# 解析规则：ROI 取首个裸小数；成本取 ROI 后 3 段内首个 'X.XX USD'；订单数取成本后 3 段内首个纯整数。
# 解析不齐一律返回 None → 该行不进入候选（宁缺毋滥，防误关）。
_TOKEN_RE_ROI = re.compile(r"^-?\d+\.\d+$")
_TOKEN_RE_COST = re.compile(r"^\d+(?:\.\d+)?\s*USD$")
_TOKEN_RE_INT = re.compile(r"^\d+$")


def parse_plan_metrics(text):
    """从行文本解析 (roi, cost_usd, orders)；任一取不到返回 None。"""
    if not text:
        return None
    toks = [t.strip() for t in text.split(" | ")]
    roi_i = cost_i = ord_i = None
    for i, t in enumerate(toks):
        if _TOKEN_RE_ROI.match(t):
            roi_i = i
            break
    if roi_i is None:
        return None
    for i in range(roi_i + 1, min(roi_i + 4, len(toks))):
        if _TOKEN_RE_COST.match(toks[i]):
            cost_i = i
            break
    if cost_i is None:
        return None
    for i in range(cost_i + 1, min(cost_i + 4, len(toks))):
        if _TOKEN_RE_INT.match(toks[i]):
            ord_i = i
            break
    if ord_i is None:
        return None
    try:
        return {"roi": float(toks[roi_i]),
                "cost": float(re.sub(r"[^\d.]", "", toks[cost_i])),
                "orders": int(toks[ord_i])}
    except Exception:
        return None


def _mb(flags, title, text):
    """Windows MessageBox（windowed exe 无控制台也能弹）。返回按钮 ID。"""
    try:
        import ctypes
        return ctypes.windll.user32.MessageBoxW(0, text, title, flags)
    except Exception as e:
        log(f"[弹窗] MessageBox 调用失败: {e}")
        return 0


def _mb_confirm(title, lines):
    """确认框（是/否，默认否=安全）。返回 True=用户点"是"。"""
    r = _mb(0x4 | 0x20 | 0x40000 | 0x100, title, "\r\n".join(lines))  # YESNO|QUESTION|TOPMOST|DEFBUTTON2
    return r == 6  # IDYES


def _mb_info(title, lines):
    """提示框（仅 OK）。"""
    _mb(0x40 | 0x40000, title, "\r\n".join(lines))  # ICONINFORMATION|TOPMOST


def dumb_close_one(auto, hwnd, args, row_no, plan_expected):
    """内置傻瓜模式：关闭单个候选行（完整 v0.9.5 安全链路）。
    返回 (ok: bool, msg: str)。"""
    log(f"[自动关闭] 处理候选 行#{row_no} [计划] {plan_expected}")
    # 点前重收树，行号+计划名双重校验（防页面切走/重排误点）
    infos_f = collect_all(auto, hwnd, with_render=args.render)
    rows_f, _h2 = scan_rows(infos_f)
    r = None
    if 1 <= row_no <= len(rows_f):
        cand = rows_f[row_no - 1]
        if plan_expected and (cand.get("plan") or "").strip() != (plan_expected or "").strip():
            # 前一条关闭后列表可能重排 → 按计划名全表重新定位，找不到才跳过
            log(f"[自动关闭] 重收树后行#{row_no} 计划名不一致（目标'{plan_expected}' != 现值"
                f"'{cand.get('plan') or '?'}'），尝试按计划名全表重定位...")
            cand = None
            for _i, rr in enumerate(rows_f, 1):
                if (rr.get("plan") or "").strip() == (plan_expected or "").strip():
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
    # 用户并行防干扰
    if not args.no_wait_idle and not wait_user_idle(min_idle=2.0, max_wait=60.0):
        log("[自动关闭] 用户键鼠未空闲满 2s，跳过本轮防干扰。")
        return False, "用户未空闲，跳过"
    sw = find_row_switch(infos_f, r["rect"])
    if sw is None:
        log("[自动关闭] 未定位到启停开关，跳过（不回退文字按钮防误点）。")
        return False, "未定位开关，跳过"
    if not switch_is_checked(sw):
        log("[自动关闭] 开关当前灰钮(off=已暂停/非运行中)，无需再关，跳过。")
        return False, "已非运行中，跳过"
    if sw["offscreen"]:
        log("[自动关闭] 开关在视口外，尝试滚动进可视区...")
        ok_sc, r2, sw2, msg_sc = auto_scroll_to_visible(auto, hwnd, args, row_no, plan_expected, sw)
        if not ok_sc:
            log(f"[自动关闭] {msg_sc}，跳过。")
            return False, msg_sc
        # v0.13：滚动后以 auto_scroll 重定位结果为准（r/sw 更新，row_rect 使用滚动后新 rect）
        r = r2
        sw = sw2
        if not switch_is_checked(sw):
            log("[自动关闭] 滚动后开关为灰钮(off=已暂停/非运行中)，跳过防误点。")
            return False, "滚动后已非运行中，跳过"
        log(f"[自动关闭] 滚动后重定位 行#{r.get('no') or row_no} rect={r['rect']} 开关 {switch_desc(sw)}")
    log(f"[自动关闭] 即将点击开关暂停该计划: {_fmt_ctrl(sw)}")
    was_checked = switch_is_checked(sw)
    ok, msg = click_by_coord(sw, hwnd)
    if not ok:
        ok, msg = do_invoke(sw)
    log(f"[自动关闭] 开关点击: {'成功' if ok else '失败'} {msg}")
    # 轮询等待：开关翻转 或 弹窗出现（最长 10 轮约 20s+收树）
    flipped = False
    dlg_buttons, cancel_btn, dlg_valid = [], [], False
    sw2_desc = None
    row_rect = r["rect"]
    for poll_i in range(1, 11):
        time.sleep(2.0)
        infos2 = collect_all(auto, hwnd, with_render=args.render)
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
    # 弹窗处理：须双按钮校验通过才自动点「确认」
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
        if not args.no_wait_idle and not wait_user_idle(min_idle=2.0, max_wait=60.0):
            return False, "点确认前用户未空闲，未点"
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


def menu_select():
    """v0.12 控制台菜单：双击启动后用键盘选择运行模式（参考库存抓取器交互）。
    返回 int：1=内置规则自动关  2=只探测不点击  3=手动选行真点关闭  0=退出
    """
    while True:
        print("=" * 66)
        print("  TK监控器 广告计划启停测试程序  v0.12.2（日志按秒拆分版）")
        print("=" * 66)
        print("  1) 内置规则自动关闭  空烧判定: ROI=0 且 订单=0 且 成本≥$5，命中后弹窗确认再暂停")
        print("  2) 只探测不点击      列出页面计划/指标/开关状态并截图，用于无候选等场景验证解析")
        print("  3) 手动选行关闭      探测后列出运行中计划，输行号真实暂停一条（测试链路用，慎选）")
        print("  0) 退出")
        print("-" * 66)
        try:
            raw = input("请选择运行模式，输入数字后回车: ").strip()
        except Exception:
            return 0
        if raw in ("1", "2", "3", "0"):
            return int(raw)
        print(f"无效输入: {raw!r}，请输入 1/2/3/0。")


def main():
    ap = argparse.ArgumentParser(description="TK监控器 帮我关闭（自动暂停）UIA 链路探测/验证")
    ap.add_argument("--keyword", action="append", default=None, help="窗口关键词（可多次，默认自动 Axxx）")
    ap.add_argument("--render", action="store_true", help="收集渲染子窗口树（默认关，先试主窗口）")
    ap.add_argument("--diag-text", action="store_true", help="诊断：dump 树中有 name 的 Text/DataItem 控件与首行 rect 内控件分布")
    ap.add_argument("--mode", choices=["open", "close"], default="open",
                    help="动作方向：open=启用已暂停计划（默认，安全优先）/ close=暂停投放中计划")
    ap.add_argument("--go", action="store_true", help="允许执行点击验证（默认 dry-run 只探测）")
    ap.add_argument("--row", type=int, default=0, help="对第 N 行执行动作按钮点击验证（需 --go，从1起）")
    ap.add_argument("--confirm", action="append", default=None,
                    help="允许点击弹窗确认按钮（可多次；不传则弹窗只 dump 不点）")
    ap.add_argument("--ask", action="store_true",
                    help="傻瓜交互模式：探测后列出运行中(白钮)行，询问行号并自动确认暂停（自动切 close 方向）")
    ap.add_argument("--no-pause", action="store_true", help="结束不暂停")
    ap.add_argument("--no-wait-idle", action="store_true", help="v0.9.5：关闭点击前用户空闲检测（测试用）")
    args = ap.parse_args()
    # v0.9.1 修复：双击 exe / 无任何命令行参数时，默认进入傻瓜交互模式
    # （ask=close 方向），避免默认 dry-run 空跑导致"肉眼没看到关掉"的误判。
    # 显式传了任何业务参数（--mode/--go/--row/--confirm/--keyword/--ask 等）则按显式行为走。
    _explicit = [a for a in sys.argv[1:] if a.startswith("--")]
    _noop = ("--no-pause", "--help", "-h")
    # v0.11：双击/无业务参数 → 傻瓜模式。v0.12：双击先进控制台菜单，由键盘选择运行模式
    args.dumb_mode = False
    args.dumb_done = False
    if not [a for a in _explicit if a not in _noop]:
        _menu = menu_select()
        if _menu == 0:
            log("[参数] 双击菜单选择[0] 退出，本次不做任何操作。")
            return 0
        if _menu == 1:
            args.dumb_mode = True
            args.mode = "close"
            log("[参数] 双击菜单[1]：内置规则自动选靶（config 已内置，无需外部 cfg 文件）。")
        elif _menu == 2:
            args.mode = "close"
            log("[参数] 双击菜单[2]：仅探测不点击（dry-run），用于无候选/排查场景验证扫描解析。")
        elif _menu == 3:
            args.ask = True
            args.mode = "close"
            log("[参数] 双击菜单[3]：手动选行真点（探测后输入行号，等效旧 --ask 交互）。")
    if args.ask:
        args.mode = "close"
        args.go = False

    try:
        pass  # 按天轮转日志（追加不覆盖），历史可追溯；控制台同步输出
    except Exception:
        pass

    mode_words = OPEN_WORDS if args.mode == "open" else CLOSE_WORDS
    confirm_words = CONFIRM_OPEN if args.mode == "open" else CONFIRM_CLOSE
    mode_label = "启用(open)" if args.mode == "open" else "暂停(close)"
    log("=" * 70)
    log("TK监控器 自动启停广告计划探测程序 v0.13(自动滚动版: 目标行视口外自动滚进可视区)  "
        f"mode={mode_label} go={args.go} row={args.row} render={args.render} diag={args.diag_text} ask={args.ask} dumb={args.dumb_mode}")
    log(f"[日志] {LOG_PATH}（exe 同级，每次启动按秒新建；控制台仅同步显示）")
    log(f"[内置参数] 窗口关键词={BUILTIN_CFG['window_keyword']} | "
        f"ROI止损线={BUILTIN_CFG['alert_threshold']} | "
        f"ROI前置消耗门槛=${BUILTIN_CFG['roi_alert_cost_min']} | "
        f"观察额度=${BUILTIN_CFG['grace_cost']} | 空烧关闭规则={BUILTIN_CFG['dumb_close']} | 调整只需改文件头 BUILTIN_CFG")

    # 1. 窗口定位
    keywords = list(args.keyword or [])
    if not keywords:
        auto_kws = trf.auto_detect_shop_keywords()
        if auto_kws:
            keywords = auto_kws
            log(f"自动检测到店铺窗口关键词: {keywords}")
        else:
            keywords = [BUILTIN_CFG["window_keyword"]]
            log(f"未自动检测到店铺窗口，按内置窗口关键词定位: {keywords}")
    hwnd, title, hit_kw = trf.find_window_by_keywords_multi(keywords)
    if not hwnd:
        log(f"未找到标题含 {keywords} 的可见窗口，请确认紫鸟已打开 TK 广告计划列表页")
        log("--- 当前可见窗口标题（前25个） ---")
        for i, (wt, _h) in enumerate(trf.list_windows()[:25], 1):
            log(f"  {i:2d}. {wt[:80]}")
        log("--- 结束 ---")
        return 2
    log(f"目标窗口 hwnd=0x{hwnd & 0xFFFFFFFFFFFFFFFF:X} 标题={title} 命中={hit_kw}")

    try:
        import uiautomation as auto
    except Exception as e:
        log(f"uiautomation 导入失败: {e}")
        return 2

    # 2. 收集整树
    log("[卡点] 收集页面控件树（主窗口+渲染子窗口），复杂页面约 5~30s ...")
    infos = collect_all(auto, hwnd, with_render=args.render)
    log(f"[卡点] 整树控件 {len(infos)} 个")

    # 3. 表格行扫描
    data_rows, header_row = scan_rows(infos)
    if header_row is None:
        log("[结果] 未识别到表头行——可能不在广告计划列表页，或表格未加载。请人工确认页面。")
    log(f"[结果] 表格数据行 {len(data_rows)} 个"
        + (f"，表头行文本：{header_row['text'][:100]}" if header_row else ""))
    # 行文本全空（页面有表格但文字未进 UIA 树）→ 自动输出诊断，无需手动 --diag-text
    empty_text_rows = [r for r in data_rows if not r["text"].strip()]
    if args.diag_text or (empty_text_rows and len(empty_text_rows) == len(data_rows)):
        _diag_dump(infos, data_rows)
    if not data_rows:
        log("未找到数据行，中止（请确认页面已展示广告计划列表且表格加载完成）。")
        return 3

    # 4. 逐行按 mode 扫描动作候选 + 行按钮 dump + v0.7 行左启停开关定位
    all_rows_report = []
    switch_found = 0
    for r in data_rows:
        ctrls = row_ctrls_in_rect(infos, r["rect"])
        st = row_status(r["text"])
        r["status"] = st
        l1, l2, l3 = find_action_candidates(ctrls, mode_words)
        r["l1"], r["l2"], r["l3"] = l1, l2, l3
        sw = find_row_switch(infos, r["rect"])
        r["switch"] = sw
        if sw is not None:
            switch_found += 1
        # 行内操作按钮对照（非目标词，全景 dump）
        action_others = []
        for c in ctrls:
            nm = c["name"]
            for w in ROW_ACTION_WORDS:
                if w in mode_words:
                    continue
                if w in nm and c not in (l1 + l2 + l3):
                    action_others.append((w, c))
                    break
        r["action_others"] = action_others
        all_rows_report.append(r)
        sw_txt = switch_desc(sw)
        log(f"行#{r['no']} rect={r['rect']} 状态={st} 目标候选({args.mode}) "
            f"L1={len(l1)} L2={len(l2)} L3={len(l3)} 其它操作按钮={len(action_others)}")
        log(f"  [计划] {r.get('plan') or '(未识别计划名)'}")
        log(f"  行文本: {r['text'][:180]}")
        log(f"  开关[{sw_txt}] " + (_fmt_ctrl(sw) if sw else "左带内未找到 type-circle Button，可能页面结构变化"))
        for tag, lst in (("L1", l1), ("L2", l2), ("L3", l3)):
            for c in lst[:8]:
                log(f"    {tag} {_fmt_ctrl(c)}")
        for w, c in action_others[:12]:
            log(f"    其它[{w}] {_fmt_ctrl(c)}")

    # 4.1 汇总开关命中；若有行未定位到开关，dump 左带兜底诊断（v0.6 逻辑）
    if data_rows:
        missing = [r for r in data_rows if r.get("switch") is None]
        if missing:
            log(f"[开关] 命中 {switch_found}/{len(data_rows)} 行；未定位行: "
                + ", ".join(f"#{r['no']}" for r in missing))
            dump_switch_zone(infos, missing[:5], max_rows=5)
        else:
            log(f"[开关] 全部 {len(data_rows)} 行均定位到启停开关（mode={args.mode} 点前将校验勾选态）。")

    # 4.9 v0.11 双击傻瓜模式：内置规则自动选靶（不靠外部 cfg/计划名）
    if args.dumb_mode:
        _rule = BUILTIN_CFG.get("dumb_close") or {}
        log(f"[内置规则] 空烧判定: ROI<= {_rule.get('roi_max', 0.0)} 且 订单数<= {_rule.get('order_max', 0)}"
            f" 且 成本>= {_rule.get('cost_min', 5.0)} USD")
        _pool = [r for r in data_rows
                 if r.get("switch") is not None and switch_is_checked(r["switch"])]
        # 解析每行指标并日志留痕
        for _rr in _pool:
            _m = parse_plan_metrics(_rr.get("text") or "")
            _rr["metrics"] = _m
            if _m is None:
                log(f"  行#{_rr['no']} [计划] {_rr.get('plan') or '?'} 指标解析失败，不进入候选（宁缺毋滥）")
            else:
                log(f"  行#{_rr['no']} [计划] {_rr.get('plan') or '?'} ROI={_m['roi']} 成本={_m['cost']} USD "
                    f"订单数={_m['orders']}")
        _cands = [r for r in _pool
                  if r.get("metrics") is not None
                  and r["metrics"]["roi"] <= float(_rule.get("roi_max", 0.0))
                  and r["metrics"]["orders"] <= int(_rule.get("order_max", 0))
                  and r["metrics"]["cost"] >= float(_rule.get("cost_min", 5.0))]
        if not _cands:
            log("[内置规则] 未发现符合空烧规则的计划，本次不做任何操作。")
            _mb_info("TK 自动关闭", ["未发现符合空烧规则的计划（ROI=0 且 订单=0 且 成本≥$5）。",
                                     "本次未做任何操作。", "",
                                     "提示：页面请停留在【广告计划列表】并确认计划已刷新为最新数据。"])
            args.dumb_done = True
        else:
            log(f"[内置规则] 命中空烧候选 {len(_cands)} 条：")
            for _rr in _cands:
                _m = _rr["metrics"]
                log(f"    [{_rr['no']}] 行#{_rr['no']} [计划] {_rr.get('plan') or '?'} "
                    f"ROI={_m['roi']} 成本={_m['cost']} USD 订单数={_m['orders']}")
            _lines = [f"检测到 {len(_cands)} 个空烧计划（ROI=0、订单=0、成本≥$5），确认暂停以下计划？", ""]
            for _rr in _cands:
                _m = _rr["metrics"]
                _lines.append(f"#{_rr['no']}  {_rr.get('plan') or '?'}   |   ROI={_m['roi']}  成本={_m['cost']} USD  订单={_m['orders']}")
            _lines.append("")
            _lines.append("点【是】→ 逐条暂停（每条点击前都会再次校验开关，防误点）")
            _lines.append("点【否】→ 不做任何操作")
            if _mb_confirm(f"TK 自动关闭 · 确认暂停 {len(_cands)} 条？", _lines):
                _ok, _fail = 0, 0
                for _rr in _cands:
                    _rs, _rm = dumb_close_one(auto, hwnd, args, _rr["no"], _rr.get("plan"))
                    if _rs:
                        _ok += 1
                    else:
                        _fail += 1
                        log(f"[内置规则] 行#{_rr['no']} [{_rr.get('plan')}] 关闭失败: {_rm}")
                    time.sleep(1.0)
                log(f"[内置规则] 本次完成：成功暂停 {_ok} 条" + (f"，失败/跳过 {_fail} 条" if _fail else "") + "。")
                _mb_info("TK 自动关闭 · 执行完成",
                         [f"本次成功暂停 {_ok} 条空烧计划" + (f"，另有 {_fail} 条失败/跳过（详见日志）。" if _fail else "。"),
                          "", "请回到页面人工核对开关状态。若有关闭失败的，看日志文件排查。"])
            else:
                log("[内置规则] 用户选择【否】，本次不做任何操作。")
                _mb_info("TK 自动关闭", ["已取消，本次未做任何操作。"])
            args.dumb_done = True

    # 5. dry-run 默认结束（截图）；--ask 傻瓜模式在 dry-run 结果上询问行号、升级为真点
    if not args.go and args.ask:
        checked_rows = [r for r in data_rows
                        if r.get("switch") is not None and switch_is_checked(r["switch"])]
        log("=" * 70)
        if not checked_rows:
            log("[结论] --ask：没有扫描到运行中(白钮开关)的计划，无可暂停目标。")
            log("  提示：请把页面筛选切到【全部】或【投放中】，让运行中的计划显示出来，再双击本程序。")
        else:
            log(f"[结论] --ask：扫描到 {len(checked_rows)} 个运行中(白钮)计划，行号如下：")
            for rr in checked_rows:
                log(f"    [{rr['no']}] 行#{rr['no']} [计划] {rr.get('plan') or '?'} rect={rr['rect']} 文本: {rr['text'][:80]}")
            log("=" * 70)
            try:
                raw = input("输入要暂停的计划行号（回车=不操作退出）: ").strip()
            except Exception:
                raw = ""
            if raw.isdigit() and 1 <= int(raw) <= len(data_rows):
                row_no = int(raw)
                rr = data_rows[row_no - 1]
                sw = rr.get("switch")
                if sw is None or not switch_is_checked(sw):
                    log(f"[结论] 行#{row_no} 当前不是运行中(白钮)状态，已取消（防误点）。")
                else:
                    args.go = True
                    args.row = row_no
                    if not args.confirm:
                        args.confirm = ["确认"]
                    log(f"[结论] 收到行号 {row_no}，自动执行 close 真点：--go --row {row_no} --confirm 确认")
            else:
                log("[结论] 未输入有效行号，本次不点击，退出。")
    if not args.go:
        if args.dumb_done:
            log(f"[结论] 内置傻瓜模式已执行完毕（mode={args.mode}），本次不再走 dry-run/交互逻辑。")
        else:
            log(f"[结论] dry-run 模式（mode={args.mode}）：仅探测未点击。开关候选已按行列出；"
                f"请人工对照截图确认开关位置/勾选态无误后，用 --go --row N 真点。")
    else:
        if args.row < 1 or args.row > len(data_rows):
            log(f"--go 但 --row {args.row} 超出数据行范围 [1, {len(data_rows)}]，不执行点击。")
            args.go = False

    # 截图（先于点击动作，留探测底稿）
    try:
        shot = os.path.join(APP_DIR, f"auto_close_probe_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png")
        if trf.save_screenshot(hwnd, shot):
            log(f"截图已保存: {shot}")
    except Exception as e:
        log(f"截图异常（跳过）: {e}")

    # 6. 点击验证（--go --row N）：v0.9.5 点前重收树按计划名定位，防 dry-run 快照过期误点
    if args.go and args.row >= 1:
        r0 = data_rows[args.row - 1]
        log("=" * 70)
        log(f"[点击验证] 目标行#{args.row} [计划] {r0.get('plan') or '?'} rect={r0['rect']} 状态={r0.get('status')} mode={args.mode}")
        # v0.9.5 F3/F7：点击前重收树，按 行号 + 计划名 双重定位目标行（dry-run 快照可能已过期）
        log("[点击验证] 点击前重新收树，校验目标行...")
        infos_fresh = collect_all(auto, hwnd, with_render=args.render)
        rows_fresh, _h2 = scan_rows(infos_fresh)
        plan_target = (r0.get("plan") or "").strip()
        r = None
        if 1 <= args.row <= len(rows_fresh):
            cand = rows_fresh[args.row - 1]
            if plan_target and (cand.get("plan") or "").strip() != plan_target:
                log(f"[点击验证] 重收树后行#{args.row} 计划名不一致（目标'{plan_target}' != 现值'{cand.get('plan') or '?'}'），页面可能已变化/重排，中止点击防误点。")
                return 4
            r = cand
        if r is None:
            log(f"[点击验证] 重收树后行#{args.row} 不存在（当前数据行 {len(rows_fresh)}），页面可能已变化/滚动，中止点击防误点。")
            return 4
        r["no"] = args.row
        log(f"[点击验证] 重收树定位成功 行#{r['no']} 计划='{r.get('plan') or '?'}' rect={r['rect']}")
        # v0.9.5 A：用户并行防干扰——点前等用户键鼠空闲满 2s（一直忙则放弃本轮，不抢输入）
        if not args.no_wait_idle and not wait_user_idle(min_idle=2.0, max_wait=60.0):
            log("[点击验证] 60s 内用户键鼠未空闲满 2s（您正在用电脑），为避免干扰，本轮中止。稍后重试即可。")
            return 4
        sw = find_row_switch(infos_fresh, r["rect"])
        target = None
        if sw is not None:
            was_checked = switch_is_checked(sw)
            if args.mode == "close" and not was_checked:
                log(f"[点击验证] 该行开关当前为灰钮(off=已暂停)，mode=close 应点运行中行，已跳过防误点。")
                return 4
            if args.mode == "open" and was_checked:
                log(f"[点击验证] 该行开关当前为白钮(checked=运行中)，mode=open 应点已暂停行，已跳过防误点。")
                return 4
            target = sw
            log(f"[点击验证] 开关当前 {switch_desc(sw)}，即将点击以"
                f"{'暂停' if args.mode == 'close' else '启用'}该计划")
            log(f"[点击验证] 目标开关: {_fmt_ctrl(sw)}")
        else:
            # v0.9.5：无开关不回退文字按钮（文字可能是行内其它操作钮），保守中止防误点
            log(f"[点击验证] 重收树后行#{args.row} 未定位到启停开关，中止点击（不回退文字按钮，防误点）。"
                f"请人工确认页面结构后再试。")
            return 4
        if target["offscreen"]:
            # v0.13：视口外不硬点（坐标会点到别处），自动滚动进可视区再重新定位校验
            # ScrollItemPattern 白嫖一次；失败则模拟滚轮逼近（auto_scroll_to_visible）
            log("[点击验证] 目标 IsOffscreen=True，尝试滚动进可视区...")
            ok_sc, r2, sw2, msg_sc = auto_scroll_to_visible(auto, hwnd, args, args.row, plan_target, target)
            if not ok_sc:
                log(f"[点击验证] {msg_sc}，中止点击防误点。")
                return 4
            r = r2
            r["no"] = args.row
            sw = sw2
            target = sw
            was_checked = switch_is_checked(sw)
            if args.mode == "close" and not was_checked:
                log("[点击验证] 滚动后开关为灰钮(off=已暂停)，mode=close 应点运行中行，中止防误点。")
                return 4
            if args.mode == "open" and was_checked:
                log("[点击验证] 滚动后开关为白钮(checked=运行中)，mode=open 应点已暂停行，中止防误点。")
                return 4
            log(f"[点击验证] 滚动后重定位 行#{r['no']} rect={r['rect']} 开关 {switch_desc(sw)}")
        # 开关本体无 name（Chromium 渲染），InvokePattern 通常缺失 → 坐标点击为主
        ok, msg = click_by_coord(target, hwnd)
        log(f"[点击验证] 坐标点击: {'成功' if ok else '失败'} {msg}")
        if not ok:
            ok, msg = do_invoke(target)
            log(f"[点击验证] Invoke 兜底: {'成功' if ok else '失败'} {msg}")

        # 等待切换生效：v0.9.5 轮询检测（点击后最多 10 轮，每轮收树 + 2s 间隔，照顾 TK 网络慢）
        # 检测信号：① 目标行开关翻转（直接生效） ② 弹窗出现（确认候选+取消按钮，须在表格行外）
        log("[点击验证] 轮询等待开关翻转或弹窗出现（最长 10 轮，约 20s+收树耗时）...")
        flipped = False
        sw2_desc = None
        dlg_buttons = []
        cancel_btn = []
        dlg_valid = False
        row_rect = r["rect"]
        for poll_i in range(1, 11):
            time.sleep(2.0)
            infos2 = collect_all(auto, hwnd, with_render=args.render)
            if not infos2:
                log(f"[点击验证] 第{poll_i}轮控件树为空（窗口可能切换/关闭），继续观察...")
                continue
            # 信号①：同 rect 行开关是否翻转（页面可能因点击重排/滚动）
            rows2, _h2 = scan_rows(infos2)
            for rr in rows2:
                if rr["rect"] == row_rect:
                    sw2 = find_row_switch(infos2, rr["rect"])
                    if sw2 is not None:
                        sw2_desc = switch_desc(sw2)
                        if sw is not None:
                            flipped = (switch_is_checked(sw2) != was_checked)
                            if flipped:
                                log(f"[点击验证] 第{poll_i}轮 开关翻转成功: {switch_desc(sw2)}")
                    break
            if flipped:
                break
            # 信号②：弹窗确认候选（v0.9.5 区域过滤：按钮须在表格行外，防把行内词当弹窗）
            dlg_buttons, cancel_btn, dlg_valid = find_confirm_dialog(
                infos2, confirm_words, [rr["rect"] for rr in rows2], diag=True)
            if dlg_buttons or cancel_btn:
                log(f"[点击验证] 第{poll_i}轮 检测到疑似弹窗（表格外确认候选 {len(dlg_buttons)} 个，"
                    f"取消按钮 {'有' if cancel_btn else '无'}，双按钮校验 {'通过' if dlg_valid else '未通过'}），进入弹窗处理。")
                break
            if sw2 is not None:
                log(f"[点击验证] 第{poll_i}轮 未翻转未弹窗，开关仍为 {sw2_desc}，继续观察...")
        if not flipped and not dlg_buttons and not cancel_btn:
            log("[点击验证] 10 轮内未观察到开关翻转也未出现弹窗"
                + (f"，目标行开关仍为 {sw2_desc}" if sw2_desc else "，且未能在对应行定位到开关（页面可能重排/滚动）")
                + "，请人工看截图确认。")

        # 弹窗处理（v0.9.5）：开关翻转=直接生效；弹窗须通过双按钮+行外校验才允许自动确认
        if not flipped and (dlg_buttons or cancel_btn):
            if dlg_buttons:
                log(f"[点击验证] 疑似弹窗确认按钮候选 {len(dlg_buttons)} 个（表格行外，默认不点，仅供确认）：")
                for c in dlg_buttons[:15]:
                    log(f"    {_fmt_ctrl(c)}")
            else:
                log("[点击验证] 未扫描到确认词按钮，但取消按钮探针证实弹窗存在（按钮文字可能未进 UIA 树）。")
            confirm_kws = [w for w in (args.confirm or []) if w]
            if not dlg_valid:
                # v0.9.5 F6：只出现一侧按钮（或词在行内被滤光）→ 疑为网络报错层/其它浮层，不自动点
                log("[点击验证] 浮层未通过双按钮校验（确认+取消按钮须同在表格行外），"
                    "疑为网络报错层/其它浮层，不自动点击确认，请人工看截图判断。")
            elif not confirm_kws:
                log(f"[点击验证] 未传 --confirm，确认按钮不点击（confirm_words={confirm_words}）。"
                    "close 弹窗按钮文字为「确认」，建议 --confirm 确认 自动点；或人工 Tab+Enter 确认。")
            else:
                hit = None
                for c in dlg_buttons:
                    for kw in confirm_kws:
                        if kw in c["name"]:
                            hit = c
                            break
                    if hit:
                        break
                # v0.8 兜底：词未命中但弹窗被取消探针证实 + 存在 name 含"确认"按钮 → 点确认钮
                if hit is None and cancel_btn:
                    for c in dlg_buttons:
                        if "确认" in (c["name"] or ""):
                            hit = c
                            log(f"[点击验证] --confirm 词 {confirm_kws} 未直接命中，"
                                f"弹窗被证实且存在「确认」按钮，兜底命中: {_fmt_ctrl(hit)}")
                            break
                if hit is None:
                    log(f"[点击验证] 弹窗按钮中未匹配 --confirm 词 {confirm_kws}，不点击。"
                        "可指定弹窗实际按钮文字（如 --confirm 确认），或人工 Tab+Enter 确认。")
                else:
                    # v0.9.5 A：点确认前同样等用户空闲
                    if not args.no_wait_idle and not wait_user_idle(min_idle=2.0, max_wait=60.0):
                        log("[点击验证] 点确认前用户键鼠未空闲满 2s，本轮不自动点，请稍后重试或人工确认。")
                    else:
                        log(f"[点击验证] 匹配确认按钮: {_fmt_ctrl(hit)} —— 即将点击（计划可能真实暂停！）")
                        ok, msg = do_invoke(hit)
                        log(f"[点击验证] 确认按钮 Invoke: {'成功' if ok else '失败'} {msg}")
                        if not ok:
                            ok2, msg2 = click_by_coord(hit, hwnd)
                            log(f"[点击验证] 确认按钮坐标兜底: {'成功' if ok2 else '失败'} {msg2}")
                        time.sleep(2.0)
        elif flipped:
            log("[点击验证] 开关已翻转（直接生效，无弹窗确认环节），请看截图确认状态列。")
        else:
            log("[点击验证] 未检测到明显弹窗确认按钮——开关应已直接生效，请看截图确认状态列。")

    # 7. 收尾截图
    try:
        shot = os.path.join(APP_DIR, f"auto_close_after_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png")
        if trf.save_screenshot(hwnd, shot):
            log(f"结果截图已保存: {shot}")
    except Exception as e:
        log(f"结果截图异常（跳过）: {e}")

    log(f"[完成] mode={args.mode} 探测/验证结束，请结合上方日志与截图人工确认。")
    log("下一步计划：若 open 链路验证成功（已暂停计划能 UIA 点启用），"
        "把 --mode close 切回暂停方向做自动关闭流程 v0.3；若需走更多菜单再补展开验证。")
    if not args.no_pause:
        try:
            input("按回车退出...")
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as e:
        # v0.9.3：最外层异常兜底——任何未捕获异常都打印 traceback 到控制台并尝试落盘，
        # 杜绝"点完直接退出、无任何痕迹"的闪退（用户实测曾遇：输入行号后程序直接关闭且无 log）
        import traceback
        tb = traceback.format_exc()
        try:
            print(f"[致命异常] {tb}", flush=True)
        except Exception:
            pass
        try:
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(f"\n[{datetime.now().strftime('%H:%M:%S')}] [致命异常]\n{tb}\n")
        except Exception:
            pass
        try:
            input("发生异常，按回车退出...")
        except Exception:
            pass
        sys.exit(99)
