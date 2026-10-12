# TikTok 广告监控器（TK Monitor）

面向 TikTok 广告投放页面的桌面监控工具：通过 UIA 控件树结构化读取广告面板数据，周期性自动识别广告列表并落盘记录，支持 ROI / 消耗 / 收入 / 订单多维监控与命中告警。

- 纯控件级自动化：不抢焦点、不模拟键盘、不注入脚本、无爬虫请求
- 无人值守：定期自动刷新页面并识别，命中止损线才推送告警
- 告警通道：PushPlus（微信）/ 企业微信 / 钉钉 / Windows 通知
- 微云日志自动备份：日志按天轮转并上传微云，防重装丢档

## 目录结构

| 路径 | 说明 |
|------|------|
| `开发/` | 源码（主程序、模块、打包 spec、文档） |
| `开发/tiktok_monitor_v10.pyw` | v10 主程序（入口） |
| `开发/mods/` | 插件模块（uia_recog 识别、example_mod 示例） |
| `开发/docs/` | 变更日志等文档 |

## 环境要求

- Windows 10 / 11
- Python 3.9+（安装时勾选 Add Python to PATH）
- 依赖见 `开发/requirements.txt`

## 安装与启动

```bat
cd 开发
INSTALL_DEP.bat    :: 一键安装依赖
START.bat          :: 启动监控面板
```

## 配置说明

运行后首次保存生成 `config.json`（已通过 .gitignore 排除，不入库）。关键配置项：

| 字段 | 说明 |
|------|------|
| `recog_mode` | 识别模式（uia） |
| `refresh_enabled` | 定期刷新开关 |
| `refresh_method` | 刷新方式（uia_reload：UIA 点击浏览器重新加载按钮） |
| `alert_*` / `preset_rules` | 告警阈值与规则（ROI 底线 / 累计消耗上限 / 空烧检测等） |
| `weiyun_*` | 微云日志备份配置 |
| `pushplus_token` / `wecom_key` / `dingtalk_url` | 告警通道凭据 |

## 免责声明

本工具仅供自动化监控广告投放数据使用，请遵守目标平台的服务条款与当地法律法规，使用者须自行承担合规责任。
