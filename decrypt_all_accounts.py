"""解密所有微信账号的数据库

用法：
  1. 先登录你要解密的微信号（只需登录一次，提取密钥后会缓存）
  2. 运行此脚本：python decrypt_all_accounts.py
  3. 脚本自动扫描所有账号，有密钥的直接解密，没密钥的跳过并提示

密钥提取后永久缓存，以后不需要微信在运行也能解密。
"""
import sys, os, io, json, subprocess

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

sys.path.insert(0, r"C:\Users\y1875\Desktop\wechat-monitor")
os.chdir(r"C:\Users\y1875\Desktop\wechat-monitor")

import config

WECHAT_DECRYPT = config.WECHAT_DECRYPT_PATH
XWECHAT_FILES = config.WECHAT_ACCOUNTS_DIR

# Find a working Python
PYTHON_EXE = r"C:\Users\y1875\AppData\Local\Programs\Python\Python311\python.exe"

print("=" * 60)
print("  微信多账号解密工具")
print("=" * 60)

# Check which WeChat is currently running
print("\n[1] 检查微信进程...")
try:
    r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Weixin.exe", "/NH"],
                       capture_output=True, text=True, timeout=5)
    wechat_running = "Weixin.exe" in r.stdout
    print(f"    Weixin.exe: {'运行中' if wechat_running else '未运行'}")
except:
    wechat_running = False

# Scan accounts
print(f"\n[2] 扫描账号目录: {XWECHAT_FILES}")
accounts = []
skip_dirs = {"all_users", "Backup"}

for entry in os.listdir(XWECHAT_FILES):
    full_path = os.path.join(XWECHAT_FILES, entry)
    if not os.path.isdir(full_path) or entry in skip_dirs:
        continue
    db_storage = os.path.join(full_path, "db_storage")
    if not os.path.isdir(db_storage):
        continue
    msg_dir = os.path.join(db_storage, "message")
    db_count = len([f for f in os.listdir(msg_dir) if f.endswith(".db")]) if os.path.isdir(msg_dir) else 0
    accounts.append({
        "dir_name": entry,
        "db_storage": db_storage,
        "msg_dir": msg_dir,
        "db_count": db_count,
    })

print(f"    发现 {len(accounts)} 个微信号:")
for a in accounts:
    print(f"    - {a['dir_name']}: {a['db_count']} 个数据库")

# Check keys for each account
print(f"\n[3] 检查密钥状态...")
for a in accounts:
    # Find matching config entry
    acct_config = None
    for ac in config.WECHAT_ACCOUNTS:
        if ac["name"] in a["dir_name"]:
            acct_config = ac
            break

    if not acct_config:
        a["keys_file"] = os.path.join(WECHAT_DECRYPT, f"keys_{a['dir_name'].split('_')[0]}.json")
        a["decrypted_dir"] = os.path.join(WECHAT_DECRYPT, f"decrypted_{a['dir_name'].split('_')[0]}")
    else:
        a["keys_file"] = acct_config["keys_file"]
        a["decrypted_dir"] = acct_config["decrypted_dir"]

    has_keys = os.path.exists(a["keys_file"]) and os.path.getsize(a["keys_file"]) > 10
    has_decrypted = os.path.isdir(a["decrypted_dir"])
    a["has_keys"] = has_keys
    a["has_decrypted"] = has_decrypted
    print(f"    {a['dir_name']}:")
    print(f"      密钥: {'已提取' if has_keys else '未提取'}")
    print(f"      已解密: {'是' if has_decrypted else '否'}")

# Decrypt each account
print(f"\n[4] 开始解密...")
for a in accounts:
    print(f"\n    === {a['dir_name']} ===")

    if not a["has_keys"]:
        if not wechat_running:
            print(f"    ⚠️  密钥未提取，且微信未运行。")
            print(f"    请先登录这个微信号，然后重新运行此脚本。")
            continue
        print(f"    密钥未提取，需要先提取密钥（微信已在运行，尝试提取）...")

    # Create a temporary config.json for this account
    temp_config = {
        "db_dir": a["db_storage"],
        "keys_file": a["keys_file"],
        "decrypted_dir": a["decrypted_dir"],
        "wechat_process": "Weixin.exe",
    }

    config_path = os.path.join(WECHAT_DECRYPT, "config.json")
    # Backup original config
    backup_path = os.path.join(WECHAT_DECRYPT, "config.json.bak")
    if os.path.exists(config_path) and not os.path.exists(backup_path):
        import shutil
        shutil.copy2(config_path, backup_path)

    # Write temp config
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(temp_config, f, indent=2, ensure_ascii=False)

    # Run decrypt
    print(f"    运行: python main.py decrypt")
    try:
        result = subprocess.run(
            [PYTHON_EXE, "main.py", "decrypt"],
            capture_output=True, text=True, timeout=300,
            cwd=WECHAT_DECRYPT,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        if result.returncode == 0:
            # Count decrypted files
            dec_count = 0
            if os.path.isdir(a["decrypted_dir"]):
                for root, dirs, files in os.walk(a["decrypted_dir"]):
                    dec_count += len([f for f in files if f.endswith(".db")])
            print(f"    ✅ 解密成功! {dec_count} 个数据库")
            a["has_decrypted"] = True
            a["has_keys"] = True
        else:
            err = (result.stderr or result.stdout or "")[:200]
            print(f"    ❌ 解密失败 (code {result.returncode}): {err}")
    except subprocess.TimeoutExpired:
        print(f"    ❌ 解密超时")
    except Exception as e:
        print(f"    ❌ 异常: {e}")

# Restore original config
backup_path = os.path.join(WECHAT_DECRYPT, "config.json.bak")
if os.path.exists(backup_path):
    import shutil
    config_path = os.path.join(WECHAT_DECRYPT, "config.json")
    shutil.copy2(backup_path, config_path)
    os.remove(backup_path)
    print(f"\n[5] 已恢复原始 config.json")

# Summary
print(f"\n{'=' * 60}")
print(f"  解密结果汇总")
print(f"{'=' * 60}")
for a in accounts:
    status = "✅" if a.get("has_decrypted") else "❌"
    print(f"  {status} {a['dir_name']}: {a['db_count']} 个数据库")
    if not a.get("has_keys"):
        print(f"     → 需要登录此微信号后重新运行脚本")

print(f"\n完成后，监控系统会自动读取所有账号的解密数据。")
