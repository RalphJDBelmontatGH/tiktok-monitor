# TK 监控器（tiktok_monitor_v10）版本演进 CHANGELOG

> 本文档由主程序文件头 docstring 历史说明外迁生成（2026-09-07 A 组拆分：纯注释瘦身）。
> 当前源码版本基线：tkm_10.23（锚定修复版，2026-09-07）；主程序文件头仅保留当前版本摘要。

---

以下为历史版本说明（保留供追溯）：
v10.22 守夜版（2026-09-06 夜间改造，基于 v10.21 源码）：
- 解耦识别轮间隔与自动关时机：识别出的自动关候选先入值守队列（_auto_close_store_pending），
  由值守步进（_auto_close_watchdog）在无人值守闸门通过后立即执行，不再等下个识别轮。
- _monitor_loop 等待循环由整段干睡改为逐秒值守：键鼠空闲达阈值 1 秒内接手待关候选；
  面板轮询间隔变更即时生效（保存后重新计时）；手动完整轮重置定时器语义保留。
- 失败防反复点关：结果含确定性结束词（已暂停/未找到/无操作等）即出队；其它失败保留并
  60s 冷却重试，3 次上限出队；执行期间防重入。同一计划队列内只留最新候选。
- ③.5 状态区实时预览：改未保存时按面板控件值显示并标注“（未保存，保存后生效）”，
  消除“明明开了却显示否/300秒”的困惑；新增待命候选数/值守执行中提示。
v10.21 engine 视口外滚动 v0.14 修复（2026-09-06 并入）：auto_close_engine.py 同步合入
  v0.14 视口外自动滚动修复——v0.13 工作机实测滚轮无效，根因为悬停点 x 取行 rect 中心
  飞出客户区（Chromium 虚拟布局坐标）、无可见行时 y 锚错。v0.14 将悬停点 clamp 进客户区
  安全带、无可见行悬停 y 偏客户区下半区、滚轮前 WindowFromPoint 命中校验；test_auto_close
  与 auto_close_engine 双端一致。打包产物与源码版本号当时仍为 v10.22（合主修复轮，
  随后 tkm_10.23 锚定修复轮整体承接）。
v10.20 调整（2026-09-04）：轮询间隔下限 5→1 分钟
- 空烧止损诉求：interval_minutes 下限从 5 放宽到 1 分钟（config 默认注释、UI 标签
  ≥1、save_interval 校验、start_monitor/_monitor_loop 的 max(1,·) 同步共 6 处），
  配合 1 分钟粒度轮询快速抓空烧。
- 以 v10.19 为冻结基线，仅上述改动；版本号字面量当时同步 v10.22。
v10.19 融合（2026-09-03）：测试刷新融合进主程序 + 并行刷新 + 防双刷
- 背景：9/1、9/2 ops 日志分析确认主程序刷新存在「每店每轮双刷」（两路调度各触发一次）
  与「就绪判定全天失败空转」（渲染子窗口收集超时控件数=0、刷新失败自动重试空转）。
- 融合：test_parallel_refresh v0.3 方案并入主程序——多目标轮首并行刷新阶段
  （点击段全局锁串行：收集树/置前/Invoke/还原焦点；就绪轮询锁外并行），
  识别阶段不再逐店刷新（_monitor_round do_refresh=False），结构性消灭双刷。
- 防双刷三道闸：① 同店刷新防重入（_refresh_inflight，两路调度对同店只允许一路点击，
  其余路等待在途刷新完成）；② 点击后「表格就绪未确认」不再自动重试点击（v10.19 起仅
  点击前失败才重试1次，识别失败由识别层重试兜底）；③ 并行刷新阶段与逐店刷新互斥。
- mods/uia_recog/mod.py（v10.19）：点击段与就绪轮询分离（_CLICK_LOCK）、就绪判定升级
  （表格就绪+确认刷新真的发生，旧表格不判成功）、_probe_table_ready 主窗口树与渲染子
  树合并统计（渲染子收集超时不漏判，join 12→22s 与点击前收集一致）、地址栏读取/按日期
  过滤/新标签导航整段持锁（RLock）。
- 刷新异常分类统计：同类记触发次数、同分类细节不同记首次细节（随主日志/ops 日志输出，
  监控轮末输出累计摘要，停止监控清零；不留独立文件，全为主程序职责）。
v10.18 功能（2026-08-31）：「当天重载刷新」（合入归档）
- 刷新前自动校正日期到当天：目标窗口当前 URL 已是当天→走原重载，非当天/缺日期/非广告列表页→新建标签页导航到「当天+已生效」URL（跨天自动追踪当天数据）。
- 方案主体在 mods/uia_recog/mod.py（v10.18）：_uia_reload_today / _uia_goto_url_new_tab / _probe_table_ready；
  主程序原封不动，仍经 core._uia_mod_actions["reload"] 注册表调用。
- 多店铺支持：_monitor_loop 遍历所有启用目标，target 全程传递，每店独立刷新+独立识别，
  mod 内部按 t.shop/name 标题匹配紫鸟每店窗口，其它店不受影响。
- 联动路径：_monitor_round → _maybe_refresh_target → _send_refresh_to_target → mod reload；
  自动监控/手动热键 ctrl+alt+shift+r/「刷新浏览器」勾选框 三条途径统一。
- 版本号 v10.18——注释合入以 10.16.2 热键修复为基线，未动运行代码。
v10.16.2 修复（2026-09-01）：热键 F/C 改为真正的监控启停
- 现象：用户按 ctrl+alt+shift+f / ctrl+alt+shift+c 只听到音效，程序未启动/停止。
- 根因：F/C 原是 v10.15 的「显示/隐藏主窗口」专用热键，只做窗口显隐+音效，
  不经过「启动自动监控/停止监控」按钮逻辑，与用户期望的启停语义不符。
- 修复：F=启动自动监控（未运行时），C=停止自动监控（运行中时），与按钮同一路径
  （start_monitor/stop_monitor）；不涉及窗口显隐（窗口显隐仅由 D 键魔法键负责）。
  已处于目标状态时仅播提示音防连按。X 键仍为直接退出。
- 版本号 v10.16.2——文件头/启动日志/窗口标题/spec 同步升级。
v10.16.1 修复（2026-08-31）：旧版检测误判自身弹窗
- 现象：打开 tkm_10.16.exe 后弹"检测到旧版监控进程"并退出。
- 根因：_detect_legacy_instances 排除版本号写死 "10.14.6"，而当前 exe 为
  tkm_10.16.exe；PyInstaller onefile 打包运行时有 bootloader 父进程 + 主程序
  子进程两个同名进程（任务管理器显示两个同名 exe 属正常现象），扫描到父进程时
  解析版本 "10.16" 匹配不上排除条件，被误判为旧版弹窗退出。与 v10.14.4 同源。
- 修复：排除条件不再写死，改为动态解析当前 exe 自身版本号（tkm_10.16.2.exe →
  "10.16.1"），版本==当前版本或 pid==当前进程一律跳过；源码运行（无 tkm_ 前缀）
  时回落文件头当前版本。真旧版（如 tkm_10.14.6.exe）仍会被检测并提示退出。
- 版本号 v10.16.1——文件头/启动日志/窗口标题/spec 同步升级。
v10.16 功能（2026-08-31）：微云日志自动上传
- 新增微云日志自动上传（防删目录丢日志/供信息部拉取）：log 按天轮转后，程序启动时
  检查并把昨天及更早的 monitor_*.log / ops_*.log 自动备份到微云指定目录。
- 新增 weiyun_upload.py 上传模块（以太坊 token 鉴权，config.json 填写 weiyun_token /
  weiyun_pdir_key（群共享「TK监控器log」目录）。
- config 新增三项：weiyun_upload_enabled（总开关，默认 False）、weiyun_token、
  weiyun_pdir_key；开关关闭或未配 token 时自动跳过，不影响主流程。
- 上传状态落盘 weiyun_upload_state.json，避免重复上传；单文件失败仅记日志不阻塞。
- 版本号 v10.16——文件头/启动日志/窗口标题/spec 同步升级。
v10.14.7 功能（2026-08-29）：第一批功能修改（按 to-be 设计文档 §7/§8 执行）
- toast 统一管控（§8.4-4）：所有 notify_toast 调用点（告警推送/识别失败提示两处）
  统一受 toast_enabled 控制，关闭后一律不弹；toast_enabled 兜底值统一 False。
- 去顶部深色拖动条（§8.4 方案A）：删除顶部 #2b2b2b 拖动条、_lbl_bar 状态小字与
  右侧 ✕ 隐藏按钮；保留面板空白处拖动、四边 resize、右键菜单、隐藏/退出热键。
- 刷新链路（§7.1/7.2/8.3）：自动监控每轮识别前强制刷新（uia_reload，UI「刷新浏览器」
  勾选框默认勾选，refresh_enabled 保留为该勾选框）；refresh_interval_sec 节流已删除；
  刷新失败自动重试 1 次（等 5 秒），重试仍失败记日志并含失败原因（§7.4-3）。
- 删除项（§7.3）：「测试刷新」按钮与 do_test_refresh/_manual_refresh_done 方法；
  push_batch_minutes 输入框与旧式告警三件套 alert_metric/alert_operator 于 v10.14.3
  已删（复核无执行残留）；uia_filter/f5 分发分支于 v10.14.3 已删（复核无执行残留）。
- refresh_hotkey 补 GUI 入口：工具→快捷键配置弹窗新增第三项「手动完整轮
  （先刷新再识别）」，保存时格式/冲突校验、写入 lowkey.refresh_hotkey、日志同步。
- 清理死代码：LOG_FILE/OPS_LOG_FILE 废弃常量、_post_refresh_key 与 F5 相关常量、
  lbl_status 分支（hasattr 判断）、push_count_text 死函数（仅剩定义无调用）。
- 版本号 v10.14.7——文件头先行同步；启动日志/窗口标题/spec 随打包批次同步。
v10.14.6 功能（2026-08-29）：校验上提——手动识别路径与自动路径一致
- 用户要求：窗口未定位校验不应只修自动监控，整个程序都要做校验，
  校验逻辑应上提为通用能力，新功能前后必须校验。
- 本次修复（一处）：手动识别（立即识别一次/热键）_run_manual_ocr 在识别前
  增加与 _monitor_round 相同的 _resolve_hwnd 预检——未定位到目标窗口时直接
  跳过识别并明确提示，不再白跑一轮刷新+识别后抛"未取得窗口句柄"异常。
- 效果：自动监控与手动识别两条触发路径的窗口定位校验行为完全一致。
- 版本号 v10.14.6——文件头/启动日志/窗口标题/spec 同步升级。
v10.14.5 功能（2026-08-29）：逆向纠错——窗口未定位不推送
- 用户反馈：没有指定窗口（浏览器后台未打开/句柄失效/关键词无匹配）时，
  自动监控仍机械推送"识别失败/监控异常"告警，没有逆向纠错。
  根因：v10.14 起"识别失败也告警推送"，窗口未定位被识别器抛异常后，
  _monitor_loop 捕获并当作识别异常生成告警推送，未先校验窗口是否定位成功。
- 本次修复（两处）：
  ① _monitor_round 识别前预检：_resolve_hwnd 未定位到目标窗口（锁定句柄失效
    或标题关键词无匹配）时，本轮直接跳过识别、不入库、不告警、不推送，
    日志提示先打开后台并『列出窗口』锁定；
  ② _monitor_loop 异常兜底：识别异常信息含"未定位到目标窗口/未取得目标窗口
    句柄/未定位窗口/句柄失效"时只记本地日志，不生成推送告警。
- 效果：窗口没定位到 → 静默跳过该目标，不再机械推送无意义告警。
- 版本号 v10.14.5——文件头/启动日志/窗口标题/spec 同步升级。
v10.14.4 功能（2026-08-29）：彻底删除最小化刷新方案 + 改名 tkm_版本号 + 日志模块化
- 用户实测 v10.14.3 仍把窗口最小化后识别失败：确认 mod.py _uia_reload 内方案A
  （方案A：点击前最小化窗口后刷新）实际未删净。
  本次彻底删除方案A整段，刷新点击前显式置前（SW_RESTORE + SW_SHOW + SetForegroundWindow），
  窗口保持原状不最小化；识别完成后不动窗口。
- 程序改名：不再叫"工作台"，统一 tkm_10.14.5——spec name / lowkey.window_title 默认值 /
  托盘 pystray.Icon name+tooltip / FindWindowW 兜底标题 / 单实例 Mutex+Event（Local\tkm_v10_*）
  全部改用 tkm 命名空间（旧标题 "TikTok 广告监控 v10.x" 匹配兼容保留）。
- 日志模块化：monitor/ops 日志统一带 [tkm_10.14.5] 版本前缀与模块标签
  （[监控]/[刷新]/[识别]/[推送]/[热键]/[窗口]/[旧版检测]）；最小化/恢复等窗口操作写
  操作级 ops 日志；旧版实例检测改为扫描 tkm_ 前缀进程并从文件名解析版本号，
  不再笼统说"旧版本"。
v10.14.3 功能（2026-08-29）：刷新路径统一「刷新后扫描」+ 抢前台修复
- 用户实测：启动自动监控只扫描不刷新、手动热键刷新却因最小化导致识别失败——
  根因：① 启动路径受 refresh_enabled=False 与 refresh_interval_sec 节流控制，默认不刷新；
  ② 手动热键走独立两段式（先 _send_refresh_to_target 再 _monitor_round），且 mod _uia_reload
  点击前最小化窗口，Chromium 暂停渲染、UIA 读不到表格报 no_header。
  本次统一：两条路径都经 _monitor_round → _maybe_refresh_target（每轮强制刷新）→
  _send_refresh_to_target（仅 uia_reload）→ recognize_once，差异仅触发来源与 push 参数。
- 退回抢前台：_uia_reload 删除最小化逻辑，改为 SW_RESTORE + SW_SHOW + SetForegroundWindow
  置前后再点击；识别完成后不动窗口。poll_sec = max(5, refresh_wait_sec)（默认 8 即用 8）。
- refresh_enabled 默认 True，UI「③ 自动监控」新增「刷新浏览器」勾选框默认勾选
  （取消勾选则跳过刷新直接识别）；refresh_interval_sec 节流已删除。
- 清理：删除「测试识别」按钮（新增「测试刷新」验证刷新链路）、「立即识别一次」改为先刷新再识别、
  删除「合并推送间隔（分钟）」输入框（push_batch_minutes）、删除旧式告警三件套
  alert_metric/alert_operator 兼容代码（alert_threshold 保留为规则预设参数承载键）、
  toast 默认值统一 False（上班防被同事看到）。
v10.14.2 功能（2026-08-28）：推送开关真修复 + 旧版实例检测 + 进程名低调化
- 用户反馈"关了推送还推"：确认 v10.14.1 的 _flush_push_batch 修复实际未落盘
  （发送前仍 load_config() 读磁盘旧值，取消勾选未保存时磁盘 true 照推）。
  本次真正修复：发送前先 _sync_push_ui_to_cfg() 同步 UI 开关到内存配置，直接用 self.cfg。
- 新增旧版实例检测：启动时扫描系统进程，若发现旧版 TikTok监控_v10.14.exe 等进程
  仍在运行，弹窗提示先退出旧版再启动（防止旧版继续推送、任务管理器多个同名进程混淆）。
- 进程名低调化：打包 exe 名不再含"TikTok监控"字样（避免被同事在任务管理器看到），
  改为中性名"工作台"；窗口标题/托盘名同步低调（lowkey.window_title 默认"工作台"）。
  【v10.14.4 改名】"工作台"废弃，统一 tkm_10.14.5（spec name / 窗口标题 / 托盘 / Mutex）。
- 版本号 v10.14.2——文件头/启动日志/窗口标题/spec 同步升级。
v10.14 功能（2026-08-28）：推送改"每轮识别完统一打包" + 识别失败也告警
- 用户反馈：为什么推送要等 30 分钟？改为——自动监控一轮（所有启用店）识别
  完成后，把本轮所有告警合并为一条立即推送，不再按 push_batch_minutes 等待；
  手动完整轮沿用"人在电脑前"原则（push=False）只记录不推送（修正 v10.12 注释
  声明的设计意图）。
- 识别失败告警：某店识别失败（结构对不上/未解析到 ROI/成本/收入/识别异常），
  自动监控时也会生成"识别失败"告警并随本轮正常告警一起打包推送，便于用户判断
  是网络不好或浏览器页面未刷新。面板⑥"合并推送间隔(分钟)"已停用（保留输入框
  兼容旧配置，不再参与推送时机）。
- 实现：_monitor_round 新增 push 参数（默认 True）；识别失败分支调用新增
  _enqueue_round_alert 入缓冲；_enqueue_push 去掉 Timer 只入缓冲；
  _monitor_loop 每轮 targets 循环结束后统一 _flush_push_batch；stop_monitor
  兜底 flush 保留；手动完整轮调用 _monitor_round(push=False)。
- 版本号 v10.14——文件头/启动日志/窗口标题/spec 同步升级。
v10.13 功能（2026-08-28）：无边框窗口四边拖拽改大小 + 最小尺寸钳制开关 + 下分板块显隐自定义
- 无边框窗口四边 resize：overrideredirect 窗口在标题栏拖动（移动窗口）基础上，扩展
  上/下/左/右四边及四角拖拽调整窗口尺寸（边缘 8px 热区，悬停显示方向光标，按下拖动
  实时改 geometry）；最小尺寸钳制开启时边缘拖拽同样受最小宽高约束（800x600），
  左/上边缘 resize 时同步移动窗口位置以保持右下角不动；resize 结束保存布局。
- 最小尺寸钳制开关：config.lowkey 新增 window_min_size_enabled（默认 True，开启时
  窗口强制不小于 800x600，关闭时不钳制可任意缩小）；视图菜单新增「最小尺寸钳制」
  勾选项，切换即生效并落盘 config。
- 下分板块显隐自定义：主面板下方各板块（②ROI/③自动监控/④识别规则/⑤筛选/⑥推送
  设置等）标题栏右侧新增「✕」隐藏按钮，点击整块隐藏不占空间；折叠态标题行同样
  提供「✕」；运行日志区操作行新增「隐藏」按钮（等价「显示日志」取消勾选）；所有
  板块均可在菜单栏「视图→显示/隐藏板块」中重新打开；显隐状态沿用 hidden_sections
  写入 config 持久化，重启保持。
- 版本号 v10.13——文件头/启动日志/窗口标题/spec 同步升级。
v10.12 功能（2026-08-28）：手动完整轮热键 + 日志分层按天 + 单实例日志 + 按日期过滤查看
- 刷新热键改为 ctrl+alt+shift+r（lowkey.refresh_hotkey 可配，注册方式不变）：按下触发
  "手动完整轮"——执行与自动监控一轮完全相同的完整流程（识别→最小化浏览器→UIA 刷新→
  等待加载→快照→告警判断→写日志），结果正常进 log，完成后重置自动监控定时器重新计时。
  手动场景沿用 v10.9 原则：人就在电脑前，告警只记录不远程推送（push=False）、不响声音。
- log 分层：monitor 主流程日志（每轮扫描/刷新完整记录，含手动触发轮次）与 ops 操作级
  日志（UIA 控件操作细节：找到的控件/点击动作/最小化动作/快照数值/就绪轮询，由 uia_recog
  mod 写入）分开独立文件。两者均按天轮转：monitor_YYYYMMDD.log / ops_YYYYMMDD.log，
  新的一天自动建新文件，老文件保留不覆盖。
- 单实例检测日志：启动时记录检测结果（通过/唤起已有实例）。
- 按日期过滤查看（uia_recog mod 注册「按日期过滤」按钮）：输入起止日期→替换 URL 中
  list_start_date/list_end_date（UTC 毫秒时间戳）→地址栏（Omnibox aid=view_1012）输入
  →回车导航→表格就绪轮询；过滤显示效果需工作电脑实测（开发机无目标窗口）。
- 版本号 v10.12——文件头/启动日志/窗口标题/spec 同步升级。
v10.11 功能（2026-08-28）：手动刷新热键 + 提示默认值调整 + 声音分类
- 新增全局热键 ctrl+shift+r：按下触发一次手动刷新（复用 _send_refresh_to_target 的
  uia_reload 刷新链路，uia_recog _uia_reload 最小化不抢前台方案不变），后台线程执行
  不阻塞 UI，日志记录"手动热键刷新"；注册方式与现有热键（ctrl+alt+shift+d 呼出/隐藏、
  ctrl+alt+shift+x 退出）一致，lowkey.refresh_hotkey 可配。
- 提示默认值调整：toast_enabled 默认改 False（toast 默认关闭，面板⑥开关保留可手动开，
  防上班时间被同事看到）；sound_alarm 保持 True 默认开。
- 声音按告警类型区分：正常扫描完成（本轮无告警）保留现有轻滴声 Beep(880,120)；
  有不好情况（负面告警：ROI低/空烧/超上限/止损等）播低沉长音 Beep(220,500)；
  加预算信号（ROI 达投放目标 preset_roi_good 命中）播明亮双连音 Beep(1300,120)×2；
  仅自动监控（push=True）且声音开关 sound_alarm 开启时发声。
- 版本号 v10.11——文件头/启动日志/窗口标题/spec 同步升级。
v10.10 功能（2026-08-27）：后台刷新不抢前台（方案A）+ 提示开关面板化
- 方案A：后台刷新前先把浏览器窗口最小化——uia_recog mod _uia_reload 在 Invoke
  点击『重新加载』按钮（aid=view_1003 ReloadButton）前，先最小化目标浏览器窗口
  （v10.14.4 已彻底删除此方案），点击后保持最小化不恢复、不做前台还原，浏览器物理上无法
  抢前台；若窗口已最小化或不可见则跳过最小化直接刷新（ReloadTest v1.3 已验证
  UIA 点击方案，本轮不做单独验证直接合入）。
- 告警 toast 开关面板化：cfg 新增 toast_enabled（默认 True，面板⑥推送设置区可勾选），
  False 时自动监控命中告警不再弹 Windows 系统通知气泡（上班时间防被同事看到）；
  声音提示（sound_alarm，v10.9.1 已加）与远程推送不受影响。
- 版本号 v10.10——文件头/启动日志/窗口标题/spec 同步升级。
v10.9.1 功能（2026-08-27）：告警轻滴声（低调提醒）
- 任务：toast 有点打眼（显眼干扰），但去掉后漏看告警。新增一项很低调的
  提醒方式——自动监控命中告警时用 winsound 播放一声很轻的短滴声（880Hz/0.12s），
  不占视线、够耳闻即知是告警；手动识别/快捷键刷新（人就在电脑前，push=False）
  依旧不响、不折腾声音。
- 实现：cfg 新增 sound_alarm（默认 True，面板⑥推送设置区可勾选）；
  run_alerts push=True 分支在 toast 播放处并行响一声；
  _load_config_to_widgets/_sync_push_ui_to_cfg 同步（顺带补上 v10.9 遗漏的
  push_batch_minutes 回填）。
- 版本号 v10.9.1——文件头/启动日志/窗口标题/spec 同步升级。
v10.9 功能（2026-08-27）：打包推送 + 手动识别不推送
- 任务：推送额度保护——推送是用户不在电脑前的告警方式，不该被"每轮扫描都推"耗尽
  （PushPlus 免费 200 条/日）。本轮落地两点：
  ① 打包推送：cfg 新增 push_batch_minutes（默认 30 分钟，面板⑥可配，0=每条立即推）。
     自动监控命中的告警先进合并缓冲（_push_buffer），窗口到点由 Timer 合并为一条
     推送发送（标题含合并条数，正文按触发时间分段）；同轮多行仍保持原有合并逻辑。
     stop_monitor 兜底 flush 剩余缓冲，避免停止时丢告警；flush 异步不卡 UI。
  ② 手动识别/快捷键刷新不走推送：run_alerts 新增 push 参数（默认 True）；
     _manual_ocr_done 手动识别调用传 push=False——只记录入库、跳过远程推送与 toast
     （用户人在电脑前，告警日志可见即可）。自动监控轮询不受影响。
- 实现：run_alerts(push=True) → _enqueue_push → threading.Timer → _flush_push_batch；
  面板⑥新增"合并推送间隔(分钟)"输入框；_load_config_to_widgets/_sync_push_ui_to_cfg 同步。
- 版本号 v10.9——文件头/启动日志/窗口标题/spec 同步升级。
v10.8.8 功能（2026-08-27）：新计划宽松观察期
- 任务：组长策略落地——新计划刚上要宽松，别因空烧/不出单就喊停。新增观察额度
  grace_cost（默认 $20）：累计消耗未达额度前，空烧检测、消耗超客单倍数止损两类
  预设告警降级为「观察提示」（文案不含立即停投/建议止损），且不计入核心告警、
  不触发自动关停；累计消耗达到 $20 后自动恢复正常告警规则。ROI 保本（0.6）、
  ROI 达标、累计消耗超上限、CVR 低等其余告警不受影响，照常推送。
- 实现：cfg 新增 grace_cost=20.0；_rule_env 注入 {grace_cost} 与 {in_grace}；
  eval_alert_rules 对 preset_burn/preset_cost_x_aov 在 in_grace 时改写提示文案、
  跳过 hit_preset_ids（check_alerts 核心判定/自动关停随之豁免）。
- 版本号 v10.8.8——文件头/启动日志/窗口标题/spec 同步升级。
v10.8.7 功能（2026-08-27）：OCR/变更检测清理版
- 全局目标"逻辑统一"：OCR 错误率高，OCR 相关功能以 mod 形式分出去
  （ocr_recog 移出 mods/ 至 mods_off/，不再参与加载、开发环境不再显示"截图OCR"选项）；
  主程序不再保留 OCR 引擎相关内容。
- 主程序删除：DEFAULT_CONFIG 中 ocr_engine / change_detect_enabled / change_threshold
  三键；ImageChops 导入；_pil_diff_norm + image_similarity；_last_frames 初始化；
  ④.7 变更检测板块 UI（f_cd/启用开关/阈值/保存按钮/sec_cd 标题）及 _reload_cfg 回填、
  save_change_detect、_capture_change_frame、_monitor_round 变更检测跳轮块；
  ④.6 OCR 引擎下拉（var_ocr_engine/OptionMenu）；save_rec_rules 写回 ocr_engine；
  rec_rules_summary engine_txt；_get_recog 降级 OCR fallback（未注册直接报错）；
  recognize_once docstring/报错文案只提 uia_recog；手动识别按钮文案固定"测试识别"。
- 保留（勿删）：截图核心（capture_region/capture_roi/_capture_full_window）、
  _manual_ocr_done/_run_manual_ocr（UIA 也在用）、_store_last_ocr_result/last_ocr_result
  系列（简略模式+ai_debug 数据源）、识别规则解析键（UIA 文本回退 _parse_recognized 在用）、
  recog_mode/uia_* 配置键。
- ai_debug mod：ALLOWED_KEYS 移除 ocr_engine / change_detect_enabled / change_threshold。
- spec：仍只内嵌 uia_recog + ai_debug，excludes 保留 rapidocr_onnxruntime/onnxruntime/cv2。
- 版本号 v10.8.7——文件头/启动日志/窗口标题/spec 同步升级。
v10.8.3 功能（2026-08-26）：
- 修复"正常显示的数据行被当排错处理、未入库"问题：所有正常解析出的计划行
  （计划名/ROI/成本/收入/状态/预算/时间戳等）全部入库/落盘做数据追踪，不再
  只入"已选中第1行"；显示/记录/入库不受推送条件限制，未达成推送条件的行
  仅不推送。
  - 手动识别（_manual_ocr_done）识别成功后对全部候选行逐行 add_record（补
    shop 字段），与自动监控 _monitor_round 入库链路对齐：先入库→再 run_alerts
    （推送条件不影响入库）；测试模式（test=True）仍只测试不入库。
  - 日志级别修正：正常候选行拾取明细从 [排错]（debug）降为 [信息]（info），
    只有真正解析失败/严格校验拒绝的行（rejects）才保留 [排错] 级；正常行
    数据可在日志区直接看到完整入库记录（每行一条 result 级日志）。
  - 推送与入库解耦保持：run_alerts 仅判断推送条件与冷却，不参与入库过滤。
- 版本号 v10.8.3——文件头/启动日志/窗口标题/spec 同步升级。
v10.8.2 功能（2026-08-26）：
- 定期刷新升级为 uia_reload（路线D）：UIA 点击浏览器工具栏『重新加载』按钮
  （ButtonControl aid=view_1003 class=ReloadButton，InvokePattern.Invoke() 点击，
  LegacyIAccessiblePattern.DoDefaultAction() 兜底），替代路线C 筛选刷新（uia_filter：
  勾选『状态→已生效→应用』在 TikTok 广告面板实测下拉弹层不在控件树，不可行，已放弃）。
  方案先由独立工具 ReloadTest（输出/探测工具/uia_reload_test.py）在工作电脑实测确认
  可刷新页面，再合并进主程序，形成『定期刷新→识别记录』闭环。
  - refresh_method 默认 "uia_reload"（保留 uia_filter/f5/ctrl_r 兼容，部署 config.json 置
    refresh_enabled=true、周期 60s）；
  - mods/uia_recog 新增 _uia_reload：目标窗口定位（复用 _resolve_hwnd 体系）→ 主窗口整树
    +渲染子窗口下钻收集 → 查找 ReloadButton（优先 aid=view_1003 且 class=ReloadButton，
    其次 aid 匹配，再其次 Name 含『重新加载』）→ Invoke 点击 → 就绪轮询（间隔1.5s，最多
    refresh_wait_sec 秒[范围5~15]，检测地址栏 URL 变化/表格行数就绪，到点固定延时兜底）；
    找不到重载按钮时输出候选清单日志（类型|Name|AutomationId|rect|Patterns）并失败返回，
    不谎报成功；全程纯控件级 UIA Pattern 调用，不抢焦点、不模拟键盘、只作用于目标浏览器窗口。
  - _maybe_refresh_target：uia_reload 成功后不再额外 sleep（就绪等待已在 mod 内完成），
    其他方式保持原行为。
- 版本号 v10.8.2——文件头/启动日志/窗口标题/spec 同步升级。
v10.8.1 功能（2026-08-26）：
- 修复全局热键无效：v10.8.0 用 RegisterHotKey(None, ...) 把 WM_HOTKEY 投递到 tkinter
  主线程消息队列，Tk 主循环会抢先取走并丢弃（hwnd=NULL 的线程消息无窗口处理），导致
  其他程序前台时热键失效。改为独立热键线程 + 隐藏消息窗口（HWND_MESSAGE）接收：
  RegisterHotKey 注册到该窗口，线程内 GetMessage 循环取 WM_HOTKEY，经线程安全队列通知
  主线程执行呼出/退出，与 tk 消息循环完全隔离；保留热键占用冲突检测提示。
- 修复单实例唤起未生效：v10.8.0 的唤出命名事件为 auto-reset，若 SetEvent 时旧实例监听
  线程未处于等待（初始化中/正在调度回调），信号即丢失；且监听线程直接跨线程调用
  root.after 调度显示，不稳定。改为 manual-reset 事件（不丢信号）+ 监听线程只写线程
  安全队列、由主线程轮询执行显示，彻底规避跨线程 tkinter；新实例 SetEvent 后延时再用
  Win32 兜底唤起旧窗口，双保险；补充"检测到已有实例，发送唤起"/"收到唤起，恢复窗口"日志。
v10.8.0 功能（2026-08-25）：
- 单实例运行：重复打开 exe 不再出现多个同名进程。启动早期（创建窗口/注册热键/加载
  Mod 之前）用命名 Mutex 检测是否已有实例；若已有：向旧实例发出"唤出"信号恢复显示并
  置前聚焦（即使处于隐藏/低调模式），新进程立即退出。兼容旧版本实例（v10.7.9 及以前
  无 Mutex）按主窗口标题兜底检测并用 ShowWindow+SetForegroundWindow 唤起。
- 快捷键配置界面：工具菜单新增「快捷键配置」入口，列出呼出/隐藏（hotkey）与低调退出
  （quit_hotkey）当前键值，支持输入新组合键（ctrl+alt+shift+字母 等，留空禁用），保存后
  写入 config.json 的 lowkey 段并实时重新注册（注销→注册，失败明确提示占用冲突）。
- 版本号 v10.8.0——文件头/启动日志/窗口标题/spec 同步升级（窗口标题顺带修正 v10.7.8 遗留）。
v10.7.9 修复（2026-08-25）：
- 筛选刷新（路线C filter_refresh）不生效排查与修复：
  ①选项匹配修复：_find_option_ctrl 原为全页面 Name 含『已生效』即取（DataItem/Text
  可作选项），TikTok 广告计划列表状态列每行文本『已生效』会被当作下拉选项，
  SelectionItem.Select() 对表格行成功返回但页面无变化（浏览器『无动静』）。
  v10.7.9：精确名优先（Name==keyword 优先于包含匹配）+ 下拉弹层区域约束（anchor
  状态筛选框矩形，仅保留下方附近候选）+ 顶层窗口扩展排除目标主窗口与其他店铺标题
  窗口，规避表格行文本误选。
  ②失败日志增强：筛选刷新每一步输出『第X步 找到/未找到 XX 控件，匹配依据=…，动作=…，
  结果=…』；任一步未命中时输出该关键词命中的候选控件清单（type|Name|AutomationId|矩形，
  前8个）与排除原因，下次实测可一眼定位卡点。
  ③DataItem/Text 类『已生效』勾选成功后输出提示：UIA 无法回读页面勾选状态，若实测无
  变化可能命中表格行文本，需回传 uia_probe_*.txt 或开 mouse_click_fallback。
- 版本号 v10.7.9——文件头/启动日志/窗口标题/spec 同步升级。
v10.7.8 修复（2026-08-25）：
- 结构化拾取断链修复：UIA 结构化读取成功（列映射已生成）后，拾取详情日志误报
  『未找到表头行（ROI+成本+收入），识别条件为宽松解析兜底』且『已选中第None行』。
  根因：_log_pick_detail 缺少 mode="structured" 分支，结构化 detail 落到 else 分支；
  _structured_detail 未写 selected_index。修复：日志增加结构化命中分支（明确按列映射
  取值），detail 补 selected_index（=结构化首行），'已选中第None行'修正为真实行号。
- 数值解析增强：_to_float_cell 整体 float 失败后回退提取文本中第一个十进制数，容忍
  'ROI:1.1' / 'e/ROI:1.1' / 'p/:18.91' 等带标签前缀的 UIA cell 文本，不再解析为
  0.0/None；结构化行记录 roi_raw/cost_raw/revenue_raw cell 原文，解析失败的行进
  detail.rejects 并在日志输出真实 cell 文本（便于下次实测定位 roi@2068 取到的文本）。
- _store_last_ocr_result 计划名回溯按 row 匹配候选行（原实现把 selected_index 行号
  当 list 索引，可能取错行）。
- 版本号 v10.7.8——文件头/启动日志/窗口标题/spec 同步升级。
v10.7.7 修复（2026-08-25）：
- 拔除部署版僵尸 OCR：v10.7.4 起 spec 内嵌整个开发\mods，把 ocr_recog（及 rapidocr/
  onnxruntime/cv2 依赖）打进 exe；v10.7.7 起 spec 只内嵌 uia_recog + ai_debug，部署版
  不再携带 ocr_recog/example_mod；exe excludes 保留 rapidocr_onnxruntime/onnxruntime/cv2。
- 默认识别模式改 UIA：DEFAULT_CONFIG.recog_mode 默认 "ocr"→"uia"，防止 config 缺键回落
  OCR；部署 config.json 设 recog_mode=uia 并加 disabled_mods=["ocr_recog","example_mod"] 兜底。
- 热键冲突处理：默认全局热键 ctrl+alt+shift+m/q 易被其它程序占用，v10.7.7 起默认改为
  ctrl+alt+shift+d（呼出/隐藏）/ ctrl+alt+shift+x（低调退出），可在 config.json lowkey 段
  自行修改；注册失败时日志明确提示去 config.json 修改，不再静默降级。
- 版本号 v10.7.7——文件头/启动日志/窗口标题/spec 同步升级。
v10.7.6 低调化改造（2026-08-25）：
- 主窗口无边框无标题栏（frameless），默认小窗启动（lowkey.default_geometry=900x600，
  不恢复历史大几何）；支持空白处拖动 + 极简拖动条 + 右键菜单低调退出。
- mini 简略模式小窗：持续显示店铺名/ROI/状态等关键数据并随识别自动刷新；右键菜单支持
  展开完整界面/隐藏/退出；置顶可配。
- 隐藏模式：tray=托盘模式（最小化到系统托盘）；extreme=极端模式（默认，无托盘，仅全局
  热键呼出/隐藏，默认 Ctrl+Alt+Shift+M；退出热键 Ctrl+Alt+Shift+Q）。
- config.json 新增 lowkey 段落集中低调配置（frameless/default_geometry/remember_geometry/
  launch_mode/mini_on_start/hide_mode/hotkey/quit_hotkey/mini_always_on_top）。
- 保留全部既有功能：自动监控、UIA 读取、路线C 筛选刷新（filter_refresh/probe）、AI 调试服务等。
v10.7.5 修复（2026-08-25）：
- 修复：路线C 筛选刷新「状态筛选框」误匹配浏览器 UI（用户工作电脑实测把「标签页重新变为
  活动状态」ButtonControl（AutomationId=view_1011 附近）当页面状态筛选框展开）。根因：
  uia_recog mod 匹配逻辑只按 Name 含『状态』+ButtonControl，未排除浏览器自身 UI。
  修复：页面控件定位限定页面内容区——优先从目标窗口渲染子窗口（Chrome_RenderWidgetHostHWND）
  子树收集控件（对照模式二能读到 core-table-tr 的下钻方式），过滤浏览器 chrome UI
  （AutomationId 以 view_ 开头 / Name 为浏览器功能名如"地址和搜索栏/标签页分组/跨境导航"等）。
- 修复：勾选『已生效』失败仍报"流程全部完成"。根因：DataItemControl 无 Toggle/SelectionItem/
  Invoke Pattern 时 _select_option 返回失败，但流程未中止继续点『应用』且失败项未记入 missing。
  修复：勾选/选中增加 LegacyIAccessiblePattern.DoDefaultAction() 兜底（DataItem 常见可选项）；
  任一步骤失败立即返回失败并中止后续步骤（勾选失败不再点『应用』），日志标注"流程失败于第X步"；
  全部 Pattern 不可用时可配置 mouse_click_fallback=true 启用鼠标点击降级
  （SetCursorPos + mouse_event 左键单击，短暂移动鼠标，默认 false）。
- 修复：控件探测不完整（dump 345 个控件主要是浏览器 chrome UI，页面内筛选框/表格/按钮未覆盖）。
  根因：控件探测仅从主窗口 root 遍历，未下钻渲染子窗口。修复：遍历范围=主窗口 + 渲染子窗口
  合并去重；每条记录标注 zone（page_content=页面内容 / browser_chrome=浏览器UI，
  依渲染子树来源 + AutomationId view_ 前缀粗分）；完整清单写入 temp 目录
  uia_probe_YYYYMMDD_HHMM.txt（方便回传分析），日志只显示统计摘要 + 页面内容控件明细。
- 版本号 v10.7.5——窗口标题/启动日志/文件头/spec 同步升级。
v10.7.4 修复（2026-08-25）：
- 修复：v10.7.3 exe 内 uia_recog mod 缺 filter_refresh/probe 能力（用户工作电脑实测
  「测试筛选刷新」「控件探测」报 mod 未注册）。根因：PyInstaller spec 未将 mods 目录
  打入 exe（datas=[]），exe 运行时依赖 exe 同级外部 mods 目录，旧部署包外部 mods 未
  同步新 mod.py。修复：spec datas 内嵌 mods 目录；load_mods 在 frozen 下优先加载 exe
  内嵌 mods（_MEIPASS/mods），内嵌不存在时回退 exe 同级外部 mods（兼容旧部署包）。
- 修复：窗口标题仍显示 v10.7.2（v10.7.3 升级时漏改），现统一为 v10.7.4。
- 版本号 v10.7.4——窗口标题/启动日志/文件头/spec 同步升级。
v10.7.3 新增（2026-08-25）：
- 任务：定期刷新路线C（UIA 控件操作筛选刷新）——路线B（PostMessage 直发 F5/Ctrl+R）
  实测失败（错误47：消息投递成功但 Chromium 过滤后台伪造按键、页面不刷新），
  改用纯控件级 UIA 操作模拟手动刷新流程：点『状态』筛选框 → 勾选『已生效』 → 点『应用』，
  既刷新数据又只显示已生效计划；全程只调 UIA Pattern，不模拟键盘鼠标、不抢焦点。
- mods/uia_recog/mod.py 新增两能力（core._uia_mod_actions 注册）：
  ① 控件探测（_uia_ctrl_probe）：遍历目标窗口 accessibility tree，dump Button/ComboBox/
     CheckBox/Text/Edit/ListItem 等控件的 ControlType+Name+AutomationId+矩形，供确认
     筛选相关控件的准确 UIA 路径；
  ② 筛选刷新（_uia_filter_refresh）：执行"点状态筛选→勾选已生效→点应用"。控件定位优先
     Name 匹配（"状态"/"已生效"/"应用"）+ ControlType 过滤（ComboBox>Button>Edit>Text、
     CheckBox>ListItem>DataItem>MenuItem）；展开用 ExpandCollapse/Invoke/LegacyIAccessible，
     勾选用 Toggle/SelectionItem/Invoke，点击用 Invoke/LegacyIAccessible；下拉弹层可能在
     同一控件树或新顶层窗口，先遍历子树、再扩展枚举 Chromium 顶层窗口；控件找不到返回明确
     失败原因（哪些控件没找到），优雅降级不崩溃。
- 主程序集成：③ 自动监控新增「测试筛选刷新」「控件探测」按钮（后台线程执行，日志逐步骤反馈
  找到状态框→展开→勾选已生效→点应用 每步成功/失败）；refresh_method 新增 "uia_filter"
  （默认，"f5"/"ctrl_r" 保留但标注已废弃）；refresh_enabled=true 且 refresh_method=uia_filter
  时 _monitor_round 开头执行筛选刷新替代按键刷新，刷新间隔仍用 refresh_interval_sec（默认 60）。
- 日志：刷新每步操作结果完整记录到 monitor.log（mod 带 [筛选刷新] 前缀）。
- 版本号 v10.7.3——窗口标题/启动日志/文件头同步升级。
v10.7.2 新增（2026-08-25）：
- 任务：定期刷新页面（路线B：Win32 消息直发）——页面不刷新数据不更新场景下，
  每隔 refresh_interval_sec 秒向目标浏览器窗口（hwnd，class=Chrome_WidgetWin_1）
  通过 PostMessage 直发刷新键消息（F5=refresh_method:"f5"，或 Ctrl+R="ctrl_r"），
  不模拟键盘鼠标（不用 pyautogui/SendInput）、不调用 SetForegroundWindow 抢焦点、
  不依赖前台窗口，与 WM_MOUSEWHEEL 滚动收集同架构。⚠️ v10.7.3 起废弃：实测 Chromium
  过滤后台伪造按键消息、页面不刷新（错误47），仅保留代码用于对比。
- 新增配置：refresh_enabled（bool，默认 False）/ refresh_interval_sec（int，默认 60）
  / refresh_method（"f5"/"ctrl_r"，默认 "f5"）/ refresh_wait_sec（int，刷新后等待
  页面加载秒数，默认 5）。
- 挂载点：_monitor_round 开头（识别之前）；独立计时
  （记录上次刷新时间，满周期才刷新，不与识别轮询间隔耦合）；刷新后 sleep
  refresh_wait_sec 再继续识别；多目标按 t 逐个刷新各自窗口。
- UI：③ 自动监控新增「测试刷新」按钮，立即对当前锁定/匹配的目标窗口发一次刷新键，
  实测 Chromium 是否响应消息直发（本机开发无紫鸟浏览器，实测由用户在工作电脑进行）。
- 日志：刷新动作/刷新目标/失败异常均记录到 monitor.log（含 hwnd 与方式）。
- 版本号 v10.7.2——窗口标题/启动日志/文件头同步升级。
v10.7.1 新增（2026-08-25）：
- 任务1：告警统一收编为单一规则入口——核心告警条件（alert_metric/alert_operator/
  alert_threshold 的 roi/cost/revenue 组合）与 CVR 低、消耗×客单全部收编为预设规则：
  新增 preset_cvr_low（{cvr} < {cvr_low}）、preset_cost_x_aov（{cost} > {aov} * {cost_x}
  and {orders} == 0）；check_alerts 删除核心条件/CVR/空烧/消耗×客单硬编码分支，改由规则
  引擎驱动（preset_burn/preset_roi_good 等既有预设承接）；_rule_env 扩展 {cvr} 字段。
- 任务2：UI 收编——删除④板块旧告警条件区（指标/比较/阈值/冷却/其他阈值输入），保留
  alert_enabled 总开关、冷却、推送模板、排除规则入口；板块改名「④ 告警规则」；规则编辑
  面板增强：预设参数（threshold/cost_max/roi_good/burn_redline/cost_x/cvr_low）数值编辑、
  表达式保存时即时校验、变量可点击插入；ensure_preset_rules 改为按 id 补缺失（不覆盖用户
  已编辑项），部署 config.json 同步注入新预设。
- 任务3：代码清理——删除死代码（_exclude_hit/RULE_TOKEN_RE/last_frame/flow_reqheight/
  _refresh_alert_rules_btn 别名）；合并重复实现（render_pushplus_content+render_alert_content
  →_render_template、_metric_label/_op_label/_plan_label/_push_count_state、公共正则列表
  弹窗 helper、save_orders 与 do_manual_record 公共写入、TEMPLATE_VARS 提升为单一常量、
  _template_insert_var 首次 lambda 直绑）；处理残留（2172 注释、_strict_validate 死分支、
  _safe_eval_rule bool 分支、log_height_ratio/sash_log_ratio 双轨合一）。
- 版本号 v10.7.1——窗口标题/启动日志/文件头同步升级。
v10.7 新增（2026-08-24）：
- 任务1：布局重构——去掉左右分栏（pw_main 水平分栏）与可拖 sash，改为单列纵向排列：
  所有配置板块（①~⑥）+⑦最近识别结果统一挂在主滚动容器 main_frame 纵向排列，
  底部冻结日志（pw_root 垂直分栏 + f_log）保留；窗口最小尺寸锁定 minsize(800,600)，
  按钮行由 FlowFrame 自动换行兜底；移除「上下分栏/选项卡堆叠」单选、布局子菜单、
  「板块布局…」弹窗（左右归属已无意义）；window_layout 保留 mode/show_result/
  show_log/log_height_ratio/geometry 字段兼容旧配置，left_sections/right_sections 不再使用。
- 任务2：新增「简略模式」小窗（视图菜单 checkbutton）——320x180 无边框置顶小窗，
  鼠标可拖动、右键/Esc 关闭；只显示当前目标名 + 每计划一行（计划名 ROI/成本/收入/订单）
  + 最近识别时间，无数据时显示「待识别」；不显示窗口句柄/ROI坐标/告警规则等敏感配置；
  识别成功后自动刷新（子线程 → root.after 主线程）。
- 任务3：多目标代码路径保护——单列布局下 f_last 展示、last_ocr_result 存储、
  目标切换 UI 均不受影响；config.targets 配置由主 Agent 另行处理。
- 版本号 v10.7——窗口标题/启动日志/文件头同步升级。
v10.6.2 新增（2026-08-24）：
- 任务1：识别成功后日志逐行显示每条广告计划数据（计划名+ROI/成本/收入/订单），
  不再只显示首行（A419 多计划区分不开）；原「识别成功」日志取到首行计划名时带上；
  汇总日志简化为「已自动入库 N 行」。
- 任务2（错误42落地）：UIA 结构化未找到广告计划表格（页面不对，如 GMV Max 概览页）时，
  自动监控与手动识别均追加 result 级日志「当前窗口可能不是广告计划列表页…」并弹本机
  toast（数据看板提醒），引导切换回广告计划列表页。
- 任务3：清理④.6识别规则板块 OCR 残留 UI——UIA 控件树模式下整个板块隐藏
  （OCR引擎/列偏移/表头关键词/跳过词等均为 OCR 时代配置，UIA 按表头 name 动态列映射不依赖），
  OCR 模式恢复显示；走既有 _apply_section_capabilities 条件渲染，切换识别方式即时生效。
v10.6 新增（2026-08-24）：
- 任务1：布局框架（Photoshop/Office 式自定义布局）——
  ① 日志冻结底部独立窗格：最外层 pw_root 垂直分栏，上部=原左右分栏整体，下部=日志
    横跨全宽、独立滚动、sash 高度可拖、视图菜单可折叠，位置永不动不参与归属配置；
  ② 配置板块左右归属可调：config.window_layout 新增 left_sections/right_sections
    有序列表（列表顺序即板块排列顺序），渲染按列表 pack(in_=目标容器) 重挂载；
    旧 config 无字段时回退默认（left=全部配置板块 / right=[sec_last]）；
  ③ 布局面板：视图菜单「板块布局」弹窗，每行 [归属左/右][▲上移][▼下移][显示/隐藏]，
    保存后即时应用并落盘 config.json；
  ④ 通用 FlowFrame 换行容器（place 布局自动换行，类似 CSS flex-wrap），
    改造③自动监控/④告警条件/④.5识别条件/④.6识别规则/⑥推送设置的按钮行；
  ⑤ 最小尺寸钳制：板块最小宽/高=内容请求尺寸（update_idletasks 后 winfo_reqwidth/height），
    sash 拖动释放后小于最小尺寸自动回弹钳制。
- 兼容保留：collapsed_sections/hidden_sections 折叠隐藏、sec_roi 能力显隐
  （requires_sections）、sash 比例恢复、多目标切换、mod 按钮行（v10.7 起挂 main_frame）。
- 版本号 v10.6.2——窗口标题/启动日志/文件头同步升级。
v10.5 新增（2026-08-24）：
- 任务1：完整行信息获取+逐行入库——UIA 结构化行完整字段（status/budget/target_roi/
  net_cost/schedule/avg_cost/roi_protection）透传到识别记录并逐行 add_record，
  不再只入首行；source 按识别模式记 uia/ocr。
- 任务2：SQLite 历史库扩展列——建表新增 plan_name/status/budget/target_roi/net_cost/
  schedule/avg_cost/roi_protection/shop 九列，PRAGMA table_info 检测缺列 ALTER 补齐
  （兼容旧库），_db_record_to_dict 与 INSERT 同步新列。
- 任务3：告警文本带计划名——所有告警文本统一加前缀「计划[xxx]」，推送一眼可辨计划。
- 任务4：推送全量快照——多行模式命中告警时，推送追加「===== 本轮全部计划 =====」
  段，10 条一并显示，进入 pending 的计划行尾标 ⚠。
- 任务5：UI 去 OCR 硬编码——识别器注册支持能力声明 requires_sections（OCR 依赖 roi
  板块、UIA 声明 []），核心按声明条件渲染：② ROI 识别区域板块显隐、测试按钮文本
  （测试 OCR/测试识别）、启动提示语随模式动态切换，识别器可插拔不再残留 OCR 板块。
- 任务6：版本号 v10.5——窗口标题/启动日志/文件头同步升级，新增 v10.5 更新说明。
v10.4 新增（2026-08-24）：
- 修复 v10.3 实机两 bug：主程序 recognize_once 二元解包 3 元组失败（先收 result 再按 len 分发）；
  mod 表头列映射重复（row_values 去重容差 round(x/8)→round(x/24)，header_cols 用 seen_fields 去重）。
- 排错记录新增错误39（3元组解包）、错误40（表头重复）。
v10.3 新增（2026-08-24）：
- 任务1：规则系统统一——预设规则与自定义规则统一为一条求值链，每行独立计算
  （alert_rules 表达式命中任意一条即告警；preset_rules 为空时按旧阈值告警）。
- 任务2：UIA 结构化读取——mods/uia_recog/mod.py 新增 _uia_structured_read，
  按表头字段映射读取计划整行（plan_name/roi/cost/revenue/orders/status/budget/
  target_roi/net_cost/schedule/avg_cost/roi_protection），config.uia_structured=true 启用。
v10.2 新增（2026-08-24）：
- 任务1：自定义告警规则系统——面板④新增「编辑自定义规则」，用户自写表达式（安全求值，
  白名单字段 {roi}{cost}{revenue}{orders}{plan_name}{shop}{aov}{cost_prev}{revenue_prev}
  {cost_x}{burn_redline}{threshold}，支持比较/逻辑运算）；规则分「告警规则」与「豁免规则」
  （命中豁免则跳过该行告警，实现 0 收入豁免/延迟告警）；单条规则失败不影响其他规则。
- 任务2：目标（店铺）独立监控——每目标独立 enabled/alert_enabled/alert_threshold/
  alert_cooldown_min/alert_rules/avg_order_value；新增目标默认不启用自动监控；
  目标条显示每目标独立启用开关；告警条件区保存到当前目标的独立配置。
- 任务3：OCR 拔除 + mod 管理 + 识别命名简化——部署不再带 ocr_recog（工作电脑只跑 UIA）；
  菜单「工具→Mod 管理」可启用/禁用 mod，禁用状态存 config.disabled_mods；
  识别方式下拉只显示已启用识别器名称（截图OCR/控件树UIA），不再用"模式一/模式二"编号。
- 任务4：周期总成本告警拔除——删除"累计消耗达上限→控制预算"规则（概览页周期总成本是
  整段累计数据，不作告警源）；config.cost_max 标注废弃不再使用。
- 任务5：推送余额估算——面板⑥显示今日已发送条数 + PushPlus 免费 200 条/日剩余估算
  （本地计数按日期存 config.push_count，跨天自动清零；仅估算，以平台实际为准）。
- 任务6：折叠顺序 bug 修复 + 按钮覆盖/自定义窗口布局——折叠/展开按创建顺序原位插入
  （考虑折叠占位行，修复"展开后跑最下面"）；面板可横/纵滚动防控件截断；
  窗口布局（字号/缩放）保存到 config.window_layout 启动恢复。
- 任务7：ai_debug 白名单同步新增 config 键。
v10.1 新增（2026-08-23）：
- 任务A：告警改为逐行判断——识别出的所有数据行（业务上限 10 行/页）全部参与告警判断，
  每行独立比较 ROI 与阈值；多条同时触发时合并推送（避免刷屏）；冷却按计划名独立检查；
  新增 config 键 max_rows_to_check（默认 10）控制参与判断的最大行数。
- 任务B：日志三级分级（结果/信息/排错）——视图菜单「日志等级」三选一（默认结果级）；
  面板只显示 >= 当前等级的行，所有等级全量落盘 monitor.log（按行带等级标签）。
- 任务C：本机 toast 通知——告警触发时弹 Windows 系统通知气泡，标题「数据看板提醒」，
  零依赖实现（PowerShell + WinForms NotifyIcon），对 PyInstaller 打包零负担。
- 任务D/E：部署文件夹只放交付物（exe+config.json+mods\），稳定版存档于项目根 archive。
v10 基础能力：
- 单主程序 + tkinter 可视化面板，所有参数在面板上调整并持久化保存（config.json）
- OCR 默认使用本地开源 RapidOCR（rapidocr_onnxruntime），中文精度 93%+；
  可选回退 Windows 内置 OCR（winocr，PowerShell 调用），无需 EasyOCR/Tesseract
- 截图使用 PIL ImageGrab，按窗口标题定位窗口并裁剪 ROI 区域，已处理 DPI 缩放
- 告警规则：ROI 低于阈值 / 成本超上限 / 空烧超红线 / CVR 低 / 连续订单未增长
- 推送：企业微信群机器人 + 钉钉群机器人，双通道独立开关
- 数据：config.json（配置）与 history.json（历史记录）分开存储
- 修复旧版已知缺陷：START.bat 恒真判断、推送脚本写死旧路径、cvr_low 不生效、
  订单数恒 0、ROI 解析错位（优先按标签行解析，失败才回退顺序解析）
依赖安装（只需轻量依赖）：
    pip install pillow requests
运行方式：
    双击 START.bat 或执行 python tiktok_monitor_v10.pyw
