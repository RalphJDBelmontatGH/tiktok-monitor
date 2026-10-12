# -*- coding: utf-8 -*-
"""历史记录持久层（SQLite 主存储 + history.json 兼容备份）

B 组拆分（2026-09-07）：从 tiktok_monitor_v10.pyw 抽取，行为语义不变。
对外 API：_db_connect/_db_record_to_dict/load_history_from_db/add_record_to_db/
load_history/save_history/add_record；常量 DB_FILE/HISTORY_FILE 供主程序复用。
"""
import json
import sqlite3
import sys
from pathlib import Path

# BASE_DIR 逻辑与主程序保持一致（打包 exe 时配置/历史/日志存 exe 所在目录）
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).parent
else:
    BASE_DIR = Path(__file__).parent
HISTORY_FILE = BASE_DIR / "history.json"
DB_FILE = BASE_DIR / "history.db"   # SQLite 历史库主存储

DB_TABLE = "history"
DB_COLUMNS = [
    "time", "roi", "cost", "orders", "revenue", "uv", "source",
    "cumulative_cost", "cumulative_orders", "cumulative_revenue",
    "avg_order_value", "conversion_rate",
    # v10.5：逐行入库扩展列——完整行信息（UIA 结构化字段）+ 店铺名
    "plan_name", "status", "budget", "target_roi", "net_cost",
    "schedule", "avg_cost", "roi_protection", "shop",
]

# v10.5：扩展列的定义（类型 + 建表 DDL 片段），供建表与旧库 ALTER 补齐共用
_DB_EXTRA_COLUMNS = [
    ("plan_name", "TEXT"),
    ("status", "TEXT"),
    ("budget", "REAL"),
    ("target_roi", "REAL"),
    ("net_cost", "REAL"),
    ("schedule", "TEXT"),
    ("avg_cost", "REAL"),
    ("roi_protection", "TEXT"),
    ("shop", "TEXT"),
]
_DB_EXTRA_COLUMN_NAMES = [c[0] for c in _DB_EXTRA_COLUMNS]


def _db_connect():
    """打开 SQLite 连接并建表（不存在时）。线程内各自连接，用完关闭。"""
    conn = sqlite3.connect(str(DB_FILE), timeout=10)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS %s ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " time TEXT, roi REAL, cost REAL, orders INTEGER, revenue REAL,"
        " uv REAL, source TEXT,"
        " cumulative_cost REAL, cumulative_orders INTEGER, cumulative_revenue REAL,"
        " avg_order_value REAL, conversion_rate REAL,"
        " plan_name TEXT, status TEXT, budget REAL, target_roi REAL,"
        " net_cost REAL, schedule TEXT, avg_cost REAL, roi_protection TEXT, shop TEXT)"
        % DB_TABLE
    )
    # v10.5：兼容旧库——老库表结构缺新列，PRAGMA table_info 检查后逐列 ALTER 补齐，
    # 保证升级后旧库可继续写新字段而不重建/丢数据
    try:
        existing = {r[1] for r in conn.execute("PRAGMA table_info(%s)" % DB_TABLE).fetchall()}
        for col_name, col_type in _DB_EXTRA_COLUMNS:
            if col_name not in existing:
                conn.execute("ALTER TABLE %s ADD COLUMN %s %s" % (DB_TABLE, col_name, col_type))
        conn.commit()
    except Exception:
        pass
    return conn


def _db_record_to_dict(row):
    """SQLite 行 -> 与 history.json 相同的记录字典（字段名一致，兼容旧读取方）"""
    rec = {
        "time": row[1],
        "roi": row[2],
        "cost": row[3],
        "orders": row[4],
        "revenue": row[5],
        "uv": row[6],
        "source": row[7],
        "cumulative_cost": row[8],
        "cumulative_orders": row[9],
        "cumulative_revenue": row[10],
        "avg_order_value": row[11],
        "conversion_rate": row[12],
    }
    # v10.5：扩展列（旧库行可能缺列，按长度容错取 None）
    if len(row) > 13:
        rec["plan_name"] = row[13]
        rec["status"] = row[14]
        rec["budget"] = row[15]
        rec["target_roi"] = row[16]
        rec["net_cost"] = row[17]
        rec["schedule"] = row[18]
        rec["avg_cost"] = row[19]
        rec["roi_protection"] = row[20]
        rec["shop"] = row[21]
    else:
        for k in _DB_EXTRA_COLUMN_NAMES:
            rec[k] = None
    return rec


def load_history_from_db():
    """读取 SQLite 全部历史记录（按写入顺序 id 升序）。失败返回 []。"""
    try:
        conn = _db_connect()
        try:
            rows = conn.execute("SELECT * FROM %s ORDER BY id ASC" % DB_TABLE).fetchall()
            return [_db_record_to_dict(r) for r in rows]
        finally:
            conn.close()
    except Exception:
        return []


def add_record_to_db(rec):
    """写入一条记录到 SQLite。字段缺失时补默认值，避免缺键导致插入失败。"""
    try:
        conn = _db_connect()
        try:
            conn.execute(
                "INSERT INTO %s (time, roi, cost, orders, revenue, uv, source,"
                " cumulative_cost, cumulative_orders, cumulative_revenue,"
                " avg_order_value, conversion_rate,"
                " plan_name, status, budget, target_roi, net_cost, schedule,"
                " avg_cost, roi_protection, shop)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)" % DB_TABLE,
                (
                    rec.get("time", ""),
                    rec.get("roi", 0),
                    rec.get("cost", 0),
                    rec.get("orders", 0),
                    rec.get("revenue", 0),
                    rec.get("uv"),
                    rec.get("source", "ocr"),
                    rec.get("cumulative_cost", 0),
                    rec.get("cumulative_orders", 0),
                    rec.get("cumulative_revenue", 0),
                    rec.get("avg_order_value", 0),
                    rec.get("conversion_rate"),
                    rec.get("plan_name"),
                    rec.get("status"),
                    rec.get("budget"),
                    rec.get("target_roi"),
                    rec.get("net_cost"),
                    rec.get("schedule"),
                    rec.get("avg_cost"),
                    rec.get("roi_protection"),
                    rec.get("shop"),
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


def load_history():
    """读取历史记录：优先 SQLite（主存储），并合并 history.json 中 SQLite 没有的旧记录，
    保证升级初期旧 json 数据不丢。SQLite 不可用且 json 也没有时返回空。"""
    db_records = load_history_from_db()
    json_records = []
    if HISTORY_FILE.exists():
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and isinstance(data.get("records"), list):
                    json_records = data["records"]
        except Exception:
            pass
    if not db_records:
        # SQLite 无数据：回退 json（保留旧兼容）
        return {"records": json_records}
    if not json_records:
        return {"records": db_records}
    # SQLite 优先，json 补充 SQLite 中不存在的旧记录（按 (time, source) 去重）
    seen = set()
    merged = []
    for r in db_records:
        key = (r.get("time"), r.get("source"))
        if key not in seen:
            seen.add(key)
            merged.append(r)
    for r in json_records:
        key = (r.get("time"), r.get("source"))
        if key not in seen:
            seen.add(key)
            merged.append(r)
    merged.sort(key=lambda r: r.get("time", ""))
    return {"records": merged}


def save_history(data):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def add_record(rec):
    # 主存储：SQLite
    add_record_to_db(rec)
    # 兼容备份：直接读 history.json 追加（不经 load_history，避免 SQLite 合并后同一记录写两遍）
    json_records = []
    if HISTORY_FILE.exists():
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and isinstance(data.get("records"), list):
                    json_records = data["records"]
        except Exception:
            pass
    json_records.append(rec)
    # 保留最近 5000 条，避免文件无限膨胀
    if len(json_records) > 5000:
        json_records = json_records[-5000:]
    save_history({"records": json_records})
