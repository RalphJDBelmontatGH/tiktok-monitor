# -*- coding: utf-8 -*-
"""AI 调试 mod：通过本地 HTTP 接口调试/控制 TK 监控器，不模拟鼠标键盘。

仅监听 127.0.0.1，端口从 18765 起，被占用则 +1，最多尝试 5 个端口。
接口：
  GET  /status              当前状态（最近识别结果/推送通道/运行状态）
  GET  /config              完整核心配置 JSON
  POST /config              修改配置 {"key": "...", "value": ...}（白名单校验）
  POST /action/recognize    触发一次手动识别
  POST /action/close_ad     关闭指定广告计划（占位实现）
  GET  /log?lines=N         最近 N 条主日志
所有接口错误 try/except 返回 JSON 错误，绝不影响核心。
"""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT_START = 18765
PORT_ATTEMPTS = 5

# 允许通过 POST /config 修改的核心配置 key 白名单
ALLOWED_KEYS = {
    "roi_low", "roi_good", "roi_max", "cost_max", "cvr_low", "cost_x_aov",
    "burn_redline", "roi_alert_cost_min", "alert_enabled", "alert_threshold",
    "alert_metric", "alert_operator", "alert_cooldown_min", "alert_template", "strict_enabled",
    "no_header_policy", "header_keywords", "header_col_name",
    "header_col_roi", "header_col_cost", "header_col_rev", "skip_words",
    "plan_name_check", "plan_name_pattern", "read_row_index", "offset_roi",
    "offset_cost", "offset_rev", "offset_name", "window_keyword",
    "interval_minutes", "pushplus_template", "pushplus_token",
    "pushplus_enabled", "wecom_key", "wecom_enabled", "dingtalk_url",
    "dingtalk_enabled", "recog_mode", "uia_scroll_collect", "uia_max_scroll", "uia_wait_sec",
    "uia_max_nodes", "uia_timeout_sec", "uia_max_depth", "cdp_port",
    # v10.2 新增：自定义告警规则 / 客单价 / mod 管理 / 布局 / 推送计数 / 目标独立监控 / 折叠隐藏状态
    "alert_rules", "avg_order_value", "alert_exclude_patterns",
    "disabled_mods", "window_layout", "push_count", "push_sent_date", "push_sent_count",
    "targets", "max_rows_to_check",
    "collapsed_sections", "hidden_sections",
}

_server = None
_port = None
_core = None
_last_ocr = None


def setup(api):
    """mod 加载入口：启动本地 HTTP 服务（daemon 线程）"""
    global _server, _port, _core
    _core = api.get_core()
    try:
        _port = _find_port()
        handler = _make_handler()
        _server = ThreadingHTTPServer(("127.0.0.1", _port), handler)
        threading.Thread(target=_server.serve_forever, daemon=True).start()
        _write_port(_port)
        api.log(f"AI 调试服务已启动 http://127.0.0.1:{_port}")
    except Exception as e:
        api.log(f"AI 调试服务启动失败: {e}")


def _find_port():
    import socket
    for p in range(PORT_START, PORT_START + PORT_ATTEMPTS):
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", p))
            s.close()
            return p
        except OSError:
            s.close()
            continue
    raise RuntimeError(f"端口 {PORT_START}-{PORT_START + PORT_ATTEMPTS - 1} 均被占用")


def _write_port(port):
    import pathlib
    try:
        (pathlib.Path(__file__).resolve().parent / "port.txt").write_text(str(port), encoding="utf-8")
    except Exception:
        pass


def _save_config():
    """保存核心配置到 config.json：优先复用核心模块 save_config，兜底直接写文件"""
    global _core
    try:
        import sys
        m = sys.modules.get(type(_core).__module__)
        if m is not None and hasattr(m, "save_config"):
            m.save_config(_core.cfg)
            return True
    except Exception:
        pass
    try:
        import pathlib
        p = pathlib.Path(__file__).resolve().parent.parent.parent / "config.json"
        p.write_text(json.dumps(_core.cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except Exception:
        return False


def _in_main(fn, *args, **kwargs):
    """把操作调度到 tkinter 主线程执行，返回结果或抛异常（最多等 5 秒）"""
    result = {}
    ev = threading.Event()

    def _do():
        try:
            result["value"] = fn(*args, **kwargs)
        except Exception as e:
            result["error"] = str(e)
        finally:
            ev.set()

    try:
        _core.root.after(0, _do)
        ev.wait(5)
    except Exception as e:
        raise RuntimeError(str(e))
    if "error" in result:
        raise RuntimeError(result["error"])
    return result.get("value")


def _push_channel_status():
    c = _core.cfg
    return {
        "pushplus": {"enabled": bool(c.get("pushplus_enabled")), "configured": bool(c.get("pushplus_token"))},
        "wecom": {"enabled": bool(c.get("wecom_enabled")), "configured": bool(c.get("wecom_key"))},
        "dingtalk": {"enabled": bool(c.get("dingtalk_enabled")), "configured": bool(c.get("dingtalk_url"))},
    }


def _make_handler():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send_json(self, obj, code=200):
            body = json.dumps(obj, ensure_ascii=False, indent=4).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_body(self):
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                return {}
            try:
                return json.loads(self.rfile.read(length).decode("utf-8"))
            except Exception:
                return {}

        def do_GET(self):
            try:
                if self.path == "/status" or self.path.startswith("/status?"):
                    self._handle_status()
                elif self.path == "/config" or self.path.startswith("/config?"):
                    self._send_json(_core.cfg)
                elif self.path.startswith("/log"):
                    self._handle_log()
                else:
                    self._send_json({"error": "not found", "path": self.path}, 404)
            except Exception as e:
                try:
                    self._send_json({"error": str(e)}, 500)
                except Exception:
                    pass

        def do_POST(self):
            try:
                body = self._read_body()
                if self.path == "/config":
                    self._handle_set_config(body)
                elif self.path == "/action/recognize":
                    self._handle_recognize(body)
                elif self.path == "/action/close_ad":
                    self._handle_close_ad(body)
                else:
                    self._send_json({"error": "not found", "path": self.path}, 404)
            except Exception as e:
                try:
                    self._send_json({"error": str(e)}, 500)
                except Exception:
                    pass

        def _handle_status(self):
            # 优先读核心结构化最近识别结果（界面按钮/自动监控触发也会更新），兜底 mod 自身记录
            last = getattr(_core, "last_ocr_result", None) or _last_ocr
            self._send_json({
                "ok": True,
                "last_ocr": last,
                "push_channels": _push_channel_status(),
                "running": bool(_core.monitor_running),
                "busy": bool(_core._manual_busy),
            })

        def _handle_log(self):
            try:
                lines = int(self.path.split("lines=", 1)[1].split("&", 1)[0])
            except Exception:
                lines = 50
            lines = max(1, min(lines, 500))
            ring = getattr(_core, "_log_ring", None)
            if ring is not None:
                all_lines = [ln for ln in ring if ln.strip()]
                tail = all_lines[-lines:]
                self._send_json({"ok": True, "total": len(all_lines), "lines": tail})
                return
            try:
                text = _in_main(lambda: _core.txt_log.get("1.0", "end-1c")) or ""
            except Exception:
                text = ""
            all_lines = [ln for ln in text.splitlines() if ln.strip()]
            tail = all_lines[-lines:]
            self._send_json({"ok": True, "total": len(all_lines), "lines": tail})

        def _handle_set_config(self, body):
            key = body.get("key")
            value = body.get("value")
            if not key or key not in ALLOWED_KEYS:
                self._send_json({"error": "key 不在白名单: %r" % key, "allowed": sorted(ALLOWED_KEYS)}, 400)
                return
            old = _core.cfg.get(key)
            _core.cfg[key] = value
            if not _save_config():
                _core.cfg[key] = old
                self._send_json({"error": "保存 config.json 失败，已回滚"}, 500)
                return
            # 尽量即时生效：推送相关同步状态文字；阈值/识别类配置由核心每次识别前重读，无需额外动作
            try:
                if key in ("pushplus_enabled", "pushplus_token", "wecom_enabled",
                           "wecom_key", "dingtalk_enabled", "dingtalk_url"):
                    _in_main(_core.update_push_status)
            except Exception:
                pass
            self._send_json({"ok": True, "key": key, "value": value, "old": old})

        def _handle_recognize(self, body):
            global _last_ocr
            if _core._manual_busy:
                self._send_json({"error": "上一轮识别还在进行中，请稍候"}, 409)
                return
            _core._manual_busy = True
            try:
                roi, cost, revenue, text, detail = _core.recognize_once()
                try:
                    _in_main(lambda: _core._manual_ocr_done(test=False, result=(roi, cost, revenue, text, detail)))
                except Exception:
                    pass
                snippet = ""
                if detail:
                    try:
                        snippet = _core._selected_snippet(detail)
                    except Exception:
                        snippet = ""
                _last_ocr = {
                    "roi": roi,
                    "cost": cost,
                    "revenue": revenue,
                    "plan_name": snippet,
                    "selected_index": (detail or {}).get("selected_index"),
                    "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                }
                last = getattr(_core, "last_ocr_result", None) or _last_ocr
                self._send_json({"ok": True, **last})
            except Exception as e:
                self._send_json({"error": str(e)}, 500)
            finally:
                _core._manual_busy = False

        def _handle_close_ad(self, body):
            plan_name = body.get("plan_name") or ""
            row_no = body.get("row_no")
            msg = ("AI 调试 mod 触发 close_ad 占位（plan_name=%r, row_no=%r）："
                   "未实现，待 MVP 后接入控件级关闭" % (plan_name, row_no))
            try:
                _core.log(msg)
            except Exception:
                pass
            self._send_json({"ok": True,
                             "message": "未实现，待 MVP 后接入控件级关闭",
                             "plan_name": plan_name, "row_no": row_no})

    return Handler
