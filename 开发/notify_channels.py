# -*- coding: utf-8 -*-
"""通知/推送渠道层（Windows toast + 企业微信 + 钉钉 + PushPlus）

B 组拆分（2026-09-07）：从 tiktok_monitor_v10.pyw 抽取，行为语义不变。
对外 API：notify_toast/push_wecom/push_dingtalk/_safe_format/_metric_label/
_op_label/_plan_label/TEMPLATE_VARS/_render_template/push_pushplus/send_alerts。
"""
import re
import subprocess

import requests

def notify_toast(title, text):
    """任务C：本机 Windows toast 通知（零依赖：PowerShell + WinForms NotifyIcon）。

    低调原则：调用方传入标题「数据看板提醒」，正文含计划名/ROI/成本/收入。
    实现：独立 PowerShell 子进程创建 NotifyIcon 弹气泡，显示后自动退出。
    不引入任何第三方库（对 PyInstaller 打包零负担、无打包兼容风险）；
    任何异常静默降级，绝不阻塞主流程/告警推送。
    """
    try:
        text = (text or "").replace("'", "''")[:200]
        title = (title or "").replace("'", "''")[:40]
        ps = (
            "Add-Type -AssemblyName System.Windows.Forms;"
            "$n=New-Object System.Windows.Forms.NotifyIcon;"
            "$n.Icon=[System.Drawing.SystemIcons]::Information;"
            "$n.BalloonTipIcon='Warning';"
            f"$n.BalloonTipTitle='{title}';"
            f"$n.BalloonTipText='{text}';"
            "$n.Visible=$true;"
            "$n.ShowBalloonTip(8000);"
            "Start-Sleep -Seconds 9;"
            "$n.Dispose()"
        )
        subprocess.Popen(
            ["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
             "-STA", "-Command", ps],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        pass


def push_wecom(cfg, title, content):
    """企业微信群机器人推送，返回 (ok, msg)"""
    key = (cfg.get("wecom_key") or "").strip()
    if not key:
        return False, "未填写企业微信 Key"
    url = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=" + key
    payload = {
        "msgtype": "markdown",
        "markdown": {"content": f"**{title}**\n{content}"},
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        data = resp.json()
        if data.get("errcode") == 0:
            return True, "企业微信已送达"
        return False, f"企业微信失败: {data.get('errmsg', data)}"
    except Exception as e:
        return False, f"企业微信异常: {e}"


def push_dingtalk(cfg, title, content):
    """钉钉群机器人推送，返回 (ok, msg)"""
    url = (cfg.get("dingtalk_url") or "").strip()
    if not url:
        return False, "未填写钉钉 Webhook 地址"
    payload = {
        "msgtype": "text",
        "text": {"content": f"【TikTok监控】{title}\n{content}"},
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        data = resp.json()
        if data.get("errcode") == 0:
            return True, "钉钉已送达"
        return False, f"钉钉失败: {data.get('errmsg', data)}"
    except Exception as e:
        return False, f"钉钉异常: {e}"


_TMPL_VAR_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)(?::[^}]*)?\}")


def _safe_format(tmpl, **kwargs):
    """模板渲染（v10.2 推送模板开放）：已知变量替换（支持 {var:格式}，如 {roi:.2f}），
    未知变量保留原文不报错；值格式化失败时回退原始字符串。"""
    def _rep(m):
        name = m.group(1)
        if name not in kwargs:
            return m.group(0)  # 未知变量保留原文，不报错
        spec = m.group(0)[len("{" + name):-1]  # 形如 ":.2f" 或 ""
        try:
            if spec:
                return format(kwargs[name], spec.lstrip(":"))
            return str(kwargs[name])
        except Exception:
            return str(kwargs[name])

    try:
        return _TMPL_VAR_RE.sub(_rep, tmpl)
    except Exception:
        return tmpl


# v10.7.1：指标/比较/计划名中文映射单一 helper（消除重复内联映射）
def _metric_label(metric):
    """指标中文名：roi/cost/revenue → ROI/累计消耗/累计收入，未知原样返回。"""
    return {"roi": "ROI", "cost": "累计消耗", "revenue": "累计收入"}.get(metric, metric)


def _op_label(op):
    """比较方式中文名：lt/gt → 低于/高于，未知原样返回。"""
    return "低于" if op == "lt" else "高于" if op == "gt" else op


def _plan_label(rec):
    """从记录提取计划名（兼容 plan_name/name，缺省给占位）。"""
    return str(rec.get("plan_name") or rec.get("name") or "未命名计划").strip()


# v10.7.1：推送模板变量单一常量（模板编辑器/规则说明/渲染提示共用）
TEMPLATE_VARS = [
    ("shop", "店铺名（当前监控目标）"),
    ("plan_name", "广告计划名"),
    ("time", "识别时间"),
    ("roi", "ROI（可加格式，如 {roi:.2f}）"),
    ("cost", "累计消耗（美元，如 {cost:.2f}）"),
    ("revenue", "累计收入（美元，如 {revenue:.2f}）"),
    ("orders", "累计订单数"),
    ("threshold", "告警阈值"),
    ("metric", "告警指标（roi/cost/revenue）"),
    ("operator", "比较符（lt/gt）"),
    ("metric_label", "指标中文名（ROI/累计消耗/累计收入）"),
    ("op_label", "比较中文名（低于/高于）"),
    ("message", "规则建议/告警明细（多行）"),
    ("alerts", "告警明细（与 message 相同，兼容旧模板）"),
    ("aov", "客单价"),
    ("cost_prev", "上一轮累计消耗（对比用）"),
    ("revenue_prev", "上一轮累计收入（对比用）"),
    ("burn_redline", "空烧红线（小时）"),
    ("cost_x", "消耗超客单倍数"),
]


def _render_template(cfg, key, default_content, **kwargs):
    """按模板键渲染推送内容（v10.7.1 合并：pushplus_template / alert_template 统一实现）。

    模板缺失/为空时回退 default_content；渲染永不因变量缺失/未知报错
    （未知变量保留原文）。kwargs 可用变量：
    time / shop / plan_name / roi / cost / revenue / orders / alerts / message /
    threshold / metric / operator / metric_label / op_label / aov / cvr /
    cost_prev / revenue_prev / burn_redline / cost_x。
    """
    tmpl = (cfg.get(key) or "").strip()
    if not tmpl:
        return default_content
    return _safe_format(tmpl, **kwargs)


def push_pushplus(cfg, title, content):
    """PushPlus 微信推送（pushplus.plus），返回 (ok, msg)"""
    token = (cfg.get("pushplus_token") or "").strip()
    if not token:
        return False, "未填写 PushPlus Token"
    url = "https://www.pushplus.plus/send"
    payload = {
        "token": token,
        "title": f"【TikTok监控】{title}",
        "content": content,
        "template": "txt",
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        data = resp.json()
        if data.get("code") == 200:
            return True, "PushPlus 已送达"
        return False, f"PushPlus 失败: {data.get('msg', data)}"
    except Exception as e:
        return False, f"PushPlus 异常: {e}"


def send_alerts(cfg, title, content):
    """按开关推送到已启用的通道，返回推送结果列表。

    逻辑闭环：任何通道只在 enabled=True 时发送；所有通道都未启用时
    不发送任何消息，仅返回一条"告警仅记录不推送"的提示结果。
    """
    results = []
    if cfg.get("wecom_enabled", False):
        results.append(("企业微信",) + push_wecom(cfg, title, content))
    if cfg.get("dingtalk_enabled", False):
        results.append(("钉钉",) + push_dingtalk(cfg, title, content))
    if cfg.get("pushplus_enabled", False):
        results.append(("PushPlus",) + push_pushplus(cfg, title, content))
    if not results:
        results.append(("推送", False, "所有推送通道未启用，告警仅记录不推送"))
    return results
