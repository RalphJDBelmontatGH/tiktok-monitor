# tkm_10.22 守夜版（2026-09-06）：值守队列解耦自动关与识别轮间隔 + 状态区实时预览（详见主程序文件头），spec name -> tkm_10.22
# v10.20: 轮询间隔下限 5->1 分钟（以 v10.19 为基线，spec 仅同步版本号）， spec name -> tkm_10.21
# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []
hiddenimports = ['platformdirs', 'pystray']
# v10.7.7 修复（拔除僵尸 OCR）：v10.7.4 起 spec 内嵌整个开发\mods，把 ocr_recog 及
# example_mod 一起打进 exe（OCR 依赖已在 excludes 排除，但僵尸 mod 仍被 load_mods 加载）。
# v10.7.7 起只内嵌 uia_recog + ai_debug 两个目录（load_mods frozen 下从 _MEIPASS/mods 加载），
# 部署版不再携带 ocr_recog/example_mod；excludes 继续排除 rapidocr_onnxruntime/onnxruntime/cv2。
# v10.7.8：结构化拾取断链修复（详见主程序文件头），spec 仅同步版本号。
# v10.7.9：筛选刷新（filter_refresh）不生效修复 + 失败日志增强（详见主程序文件头），spec 仅同步版本号。
# v10.8.0：单实例运行 + 快捷键配置界面（详见主程序文件头），spec 仅同步版本号。
# v10.8.1：修复全局热键无效（独立热键线程+隐藏消息窗口+GetMessage 循环）与单实例唤起未生效
# （manual-reset 事件 + 队列转交主线程 + Win32 兜底双保险，详见主程序文件头），spec 仅同步版本号。
# v10.8.2：定期刷新升级为 uia_reload（路线D：UIA 点击浏览器『重新加载』按钮，ReloadTest 实测
# 可行后合并进主程序，详见主程序文件头；mods\uia_recog 新增 _uia_reload），spec 仅同步版本号。
# v10.8.3：全行入库修复——手动识别成功后对全部正常解析行逐行 add_record（推送条件与入库
# 解耦，未达成推送条件的行仅不推送）；正常行拾取明细日志由 [排错] 级降为 [信息] 级。
# （详见主程序文件头），spec 仅同步版本号。
# v10.8.5：『有一条显示一条』——①主窗口只读到浏览器外壳时总是合并渲染子窗口内容
# （原 items<10 才切子窗口，主窗口 chrome 控件多时页面表格缺失导致 no_header）；
# ②结构化读取主窗口未找到 core-table-tr 时切渲染子窗口再找一次；
# ③strict 路径 no_header/no_anchor 但页面有可见数据行时，宽松模式按行入库不再整轮跳过，
# 宽松仍无候选行才报"没有找到广告计划"（详见主程序文件头），spec 仅同步版本号。
# v10.8.6：三项待办落地——①只识别"已生效"广告计划（_structured_detail 按 status 过滤，
# config only_active_plans 默认 true）；②主窗口拖出屏幕钳制（虚拟屏幕范围至少 80x40 可见）
# +右键菜单"窗口复位到屏幕中央"；③uia_reload 刷新后还原前台焦点（Invoke 前记录前台句柄，
# 点击后 AttachThreadInput+SetForegroundWindow 还原）；另含冷却半小时移除（alert_cooldown_min
# 机制已删），spec 仅同步版本号。
# v10.8.7：OCR/变更检测清理版——主程序删除 OCR 引擎与变更检测全链路（ocr_engine 配置键、
# ④.6 OCR 引擎下拉、_get_recog OCR fallback、_pil_diff_norm/image_similarity/ImageChops、
# change_detect_* 配置与 UI/回填/save_change_detect/_capture_change_frame/_monitor_round 跳轮块、
# _last_frames），识别规则板块常驻、按钮固定"测试识别"；ocr_recog mod 移出 mods 至 mods_off，
# ai_debug 白名单移除 ocr_engine/change_detect 键；spec 仍只内嵌 uia_recog+ai_debug，excludes 不变。
# v10.9：打包推送——告警合并窗口（push_batch_minutes 默认30分钟），多轮告警攒成一条推送省日额度；
# 手动识别/快捷键刷新不走推送（push=False，推送仅离席告警用）；spec 仅同步版本号。
# v10.9.1：告警轻滴声——自动监控命中告警用 winsound 播放很轻短滴声（880Hz/0.12s），不占视线；
# 手动识别/快捷键刷新（人就在电脑前）不响；spec 仅同步版本号。
# v10.10：后台刷新不抢前台（方案A）+ 提示开关面板化——uia_recog _uia_reload 点击『重新加载』
# 前先将目标浏览器窗口最小化（SW_MINIMIZE，已最小化/不可见则跳过），点击后保持最小化不恢复、
# 不做前台还原，浏览器物理上无法抢前台；cfg 新增 toast_enabled（默认 True，面板⑥可勾选），
# False 时告警不弹本机 toast（上班防被同事看到），声音/推送不受影响；spec 仅同步版本号。
# v10.11：手动刷新热键 + 提示默认值调整 + 声音分类——新增全局热键 ctrl+shift+r（lowkey.refresh_hotkey
# 可配，复用 uia_reload 最小化不抢前台链路，后台线程执行，日志"手动热键刷新"）；toast_enabled 默认
# 改 False（默认关 toast，面板开关保留），sound_alarm 保持 True；声音按告警类型区分：正常=880/120
# 轻滴声、不好=220Hz 低沉长音、加预算=1300Hz 双连明亮音；spec 仅同步版本号。
datas += [('D:\\Program Files of mine\\TikTok监控器\\开发\\mods\\uia_recog', 'mods/uia_recog')]
datas += [('D:\\Program Files of mine\\TikTok监控器\\开发\\mods\\ai_debug', 'mods/ai_debug')]
tmp_ret = collect_all('platformdirs')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('uiautomation')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('websocket')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('PIL')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


# v10.12：手动完整轮热键 ctrl+alt+shift+r + 日志分层按天 + 单实例日志 + 按日期过滤查看；
# （uia_reload 操作级日志→ops.log，monitor/ops 按天轮转；goto_url 地址栏日期导航），spec 同步版本号。
# v10.13：无边框窗口四边拖拽 resize + 最小尺寸钳制开关 + 下分板块显隐自定义
# （详见主程序文件头），spec 仅同步版本号。
# v10.14：推送改"每轮识别完统一打包" + 识别失败也告警——_enqueue_push 去掉 Timer
# v10.14.1：两处修复（详见主程序文件头）——①推送开关真实生效：_flush_push_batch 发送前
# 先 _sync_push_ui_to_cfg() 并改用 self.cfg，不再 load_config() 读磁盘旧值（此前取消勾选
# 未点保存仍会推送）；②热键修复：self._lowkey 用 DEFAULT_CONFIG.lowkey 补缺失键（浅合并
# 导致旧 config 无 refresh_hotkey 时不注册），"未定位目标"日志提升为 result 级；spec 仅同步版本号。
# 只入轮次缓冲，_monitor_loop 每轮 targets 循环结束后统一 _flush_push_batch；
# _monitor_round 新增 push 参数（自动监控 True/手动完整轮 False），识别失败分支调用
# 新增 _enqueue_round_alert 生成"识别失败"告警随本轮打包推送；面板⑥"合并推送间隔"
# 已停用（保留输入框兼容旧配置，不再参与推送时机）；spec 仅同步版本号。
a = Analysis(
    [r'D:\Program Files of mine\TikTok监控器\开发\tiktok_monitor_v10.pyw'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['torch', 'transformers', 'llvmlite', 'numba', 'av', 'pandas', 'tiktoken', 'tokenizers', 'hf_xet', 'shapely', 'lxml', 'scipy', 'rapidocr_onnxruntime', 'onnxruntime', 'cv2'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='tkm_10.22',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# v10.14.2：exe 名低调化（工作台.exe，任务管理器不再暴露 TikTok 监控字样）+
# 推送开关真修复（_flush_push_batch 发送前同步 UI 开关用 self.cfg）+
# 旧版实例检测（启动时发现旧版 TikTok监控_v10.14.exe 进程则提示先退出）。

# v10.14.3��ˢ��·��ͳһ��ˢ�º�ɨ�衹+ ��ǰ̨�޸�������������ļ�ͷ������

# v10.14.4������ɾ�� SW_MINIMIZE ����A������ʽ��ǰ�������� tkm_10.14.4����־ͳһ�汾+ģ���ǩ���ɰ�������汾�š�# �� ����Զ�������ֶ��ȼ� ctrl+alt+shift+r ͳһ�� _monitor_round �� _maybe_refresh_target��ÿ��ǿ��ˢ�£�
# �� _send_refresh_to_target���� uia_reload������ mod _uia_reload ɾ�� SW_MINIMIZE����Ϊ
# SW_RESTORE+SW_SHOW+SetForegroundWindow ��ǰ̨���� refresh_enabled Ĭ�� True + UI ��ѡ��
# �� ɾ�� refresh_interval_sec ����������ʶ��ť��push_batch_minutes��alert_metric/alert_operator��
# �� poll_sec=max(5, refresh_wait_sec)��toast Ĭ�� False ͳһ��spec ��ͬ���汾�š�
# v10.16: weiyun auto-upload integrated, spec name -> tkm_10.16

# v10.18: 当天重载+新标签页分流（_uia_goto_url_new_tab）+ 控件树超时保护（_find_render_sub_hwnd_safe/_control_from_handle_safe），spec name -> tkm_10.18

