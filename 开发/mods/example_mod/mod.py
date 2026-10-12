# -*- coding: utf-8 -*-
"""
示例 Mod（example_mod）
=================================================
验证 TK 监控器 v10 Mod 体系加载链路的最小示例：
- 加载时通过 api.log 输出「示例 mod 加载成功」
- 注册一个「示例 Mod 板块」面板，面板内显示一行说明文字
"""

import tkinter as tk


def setup(api):
    # 1. 加载时输出日志（验证加载链路）
    api.log("示例 mod 加载成功")

    # 2. 注册一个示例板块（验证 api.register_panel）
    def build(container):
        tk.Label(
            container,
            text="Mod 体系工作正常：本面板由 example_mod 注册，修改本 mod 无需重新打包主程序。",
            fg="#1565C0",
            anchor="w",
            justify="left",
        ).pack(fill="x", padx=5, pady=8)

    api.register_panel("示例 Mod 板块", build)

    # 3. 演示 mod 命名空间配置读写（可选，写入一条安装记录）
    installed = api.get_config("installed_version")
    if installed != "1.0.0":
        api.set_config("installed_version", "1.0.0")
        api.log("已写入 mod 命名空间配置 installed_version=1.0.0")
