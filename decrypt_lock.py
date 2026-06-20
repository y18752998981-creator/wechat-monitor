"""微信求购监控系统 - 解密锁

防止多个进程同时调用 wechat-decrypt 解密：
  - 文件锁：同一时间只有一个进程能跑 decrypt
  - 时间间隔：5 分钟内不重复解密
  - 静默运行：Windows 上 CREATE_NO_WINDOW，不弹黑框
"""
import os
import sys
import time
import subprocess
import config

LOCK_FILE = os.path.join(config.DATA_DIR, ".decrypt.lock")
LAST_DECRYPT_FILE = os.path.join(config.DATA_DIR, ".decrypt_last_time")
MIN_INTERVAL = 300  # 最少间隔 5 分钟


def _check_interval():
    """检查距离上次解密是否已过最小间隔"""
    if not os.path.exists(LAST_DECRYPT_FILE):
        return True
    try:
        with open(LAST_DECRYPT_FILE, "r") as f:
            last_ts = float(f.read().strip())
        return (time.time() - last_ts) >= MIN_INTERVAL
    except Exception:
        return True


def _save_last_time():
    """记录本次解密时间"""
    os.makedirs(os.path.dirname(LAST_DECRYPT_FILE), exist_ok=True)
    with open(LAST_DECRYPT_FILE, "w") as f:
        f.write(str(time.time()))


def _acquire_lock(timeout=5):
    """尝试获取文件锁，返回 True 表示成功"""
    os.makedirs(os.path.dirname(LOCK_FILE), exist_ok=True)
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            # 用创建文件作为锁（O_CREAT | O_EXCL = 原子操作）
            fd = os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            return True
        except FileExistsError:
            # 检查持锁进程是否还活着
            try:
                with open(LOCK_FILE, "r") as f:
                    pid = int(f.read().strip())
                # Windows 上用 tasklist 检查进程
                r = subprocess.run(
                    ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                    capture_output=True, text=True, timeout=5,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                )
                if str(pid) not in r.stdout:
                    # 进程已死，清理旧锁
                    os.remove(LOCK_FILE)
                    continue
            except Exception:
                pass
            time.sleep(1)
    return False


def _release_lock():
    """释放文件锁"""
    try:
        os.remove(LOCK_FILE)
    except Exception:
        pass


def run_decrypt(python_exe=None, timeout=180):
    """
    安全地调用 wechat-decrypt 解密

    特性：
      - 文件锁防并发
      - 时间间隔防频繁调用
      - CREATE_NO_WINDOW 不弹黑框

    Args:
        python_exe: Python 解释器路径（None 则用 sys.executable）
        timeout: 超时秒数

    Returns:
        (bool, str): (是否成功, 输出/错误信息)
    """
    # 检查时间间隔
    if not _check_interval():
        return True, "距上次解密不足5分钟，跳过"

    # 获取锁
    if not _acquire_lock(timeout=10):
        return False, "另一个进程正在解密，跳过"

    try:
        decrypt_path = config.WECHAT_DECRYPT_PATH
        main_py = os.path.join(decrypt_path, "main.py")

        if not os.path.exists(main_py):
            return False, f"找不到 {main_py}"

        py = python_exe or sys.executable

        # Windows 上隐藏控制台窗口
        kwargs = {
            "capture_output": True,
            "text": True,
            "timeout": timeout,
            "cwd": decrypt_path,
        }
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

        result = subprocess.run(
            [py, main_py, "decrypt"],
            **kwargs,
        )

        _save_last_time()

        if result.returncode == 0:
            return True, "解密成功"
        else:
            err = (result.stderr or "")[:500]
            return False, f"解密失败 (code {result.returncode}): {err}"

    except subprocess.TimeoutExpired:
        return False, f"解密超时 ({timeout}s)"
    except Exception as e:
        return False, f"解密异常: {e}"
    finally:
        _release_lock()
