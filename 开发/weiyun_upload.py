# -*- coding: utf-8 -*-
"""
微云日志自动上传模块（TK监控器 内置版）。

功能：把本地日志文件上传到微云指定目录，供跨设备拉取 / 目录清理前备份。

对外接口：
    upload_log_to_weiyun(file_path, token, pdir_key="", mcp_url=..., timeout=120)
        成功返回 {"ok": True, "file_id": ..., "filename": ...}
        失败返回 {"ok": False, "error": "..."}（不抛异常，便于主程序静默降级）

依赖：requests（如未安装：pip install requests）
说明：本模块为纯 Python SHA1 实现（支持提取内部寄存器状态），
      这是微云上传协议分块校验的核心需求，标准库 hashlib 无法完成。
"""

import base64
import hashlib
import json
import os
import struct
import time

try:
    import requests
except ImportError:
    requests = None

BLOCK_SIZE = 524288  # 512KB
DEFAULT_MCP_URL = "https://www.weiyun.com/api/v3/mcpserver"


# ========== 纯 Python SHA1（支持获取内部状态） ==========

def _left_rotate(n, b):
    return ((n << b) | (n >> (32 - b))) & 0xFFFFFFFF


class SHA1:
    """纯 Python SHA1，支持读取未 finalize 的内部寄存器状态（小端序）。"""

    def __init__(self):
        self.h0 = 0x67452301
        self.h1 = 0xEFCDAB89
        self.h2 = 0x98BADCFE
        self.h3 = 0x10325476
        self.h4 = 0xC3D2E1F0
        self._message_byte_length = 0
        self._unprocessed = b""

    def update(self, data):
        self._unprocessed += data
        self._message_byte_length += len(data)
        while len(self._unprocessed) >= 64:
            self._process_chunk(self._unprocessed[:64])
            self._unprocessed = self._unprocessed[64:]

    def _process_chunk(self, chunk):
        assert len(chunk) == 64
        w = [0] * 80
        for i in range(16):
            w[i] = struct.unpack(">I", chunk[i * 4:(i + 1) * 4])[0]
        for i in range(16, 80):
            w[i] = _left_rotate(w[i - 3] ^ w[i - 8] ^ w[i - 14] ^ w[i - 16], 1)
        a, b, c, d, e = self.h0, self.h1, self.h2, self.h3, self.h4
        for i in range(80):
            if 0 <= i <= 19:
                f = (b & c) | ((~b) & d)
                k = 0x5A827999
            elif 20 <= i <= 39:
                f = b ^ c ^ d
                k = 0x6ED9EBA1
            elif 40 <= i <= 59:
                f = (b & c) | (b & d) | (c & d)
                k = 0x8F1BBCDC
            else:
                f = b ^ c ^ d
                k = 0xCA62C1D6
            temp = (_left_rotate(a, 5) + f + e + k + w[i]) & 0xFFFFFFFF
            e = d
            d = c
            c = _left_rotate(b, 30)
            b = a
            a = temp
        self.h0 = (self.h0 + a) & 0xFFFFFFFF
        self.h1 = (self.h1 + b) & 0xFFFFFFFF
        self.h2 = (self.h2 + c) & 0xFFFFFFFF
        self.h3 = (self.h3 + d) & 0xFFFFFFFF
        self.h4 = (self.h4 + e) & 0xFFFFFFFF

    def get_state(self):
        assert len(self._unprocessed) == 0, \
            f"get_state 要求 unprocessed 为空，当前有 {len(self._unprocessed)} 字节"
        result = b""
        for h in (self.h0, self.h1, self.h2, self.h3, self.h4):
            result += struct.pack("<I", h)
        return result.hex()

    def hexdigest(self):
        message_byte_length = self._message_byte_length
        unprocessed = self._unprocessed
        h0, h1, h2, h3, h4 = self.h0, self.h1, self.h2, self.h3, self.h4
        unprocessed += b"\x80"
        unprocessed += b"\x00" * ((56 - len(unprocessed) % 64) % 64)
        unprocessed += struct.pack(">Q", message_byte_length * 8)
        tmp = SHA1.__new__(SHA1)
        tmp.h0, tmp.h1, tmp.h2, tmp.h3, tmp.h4 = h0, h1, h2, h3, h4
        tmp._unprocessed = b""
        tmp._message_byte_length = message_byte_length
        while len(unprocessed) >= 64:
            tmp._process_chunk(unprocessed[:64])
            unprocessed = unprocessed[64:]
        return "{:08x}{:08x}{:08x}{:08x}{:08x}".format(
            tmp.h0, tmp.h1, tmp.h2, tmp.h3, tmp.h4)


# ========== 文件上传参数计算 ==========

def calc_upload_params(file_path):
    file_size = os.path.getsize(file_path)
    filename = os.path.basename(file_path)
    last_block_size = file_size % BLOCK_SIZE
    if last_block_size == 0:
        last_block_size = BLOCK_SIZE
    check_block_size = last_block_size % 128
    if check_block_size == 0:
        check_block_size = 128
    before_block_size = file_size - last_block_size
    block_sha_list = []
    sha1 = SHA1()
    md5 = hashlib.md5()
    with open(file_path, "rb") as f:
        for offset in range(0, before_block_size, BLOCK_SIZE):
            data = f.read(BLOCK_SIZE)
            sha1.update(data)
            md5.update(data)
            block_sha_list.append(sha1.get_state())
        between_data = f.read(last_block_size - check_block_size)
        sha1.update(between_data)
        md5.update(between_data)
        check_sha = sha1.get_state()
        check_data_bytes = f.read(check_block_size)
        sha1.update(check_data_bytes)
        md5.update(check_data_bytes)
        file_sha = sha1.hexdigest()
        check_data = base64.b64encode(check_data_bytes).decode("utf-8")
        block_sha_list.append(file_sha)
    return {
        "filename": filename,
        "file_size": file_size,
        "file_sha": file_sha,
        "file_md5": md5.hexdigest(),
        "block_sha_list": block_sha_list,
        "check_sha": check_sha,
        "check_data": check_data,
    }


# ========== MCP 调用 ==========

_request_id = 0


def _mcp_call(mcp_url, headers, tool_name, arguments, timeout=120):
    global _request_id
    _request_id += 1
    payload = {
        "jsonrpc": "2.0",
        "id": _request_id,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
    }
    resp = requests.post(mcp_url, headers=headers, json=payload, timeout=timeout)
    resp.raise_for_status()
    result = resp.json()
    content = result.get("result", {}).get("content", [])
    for item in content:
        if item.get("type") == "text":
            return json.loads(item["text"])
    return result


# ========== 主上传流程 ==========

def _upload_file_impl(file_path, token, pdir_key, mcp_url, timeout, max_rounds=20):
    """实际上传，失败抛异常，成功返回 dict。"""
    if requests is None:
        raise RuntimeError("缺少 requests 库，请执行 pip install requests")
    file_size = os.path.getsize(file_path)
    filename = os.path.basename(file_path)
    params = calc_upload_params(file_path)
    headers = {
        "Content-Type": "application/json",
        "WyHeader": f"mcp_token={token};appid=80011",
    }
    pre_upload_args = {
        "filename": params["filename"],
        "file_size": params["file_size"],
        "file_sha": params["file_sha"],
        "file_md5": params["file_md5"],
        "block_sha_list": params["block_sha_list"],
        "check_sha": params["check_sha"],
        "check_data": params["check_data"],
    }
    if pdir_key:
        pre_upload_args["pdir_key"] = pdir_key
    with open(file_path, "rb") as f:
        file_data = f.read()
    round_num = 0
    while round_num < max_rounds:
        round_num += 1
        pre_rsp = _mcp_call(mcp_url, headers, "weiyun.upload", pre_upload_args, timeout=timeout)
        if pre_rsp.get("error"):
            raise RuntimeError(f"预上传失败: {pre_rsp['error']}")
        if pre_rsp.get("file_exist", False):
            return {"file_id": pre_rsp.get("file_id", ""), "filename": pre_rsp.get("filename", filename)}
        ch_list = pre_rsp.get("channel_list", [])
        uk = pre_rsp.get("upload_key", "")
        ex = pre_rsp.get("ex", "")
        ch = None
        for c in ch_list:
            if int(c.get("len", 0)) > 0:
                ch = c
                break
        if ch is None:
            state = int(pre_rsp.get("upload_state", 0))
            if state == 2:
                return {"file_id": pre_rsp.get("file_id", ""), "filename": pre_rsp.get("filename", filename)}
            raise RuntimeError(f"无可上传通道，upload_state={state}")
        offset = int(ch["offset"])
        length = int(ch["len"])
        channel_id = int(ch["id"])
        actual_len = min(length, len(file_data) - offset)
        chunk = file_data[offset:offset + actual_len]
        chunk_b64 = base64.b64encode(chunk).decode("utf-8")
        cl = [{"id": int(c["id"]), "offset": int(c["offset"]), "len": int(c["len"])}
              for c in ch_list]
        up_rsp = _mcp_call(mcp_url, headers, "weiyun.upload", {
            "filename": filename,
            "file_size": file_size,
            "file_sha": params["file_sha"],
            "block_sha_list": [],
            "check_sha": params["check_sha"],
            "upload_key": uk,
            "channel_list": cl,
            "channel_id": channel_id,
            "ex": ex,
            "file_data": chunk_b64,
        }, timeout=timeout)
        if up_rsp.get("error"):
            raise RuntimeError(f"分片上传失败: {up_rsp['error']}")
        state = int(up_rsp.get("upload_state", 0))
        if state == 2:
            return {"file_id": up_rsp.get("file_id", ""), "filename": up_rsp.get("filename", filename)}
    raise RuntimeError(f"超过最大上传轮数 ({max_rounds})")


def upload_log_to_weiyun(file_path, token, pdir_key="", mcp_url=DEFAULT_MCP_URL, timeout=120):
    """
    上传单个日志文件到微云。

    成功返回 {"ok": True, "file_id": str, "filename": str}
    失败返回 {"ok": False, "error": str}（不抛异常）
    """
    try:
        if not token:
            return {"ok": False, "error": "未配置微云 token"}
        if not os.path.isfile(file_path):
            return {"ok": False, "error": f"文件不存在: {file_path}"}
        result = _upload_file_impl(file_path, token, pdir_key, mcp_url, timeout)
        return {"ok": True, "file_id": result.get("file_id", ""), "filename": result.get("filename", os.path.basename(file_path))}
    except Exception as e:
        return {"ok": False, "error": str(e)}


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("用法: python weiyun_upload.py <文件路径> <token> [pdir_key]")
        sys.exit(1)
    path = sys.argv[1]
    tok = sys.argv[2]
    pkey = sys.argv[3] if len(sys.argv) > 3 else ""
    t0 = time.time()
    r = upload_log_to_weiyun(path, tok, pkey)
    print(json.dumps(r, ensure_ascii=False, indent=2))
    print(f"耗时 {time.time() - t0:.1f}s")
    sys.exit(0 if r.get("ok") else 1)
