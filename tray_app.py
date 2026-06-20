"""微信求购监控系统 - 桌面托盘应用

双击启动，系统托盘运行。
右键菜单：启动/停止监控、测试连接、查看日志、退出。
"""
import os
import sys
import threading
import time
import logging
from datetime import datetime

# 兼容 PyInstaller 打包后的路径
if getattr(sys, 'frozen', False):
    # 打包后：exe 所在目录
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))

os.chdir(APP_DIR)
sys.path.insert(0, APP_DIR)

import config
import wx_reader
import purchase_detector
import purchase_filter
import group_tracker
import notifier
import daily_report

# ============================================================
# 日志配置
# ============================================================
LOG_DIR = os.path.join(APP_DIR, "data", "logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, f"monitor-{datetime.now().strftime('%Y-%m-%d')}.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("tray")


# ============================================================
# 监控线程
# ============================================================
class MonitorThread:
    """在后台线程运行监控循环"""

    def __init__(self):
        self._thread = None
        self._running = False
        self._scan_count = 0
        self._purchase_count = 0
        self._last_scan_time = ""
        self._status_callback = None

    @property
    def is_running(self):
        return self._running

    @property
    def stats(self):
        return {
            "running": self._running,
            "scan_count": self._scan_count,
            "purchase_count": self._purchase_count,
            "last_scan": self._last_scan_time,
        }

    def start(self, status_callback=None):
        if self._running:
            log.info("监控已在运行中")
            return
        self._status_callback = status_callback
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        log.info("监控已启动")

    def stop(self):
        if not self._running:
            return
        self._running = False
        log.info("监控正在停止...")
        if self._thread:
            self._thread.join(timeout=10)
        log.info("监控已停止")

    def _scan_once(self):
        """执行一次扫描"""
        interval = config.BUSY_INTERVAL
        now = datetime.now()
        hour = now.hour
        is_busy = config.BUSY_HOURS_START <= hour < config.BUSY_HOURS_END
        interval = config.BUSY_INTERVAL if is_busy else config.IDLE_INTERVAL

        now_str = now.strftime("%H:%M:%S")
        log.info(f"[{now_str}] 开始扫描微信消息 (最近{interval+2}分钟)...")

        messages = wx_reader.get_new_messages(interval + 2)

        if not messages:
            log.info(f"[{now_str}] 没有新消息")
            return 0

        log.info(f"[{now_str}] 读取到 {len(messages)} 条消息")

        purchases = purchase_detector.batch_detect(messages)
        if not purchases:
            log.info(f"[{now_str}] 没有发现求购信息")
            msg_with_flags = [
                {"group_name": m.get("group_name", ""), "is_purchase": False}
                for m in messages
            ]
            group_tracker.process_new_messages(msg_with_flags)
            return 0

        log.info(f"[{now_str}] 发现 {len(purchases)} 条求购信息")

        # 过滤推销广告
        real_purchases = purchase_filter.filter_purchases(purchases)
        filtered_count = len(purchases) - len(real_purchases)
        if filtered_count > 0:
            log.info(f"[{now_str}] 过滤 {filtered_count} 条推销，保留 {len(real_purchases)} 条真实求购")

        if not real_purchases:
            log.info(f"[{now_str}] 过滤后无真实求购")
            msg_with_flags = [
                {"group_name": m.get("group_name", ""), "is_purchase": False}
                for m in messages
            ]
            group_tracker.process_new_messages(msg_with_flags)
            return 0

        # 保存记录
        daily_report.add_purchases(real_purchases)

        # 推送
        urgent = [p for p in real_purchases if p.urgency == "高"]
        normal = [p for p in real_purchases if p.urgency != "高"]

        for p in urgent:
            notifier.send_notification(p)
            time.sleep(1)

        if normal:
            notifier.send_batch_notification(normal)

        # 更新群统计
        msg_with_flags = []
        purchase_texts = set(p.raw_text for p in real_purchases)
        for m in messages:
            msg_with_flags.append({
                "group_name": m.get("group_name", ""),
                "is_purchase": m.get("text", "") in purchase_texts,
            })
        group_tracker.process_new_messages(msg_with_flags)

        for p in real_purchases:
            tag = "急单" if p.urgency == "高" else "求购"
            log.info(f"  [{tag}] [{p.group_name}] {p.sender_name}: {p.summary()}")

        return len(real_purchases)

    def _loop(self):
        """主监控循环"""
        last_daily_report = ""

        while self._running:
            try:
                self._last_scan_time = datetime.now().strftime("%H:%M:%S")
                count = self._scan_once()
                self._scan_count += 1
                self._purchase_count += count
            except Exception as e:
                log.error(f"扫描异常: {e}", exc_info=True)

            # 检查日报 (22:00)
            now = datetime.now()
            today = now.strftime("%Y-%m-%d")
            if now.hour >= 22 and last_daily_report != today:
                try:
                    report = daily_report.generate_daily_report()
                    report_path = os.path.join(config.REPORTS_DIR, f"日报-{today}.txt")
                    os.makedirs(config.REPORTS_DIR, exist_ok=True)
                    with open(report_path, "w", encoding="utf-8") as f:
                        f.write(report)
                    notifier.send_daily_report(report)
                    last_daily_report = today
                    log.info(f"日报已生成并推送: {report_path}")
                except Exception as e:
                    log.error(f"日报生成异常: {e}", exc_info=True)

            # 等待
            hour = now.hour
            is_busy = config.BUSY_HOURS_START <= hour < config.BUSY_HOURS_END
            interval = config.BUSY_INTERVAL if is_busy else config.IDLE_INTERVAL

            log.info(f"等待 {interval} 分钟后下次扫描...")
            for _ in range(interval * 60):
                if not self._running:
                    break
                time.sleep(1)

        log.info("监控循环已退出")


# ============================================================
# 图标生成
# ============================================================
def _create_icon_image(color="green"):
    """用 Pillow 生成一个简单的托盘图标 (64x64)"""
    from PIL import Image, ImageDraw, ImageFont

    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # 圆形背景
    if color == "green":
        bg_color = (46, 204, 113, 255)
    elif color == "gray":
        bg_color = (149, 165, 166, 255)
    else:
        bg_color = (231, 76, 60, 255)

    draw.ellipse([4, 4, size - 4, size - 4], fill=bg_color)

    # 中间文字 "购"
    try:
        # 尝试用系统字体
        font = ImageFont.truetype("msyh.ttc", 28)
    except Exception:
        try:
            font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 28)
        except Exception:
            try:
                font = ImageFont.truetype("C:/Windows/Fonts/simhei.ttf", 28)
            except Exception:
                font = ImageFont.load_default()

    # 居中绘制文字
    text = "购"
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    x = (size - tw) / 2
    y = (size - th) / 2 - 2
    draw.text((x, y), text, fill=(255, 255, 255, 255), font=font)

    return img


# ============================================================
# 日志查看窗口 (Tkinter)
# ============================================================
_log_window = None


def _open_log_window():
    """打开日志查看窗口"""
    global _log_window
    if _log_window is not None:
        try:
            _log_window.lift()
            return
        except Exception:
            _log_window = None

    import tkinter as tk
    from tkinter import scrolledtext

    _log_window = tk.Tk()
    _log_window.title("微信求购监控 - 日志")
    _log_window.geometry("700x500")
    _log_window.resizable(True, True)

    text_widget = scrolledtext.ScrolledText(
        _log_window, wrap=tk.WORD, font=("Consolas", 10), bg="#1e1e1e", fg="#d4d4d4"
    )
    text_widget.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

    def load_log():
        text_widget.delete("1.0", tk.END)
        try:
            with open(LOG_FILE, "r", encoding="utf-8") as f:
                content = f.read()
                # 只显示最后 500 行
                lines = content.split("\n")
                if len(lines) > 500:
                    lines = lines[-500:]
                text_widget.insert(tk.END, "\n".join(lines))
                text_widget.see(tk.END)
        except FileNotFoundError:
            text_widget.insert(tk.END, "暂无日志")

    def auto_refresh():
        if _log_window is None:
            return
        try:
            load_log()
            _log_window.after(5000, auto_refresh)
        except Exception:
            pass

    load_log()
    auto_refresh()

    def on_close():
        global _log_window
        _log_window = None
        try:
            _log_window_ref = _log_window
        except Exception:
            pass

    _log_window.protocol("WM_DELETE_WINDOW", on_close)
    _log_window.mainloop()


# ============================================================
# 托盘应用入口
# ============================================================
def main():
    import pystray

    monitor = MonitorThread()

    # 创建图标
    icon_green = _create_icon_image("green")
    icon_gray = _create_icon_image("gray")

    def get_tooltip(icon):
        stats = monitor.stats
        if stats["running"]:
            return (
                f"微信求购监控 - 运行中\n"
                f"已扫描 {stats['scan_count']} 次 | "
                f"发现 {stats['purchase_count']} 条求购\n"
                f"最近扫描: {stats['last_scan']}"
            )
        return "微信求购监控 - 已停止"

    def on_start_stop(icon, item):
        if monitor.is_running:
            monitor.stop()
            icon.icon = icon_gray
        else:
            monitor.start()
            icon.icon = icon_green

    def on_test_tg(icon, item):
        """测试 Telegram 连接"""
        threading.Thread(target=_test_telegram, daemon=True).start()

    def _test_telegram():
        log.info("测试 Telegram 连接...")
        ok = notifier.test_connection()
        if ok:
            log.info("Telegram 连接正常!")
        else:
            log.error("Telegram 连接失败，请检查配置和网络")

    def on_view_log(icon, item):
        threading.Thread(target=_open_log_window, daemon=True).start()

    def on_quit(icon, item):
        log.info("退出应用...")
        monitor.stop()
        icon.stop()

    # 构建菜单
    menu = pystray.Menu(
        pystray.MenuItem("微信求购监控 v1.1", None, enabled=False),
        pystray.MenuItem(
            lambda item: "停止监控" if monitor.is_running else "启动监控",
            on_start_stop,
        ),
        pystray.MenuItem("测试 Telegram", on_test_tg),
        pystray.MenuItem("查看日志", on_view_log),
        pystray.MenuItem("退出", on_quit),
    )

    # 创建托盘图标
    tray = pystray.Icon(
        "wechat-monitor",
        icon=icon_gray,
        title="微信求购监控 - 已停止",
        menu=menu,
    )

    # 启动时自动开始监控
    def on_ready(icon):
        log.info("托盘应用已启动，自动开始监控...")
        monitor.start()
        icon.icon = icon_green

    log.info("=" * 40)
    log.info("微信求购监控系统 v1.1 启动")
    log.info(f"Telegram: {'已配置' if config.TELEGRAM_BOT_TOKEN else '未配置'}")
    log.info(f"扫描间隔: 工作时段 {config.BUSY_INTERVAL}分钟")
    log.info("=" * 40)

    # 用 setup 回调在图标就绪后自动启动
    tray.run(setup=on_ready)


if __name__ == "__main__":
    main()
