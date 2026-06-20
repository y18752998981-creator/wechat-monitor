"""微信求购监控系统 - 主程序

功能:
  1. 定时扫描微信聊天记录 (每5分钟)
  2. 自动识别求购信息
  3. 过滤推销广告，只保留真实求购
  4. 真实求购推送到 Telegram
  5. 活跃群自动标记
  6. 每天生成汇总日报

使用方法:
  1. 安装 wechat-decrypt:
     git clone https://github.com/ylytdeng/wechat-decrypt.git
     cd wechat-decrypt && pip install -r requirements.txt
  2. 配置 Telegram:  在 config.py 填入 BOT_TOKEN 和 CHAT_ID
  3. 启动监控:        python monitor.py
  4. 单次测试:        python monitor.py --scan
  5. 后台运行:        python monitor.py --daemon
"""
import sys
import os
import time
import signal
import argparse
from datetime import datetime
import config
import wx_reader
import purchase_detector
import purchase_filter
import group_tracker
import notifier
import daily_report
import sns_reader


# 运行状态
running = True


def signal_handler(sig, frame):
    """处理退出信号"""
    global running
    print("\n[Monitor] 收到退出信号，正在停止...")
    running = False


signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


def scan_once(since_minutes=10):
    """
    执行一次扫描
    
    Args:
        since_minutes: 读取最近N分钟的消息
    
    Returns:
        int: 新发现的求购数量
    """
    now = datetime.now().strftime("%H:%M:%S")
    print(f"[{now}] 开始扫描微信消息 (最近{since_minutes}分钟)...")

    # 1. 读取微信新消息
    messages = wx_reader.get_new_messages(since_minutes)

    if not messages:
        print(f"[{now}] 没有新消息")
        return 0

    print(f"[{now}] 读取到 {len(messages)} 条消息")

    # 2. 检测求购信息
    purchases = purchase_detector.batch_detect(messages)

    if not purchases:
        print(f"[{now}] 没有发现求购信息")
        # 仍然更新群统计
        msg_with_flags = [
            {"group_name": m.get("group_name", ""), "is_purchase": False}
            for m in messages
        ]
        group_tracker.process_new_messages(msg_with_flags)
        return 0

    print(f"[{now}] 发现 {len(purchases)} 条求购信息!")

    # 3. 过滤推销广告，只保留真实求购
    real_purchases = purchase_filter.filter_purchases(purchases)
    filtered_count = len(purchases) - len(real_purchases)
    if filtered_count > 0:
        print(f"[{now}] 过滤掉 {filtered_count} 条推销/无关信息，保留 {len(real_purchases)} 条真实求购")

    if not real_purchases:
        print(f"[{now}] 过滤后无真实求购，不推送")
        # 仍然更新群统计
        msg_with_flags = [
            {"group_name": m.get("group_name", ""), "is_purchase": False}
            for m in messages
        ]
        group_tracker.process_new_messages(msg_with_flags)
        return 0

    # 4. 保存求购记录
    daily_report.add_purchases(real_purchases)

    # 5. 推送通知
    # 急单立即单独推送
    urgent = [p for p in real_purchases if p.urgency == "高"]
    normal = [p for p in real_purchases if p.urgency != "高"]

    for p in urgent:
        notifier.send_notification(p)
        time.sleep(1)  # 避免推送太快

    # 普通求购批量推送
    if normal:
        notifier.send_batch_notification(normal)

    # 6. 更新群统计
    msg_with_flags = []
    purchase_texts = set(p.raw_text for p in real_purchases)
    for m in messages:
        msg_with_flags.append({
            "group_name": m.get("group_name", ""),
            "is_purchase": m.get("text", "") in purchase_texts,
        })
    group_tracker.process_new_messages(msg_with_flags)

    # 7. 打印结果
    for p in real_purchases:
        urgency = "🔥" if p.urgency == "高" else "📌"
        print(f"  {urgency} [{p.group_name}] {p.sender_name}: {p.summary()}")

    # 8. 扫描朋友圈
    try:
        moments = sns_reader.get_new_moments(hours=max(1, since_minutes // 60))
        if moments:
            print(f"[{now}] 朋友圈: {len(moments)} 条新动态")
            sns_msgs = [{
                "text": m["text"],
                "group_name": "朋友圈",
                "sender_name": m["display_name"],
                "sender_wxid": m["wxid"],
                "timestamp": m["time_str"],
                "msg_type": "text",
            } for m in moments]

            sns_purchases = purchase_detector.batch_detect(sns_msgs)
            if sns_purchases:
                sns_real = purchase_filter.filter_purchases(sns_purchases)
                if sns_real:
                    print(f"[{now}] 朋友圈发现 {len(sns_real)} 条求购")
                    text_to_moment = {m["text"]: m for m in moments}
                    moments_with_p = []
                    urgent_sns = []
                    for p in sns_real:
                        moment = text_to_moment.get(p.raw_text)
                        if not moment:
                            for m in moments:
                                if p.raw_text in m["text"] or m["text"] in p.raw_text:
                                    moment = m
                                    break
                        if moment:
                            if p.urgency == "高":
                                urgent_sns.append((moment, p))
                            else:
                                moments_with_p.append((moment, p))

                    for moment, p in urgent_sns:
                        notifier.send_moments_notification(moment, p)
                        time.sleep(1)
                    if moments_with_p:
                        notifier.send_moments_batch(moments_with_p)

                    for moment, p in (urgent_sns + moments_with_p):
                        badge = "🔥" if p.urgency == "高" else "📢"
                        print(f"  {badge} [朋友圈] {moment['display_name']}: {p.summary()}")
    except Exception as e:
        print(f"[{now}] 朋友圈扫描异常: {e}")

    return len(real_purchases)


def generate_and_send_daily_report():
    """生成并推送每日报告"""
    now = datetime.now().strftime("%H:%M:%S")
    print(f"[{now}] 生成每日汇总报告...")

    report = daily_report.generate_daily_report()

    # 保存报告文件
    today = datetime.now().strftime("%Y-%m-%d")
    report_path = os.path.join(config.REPORTS_DIR, f"日报-{today}.txt")
    os.makedirs(config.REPORTS_DIR, exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"[{now}] 报告已保存: {report_path}")

    # 推送到微信
    notifier.send_daily_report(report)

    return report


def run_monitor():
    """主监控循环"""
    print("=" * 60)
    print("  微信求购监控系统 v1.1")
    print(f"  启动时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  工作时段: {config.BUSY_HOURS_START}:00 - {config.BUSY_HOURS_END}:00")
    print(f"  扫描间隔: 工作时段 {config.BUSY_INTERVAL}分钟 / 非工作时段 {config.IDLE_INTERVAL}分钟")
    tg_ok = bool(config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID)
    print(f"  Telegram推送: {'已配置' if tg_ok else '❌ 未配置!'}")
    print(f"  推销过滤: 已启用 (purchase_filter)")
    print("=" * 60)

    if not tg_ok:
        print("\n⚠️  Telegram 未配置！")
        print("   请在 config.py 填入 TELEGRAM_BOT_TOKEN 和 TELEGRAM_CHAT_ID\n")

    # 检查 wechat-decrypt
    decrypt_ok = os.path.exists(config.WECHAT_DECRYPT_PATH) and os.path.exists(
        os.path.join(config.WECHAT_DECRYPT_PATH, "main.py")
    )
    if decrypt_ok:
        print("  wechat-decrypt: ✓ 已安装")
    else:
        print(f"  wechat-decrypt: ❌ 未找到! 请安装到: {config.WECHAT_DECRYPT_PATH}")
        print("    git clone https://github.com/ylytdeng/wechat-decrypt.git")
        print()

    last_daily_report = ""

    while running:
        now = datetime.now()
        hour = now.hour

        # 判断是否在非工作时段
        is_busy = config.BUSY_HOURS_START <= hour < config.BUSY_HOURS_END
        interval = config.BUSY_INTERVAL if is_busy else config.IDLE_INTERVAL

        # 执行扫描
        try:
            scan_once(since_minutes=interval + 2)  # 多读2分钟避免遗漏
        except Exception as e:
            print(f"[Error] 扫描异常: {e}")
            import traceback
            traceback.print_exc()

        # 检查是否需要生成日报（晚上 22:00）
        today = now.strftime("%Y-%m-%d")
        if hour >= 22 and last_daily_report != today:
            try:
                generate_and_send_daily_report()
                last_daily_report = today
            except Exception as e:
                print(f"[Error] 日报生成异常: {e}")

        # 等待下次扫描
        print(f"[{datetime.now().strftime('%H:%M:%S')}] 等待 {interval} 分钟后下次扫描...")
        for _ in range(interval * 60):
            if not running:
                break
            time.sleep(1)

    print("\n[Monitor] 已停止")


def run_single_scan():
    """单次扫描模式（用于测试）"""
    print("执行单次扫描测试...")
    count = scan_once(since_minutes=60)
    print(f"\n扫描完成，发现 {count} 条求购信息")

    # 显示群统计
    ranking = group_tracker.get_all_groups_ranking()[:5]
    if ranking:
        print("\n活跃群 TOP 5:")
        for i, g in enumerate(ranking, 1):
            print(f"  {i}. {g['name']}: {g['recent_7d_purchases']} 条求购")


def run_daily_report_only():
    """只生成日报（用于定时任务）"""
    report = generate_and_send_daily_report()
    print(report)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="微信求购监控系统")
    parser.add_argument("--scan", action="store_true", help="执行一次扫描（测试模式）")
    parser.add_argument("--report", action="store_true", help="生成今日日报")
    parser.add_argument("--daemon", action="store_true", help="后台持续运行")
    args = parser.parse_args()

    if args.scan:
        run_single_scan()
    elif args.report:
        run_daily_report_only()
    else:
        run_monitor()
