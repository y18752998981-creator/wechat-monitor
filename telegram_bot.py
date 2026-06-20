"""微信求购监控系统 - 交互式 Telegram Bot

支持对话式交互：
  - 发送关键词 → 搜索微信聊天记录并返回结果
  - /monitor 关键词 → 设定监控方向，按关键词推送
  - /status → 查看系统状态
  - /help → 使用帮助
  - /groups → 查看所有群列表
  - /recent → 最近求购信息
"""
import json
import os
import sys
import time
import threading
import urllib.request
import urllib.error
import logging
from datetime import datetime

# 路径
APP_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(APP_DIR)
sys.path.insert(0, APP_DIR)

import config
import search_engine
import purchase_filter
import notifier
import wx_reader
import purchase_detector
import group_tracker
import daily_report
import sns_reader

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("bot")

# ============================================================
# Telegram API
# ============================================================
API = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}"

# 监控状态
monitor_state = {
    "running": True,       # 启动即自动监控
    "keywords": list(config.PURCHASE_KEYWORDS[:8]),  # 默认关键词
    "last_scan": "",
    "scan_count": 0,
    "purchase_count": 0,
    "last_daily_report": "",  # 上次日报日期
}

# 扫描线程
_scan_thread = None
_scan_stop = threading.Event()


def _tg_api(method, data=None, timeout=20):
    """调用 Telegram API，带详细错误分类"""
    url = f"{API}/{method}"
    if data is None:
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as e:
            reason = str(getattr(e, 'reason', e))
            if 'timed out' in reason.lower() or 'timeout' in reason.lower():
                log.warning(f"API {method}: 连接超时")
            else:
                log.warning(f"API {method}: 网络错误 - {reason}")
            return None
        except Exception as e:
            log.error(f"API {method} 异常: {e}")
            return None

    body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(url, data=body,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        reason = str(getattr(e, 'reason', e))
        if 'timed out' in reason.lower() or 'timeout' in reason.lower():
            log.warning(f"API {method}: 连接超时")
        elif 'Remote end closed' in reason or 'ConnectionReset' in reason:
            log.warning(f"API {method}: 服务器断开连接")
        else:
            log.warning(f"API {method}: 网络错误 - {reason}")
        return None
    except Exception as e:
        log.error(f"API {method} 异常: {e}")
        return None


def _send(chat_id, text, reply_to=None):
    """发送消息到 chat"""
    data = {
        "chat_id": chat_id,
        "text": text[:4000],
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_to:
        data["reply_to_message_id"] = reply_to
    return _tg_api("sendMessage", data)


# ============================================================
# 命令处理
# ============================================================

def cmd_help(chat_id, msg_id):
    text = (
        "<b>微信求购监控 Bot</b>\n\n"
        "🟢 <b>自动监控已开启</b> — 启动即扫描，发现求购自动推送\n\n"
        "<b>搜索微信记录：</b>\n"
        "  直接发关键词，如：<code>塔丝隆</code>、<code>春亚纺</code>\n"
        "  默认搜最近24小时，加时间如：<code>塔丝隆 48h</code>\n\n"
        "<b>命令列表：</b>\n"
        "/monitor - 查看/修改监控关键词\n"
        "/stop_monitor - 停止自动监控\n"
        "/start_monitor - 重新启动监控\n"
        "/recent - 最近求购信息\n"
        "/groups - 群列表\n"
        "/status - 系统状态\n"
        "/help - 本帮助"
    )
    _send(chat_id, text, reply_to=msg_id)


def cmd_search(chat_id, keyword, hours, msg_id):
    """搜索并返回结果"""
    _send(chat_id, f"🔍 搜索 <b>{keyword}</b> (最近{hours}小时)...", reply_to=msg_id)

    results = search_engine.search_messages(keyword, hours=hours, limit=15)

    if not results:
        _send(chat_id, f"没有找到包含「{keyword}」的消息")
        return

    lines = [f"📋 找到 <b>{len(results)}</b> 条结果：\n"]
    for i, r in enumerate(results[:15], 1):
        content = r["content"].replace("<", "&lt;").replace(">", "&gt;")
        if len(content) > 80:
            content = content[:77] + "..."
        lines.append(f"{i}. <b>{r['chat_name']}</b> {r['time_str']}")
        lines.append(f"   <i>{content}</i>")

    _send(chat_id, "\n".join(lines))


def cmd_monitor(chat_id, args, msg_id):
    """设定监控关键词"""
    if not args:
        # 显示当前关键词
        kws = "、".join(monitor_state["keywords"])
        _send(chat_id,
            f"📌 当前监控关键词：\n{kws}\n\n"
            f"发送 <code>/monitor 关键词1,关键词2</code> 来修改",
            reply_to=msg_id)
        return

    # 解析新关键词
    new_keywords = [k.strip() for k in args.split(",") if k.strip()]
    if not new_keywords:
        _send(chat_id, "关键词不能为空", reply_to=msg_id)
        return

    monitor_state["keywords"] = new_keywords
    kws = "、".join(new_keywords)
    _send(chat_id, f"✅ 监控关键词已更新：\n{kws}", reply_to=msg_id)
    log.info(f"监控关键词更新: {new_keywords}")


def cmd_start_monitor(chat_id, msg_id):
    """启动监控扫描"""
    global _scan_thread
    if monitor_state["running"]:
        _send(chat_id, "⚠️ 监控已在运行中", reply_to=msg_id)
        return

    _scan_stop.clear()
    monitor_state["running"] = True
    _scan_thread = threading.Thread(target=_monitor_loop, daemon=True)
    _scan_thread.start()

    kws = "、".join(monitor_state["keywords"])
    _send(chat_id, f"▶️ 监控已启动\n关键词：{kws}\n扫描间隔：{config.BUSY_INTERVAL}分钟", reply_to=msg_id)
    log.info("监控线程已启动")


def cmd_stop_monitor(chat_id, msg_id):
    """停止监控"""
    if not monitor_state["running"]:
        _send(chat_id, "⚠️ 监控未在运行", reply_to=msg_id)
        return

    _scan_stop.set()
    monitor_state["running"] = False
    _send(chat_id, f"⏹ 监控已停止\n共扫描 {monitor_state['scan_count']} 次，发现 {monitor_state['purchase_count']} 条求购",
          reply_to=msg_id)
    log.info("监控线程已停止")


def cmd_status(chat_id, msg_id):
    """系统状态"""
    fts_exists = os.path.exists(search_engine.FTS_DB)
    fts_size = os.path.getsize(search_engine.FTS_DB) // 1024 // 1024 if fts_exists else 0

    status = "🟢 运行中" if monitor_state["running"] else "⚪ 已停止"
    lines = [
        f"<b>系统状态</b>\n",
        f"监控：{status}",
        f"扫描次数：{monitor_state['scan_count']}",
        f"发现求购：{monitor_state['purchase_count']}",
        f"最近扫描：{monitor_state['last_scan'] or '无'}",
        f"",
        f"FTS数据库：{'✅' if fts_exists else '❌'} {fts_size}MB",
        f"Telegram：✅ 已连接",
    ]
    _send(chat_id, "\n".join(lines), reply_to=msg_id)


def cmd_recent(chat_id, msg_id):
    """最近求购信息"""
    _send(chat_id, "🔍 搜索最近求购信息...", reply_to=msg_id)

    results = search_engine.search_purchase_related(hours=24, limit=10)

    if not results:
        _send(chat_id, "最近24小时没有找到求购信息")
        return

    lines = [f"📋 最近 <b>{len(results)}</b> 条求购相关消息：\n"]
    for i, r in enumerate(results[:10], 1):
        content = r["content"].replace("<", "&lt;").replace(">", "&gt;")
        if len(content) > 80:
            content = content[:77] + "..."
        lines.append(f"{i}. <b>{r['chat_name']}</b> {r['time_str']}")
        lines.append(f"   <i>{content}</i>")

    _send(chat_id, "\n".join(lines))


def cmd_groups(chat_id, msg_id):
    """群列表"""
    groups = search_engine.get_available_groups()
    if not groups:
        _send(chat_id, "没有找到群记录", reply_to=msg_id)
        return

    lines = [f"📋 共 <b>{len(groups)}</b> 个群：\n"]
    for g in groups[:30]:
        lines.append(f"  · {g['name']}")
    if len(groups) > 30:
        lines.append(f"\n  ... 还有 {len(groups)-30} 个")

    _send(chat_id, "\n".join(lines), reply_to=msg_id)


# ============================================================
# 消息处理
# ============================================================

def handle_message(msg):
    """处理一条 Telegram 消息"""
    chat_id = msg.get("chat", {}).get("id")
    text = msg.get("text", "").strip()
    msg_id = msg.get("message_id")

    if not chat_id or not text:
        return

    # 只响应配置中的 chat_id
    if str(chat_id) != str(config.TELEGRAM_CHAT_ID):
        return

    # 命令处理
    if text.startswith("/"):
        parts = text.split(maxsplit=1)
        cmd = parts[0].split("@")[0].lower()  # 去掉 @botname
        args = parts[1].strip() if len(parts) > 1 else ""

        if cmd == "/help" or cmd == "/start":
            cmd_help(chat_id, msg_id)
        elif cmd == "/monitor":
            cmd_monitor(chat_id, args, msg_id)
        elif cmd == "/start_monitor":
            cmd_start_monitor(chat_id, msg_id)
        elif cmd == "/stop_monitor":
            cmd_stop_monitor(chat_id, msg_id)
        elif cmd == "/status":
            cmd_status(chat_id, msg_id)
        elif cmd == "/recent":
            cmd_recent(chat_id, msg_id)
        elif cmd == "/groups":
            cmd_groups(chat_id, msg_id)
        else:
            _send(chat_id, f"未知命令，发 /help 查看帮助", reply_to=msg_id)
        return

    # 非命令 → 当作搜索
    # 解析时间范围：「塔丝隆 48h」「春亚纺 7d」
    hours = 24
    import re
    time_match = re.search(r'(\d+)\s*[hH小时]', text)
    if time_match:
        hours = int(time_match.group(1))
        text = text[:time_match.start()].strip()

    day_match = re.search(r'(\d+)\s*[dD天]', text)
    if day_match:
        hours = int(day_match.group(1)) * 24
        text = text[:day_match.start()].strip()

    if not text:
        _send(chat_id, "请输入搜索关键词", reply_to=msg_id)
        return

    cmd_search(chat_id, text, hours, msg_id)


# ============================================================
# 监控扫描循环
# ============================================================

def _monitor_loop():
    """后台监控循环（自动扫描 + 推送 + 日报 + 群追踪）"""
    log.info("🟢 自动监控已启动，扫描间隔: 工作时段{0}分钟 / 非工作时段{1}分钟".format(
        config.BUSY_INTERVAL, config.IDLE_INTERVAL))

    while not _scan_stop.is_set():
        try:
            now = datetime.now()
            hour = now.hour
            now_str = now.strftime("%H:%M:%S")
            is_busy = config.BUSY_HOURS_START <= hour < config.BUSY_HOURS_END
            interval = config.BUSY_INTERVAL if is_busy else config.IDLE_INTERVAL

            monitor_state["last_scan"] = now_str
            monitor_state["scan_count"] += 1

            log.info(f"[{now_str}] 第{monitor_state['scan_count']}次扫描 (最近{interval+2}分钟)")

            # 1. 读取微信新消息
            messages = wx_reader.get_new_messages(interval + 2)

            if messages:
                log.info(f"读取到 {len(messages)} 条消息")

                # 2. 检测求购信息
                purchases = purchase_detector.batch_detect(messages)

                if purchases:
                    # 3. 过滤推销广告
                    real = purchase_filter.filter_purchases(purchases)
                    filtered = len(purchases) - len(real)
                    if filtered:
                        log.info(f"过滤 {filtered} 条推销，保留 {len(real)} 条真实求购")

                    if real:
                        # 4. 保存求购记录
                        daily_report.add_purchases(real)
                        monitor_state["purchase_count"] += len(real)

                        # 5. 推送通知：急单立即单独推，普通批量推
                        urgent = [p for p in real if p.urgency == "高"]
                        normal = [p for p in real if p.urgency != "高"]
                        for p in urgent:
                            notifier.send_notification(p)
                            time.sleep(1)
                        if normal:
                            notifier.send_batch_notification(normal)

                        # 6. 打印结果
                        for p in real:
                            badge = "🔥" if p.urgency == "高" else "📌"
                            log.info(f"  {badge} [{p.group_name}] {p.sender_name}: {p.summary()}")

                # 7. 更新群统计
                purchase_texts = set(p.raw_text for p in (real if purchases else []))
                msg_with_flags = [
                    {
                        "group_name": m.get("group_name", ""),
                        "is_purchase": m.get("text", "") in purchase_texts,
                    }
                    for m in messages
                ]
                group_tracker.process_new_messages(msg_with_flags)
            else:
                log.info(f"没有新消息")

            # 7.5 扫描朋友圈
            try:
                moments = sns_reader.get_new_moments(hours=max(1, (interval + 5) // 60))
                if moments:
                    log.info(f"朋友圈: {len(moments)} 条新动态")

                    # 将朋友圈转成 purchase_detector 输入格式
                    sns_msgs = []
                    for m in moments:
                        sns_msgs.append({
                            "text": m["text"],
                            "group_name": "朋友圈",
                            "sender_name": m["display_name"],
                            "sender_wxid": m["wxid"],
                            "timestamp": m["time_str"],
                            "msg_type": "text",
                        })

                    sns_purchases = purchase_detector.batch_detect(sns_msgs)
                    if sns_purchases:
                        sns_real = purchase_filter.filter_purchases(sns_purchases)
                        if sns_real:
                            log.info(f"朋友圈发现 {len(sns_real)} 条求购")
                            monitor_state["purchase_count"] += len(sns_real)

                            # 构建 (moment, purchase) 配对
                            # 通过 text 匹配回原始 moment
                            text_to_moment = {m["text"]: m for m in moments}
                            moments_with_p = []
                            urgent_sns = []

                            for p in sns_real:
                                moment = text_to_moment.get(p.raw_text, None)
                                if not moment:
                                    # 尝试模糊匹配
                                    for m in moments:
                                        if p.raw_text in m["text"] or m["text"] in p.raw_text:
                                            moment = m
                                            break
                                if moment:
                                    if p.urgency == "高":
                                        urgent_sns.append((moment, p))
                                    else:
                                        moments_with_p.append((moment, p))

                            # 急单单独推送
                            for moment, p in urgent_sns:
                                notifier.send_moments_notification(moment, p)
                                time.sleep(1)

                            # 普通批量推送
                            if moments_with_p:
                                notifier.send_moments_batch(moments_with_p)

                            for moment, p in (urgent_sns + moments_with_p):
                                log.info(f"  🔥 [{moment['display_name']}] {p.summary()}" if p.urgency == "高"
                                         else f"  📢 [{moment['display_name']}] {p.summary()}")
            except Exception as e:
                log.warning(f"朋友圈扫描异常: {e}")

            # 8. 日报：晚上 22:00 自动生成推送
            today = now.strftime("%Y-%m-%d")
            if hour >= 22 and monitor_state["last_daily_report"] != today:
                try:
                    log.info("生成每日汇总报告...")
                    report = daily_report.generate_daily_report()
                    report_path = os.path.join(config.REPORTS_DIR, f"日报-{today}.txt")
                    os.makedirs(config.REPORTS_DIR, exist_ok=True)
                    with open(report_path, "w", encoding="utf-8") as f:
                        f.write(report)
                    notifier.send_daily_report(report)
                    monitor_state["last_daily_report"] = today
                    log.info(f"日报已生成并推送: {report_path}")
                except Exception as e:
                    log.error(f"日报生成异常: {e}")

        except Exception as e:
            log.error(f"扫描异常: {e}", exc_info=True)

        # 等待下次扫描
        log.info(f"等待 {interval} 分钟后下次扫描...")
        _scan_stop.wait(interval * 60)

    log.info("监控循环已停止")


# ============================================================
# 主循环（长轮询）
# ============================================================

def run_bot():
    """启动 Bot 长轮询（带指数退避重连）"""
    log.info("=" * 40)
    log.info("交互式 Telegram Bot 启动")
    log.info(f"Bot Token: ...{config.TELEGRAM_BOT_TOKEN[-8:]}")
    log.info("=" * 40)

    # 测试连接
    me = _tg_api("getMe")
    if not me or not me.get("ok"):
        log.error("Telegram 连接失败，请检查 Token 和网络")
        return

    bot_name = me.get("result", {}).get("first_name", "?")
    username = me.get("result", {}).get("username", "?")
    log.info(f"🟢 Bot 已启动: @{username} ({bot_name})")

    # 自动启动监控线程
    _scan_stop.clear()
    monitor_state["running"] = True
    _scan_thread = threading.Thread(target=_monitor_loop, daemon=True)
    _scan_thread.start()
    kws = "、".join(monitor_state["keywords"][:6])
    log.info(f"🟢 自动监控已启动，关键词: {kws}")

    # 发一条启动通知到 Telegram
    _send(config.TELEGRAM_CHAT_ID,
          f"🟢 <b>微信求购监控已启动</b>\n"
          f"关键词: {kws}\n"
          f"扫描间隔: 工作时段{config.BUSY_INTERVAL}分钟\n"
          f"发 /help 查看命令")

    log.info("等待消息中...")

    offset = 0          # 消息偏移量
    err_count = 0       # 连续错误计数
    backoff = 3         # 当前退避秒数（初始）
    MAX_BACKOFF = 120   # 最大退避 2 分钟
    POLL_TIMEOUT = 15   # 服务端轮询超时（秒）—— 缩短以便更快恢复
    CLIENT_TIMEOUT = POLL_TIMEOUT + 10  # 客户端超时留余量

    while True:
        try:
            params = {"offset": offset, "timeout": POLL_TIMEOUT,
                      "allowed_updates": ["message"]}
            result = _tg_api("getUpdates", params, timeout=CLIENT_TIMEOUT)

            if not result or not result.get("ok"):
                # API 调用失败（超时 / 网络错误）
                err_count += 1
                if err_count == 1:
                    log.info(f"网络波动，{backoff}秒后重试...")
                elif err_count % 5 == 0:
                    # 每5次错误做一次健康检查
                    check = _tg_api("getMe", timeout=10)
                    if check and check.get("ok"):
                        log.info(f"✅ Bot 连接恢复正常（第{err_count}次重试后）")
                        err_count = 0
                        backoff = 3
                        continue
                    else:
                        log.warning(f"仍无法连接，已重试{err_count}次，退避{backoff}秒")
                time.sleep(backoff)
                backoff = min(backoff * 1.5, MAX_BACKOFF)  # 指数退避
                continue

            # 成功收到响应
            if err_count > 0:
                log.info(f"✅ Bot 连接恢复（经过{err_count}次重试）")
                err_count = 0
                backoff = 3

            updates = result.get("result", [])
            for update in updates:
                offset = update["update_id"] + 1
                msg = update.get("message")
                if msg:
                    try:
                        handle_message(msg)
                    except Exception as e:
                        log.error(f"消息处理异常: {e}", exc_info=True)

        except KeyboardInterrupt:
            log.info("Bot 收到退出信号")
            _scan_stop.set()
            monitor_state["running"] = False
            break
        except Exception as e:
            err_count += 1
            log.warning(f"轮询异常: {e}（{backoff}秒后重试）")
            time.sleep(backoff)
            backoff = min(backoff * 1.5, MAX_BACKOFF)

    log.info("Bot 已停止")


if __name__ == "__main__":
    run_bot()
