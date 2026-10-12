# -*- coding: utf-8 -*-
"""
UIA Probe Tool v1.0
=====================================================
独立 UIA 控件探测小工具（不依赖主程序 TikTok 监控器）。

用途：
    在无 Python 的工作电脑上运行，探测目标窗口（如紫鸟浏览器 A419
    广告计划列表页）的 UIA 控件树，输出 txt 文件传回分析，用于定位
    筛选刷新（状态筛选框 / 已生效选项 / 应用按钮）的真实控件结构。

功能：
    1. 自动匹配标题含关键词的窗口（默认 "A419"），未找到则列出当前
       所有可见窗口标题供选择；
    2. 从目标窗口根元素遍历 UIA 控件树（ControlView，含渲染子窗口
       补充下钻），dump 每个控件：ControlType / Name / AutomationId /
       ClassName / 矩形坐标 / 可用 Patterns（Invoke / SelectionItem /
       ExpandCollapse / Toggle / LegacyIAccessible / Value / Scroll 等）；
    3. Name 含「状态 / 筛选 / 已生效 / 生效 / 应用 / 确定 / 全部」的
       控件单独汇总一节，便于一眼定位筛选相关控件；
    4. 输出 uia_probe_<时间戳>.txt 到工具所在目录；
    5. console 打印摘要：窗口数 / 控件数 / 关键词命中数 / 输出文件路径。

用法：
    UIAProbe.exe                 # 交互模式：输入关键词或从窗口列表选择
    UIAProbe.exe --keyword A419  # 自动模式：匹配第一个可见窗口并扫描
    UIAProbe.exe --keyword A419 --max-depth 14 --max-nodes 3000

依赖：uiautomation + comtypes（Windows UIA 官方 API 封装）。
"""

import sys
import os
import time
import argparse

import uiautomation as auto
from uiautomation import PatternId

# 显式引入 comtypes gen（便于 PyInstaller 收集）
try:
    import comtypes  # noqa: F401
    import comtypes.gen  # noqa: F401
except Exception:
    pass

KEYWORDS = ['状态', '筛选', '已生效', '生效', '应用', '确定', '全部']

PATTERN_LIST = [
    (PatternId.InvokePattern, 'Invoke'),
    (PatternId.SelectionItemPattern, 'SelectionItem'),
    (PatternId.ExpandCollapsePattern, 'ExpandCollapse'),
    (PatternId.TogglePattern, 'Toggle'),
    (PatternId.LegacyIAccessiblePattern, 'LegacyIAccessible'),
    (PatternId.ValuePattern, 'Value'),
    (PatternId.ScrollPattern, 'Scroll'),
    (PatternId.WindowPattern, 'Window'),
    (PatternId.SelectionPattern, 'Selection'),
    (PatternId.TextPattern, 'Text'),
    (PatternId.ScrollItemPattern, 'ScrollItem'),
    (PatternId.TableItemPattern, 'TableItem'),
    (PatternId.TablePattern, 'Table'),
    (PatternId.GridPattern, 'Grid'),
    (PatternId.RangeValuePattern, 'RangeValue'),
]

# ==================== Win32 窗口枚举（ctypes，已验证稳定） ====================
import ctypes
import ctypes.wintypes as wt

user32 = ctypes.WinDLL('user32', use_last_error=True)
user32.EnumWindows.argtypes = [ctypes.c_void_p, wt.LPARAM]
user32.EnumWindows.restype = ctypes.c_bool
user32.GetWindowTextLengthW.argtypes = [wt.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.IsWindowVisible.restype = ctypes.c_bool
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetWindowThreadProcessId.restype = wt.DWORD
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.GetWindowRect.restype = ctypes.c_bool
user32.EnumChildWindows.argtypes = [wt.HWND, ctypes.c_void_p, wt.LPARAM]
user32.EnumChildWindows.restype = ctypes.c_bool


def enum_windows(visible_only=True):
    results = []

    def cb(hwnd, lparam):
        if visible_only and not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value
        if not title.strip():
            return True
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        rect = wt.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        results.append({
            'hwnd': hwnd, 'title': title, 'class': cls.value,
            'pid': pid.value,
            'rect': (rect.left, rect.top, rect.right, rect.bottom),
        })
        return True

    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return results


def enum_child_windows(hwnd):
    results = []

    def cb(child, lparam):
        if user32.IsWindowVisible(child):
            length = user32.GetWindowTextLengthW(child)
            title = ''
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(child, buf, length + 1)
                title = buf.value
            cls = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(child, cls, 256)
            results.append({'hwnd': child, 'title': title, 'class': cls.value})
        return True

    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    user32.EnumChildWindows(hwnd, WNDENUMPROC(cb), 0)
    return results


# ==================== 探测核心 ====================
class ProbeState:
    def __init__(self, max_nodes=3000, max_depth=14):
        self.max_nodes = max_nodes
        self.max_depth = max_depth
        self.nodes = 0
        self.lines = []
        self.key_hits = []
        self.truncated = False

    def add_line(self, text):
        self.lines.append(text)


def fmt_rect(rect):
    try:
        return 'rect=(%d,%d)-(%d,%d)' % (
            int(rect.left), int(rect.top), int(rect.right), int(rect.bottom))
    except Exception:
        return 'rect=?'


def control_key(c):
    try:
        return (c.ControlTypeName, c.Name, str(c.BoundingRectangle))
    except Exception:
        return (c.ControlTypeName, c.Name, '')


def get_patterns(c):
    names = []
    for pid, name in PATTERN_LIST:
        try:
            if c.GetPattern(pid) is not None:
                names.append(name)
        except Exception:
            pass
    return names


def walk_control(c, depth, state):
    """递归遍历控件树，返回本层遍历的控件 key 集合（用于层内去重）。"""
    if state.nodes >= state.max_nodes:
        state.truncated = True
        return set()
    state.nodes += 1

    try:
        ct = c.ControlTypeName
        name = c.Name or ''
        aid = c.AutomationId or ''
        cls = c.ClassName or ''
        rect = fmt_rect(c.BoundingRectangle)
    except Exception as e:
        state.add_line('  ' * depth + '[?] (读取属性失败: %s)' % e)
        return set()

    patterns = get_patterns(c)
    indent = '  ' * depth
    parts = ['[%s]' % ct]
    if name:
        parts.append('name="%s"' % name)
    if aid:
        parts.append('aid="%s"' % aid)
    if cls:
        parts.append('class="%s"' % cls)
    parts.append(rect)
    if patterns:
        parts.append('patterns=%s' % ','.join(patterns))
    state.add_line(indent + ' '.join(parts))

    if name:
        for kw in KEYWORDS:
            if kw in name:
                state.key_hits.append({
                    'ct': ct, 'name': name, 'aid': aid, 'rect': rect,
                    'patterns': patterns, 'depth': depth, 'kw': kw,
                })
                break

    if depth >= state.max_depth:
        return set()

    children = []
    try:
        children = c.GetChildren()
    except Exception:
        pass

    layer_keys = set()
    for child in children:
        if state.nodes >= state.max_nodes:
            state.truncated = True
            break
        ck = control_key(child)
        if ck in layer_keys:
            # UIA 虚拟化控件可能无限暴露相同兄弟，见则跳过
            continue
        layer_keys.add(ck)
        walk_control(child, depth + 1, state)
    return layer_keys


def probe_control(c, max_nodes, max_depth):
    state = ProbeState(max_nodes, max_depth)
    walk_control(c, 0, state)
    return state


def render_child_probe(root_control, hwnd, state, max_depth):
    """渲染子窗口下钻：Chromium 渲染内容可能挂在子窗口上。"""
    children = enum_child_windows(hwnd)
    for ch in children[:6]:
        cls = ch['class']
        if not ('Chrome' in cls or 'RenderWidget' in cls or 'Chrome_WidgetWin' in cls):
            continue
        sub = auto.ControlFromHandle(ch['hwnd'])
        if sub is None:
            continue
        try:
            sub_name = sub.Name or ''
            sub_ct = sub.ControlTypeName
        except Exception:
            sub_name, sub_ct = '', '?'
        state.add_line('')
        state.add_line('## 子窗口下钻 hwnd=0x%X class=%s title="%s" root=[%s] name="%s"' % (
            ch['hwnd'], cls, ch['title'], sub_ct, sub_name))
        walk_control(sub, 1, state)
    return state


def write_report(state, target_info, output_dir):
    ts = time.strftime('%Y%m%d_%H%M%S')
    fname = 'uia_probe_%s.txt' % ts
    path = os.path.join(output_dir, fname)
    lines = []
    lines.append('=' * 72)
    lines.append('UIA Probe Tool v1.0 探测报告')
    lines.append('=' * 72)
    lines.append('探测时间: %s' % time.strftime('%Y-%m-%d %H:%M:%S'))
    lines.append('目标窗口 hwnd: 0x%X' % target_info['hwnd'])
    lines.append('目标窗口标题: %s' % target_info['title'])
    lines.append('目标窗口类名: %s' % target_info['class'])
    lines.append('目标窗口 PID: %d' % target_info['pid'])
    lines.append('控件总数: %d%s' % (state.nodes, '（已达上限截断）' if state.truncated else ''))
    lines.append('关键词命中数: %d' % len(state.key_hits))
    lines.append('')
    lines.append('=' * 72)
    lines.append('【关键词命中清单】（状态/筛选/已生效/生效/应用/确定/全部）')
    lines.append('=' * 72)
    if state.key_hits:
        for i, h in enumerate(state.key_hits, 1):
            pat = ','.join(h['patterns']) if h['patterns'] else '-'
            lines.append('%d. [%s] name="%s" aid="%s" %s patterns=%s (命中词:%s)' % (
                i, h['ct'], h['name'], h.get('aid', ''), h['rect'], pat, h['kw']))
    else:
        lines.append('（未命中关键词控件）')
    lines.append('')
    lines.append('=' * 72)
    lines.append('【完整控件树】（层级缩进，ControlView 视图）')
    lines.append('=' * 72)
    lines.extend(state.lines)
    lines.append('')
    lines.append('--- END ---')

    with open(path, 'w', encoding='utf-8-sig') as f:
        f.write('\n'.join(lines))
    return path


def print_summary(windows, state, path):
    print('=' * 60)
    print('UIA Probe Tool v1.0 扫描完成')
    print('=' * 60)
    print('可见窗口总数: %d' % len(windows))
    print('控件总数: %d%s' % (state.nodes, '（截断）' if state.truncated else ''))
    print('关键词命中数: %d' % len(state.key_hits))
    print('输出文件: %s' % path)


def interactive_select(windows, default_kw='A419'):
    print('当前可见窗口（共 %d 个）：' % len(windows))
    print('  默认关键词: %s' % default_kw)
    kw = input('输入窗口标题关键词（直接回车用默认 %s，输入 "list" 列出全部窗口）: ' % default_kw).strip()
    if not kw:
        kw = default_kw
    if kw.lower() == 'list':
        for i, w in enumerate(windows, 1):
            print('%2d. hwnd=0x%X [%s] %s' % (i, w['hwnd'], w['class'], w['title']))
        sel = input('输入序号选择窗口: ').strip()
        try:
            idx = int(sel) - 1
            if 0 <= idx < len(windows):
                return windows[idx]
        except ValueError:
            pass
        return None
    matched = [w for w in windows if kw.lower() in w['title'].lower()]
    if not matched:
        print('未找到标题含 "%s" 的窗口，列出全部可见窗口：' % kw)
        for i, w in enumerate(windows, 1):
            print('%2d. hwnd=0x%X [%s] %s' % (i, w['hwnd'], w['class'], w['title']))
        sel = input('输入序号选择窗口（回车取消）: ').strip()
        try:
            idx = int(sel) - 1
            if 0 <= idx < len(windows):
                return windows[idx]
        except ValueError:
            pass
        return None
    if len(matched) == 1:
        print('匹配窗口: hwnd=0x%X [%s] %s' % (matched[0]['hwnd'], matched[0]['class'], matched[0]['title']))
        return matched[0]
    print('匹配到 %d 个窗口：' % len(matched))
    for i, w in enumerate(matched, 1):
        print('%2d. hwnd=0x%X [%s] %s' % (i, w['hwnd'], w['class'], w['title']))
    sel = input('输入序号选择窗口: ').strip()
    try:
        idx = int(sel) - 1
        if 0 <= idx < len(matched):
            return matched[idx]
    except ValueError:
        pass
    return None


def main():
    parser = argparse.ArgumentParser(description='UIA Probe Tool v1.0')
    parser.add_argument('--keyword', default=None, help='自动模式：标题含关键词的第一个可见窗口')
    parser.add_argument('--max-depth', type=int, default=14, help='最大下钻深度（默认14）')
    parser.add_argument('--max-nodes', type=int, default=3000, help='最大控件数（默认3000）')
    parser.add_argument('--outdir', default=None, help='输出目录（默认 exe/脚本所在目录）')
    args = parser.parse_args()

    if args.outdir:
        outdir = args.outdir
    elif getattr(sys, 'frozen', False):
        outdir = os.path.dirname(sys.executable)
    else:
        outdir = os.path.dirname(os.path.abspath(__file__))
    os.makedirs(outdir, exist_ok=True)

    print('UIA Probe Tool v1.0 启动...')
    print('枚举当前可见窗口...')
    windows = enum_windows(visible_only=True)
    print('可见窗口总数: %d' % len(windows))

    target = None
    if args.keyword:
        matched = [w for w in windows if args.keyword.lower() in w['title'].lower()]
        if matched:
            target = matched[0]
            print('自动模式匹配窗口: hwnd=0x%X [%s] %s' % (target['hwnd'], target['class'], target['title']))
        else:
            print('自动模式未找到标题含 "%s" 的窗口，回退到交互选择...' % args.keyword)
    if target is None:
        target = interactive_select(windows)
        if target is None:
            print('未选择目标窗口，退出。')
            return

    print('获取目标窗口 UIA 根元素...')
    root = auto.ControlFromHandle(target['hwnd'])
    if root is None:
        print('!! 无法从 hwnd=0x%X 获取 UIA 根元素（可能非 UIA 可访问窗口）' % target['hwnd'])
        return

    print('开始扫描控件树（hwnd=0x%X）...' % target['hwnd'])
    state = probe_control(root, args.max_nodes, args.max_depth)
    render_child_probe(root, target['hwnd'], state, args.max_depth)

    path = write_report(state, target, outdir)
    print_summary(windows, state, path)

    if not args.keyword:
        try:
            input('按回车退出...')
        except EOFError:
            pass


if __name__ == '__main__':
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
        try:
            input('发生异常，按回车退出...')
        except EOFError:
            pass
        sys.exit(1)
