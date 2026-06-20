# 微信求购监控系统

自动扫描微信群聊消息，识别求购信息，实时推送到 Telegram。

## 功能

- 自动监控微信群消息，识别求购意向
- 智能过滤推销广告，只保留真实求购
- 急单立即推送，普通求购每30分钟汇总推送
- 每晚 22:00 自动生成当日汇总日报
- Telegram Bot 交互式搜索（发关键词即可查微信记录）
- 桌面端应用（支持 DeepSeek AI 智能分析）
- 支持多微信账号同时监控（编辑 `config.py` 中的 `WECHAT_ACCOUNTS` 列表）


## 环境要求

- Windows 10/11
- Python 3.10+（[下载](https://www.python.org/downloads/)，安装时勾选 "Add Python to PATH"）
- 微信 PC 版 4.x（需保持登录）
- Telegram 账号 + Bot（用于接收推送）
- [wechat-decrypt](https://github.com/ylytdeng/wechat-decrypt)（用于解密微信本地数据库）

## 安装步骤

### 1. 安装 wechat-decrypt

```bash
git clone https://github.com/ylytdeng/wechat-decrypt.git
cd wechat-decrypt
pip install -r requirements.txt
```

安装后先手动运行一次解密，确保能正常获取微信数据库：

```bash
cd wechat-decrypt
python main.py decrypt
```

### 2. 创建 Telegram Bot

1. 打开 Telegram，搜索 `@BotFather`
2. 发送 `/newbot`，按提示设置 Bot 名称
3. 创建成功后复制 Bot Token（形如 `123456:ABC-DEF...`）
4. 搜索 `@userinfobot`，发送任意消息获取你的 Chat ID（一串数字）

### 3. 安装本项目

双击运行 `安装依赖.bat`，会自动安装所需的 Python 包。

### 4. 编辑配置文件

将 `config.example.py` 复制为 `config.py`，然后用记事本打开编辑：

```python
# 填入你的 Telegram Bot Token
TELEGRAM_BOT_TOKEN = "你的Bot Token"

# 填入你的 Telegram Chat ID
TELEGRAM_CHAT_ID = "你的Chat ID"

# 修改为你的 wechat-decrypt 实际路径
WECHAT_DECRYPT_PATH = r"C:\Users\你的用户名\wechat-decrypt"
```

其他配置（关键词、扫描间隔等）可根据需要调整。

## 使用方法

### 方式一：一键启动（推荐）

双击 `一键启动.vbs`，会在后台同时启动：
- Telegram Bot（自动监控 + 交互搜索）
- 桌面端应用（AI 分析界面）

### 方式二：单独启动

- `启动Bot.bat` — 只启动 Telegram Bot（在命令行窗口中运行，可看日志）
- `启动桌面端.bat` — 只启动桌面端应用

## Telegram Bot 命令

| 命令 | 功能 |
|------|------|
| 直接发关键词 | 搜索微信聊天记录（如：`塔丝隆`、`春亚纺 48h`） |
| `/monitor` | 查看当前监控关键词 |
| `/monitor 关键词1,关键词2` | 修改监控关键词 |
| `/start_monitor` | 启动自动监控 |
| `/stop_monitor` | 停止自动监控 |
| `/recent` | 查看最近求购信息 |
| `/groups` | 查看所有群列表 |
| `/status` | 查看系统状态 |
| `/help` | 显示帮助 |

## 文件结构

```
wechat-monitor/
├── config.example.py      # 配置模板（需复制为 config.py 后编辑）
├── config.py              # 你的配置文件（不随项目分发）
├── telegram_bot.py        # Telegram Bot 主程序（推荐入口）
├── desktop_app.py         # 桌面端 GUI 应用
├── tray_app.py            # 系统托盘应用
├── monitor.py             # 命令行监控程序
├── wx_reader.py           # 微信消息读取
├── purchase_detector.py   # 求购信息检测
├── purchase_filter.py     # 推销广告过滤
├── search_engine.py       # 全文搜索引擎
├── group_tracker.py       # 群活跃度统计
├── daily_report.py        # 每日汇总报告
├── notifier.py            # Telegram 推送
├── decrypt_lock.py        # 解密进程锁
├── app_icon.ico           # 应用图标
├── requirements.txt       # Python 依赖
├── 安装依赖.bat           # 一键安装依赖
├── 启动Bot.bat            # 启动 Bot
├── 启动桌面端.bat         # 启动桌面端
├── 一键启动.vbs           # 后台静默启动全部
└── data/                  # 运行数据目录
    ├── exports/
    ├── images/
    └── reports/
```

## 常见问题

**Q: 扫描不到消息？**
确保微信 PC 版已登录，且 wechat-decrypt 已正确解密数据库。

**Q: Telegram 收不到推送？**
检查 config.py 中的 Token 和 Chat ID 是否正确，网络是否能访问 Telegram。

**Q: 想调整扫描频率？**
编辑 config.py 中的 `BUSY_INTERVAL`（工作时段，默认30分钟）和 `IDLE_INTERVAL`（非工作时段，默认60分钟）。
