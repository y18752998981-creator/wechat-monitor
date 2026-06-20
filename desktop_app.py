"""微信求购监控系统 - 桌面端应用

功能：
  - 聊天式交互界面
  - DeepSeek AI 智能搜索微信记录
  - 关键词监控配置
  - 实时求购推送状态
"""
import os
import sys
import json
import threading
import time
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
from datetime import datetime

APP_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(APP_DIR)
sys.path.insert(0, APP_DIR)

import config
import search_engine

# Bot 日志文件（调试用）
BOT_LOG_FILE = os.path.join(APP_DIR, "bot_debug.log")
def bot_debug(msg):
    """写调试日志，不影响 UI"""
    try:
        with open(BOT_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n")
    except:
        pass

# ============================================================
# wechat-decrypt 自动刷新
# ============================================================
WECHAT_DECRYPT_DIR = getattr(config, "WECHAT_DECRYPT_PATH", r"C:\Users\y1875\wechat-decrypt")

# 硬编码使用 venv 里的 Python（避免系统 PATH 不一致导致找不到依赖）
# desktop_app 是用 venv pythonw.exe 启动的，但有时 subprocess 会 fallback 到 system Python
HERMES_VENV_PYTHON = r"C:\Users\y1875\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe"
SYSTEM_PYTHON = r"C:\Users\y1875\AppData\Local\Programs\Python\Python311\python.exe"

def _find_working_python():
    """找一个能 import Crypto 的 Python 解释器"""
    import subprocess as sp
    kwargs = {"capture_output": True, "text": True, "timeout": 10}
    if sys.platform == "win32":
        kwargs["creationflags"] = sp.CREATE_NO_WINDOW
    for py in [HERMES_VENV_PYTHON, SYSTEM_PYTHON, sys.executable]:
        if os.path.exists(py):
            try:
                r = sp.run([py, "-c", "import Crypto; from zstandard import ZstdDecompressor; import pilk; print('OK')"],
                          **kwargs)
                if r.returncode == 0 and "OK" in r.stdout:
                    return py
            except Exception:
                continue
    return sys.executable  # fallback

PYTHON_EXE = _find_working_python()

def refresh_wechat_data(callback=None):
    """调用 wechat-decrypt 更新数据库（使用共享锁，不弹黑框）。
    callback(stage, msg) 用于 UI 反馈。
    """
    import decrypt_lock

    def emit(stage, msg):
        bot_debug(f"refresh: [{stage}] {msg[:200]}")
        if callback:
            callback(stage, msg)

    emit("start", f"开始刷新微信数据库 (路径: {WECHAT_DECRYPT_DIR})")
    if not os.path.isdir(WECHAT_DECRYPT_DIR):
        emit("error", f"wechat-decrypt 目录不存在: {WECHAT_DECRYPT_DIR}")
        return False

    emit("run", f"调用: python main.py decrypt (可能需要 30秒-2分钟)")
    ok, msg = decrypt_lock.run_decrypt(python_exe=PYTHON_EXE, timeout=300)

    if ok:
        emit("done", f"✅ 刷新完成: {msg}")
        return True
    else:
        emit("error", f"❌ {msg}")
        return False

# ============================================================
# DeepSeek API
# ============================================================

def deepseek_chat(messages, api_key=None):
    """
    调用 DeepSeek API 进行对话

    Args:
        messages: list of {"role": "user"|"assistant"|"system", "content": "..."}
        api_key: DeepSeek API Key

    Returns:
        str: AI 回复内容
    """
    import urllib.request

    key = api_key or getattr(config, "DEEPSEEK_API_KEY", "")
    if not key:
        return "[未配置 DeepSeek API Key，请在设置中填入]"

    data = json.dumps({
        "model": "deepseek-chat",
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": 1000,
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.deepseek.com/v1/chat/completions",
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result["choices"][0]["message"]["content"]
    except Exception as e:
        return f"[DeepSeek 调用失败: {e}]"


def ai_search(keyword, context_messages):
    """用 AI 分析搜索结果，提取关键信息"""
    results = search_engine.search_messages(keyword, hours=48, limit=20)

    if not results:
        return f"没有找到包含「{keyword}」的微信记录。"

    # 构建上下文
    result_text = "\n".join(
        f"- [{r['chat_name']}] {r['time_str']}: {r['content'][:100]}"
        for r in results[:15]
    )

    system_prompt = """你是一个面料行业助手，帮助用户分析微信聊天记录中的求购信息。
请从以下搜索结果中：
1. 识别出真正的求购/询价需求（排除推销广告）
2. 提取关键信息：面料品种、规格、数量、联系人、群名
3. 给出简短的分析和建议
用简洁的中文回答。"""

    user_msg = f"搜索关键词：{keyword}\n\n微信记录：\n{result_text}\n\n请分析这些消息中的求购需求。"

    messages = [
        {"role": "system", "content": system_prompt},
    ]
    # 加入历史对话（最近4条）
    messages.extend(context_messages[-4:])
    messages.append({"role": "user", "content": user_msg})

    return deepseek_chat(messages)


# ============================================================
# GUI 应用
# ============================================================

class WechatMonitorApp:
    def __init__(self, root):
        self.root = root
        self.root.title("微信求购监控系统")
        self.root.geometry("800x600")
        self.root.configure(bg="#f5f5f5")

        # 对话历史
        self.chat_history = []  # [{"role": "user"|"assistant", "content": "..."}]

        # 监控关键词
        self.monitor_keywords = list(config.PURCHASE_KEYWORDS[:8])

        # 数据刷新状态
        self._refreshing = False
        self._refresh_timer = None

        self._build_ui()

        # 启动后自动启动 Telegram Bot
        self.root.after(2000, self._start_bot)

        # 启动后 3 秒自动刷新一次微信数据（如果用户开了自动刷新）
        self.root.after(3000, self._schedule_next_refresh)

    def _build_ui(self):
        # 顶部标题栏
        header = tk.Frame(self.root, bg="#2c3e50", height=50)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        tk.Label(header, text="  微信求购监控系统", font=("Microsoft YaHei", 14, "bold"),
                 bg="#2c3e50", fg="white").pack(side=tk.LEFT, padx=10, pady=10)

        # 状态标签
        self.status_label = tk.Label(header, text="就绪", font=("Microsoft YaHei", 10),
                                     bg="#2c3e50", fg="#95a5a6")
        self.status_label.pack(side=tk.RIGHT, padx=15)

        # 主内容区（Notebook 选项卡）
        notebook = ttk.Notebook(self.root)
        notebook.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # 选项卡1：AI 聊天
        chat_frame = tk.Frame(notebook, bg="white")
        notebook.add(chat_frame, text=" AI 对话 ")
        self._build_chat_tab(chat_frame)

        # 选项卡2：搜索
        search_frame = tk.Frame(notebook, bg="white")
        notebook.add(search_frame, text=" 关键词搜索 ")
        self._build_search_tab(search_frame)

        # 选项卡3：监控设置
        settings_frame = tk.Frame(notebook, bg="white")
        notebook.add(settings_frame, text=" 监控设置 ")
        self._build_settings_tab(settings_frame)

        # 选项卡4：Bot 控制
        bot_frame = tk.Frame(notebook, bg="white")
        notebook.add(bot_frame, text=" Telegram Bot ")
        self._build_bot_tab(bot_frame)

    def _build_chat_tab(self, parent):
        """AI 对话选项卡"""
        # 聊天显示区
        self.chat_display = scrolledtext.ScrolledText(
            parent, wrap=tk.WORD, font=("Microsoft YaHei", 11),
            bg="white", fg="#333", relief=tk.FLAT, padx=10, pady=10,
            state=tk.DISABLED,
        )
        self.chat_display.pack(fill=tk.BOTH, expand=True, padx=5, pady=(5, 0))

        # 配置文字标签样式
        self.chat_display.tag_configure("user", foreground="#2980b9", font=("Microsoft YaHei", 11, "bold"))
        self.chat_display.tag_configure("ai", foreground="#27ae60", font=("Microsoft YaHei", 11, "bold"))
        self.chat_display.tag_configure("system", foreground="#95a5a6", font=("Microsoft YaHei", 9))

        # 输入区
        input_frame = tk.Frame(parent, bg="white")
        input_frame.pack(fill=tk.X, padx=5, pady=5)

        self.chat_input = tk.Entry(
            input_frame, font=("Microsoft YaHei", 11),
            relief=tk.SOLID, bd=1,
        )
        self.chat_input.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5), ipady=8)
        self.chat_input.bind("<Return>", self._on_chat_send)

        send_btn = tk.Button(
            input_frame, text="发送", font=("Microsoft YaHei", 10),
            bg="#3498db", fg="white", relief=tk.FLAT, padx=15,
            command=self._on_chat_send,
        )
        send_btn.pack(side=tk.RIGHT, ipady=5)

        # 欢迎消息
        self._append_chat("system", "系统已就绪。输入关键词让 AI 帮你搜索微信记录，例如：")
        self._append_chat("system", "  「塔丝隆最近的求购」")
        self._append_chat("system", "  「春亚纺 300T 谁在找」")
        self._append_chat("system", "  「帮我分析最近的坯布需求」")

    def _build_search_tab(self, parent):
        """关键词搜索选项卡"""
        # 搜索栏
        search_bar = tk.Frame(parent, bg="white")
        search_bar.pack(fill=tk.X, padx=10, pady=10)

        tk.Label(search_bar, text="关键词：", font=("Microsoft YaHei", 10), bg="white").pack(side=tk.LEFT)

        self.search_entry = tk.Entry(search_bar, font=("Microsoft YaHei", 11), width=30, relief=tk.SOLID, bd=1)
        self.search_entry.pack(side=tk.LEFT, padx=5, ipady=5)
        self.search_entry.bind("<Return>", self._on_search)

        tk.Label(search_bar, text="时间：", font=("Microsoft YaHei", 10), bg="white").pack(side=tk.LEFT, padx=(15, 0))

        self.hours_var = tk.StringVar(value="24")
        hours_spin = tk.Spinbox(search_bar, from_=1, to=168, textvariable=self.hours_var,
                                font=("Microsoft YaHei", 10), width=5)
        hours_spin.pack(side=tk.LEFT, padx=2)
        tk.Label(search_bar, text="小时", font=("Microsoft YaHei", 10), bg="white").pack(side=tk.LEFT)

        search_btn = tk.Button(search_bar, text="搜索", font=("Microsoft YaHei", 10),
                               bg="#e74c3c", fg="white", relief=tk.FLAT, padx=15,
                               command=self._on_search)
        search_btn.pack(side=tk.RIGHT, ipady=3)

        # 结果显示
        self.search_result = scrolledtext.ScrolledText(
            parent, wrap=tk.WORD, font=("Consolas", 10),
            bg="#fafafa", fg="#333", relief=tk.FLAT, padx=10, pady=10,
        )
        self.search_result.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

    def _build_settings_tab(self, parent):
        """设置选项卡"""
        canvas = tk.Canvas(parent, bg="white", highlightthickness=0)
        canvas.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        inner = tk.Frame(canvas, bg="white")
        canvas.create_window(0, 0, window=inner, anchor=tk.NW)

        row = 0

        # DeepSeek API Key
        tk.Label(inner, text="DeepSeek API Key：", font=("Microsoft YaHei", 10, "bold"),
                 bg="white").grid(row=row, column=0, sticky=tk.W, pady=10)
        row += 1

        self.api_key_var = tk.StringVar(value=getattr(config, "DEEPSEEK_API_KEY", ""))
        tk.Entry(inner, textvariable=self.api_key_var, font=("Consolas", 10), width=50,
                 show="*", relief=tk.SOLID, bd=1).grid(row=row, column=0, sticky=tk.W, pady=2)
        row += 1

        # 监控关键词
        tk.Label(inner, text="监控关键词（逗号分隔）：", font=("Microsoft YaHei", 10, "bold"),
                 bg="white").grid(row=row, column=0, sticky=tk.W, pady=(20, 5))
        row += 1

        self.keywords_var = tk.StringVar(value=",".join(self.monitor_keywords))
        tk.Entry(inner, textvariable=self.keywords_var, font=("Microsoft YaHei", 10), width=50,
                 relief=tk.SOLID, bd=1).grid(row=row, column=0, sticky=tk.W, pady=2)
        row += 1

        # 保存按钮
        save_btn = tk.Button(inner, text="保存设置", font=("Microsoft YaHei", 10),
                             bg="#27ae60", fg="white", relief=tk.FLAT, padx=20,
                             command=self._save_settings)
        save_btn.grid(row=row, column=0, sticky=tk.W, pady=20)
        row += 1

        # ================ 数据管理（wechat-decrypt 刷新） ================
        data_frame = tk.LabelFrame(inner, text="📦 微信数据管理", font=("Microsoft YaHei", 10, "bold"),
                                   bg="white", padx=10, pady=10, fg="#2c3e50")
        data_frame.grid(row=row, column=0, sticky=tk.EW, pady=(20, 0))
        row += 1

        # 最后刷新时间
        self.data_fresh_label = tk.Label(data_frame, text="最后刷新: 从未",
                                         font=("Microsoft YaHei", 9), bg="white", fg="#666")
        self.data_fresh_label.grid(row=0, column=0, sticky=tk.W, pady=2)

        # 状态文字（刷新中/成功/失败）
        self.data_status_label = tk.Label(data_frame, text="", font=("Microsoft YaHei", 9),
                                          bg="white", fg="#3498db", wraplength=400, justify=tk.LEFT)
        self.data_status_label.grid(row=1, column=0, sticky=tk.W, pady=2)

        # 按钮行
        btn_row = tk.Frame(data_frame, bg="white")
        btn_row.grid(row=2, column=0, sticky=tk.W, pady=8)

        self.refresh_btn = tk.Button(btn_row, text="🔄 立即刷新数据",
                                     font=("Microsoft YaHei", 10),
                                     bg="#3498db", fg="white", relief=tk.FLAT, padx=15,
                                     command=self._on_refresh_click)
        self.refresh_btn.pack(side=tk.LEFT, padx=(0, 5), ipady=3)

        # 自动刷新开关
        self.auto_refresh_var = tk.BooleanVar(value=True)
        tk.Checkbutton(btn_row, text="自动刷新（每 5 分钟）", variable=self.auto_refresh_var,
                       font=("Microsoft YaHei", 9), bg="white",
                       command=self._on_auto_refresh_toggle).pack(side=tk.LEFT, padx=10)

        # 提示
        tk.Label(data_frame,
                 text="💡 自动调用 wechat-decrypt 解密微信数据库。首次需要微信在登录状态。",
                 font=("Microsoft YaHei", 8), bg="white", fg="#999",
                 wraplength=400, justify=tk.LEFT).grid(row=3, column=0, sticky=tk.W, pady=(5, 0))

    # ============================================================
    # 事件处理
    # ============================================================

    def _on_chat_send(self, event=None):
        """发送聊天消息"""
        text = self.chat_input.get().strip()
        if not text:
            return

        self.chat_input.delete(0, tk.END)
        self._append_chat("user", text)
        self.status_label.config(text="AI 思考中...")

        # 后台线程处理
        threading.Thread(target=self._process_chat, args=(text,), daemon=True).start()

    def _process_chat(self, user_text):
        """处理用户消息（后台线程）"""
        # 更新 API key
        config.DEEPSEEK_API_KEY = self.api_key_var.get().strip()

        # 调用 AI 搜索
        reply = ai_search(user_text, self.chat_history)

        self.chat_history.append({"role": "user", "content": user_text})
        self.chat_history.append({"role": "assistant", "content": reply})

        self.root.after(0, self._append_chat, "ai", reply)
        self.root.after(0, lambda: self.status_label.config(text="就绪"))

    def _append_chat(self, tag, text):
        """追加聊天内容"""
        self.chat_display.config(state=tk.NORMAL)
        if tag == "user":
            self.chat_display.insert(tk.END, f"\n你：{text}\n", "user")
        elif tag == "ai":
            self.chat_display.insert(tk.END, f"\nAI：{text}\n", "ai")
        else:
            self.chat_display.insert(tk.END, f"\n{text}\n", "system")
        self.chat_display.config(state=tk.DISABLED)
        self.chat_display.see(tk.END)

    def _on_search(self, event=None):
        """执行搜索"""
        keyword = self.search_entry.get().strip()
        if not keyword:
            return

        try:
            hours = int(self.hours_var.get())
        except ValueError:
            hours = 24

        self.search_result.delete("1.0", tk.END)
        self.search_result.insert(tk.END, f"搜索「{keyword}」(最近{hours}小时)...\n\n")
        self.status_label.config(text="搜索中...")

        def do_search():
            results = search_engine.search_messages(keyword, hours=hours, limit=30)
            self.root.after(0, self._display_search_results, keyword, results)
            self.root.after(0, lambda: self.status_label.config(text="就绪"))

        threading.Thread(target=do_search, daemon=True).start()

    def _display_search_results(self, keyword, results):
        """显示搜索结果"""
        self.search_result.delete("1.0", tk.END)

        if not results:
            self.search_result.insert(tk.END, f"没有找到包含「{keyword}」的消息")
            return

        self.search_result.insert(tk.END, f"找到 {len(results)} 条结果：\n")
        self.search_result.insert(tk.END, "─" * 60 + "\n\n")

        for i, r in enumerate(results, 1):
            self.search_result.insert(tk.END, f"[{r['time_str']}] {r['chat_name']}\n")
            self.search_result.insert(tk.END, f"  {r['content'][:150]}\n\n")

    def _save_settings(self):
        """保存设置"""
        config.DEEPSEEK_API_KEY = self.api_key_var.get().strip()
        self.monitor_keywords = [k.strip() for k in self.keywords_var.get().split(",") if k.strip()]
        messagebox.showinfo("保存成功", "设置已保存")

    # ============================================================
    # 数据刷新（wechat-decrypt）
    # ============================================================

    def _on_refresh_click(self):
        """点击刷新按钮"""
        if self._refreshing:
            messagebox.showinfo("提示", "数据正在刷新中...")
            return
        threading.Thread(target=self._do_refresh, daemon=True).start()

    def _on_auto_refresh_toggle(self):
        """切换自动刷新"""
        if self.auto_refresh_var.get():
            self._schedule_next_refresh()
            self._append_bot_log("info", "🟢 自动刷新已开启（每5分钟）")
        else:
            self._refresh_timer = None
            self._append_bot_log("info", "⚪ 自动刷新已关闭")

    def _do_refresh(self):
        """实际执行刷新（后台线程）"""
        self._refreshing = True
        try:
            self.root.after(0, lambda: self.refresh_btn.config(state=tk.DISABLED, text="⏳ 刷新中..."))
            self.root.after(0, lambda: self.data_status_label.config(text="⏳ 正在刷新微信数据..."))

            def cb(stage, msg):
                # 截短消息给 UI
                short = msg[:200] + ("..." if len(msg) > 200 else "")
                if stage == "done":
                    self.root.after(0, lambda: self.data_status_label.config(text=short, fg="#27ae60"))
                    self.root.after(0, lambda: self.data_fresh_label.config(
                        text=f"最后刷新: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"))
                elif stage == "error":
                    self.root.after(0, lambda: self.data_status_label.config(text=short, fg="#e74c3c"))
                else:
                    self.root.after(0, lambda: self.data_status_label.config(text=short, fg="#3498db"))

            refresh_wechat_data(callback=cb)
        finally:
            self._refreshing = False
            self.root.after(0, lambda: self.refresh_btn.config(state=tk.NORMAL, text="🔄 立即刷新数据"))
            # 安排下一次自动刷新
            if self.auto_refresh_var.get():
                self._schedule_next_refresh()

    def _schedule_next_refresh(self):
        """5分钟后自动刷新"""
        if not self.auto_refresh_var.get():
            return
        self._refresh_timer = self.root.after(5 * 60 * 1000, self._auto_refresh_tick)

    def _auto_refresh_tick(self):
        """定时器触发：执行刷新"""
        if not self.auto_refresh_var.get():
            return
        if not self._refreshing:
            threading.Thread(target=self._do_refresh, daemon=True).start()
        # 无论是否执行，都安排下一次
        self._schedule_next_refresh()

    # ============================================================
    # Telegram Bot 控制
    # ============================================================

    def _build_bot_tab(self, parent):
        """Telegram Bot 控制选项卡"""
        # 状态面板
        status_frame = tk.LabelFrame(parent, text="Bot 状态", font=("Microsoft YaHei", 10, "bold"),
                                     bg="white", padx=10, pady=10)
        status_frame.pack(fill=tk.X, padx=10, pady=10)

        self.bot_status_var = tk.StringVar(value="未启动")
        self.bot_status_label = tk.Label(status_frame, textvariable=self.bot_status_var,
                                         font=("Microsoft YaHei", 12, "bold"),
                                         bg="white", fg="#e74c3c")
        self.bot_status_label.pack(anchor=tk.W, pady=5)

        self.bot_info_var = tk.StringVar(value="")
        tk.Label(status_frame, textvariable=self.bot_info_var,
                 font=("Microsoft YaHei", 9), bg="white", fg="#666",
                 wraplength=500, justify=tk.LEFT).pack(anchor=tk.W)

        # Bot 对话窗口
        tk.Label(parent, text="Bot 对话日志", font=("Microsoft YaHei", 10, "bold"),
                 bg="white").pack(anchor=tk.W, padx=10, pady=(15, 5))

        self.bot_log = scrolledtext.ScrolledText(
            parent, wrap=tk.WORD, font=("Consolas", 10),
            bg="#1e1e1e", fg="#d4d4d4", relief=tk.FLAT, padx=10, pady=10,
            height=12, state=tk.DISABLED,
        )
        self.bot_log.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))
        self.bot_log.tag_configure("info", foreground="#4ec9b0")
        self.bot_log.tag_configure("error", foreground="#f44747")
        self.bot_log.tag_configure("msg", foreground="#d4d4d4")

        # 控制按钮
        btn_frame = tk.Frame(parent, bg="white")
        btn_frame.pack(fill=tk.X, padx=10, pady=(0, 10))

        self.bot_start_btn = tk.Button(btn_frame, text="▶ 启动 Bot",
                                       font=("Microsoft YaHei", 10),
                                       bg="#27ae60", fg="white", relief=tk.FLAT, padx=15,
                                       command=self._start_bot)
        self.bot_start_btn.pack(side=tk.LEFT, padx=(0, 5), ipady=5)

        self.bot_stop_btn = tk.Button(btn_frame, text="■ 停止 Bot",
                                      font=("Microsoft YaHei", 10),
                                      bg="#e74c3c", fg="white", relief=tk.FLAT, padx=15,
                                      state=tk.DISABLED, command=self._stop_bot)
        self.bot_stop_btn.pack(side=tk.LEFT, padx=5, ipady=5)

        tk.Button(btn_frame, text="测试连接", font=("Microsoft YaHei", 10),
                  bg="#f39c12", fg="white", relief=tk.FLAT, padx=10,
                  command=self._test_bot_connection).pack(side=tk.LEFT, padx=5, ipady=5)

        # Bot 线程控制
        self._bot_thread = None
        self._bot_stop_event = threading.Event()
        self._bot_running = False

        # 启动时自动连接测试
        self.root.after(1000, self._test_bot_connection)

    def _append_bot_log(self, tag, text):
        """追加 Bot 日志"""
        self.bot_log.config(state=tk.NORMAL)
        self.bot_log.insert(tk.END, f"[{datetime.now().strftime('%H:%M:%S')}] {text}\n", tag)
        self.bot_log.see(tk.END)
        self.bot_log.config(state=tk.DISABLED)

    def _test_bot_connection(self):
        """测试 Telegram Bot 连接"""
        def do_test():
            import urllib.request, json
            try:
                api = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/getMe"
                resp = urllib.request.urlopen(api, timeout=10)
                data = json.loads(resp.read().decode())
                if data.get("ok"):
                    bot = data["result"]
                    info = f"✅ Bot @{bot.get('username')} - {bot.get('first_name')} 连接正常"
                    self.root.after(0, lambda: self.bot_info_var.set(info))
                    self.root.after(0, lambda: self._append_bot_log("info", info))
                else:
                    self.root.after(0, lambda: self.bot_info_var.set(f"❌ API 错误: {data}"))
            except Exception as e:
                self.root.after(0, lambda: self.bot_info_var.set(f"❌ 连接失败: {e}"))
        threading.Thread(target=do_test, daemon=True).start()

    def _bot_poll_loop(self):
        """Bot 长轮询循环"""
        import urllib.request, json

        bot_debug("===== Bot 轮询线程启动 =====")
        self._bot_running = True
        api = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}"
        offset = 0

        bot_debug(f"ChatID={config.TELEGRAM_CHAT_ID}, offset=0")
        self.root.after(0, lambda: self._append_bot_log("info", "🟢 Bot 已启动，等待消息..."))
        self.root.after(0, lambda: self.bot_status_label.config(fg="#27ae60"))

        while not self._bot_stop_event.is_set():
            try:
                url = f"{api}/getUpdates?offset={offset}&timeout=30"
                bot_debug(f"轮询: offset={offset}")
                resp = urllib.request.urlopen(url, timeout=35)
                data = json.loads(resp.read().decode())
                updates = data.get("result", [])
                bot_debug(f"getUpdates 返回: ok={data.get('ok')}, 条数={len(updates)}")

                if data.get("ok"):
                    for update in updates:
                        offset = update["update_id"] + 1
                        msg = update.get("message")
                        if msg:
                            chat_id = msg.get("chat", {}).get("id")
                            text = msg.get("text", "").strip()
                            bot_debug(f"收到: update_id={update['update_id']}, chat_id={chat_id}, text='{text[:50]}'")

                            if str(chat_id) != str(config.TELEGRAM_CHAT_ID):
                                bot_debug(f"跳过 chat_id={chat_id} != 配置={config.TELEGRAM_CHAT_ID}")
                                continue

                            if text:
                                bot_debug(f"处理消息: '{text[:50]}'")
                                self.root.after(0, lambda t=text: self._append_bot_log("msg", f"📨 {t[:100]}"))
                                self._handle_bot_message(chat_id, text, msg.get("message_id"))

            except Exception as e:
                err_str = str(e)
                bot_debug(f"异常: {type(e).__name__}: {err_str[:100]}")
                if "409" in err_str:
                    self.root.after(0, lambda: self._append_bot_log("info", "⏳ 检测到其他 Bot 实例，等待 15 秒后重试..."))
                    self._bot_stop_event.wait(15)
                else:
                    self.root.after(0, lambda e=e: self._append_bot_log("error", f"轮询错误: {e}"))
                    self._bot_stop_event.wait(5)

        self._bot_running = False
        bot_debug("Bot 轮询线程结束")
        self.root.after(0, lambda: self._append_bot_log("info", "🔴 Bot 已停止"))
        self.root.after(0, lambda: self.bot_status_label.config(fg="#e74c3c"))
    def _handle_bot_message(self, chat_id, text, msg_id):
        """响应 Bot 消息"""
        bot_debug(f"_handle_bot_message 入口: text='{text[:30]}', msg_id={msg_id}")
        import re
        import urllib.request, json

        api = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}"
        bot_debug(f"api URL OK, kw={self.monitor_keywords[:3] if self.monitor_keywords else 'EMPTY'}")

        def send_reply(reply_text):
            data = json.dumps({
                "chat_id": chat_id,
                "text": reply_text[:4000],
                "parse_mode": "HTML",
                "reply_to_message_id": msg_id,
            }).encode()
            req = urllib.request.Request(f"{api}/sendMessage", data=data,
                                         headers={"Content-Type": "application/json"})
            try:
                bot_debug(f"sendMessage: text_len={len(reply_text)}")
                resp = urllib.request.urlopen(req, timeout=10)
                body = resp.read().decode()
                bot_debug(f"sendMessage OK: HTTP {resp.status}")
                self.root.after(0, lambda: self._append_bot_log("info", f"✅ 已回复: {reply_text[:50]}"))
            except urllib.error.HTTPError as e:
                err_body = e.read().decode() if hasattr(e, 'read') else str(e)
                bot_debug(f"sendMessage FAIL HTTP {e.code}: {err_body[:200]}")
                self.root.after(0, lambda b=err_body: self._append_bot_log("error", f"发送失败 HTTP {e.code}: {b[:80]}"))
            except Exception as e:
                bot_debug(f"sendMessage 异常: {type(e).__name__}: {e}")

        def process():
            bot_debug(f"process 线程启动: text='{text[:30]}'")
            try:
                # 命令处理
                if text.startswith("/"):
                    cmd = text.split()[0].lower()
                    bot_debug(f"命令: {cmd}")
                    if cmd in ("/start", "/help"):
                        send_reply(
                            "<b>微信求购监控 Bot</b>\n\n"
                            "<b>用法：</b>\n"
                            "• 直接发关键词 → 搜最近 72 小时\n"
                            "• 关键词 + 时间 → 自定范围（<code>塔丝隆 7d</code>）\n"
                            "• <code>/recent</code> → 看最近所有求购\n"
                            "• <code>/status</code> → 系统状态\n"
                            "• <code>/keywords</code> → 监控关键词列表"
                        )
                    elif cmd == "/status":
                        fts_ok = os.path.exists(search_engine.FTS_DB)
                        send_reply(
                            f"📊 Bot 状态：🟢 运行中\n"
                            f"FTS 数据库：{'✅' if fts_ok else '❌'}\n"
                            f"监控关键词：{'、'.join(self.monitor_keywords[:6])}"
                        )
                    elif cmd == "/keywords":
                        kws = "、".join(self.monitor_keywords) if self.monitor_keywords else "（空）"
                        send_reply(f"<b>监控关键词：</b>\n{kws}")
                    elif cmd in ("/recent", "/求购", "/purchase"):
                        t0 = time.time()
                        results = search_engine.search_purchase_related(hours=168, limit=15)
                        bot_debug(f"/recent: {len(results)} 条 ({time.time()-t0:.2f}s)")
                        if not results:
                            send_reply(
                                "最近 7 天没有找到求购信息\n\n"
                                "💡 <b>提示：</b>如果数据停在 06-08，说明需要先运行 wechat-decrypt 更新微信数据库。"
                            )
                        else:
                            lines = [f"📋 最近 <b>{len(results)}</b> 条求购相关消息（7天）：\n"]
                            for i, r in enumerate(results[:15], 1):
                                c = r["content"].replace("<", "&lt;").replace(">", "&gt;")
                                if len(c) > 80:
                                    c = c[:77] + "..."
                                kw = r.get('matched_keyword', '')
                                kw_tag = f" <code>[{kw}]</code>" if kw else ""
                                lines.append(f"{i}. <b>{r['chat_name']}</b> {r['time_str']}{kw_tag}")
                                lines.append(f"   <i>{c}</i>")
                            send_reply("\n".join(lines))
                    else:
                        send_reply(f"未知命令，发 /help 查看帮助")
                    return

                # 非命令 → 搜索
                hours = 72  # 默认 72 小时
                text_local = text
                time_match = re.search(r'(\d+)\s*[hH小时]', text)
                if time_match:
                    hours = int(time_match.group(1))
                    text_local = text[:time_match.start()].strip()
                day_match = re.search(r'(\d+)\s*[dD天]', text_local)
                if day_match:
                    hours = int(day_match.group(1)) * 24
                    text_local = text_local[:day_match.start()].strip()

                if not text_local:
                    send_reply("请输入搜索关键词")
                    return

                t0 = time.time()
                results = search_engine.search_messages(text_local, hours=hours, limit=10)
                bot_debug(f"搜索 '{text_local}' ({hours}h): {len(results)} 条 ({time.time()-t0:.2f}s)")

                if not results:
                    # 友好提示：可能数据没更新
                    send_reply(
                        f"没有找到包含「{text_local}」的消息\n\n"
                        f"💡 已搜最近 {hours} 小时。如果都搜不到，可能是：\n"
                        f"• 关键词太短（如 '胃'）\n"
                        f"• 数据未更新（运行 wechat-decrypt）\n"
                        f"试试 <code>/recent</code> 看求购汇总"
                    )
                else:
                    lines = [f"📋 找到 {len(results)} 条「{text_local}」（{hours}h）：\n"]
                    for i, r in enumerate(results[:10], 1):
                        c = r["content"].replace("<", "&lt;").replace(">", "&gt;")[:80]
                        lines.append(f"{i}. <b>{r['chat_name']}</b> {r['time_str']}")
                        lines.append(f"   <i>{c}</i>")
                    send_reply("\n".join(lines))
            except Exception as e:
                bot_debug(f"process 异常: {type(e).__name__}: {e}")
                import traceback
                bot_debug(f"traceback: {traceback.format_exc()[:500]}")

        bot_debug("启动 process 线程")
        threading.Thread(target=process, daemon=True).start()

    def _start_bot(self):
        """启动 Bot"""
        if self._bot_running:
            return
        self._bot_stop_event.clear()
        self.bot_status_var.set("运行中")
        self.bot_start_btn.config(state=tk.DISABLED)
        self.bot_stop_btn.config(state=tk.NORMAL)
        self._bot_thread = threading.Thread(target=self._bot_poll_loop, daemon=True)
        self._bot_thread.start()

    def _stop_bot(self):
        """停止 Bot"""
        if not self._bot_running:
            return
        self._bot_stop_event.set()
        self.bot_status_var.set("正在停止...")
        self.bot_start_btn.config(state=tk.NORMAL)
        self.bot_stop_btn.config(state=tk.DISABLED)


# ============================================================
# 入口
# ============================================================

def _create_tray_icon(color="green"):
    """生成托盘图标"""
    from PIL import Image, ImageDraw, ImageFont
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    bg_color = (46, 204, 113, 255) if color == "green" else (149, 165, 166, 255)
    draw.ellipse([4, 4, size - 4, size - 4], fill=bg_color)
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 28)
    except Exception:
        try:
            font = ImageFont.truetype("C:/Windows/Fonts/simhei.ttf", 28)
        except Exception:
            font = ImageFont.load_default()
    text = "购"
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = (size - tw) / 2
    y = (size - th) / 2 - 2
    draw.text((x, y), text, fill=(255, 255, 255, 255), font=font)
    return img


def main():
    import pystray
    import threading

    root = tk.Tk()

    # 设置 DPI 感知（Windows 高分屏）
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    # 设置样式
    style = ttk.Style()
    try:
        style.theme_use("clam")
    except Exception:
        pass

    app = WechatMonitorApp(root)

    # 系统托盘
    tray_icon = _create_tray_icon("green")
    tray_stopped = _create_tray_icon("gray")

    def on_show(icon, item):
        root.deiconify()
        root.lift()
        root.focus_force()

    def on_hide(icon, item):
        root.withdraw()

    def on_quit_app(icon, item):
        app._stop_bot()
        icon.stop()
        root.quit()
        root.destroy()

    tray_menu = pystray.Menu(
        pystray.MenuItem("微信求购监控 v1.1", None, enabled=False),
        pystray.MenuItem("显示窗口", on_show, default=True),
        pystray.MenuItem("隐藏到托盘", on_hide),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("退出", on_quit_app),
    )

    tray = pystray.Icon("wechat-monitor", icon=tray_icon, title="微信求购监控", menu=tray_menu)

    # 关闭窗口时缩到托盘
    def on_closing():
        root.withdraw()
        tray.notify("微信求购监控仍在后台运行", "程序已最小化到系统托盘")

    root.protocol("WM_DELETE_WINDOW", on_closing)

    # 在后台线程启动托盘
    threading.Thread(target=tray.run, daemon=True).start()

    root.mainloop()


if __name__ == "__main__":
    main()
