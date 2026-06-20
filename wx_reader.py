"""微信求购监控系统 - 微信数据库读取模块 (wechat-decrypt 版)

支持微信4.x版本，通过 wechat-decrypt 解密后的 SQLite 数据库读取消息。

前置条件:
  1. 安装 wechat-decrypt:
     git clone https://github.com/ylytdeng/wechat-decrypt.git
     cd wechat-decrypt && pip install -r requirements.txt

  2. 解密数据库 (微信需保持登录):
     cd wechat-decrypt
     python main.py decrypt
"""
import os
import json
import sqlite3
import re
from datetime import datetime, timedelta
import config

# 记录上次读取的位置，避免重复处理
LAST_READ_FILE = os.path.join(config.DATA_DIR, "last_read.json")

# 已处理过的消息去重集合（用消息 hash）
_seen_message_hashes = set()

# 缓存：表名 -> 群名/联系人名
_table_name_cache = None


def load_last_read():
    """加载上次读取位置"""
    if os.path.exists(LAST_READ_FILE):
        with open(LAST_READ_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_last_read(data):
    """保存读取位置"""
    os.makedirs(os.path.dirname(LAST_READ_FILE), exist_ok=True)
    with open(LAST_READ_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ============================================================
# 联系人/群名映射
# ============================================================

def _get_all_decrypted_dirs():
    """获取所有账号的解密目录列表"""
    dirs = []
    primary = config.WECHAT_DECRYPT_DB_DIR
    if os.path.isdir(primary):
        dirs.append(primary)
    for acct in getattr(config, "WECHAT_ACCOUNTS", []):
        dec_dir = acct.get("decrypted_dir", "")
        if dec_dir and os.path.isdir(dec_dir) and dec_dir != primary:
            dirs.append(dec_dir)
    return dirs


def _load_contact_map():
    """
    从所有账号的联系人数据库加载 username -> display_name 映射

    Returns:
        (name_map, is_group):
        - name_map: dict, username -> 显示名 (备注 > 昵称 > username)
        - is_group: dict, username -> bool
    """
    name_map = {}
    is_group = {}

    for db_base in _get_all_decrypted_dirs():
        contact_db = os.path.join(db_base, "contact", "contact.db")
        if not os.path.exists(contact_db):
            continue

        try:
            conn = sqlite3.connect(contact_db)
            cursor = conn.cursor()

            # 读取联系人 (微信4.0列名: username, nick_name, remark)
            cursor.execute("SELECT username, nick_name, remark FROM contact")
            for row in cursor.fetchall():
                username, nickname, remark = row
                if username:
                    name_map[username] = remark or nickname or username
                    if username.endswith("@chatroom"):
                        is_group[username] = True

            # 从 chat_room_info_detail 获取群名（微信4.0列名带下划线后缀）
            try:
                cursor.execute("SELECT username_, room_id_ FROM chat_room_info_detail")
                for row in cursor.fetchall():
                    username, room_id = row
                    if username and username.endswith("@chatroom"):
                        is_group[username] = True
            except sqlite3.OperationalError:
                pass

            # 从 chat_room 表补充，并解析 ext_buffer 中的群成员信息
            try:
                cursor.execute("SELECT id, username, owner, ext_buffer FROM chat_room")
                for room_id, username, owner, ext_buffer in cursor.fetchall():
                    if username:
                        is_group[username] = True
                        if username not in name_map:
                            name_map[username] = username
                    if ext_buffer and isinstance(ext_buffer, bytes):
                        _parse_chatroom_members(ext_buffer, name_map)
            except sqlite3.OperationalError:
                pass

            # 从 SessionTable 获取群显示名
            session_db = os.path.join(db_base, "session", "session.db")
            if os.path.exists(session_db):
                try:
                    sconn = sqlite3.connect(session_db)
                    scursor = sconn.cursor()
                    scursor.execute("""
                        SELECT username, last_sender_display_name, summary
                        FROM SessionTable
                        WHERE username LIKE '%@chatroom'
                    """)
                    for username, display_name, summary in scursor.fetchall():
                        if username and username.endswith("@chatroom"):
                            is_group[username] = True
                            if username not in name_map or name_map[username] == username:
                                name_map[username] = display_name or username
                    sconn.close()
                except Exception:
                    pass

            conn.close()
        except Exception as e:
            print(f"[Reader] 加载联系人数据失败 ({db_base}): {e}")

    return name_map, is_group


def _build_table_to_chat_map():
    """
    建立消息表名 -> 聊天对象 的映射（支持多账号）
    """
    global _table_name_cache
    if _table_name_cache is not None:
        return _table_name_cache

    table_map = {}
    name_map, is_group = _load_contact_map()

    for db_dir in _get_all_decrypted_dirs():
        resource_db = os.path.join(db_dir, "message", "message_resource.db")
        msg_db = os.path.join(db_dir, "message", "message_0.db")

        if not os.path.exists(resource_db) or not os.path.exists(msg_db):
            continue

        try:
            rconn = sqlite3.connect(resource_db)
            rcursor = rconn.cursor()

            chat_id_to_name = {}
            rcursor.execute("SELECT rowid, user_name FROM ChatName2Id")
            for rowid, user_name in rcursor.fetchall():
                chat_id_to_name[rowid] = user_name

            chat_id_samples = {}
            rcursor.execute("""
                SELECT chat_id, message_local_id, message_create_time
                FROM MessageResourceInfo
                WHERE message_local_type = 1
                ORDER BY message_id DESC
            """)
            for chat_id, local_id, create_time in rcursor.fetchall():
                if chat_id not in chat_id_samples and local_id > 0:
                    chat_id_samples[chat_id] = (local_id, create_time)
            rconn.close()

            mconn = sqlite3.connect(msg_db)
            mcursor = mconn.cursor()
            mcursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%'")
            msg_tables = [r[0] for r in mcursor.fetchall()]

            matched_tables = set()
            for chat_id, (local_id, create_time) in chat_id_samples.items():
                if chat_id not in chat_id_to_name:
                    continue
                user_name = chat_id_to_name[chat_id]
                for table in msg_tables:
                    if table in matched_tables:
                        continue
                    try:
                        mcursor.execute(
                            f"SELECT 1 FROM [{table}] WHERE local_id = ? AND create_time = ? LIMIT 1",
                            (local_id, create_time)
                        )
                        if mcursor.fetchone():
                            display_name = name_map.get(user_name, user_name)
                            table_map[table] = (user_name, display_name)
                            matched_tables.add(table)
                            break
                    except:
                        continue

            session_db = os.path.join(db_dir, "session", "session.db")
            if os.path.exists(session_db) and len(matched_tables) < len(msg_tables):
                try:
                    sconn = sqlite3.connect(session_db)
                    scursor = sconn.cursor()
                    scursor.execute("""
                        SELECT username, last_msg_locald_id, last_timestamp
                        FROM SessionTable
                        WHERE last_msg_locald_id > 0
                    """)
                    session_samples = scursor.fetchall()
                    sconn.close()

                    for username, local_id, ts in session_samples:
                        if not local_id or not ts:
                            continue
                        for table in msg_tables:
                            if table in matched_tables:
                                continue
                            try:
                                mcursor.execute(
                                    f"SELECT 1 FROM [{table}] WHERE local_id = ? AND create_time = ? LIMIT 1",
                                    (local_id, ts)
                                )
                                if mcursor.fetchone():
                                    display_name = name_map.get(username, username)
                                    table_map[table] = (username, display_name)
                                    matched_tables.add(table)
                                    break
                            except:
                                continue
                except Exception:
                    pass

            mconn.close()
        except Exception as e:
            print(f"[Reader] 建立表名映射失败 ({db_dir}): {e}")

    total_tables = sum(1 for t in table_map)
    print(f"[Reader] 表名映射: {total_tables} 个表已识别 (多账号合计)")

    _table_name_cache = table_map
    return table_map


# ============================================================
# 微信 4.0 数据库读取
# ============================================================

def _find_decrypted_databases():
    """查找所有微信账号解密后的消息数据库"""
    db_files = []

    # 所有账号的解密目录
    all_decrypted_dirs = []

    # 主账号（向后兼容）
    primary = config.WECHAT_DECRYPT_DB_DIR
    if os.path.isdir(primary):
        all_decrypted_dirs.append(("primary", primary))

    # 多账号
    for acct in getattr(config, "WECHAT_ACCOUNTS", []):
        dec_dir = acct.get("decrypted_dir", "")
        if dec_dir and os.path.isdir(dec_dir) and dec_dir != primary:
            all_decrypted_dirs.append((acct["name"], dec_dir))

    for acct_name, dec_dir in all_decrypted_dirs:
        msg_dir = os.path.join(dec_dir, "message")
        if os.path.exists(msg_dir):
            for f in os.listdir(msg_dir):
                if f.endswith(".db"):
                    db_files.append(os.path.join(msg_dir, f))

    return db_files


def _parse_chatroom_members(ext_buffer, name_map):
    """
    解析 chat_room.ext_buffer 中的群成员 wxid→显示名映射

    ext_buffer 是 protobuf 编码，每个成员记录包含:
      field 1 (bytes): wxid/username
      field 2 (bytes): 群内昵称/显示名
      field 3 (int): 角色标记
      field 4 (bytes): 引用ID

    解析后将 wxid→显示名写入 name_map（仅在 wxid 尚无显示名时写入）
    """
    if not ext_buffer:
        return

    data = ext_buffer
    i = 0
    while i < len(data) - 3:
        # 找 wxid_ 或其他 ID 格式
        match = re.search(
            rb'((?:wxid_[a-z0-9]+|[a-zA-Z][a-zA-Z0-9_]{3,}|[\d]+@openim))',
            data[i:]
        )
        if not match:
            break
        wxid = match.group(1).decode('ascii', errors='ignore')
        pos = i + match.end()

        # 后面期望 \x12<len><utf8 nickname>
        if pos < len(data) - 2 and data[pos] == 0x12:
            nick_len = data[pos + 1]
            if nick_len > 0 and pos + 2 + nick_len <= len(data):
                try:
                    nickname = data[pos + 2:pos + 2 + nick_len].decode('utf-8')
                    # 只在该 wxid 尚无更好显示名时写入
                    if wxid not in name_map or name_map[wxid] == wxid:
                        if nickname and not nickname.startswith('wxid_'):
                            name_map[wxid] = nickname
                except (UnicodeDecodeError, IndexError):
                    pass
        i = max(pos, i + 1)


def _parse_group_message_content(content):
    """
    解析群聊消息内容，提取发送者标识和实际消息

    微信 4.0 群聊消息格式:
      "[二进制前缀]发送者标识:\n消息内容"
      其中二进制前缀以 "(/" 开头，包含 null 字节等不可见字符

    私聊消息格式: "消息内容" (无发送者前缀)

    Returns:
        (sender_identifier, actual_content)
        sender_identifier 通常是 wxid 或自定义用户名
    """
    if not content:
        return "", ""

    # Step 1: 剥离可能的二进制前缀
    # 微信 4.0 群消息常以 "(/\x60..." 等二进制数据开头
    # 找到第一个可见字符位置（跳过 null 字节和控制字符）
    clean = _strip_binary_prefix(content)

    # Step 2: 尝试匹配 "标识:\n内容" 格式
    match = re.match(r'^([^:\n]{1,60}):\n(.*)$', clean, re.DOTALL)
    if match:
        sender_id = match.group(1).strip()
        actual = match.group(2)
        # 清理 sender_id 中可能的残留二进制
        sender_id = _clean_sender_id(sender_id)
        return sender_id, actual

    # 尝试匹配 "标识:\r\n内容" 格式
    match = re.match(r'^([^:\r\n]{1,60}):\r\n(.*)$', clean, re.DOTALL)
    if match:
        sender_id = match.group(1).strip()
        actual = match.group(2)
        sender_id = _clean_sender_id(sender_id)
        return sender_id, actual

    # 没有匹配到前缀格式，返回清理后的原始内容
    return "", clean


def _strip_binary_prefix(content):
    """
    剥离微信 4.0 群消息中的二进制前缀

    典型格式: "(/\x60\x35\x00\x05\x09\x00\x04\x11wxid_xxx:\n内容"
    前缀以 "(/" 开始，包含 null 字节，后面跟着可读的 sender 标识
    """
    if not content:
        return content

    # 如果内容直接以可读字符开头（无二进制前缀），直接返回
    if content[0] not in '(/' or (len(content) > 2 and content[2] not in '`^~!@#$%&*'):
        # 不以 "(/" 开头，或第三个字符不是特殊符号
        # 可能是普通消息
        if '\x00' not in content[:60]:
            return content

    # 找到二进制前缀的结束位置
    # 策略：找到第一个 ":" 字符，它前面的就是 sender（可能混有二进制垃圾）
    colon_pos = content.find(':\n')
    if colon_pos == -1:
        colon_pos = content.find(':\r\n')
    if colon_pos == -1 or colon_pos > 80:
        return content

    # 从 colon_pos 往前找到 sender 标识的开始
    # sender 通常是 wxid_xxx、qq123456、英文名等 ASCII 可读字符
    sender_start = colon_pos
    for i in range(colon_pos - 1, -1, -1):
        ch = content[i]
        # 可打印 ASCII 字符或中文
        if (32 <= ord(ch) <= 126) or ord(ch) > 127:
            sender_start = i
        else:
            # 遇到控制字符/null，sender 从这里之后开始
            sender_start = i + 1
            break

    # 提取 sender 标识和消息内容
    sender_id = content[sender_start:colon_pos]
    if '\n' in content[colon_pos:colon_pos + 3]:
        msg_content = content[colon_pos + 2:]  # skip ":\n"
    else:
        msg_content = content[colon_pos + 3:]  # skip ":\r\n"

    return sender_id + ':\n' + msg_content


def _clean_sender_id(sender_id):
    """
    清理 sender 标识中可能的残留二进制字符

    保留: ASCII 字母数字、下划线、@、中文等可读字符
    去除: null 字节、控制字符
    """
    if not sender_id:
        return ""

    # 去除 null 字节和控制字符（保留空格、tab等）
    cleaned = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', sender_id)

    # 如果清理后包含 wxid_ 或其他已知模式，提取它
    match = re.search(r'(wxid_[a-z0-9]+)', cleaned)
    if match:
        return match.group(1)

    match = re.search(r'([a-zA-Z][a-zA-Z0-9_@.]{3,})', cleaned)
    if match:
        return match.group(1)

    match = re.search(r'(\d+@openim)', cleaned)
    if match:
        return match.group(1)

    return cleaned.strip()


def _resolve_sender_name(sender_raw, name_map):
    """
    将群消息中提取到的 sender (通常是 wxid) 转换为显示名

    查找优先级:
      1. name_map 中精确匹配 (来自 contact 表 + chat_room ext_buffer)
      2. 如果 sender_raw 本身就是可读名称 (非 wxid 开头)，直接返回
      3. 兜底返回 sender_raw

    Args:
        sender_raw: 从消息内容中提取的发送者标识 (如 wxid_xxx, qq123456 等)
        name_map: wxid/username 到显示名的映射

    Returns:
        str: 显示名
    """
    if not sender_raw:
        return ""

    # 1. 精确匹配 name_map
    if sender_raw in name_map:
        resolved = name_map[sender_raw]
        if resolved and resolved != sender_raw:
            return resolved

    # 2. 如果 sender_raw 不像 wxid（非 wxid_ 开头、非纯数字@openim），
    #    说明已经是可读名称，直接返回
    if not sender_raw.startswith('wxid_') and not sender_raw.endswith('@openim'):
        # 可能是 qq123456、fl1992harbin 等可读 ID，直接作为显示名
        return sender_raw

    # 3. 兜底返回原始值
    return sender_raw


def _is_group_table(table_name, table_map):
    """判断一个消息表是否属于群聊"""
    if table_name in table_map:
        chat_username = table_map[table_name][0]
        return chat_username.endswith("@chatroom")
    return False


def _read_message_table(db_path, table_name, since_timestamp, table_map, name_map=None):
    """
    从单个消息表读取消息

    Args:
        db_path: 数据库文件路径
        table_name: 表名 (Msg_xxx)
        since_timestamp: 只读取此时间戳之后的消息
        table_map: 表名到聊天对象的映射
        name_map: wxid/username 到显示名的映射（用于群消息发送者解析）

    Returns:
        list of dict: 标准化消息列表
    """
    if name_map is None:
        name_map = {}
    messages = []

    # 获取聊天对象信息
    chat_username, chat_display_name = table_map.get(table_name, ("", table_name))
    is_group = chat_username.endswith("@chatroom") if chat_username else False

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # 查询消息
        query = """
            SELECT local_id, local_type, create_time, message_content
            FROM [{table}]
            WHERE create_time > ?
            ORDER BY create_time ASC
        """.format(table=table_name)

        cursor.execute(query, (since_timestamp,))
        rows = cursor.fetchall()

        for row in rows:
            local_id, local_type, create_time, content = row

            # 转换时间戳
            if isinstance(create_time, (int, float)) and create_time > 1000000000:
                dt = datetime.fromtimestamp(create_time)
                time_str = dt.strftime("%Y-%m-%d %H:%M:%S")
            else:
                time_str = str(create_time) if create_time else ""

            # 处理文本消息 (local_type=1)
            if local_type == 1 and content:
                # 确保 content 是字符串
                if isinstance(content, bytes):
                    try:
                        content = content.decode('utf-8', errors='ignore')
                    except:
                        continue

                # 解析群消息中的发送者
                if is_group:
                    sender_raw, actual_content = _parse_group_message_content(content)
                    # 将 wxid/username 转换为显示名
                    sender_display = _resolve_sender_name(sender_raw, name_map)
                    sender_wxid = sender_raw
                else:
                    sender_display = chat_display_name
                    actual_content = content
                    sender_wxid = chat_username

                # 消息去重
                msg_hash = hash(f"{db_path}:{table_name}:{create_time}:{actual_content[:30]}")
                if msg_hash in _seen_message_hashes:
                    continue
                _seen_message_hashes.add(msg_hash)

                messages.append({
                    "text": actual_content,
                    "group_name": chat_display_name if is_group else "",
                    "sender_name": sender_display,
                    "sender_wxid": sender_wxid,
                    "timestamp": time_str,
                    "msg_type": "text",
                    "image_path": "",
                    "raw_content": content,
                })

            # 图片消息 (local_type=3)
            elif local_type == 3:
                msg_hash = hash(f"{db_path}:{table_name}:{create_time}:image")
                if msg_hash in _seen_message_hashes:
                    continue
                _seen_message_hashes.add(msg_hash)

                messages.append({
                    "text": "",
                    "group_name": chat_display_name if is_group else "",
                    "sender_name": "",
                    "sender_wxid": "",
                    "timestamp": time_str,
                    "msg_type": "image",
                    "image_path": "",  # 微信4.0图片需要额外解密
                    "raw_content": "",
                })

        conn.close()

    except Exception as e:
        pass  # 忽略单个表的错误

    return messages


def read_decrypted_databases(since_minutes=10):
    """
    从 wechat-decrypt 解密后的数据库读取消息

    Args:
        since_minutes: 只读取最近N分钟的消息

    Returns:
        list of dict: 标准化消息列表
    """
    messages = []
    db_files = _find_decrypted_databases()

    if not db_files:
        print(f"[Reader] 未找到解密后的数据库文件")
        print(f"[Reader] 请先运行: cd {config.WECHAT_DECRYPT_PATH} && python main.py decrypt")
        return messages

    # 计算时间阈值
    if since_minutes:
        threshold = datetime.now() - timedelta(minutes=since_minutes)
        since_timestamp = int(threshold.timestamp())
    else:
        since_timestamp = 0

    # 构建表名映射
    table_map = _build_table_to_chat_map()

    # 加载联系人映射（包含群成员 wxid→显示名）
    name_map, _ = _load_contact_map()

    for db_path in db_files:
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()

            # 获取所有消息表
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [r[0] for r in cursor.fetchall()]
            msg_tables = [t for t in tables if t.startswith("Msg_")]

            conn.close()

            # 读取每个消息表
            for table in msg_tables:
                msgs = _read_message_table(db_path, table, since_timestamp, table_map, name_map)
                messages.extend(msgs)

        except Exception as e:
            print(f"[Reader] 读取数据库失败 {db_path}: {e}")

    if messages:
        print(f"[Reader] 从解密数据库读取 {len(messages)} 条消息")

    return messages


# ============================================================
# 导出文件读取（兼容模式）
# ============================================================

def read_exported_messages(since_minutes=None):
    """读取 wechat-decrypt 导出的 JSON/CSV 消息文件"""
    messages = []
    export_dir = config.WECHAT_DECRYPT_EXPORT_DIR

    if not os.path.exists(export_dir):
        return messages

    for filename in os.listdir(export_dir):
        filepath = os.path.join(export_dir, filename)

        if filename.endswith(".csv"):
            messages.extend(_read_csv_messages(filepath, since_minutes))
        elif filename.endswith(".json"):
            messages.extend(_read_json_messages(filepath, since_minutes))

    if messages:
        print(f"[Reader] 从导出文件读取 {len(messages)} 条消息")
    return messages


def _read_csv_messages(filepath, since_minutes=None):
    """从 CSV 文件读取消息"""
    import csv
    messages = []
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                msg = _normalize_exported_message(row)
                if msg and _is_recent(msg, since_minutes):
                    messages.append(msg)
    except Exception as e:
        print(f"[Reader] CSV读取错误 {filepath}: {e}")
    return messages


def _read_json_messages(filepath, since_minutes=None):
    """从 JSON 文件读取消息"""
    messages = []
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            items = data.get("messages", data.get("data", data.get("records", [])))
        else:
            items = []

        for item in items:
            msg = _normalize_exported_message(item)
            if msg and _is_recent(msg, since_minutes):
                messages.append(msg)
    except Exception as e:
        print(f"[Reader] JSON读取错误 {filepath}: {e}")
    return messages


def _normalize_exported_message(raw):
    """标准化导出格式的消息"""
    msg = {
        "text": "",
        "group_name": "",
        "sender_name": "",
        "timestamp": "",
        "msg_type": "text",
        "image_path": "",
    }

    # 尝试不同的字段名
    for k in ["content", "message", "msg", "text", "strContent", "message_content"]:
        if k in raw and raw[k]:
            msg["text"] = str(raw[k])
            break

    for k in ["sender", "nickname", "sender_name", "talker"]:
        if k in raw and raw[k]:
            msg["sender_name"] = str(raw[k])
            break

    for k in ["group", "group_name", "chat_name", "chatroom"]:
        if k in raw and raw[k]:
            msg["group_name"] = str(raw[k])
            break

    for k in ["time", "timestamp", "create_time", "sendTime"]:
        if k in raw and raw[k]:
            val = raw[k]
            if isinstance(val, (int, float)) and val > 1000000000:
                msg["timestamp"] = datetime.fromtimestamp(val).strftime("%Y-%m-%d %H:%M:%S")
            else:
                msg["timestamp"] = str(val)
            break

    for k in ["type", "msg_type"]:
        if k in raw:
            t = str(raw[k]).lower()
            if "image" in t or t == "3":
                msg["msg_type"] = "image"
            break

    return msg if msg["text"] or msg["msg_type"] == "image" else None


# ============================================================
# 触发解密
# ============================================================

def trigger_wechat_decrypt():
    """调用 wechat-decrypt 执行解密（使用共享锁，不弹黑框）"""
    import decrypt_lock

    print("[Reader] 正在调用 wechat-decrypt 解密...")
    ok, msg = decrypt_lock.run_decrypt()
    if ok:
        print(f"[Reader] wechat-decrypt: {msg}")
    else:
        print(f"[Reader] wechat-decrypt 失败: {msg}")
    return ok


# ============================================================
# 工具函数
# ============================================================

def _is_recent(msg, since_minutes):
    """检查消息是否在指定时间范围内"""
    if not since_minutes:
        return True

    ts = msg.get("timestamp", "")
    if not ts:
        return True

    try:
        if isinstance(ts, str):
            for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S"]:
                try:
                    dt = datetime.strptime(ts, fmt)
                    break
                except ValueError:
                    continue
            else:
                return True
        else:
            dt = datetime.fromtimestamp(ts)

        threshold = datetime.now() - timedelta(minutes=since_minutes)
        return dt >= threshold
    except:
        return True


# ============================================================
# 主入口
# ============================================================

def get_new_messages(since_minutes=10):
    """
    主入口：获取新的微信消息

    自动尝试：解密数据库 → 导出文件 → 触发解密后重读

    Args:
        since_minutes: 读取最近N分钟的消息

    Returns:
        list of dict: 标准化消息列表
    """
    messages = []

    # 方式 1: 直接读取解密后的数据库
    db_messages = read_decrypted_databases(since_minutes)
    if db_messages:
        messages.extend(db_messages)

    # 方式 2: 读取导出文件
    if not messages:
        export_messages = read_exported_messages(since_minutes)
        messages.extend(export_messages)

    # 方式 3: 如果都没读到，尝试触发解密后重读
    if not messages:
        if trigger_wechat_decrypt():
            # 清除缓存以重新读取
            global _table_name_cache
            _table_name_cache = None
            db_messages = read_decrypted_databases(since_minutes)
            messages.extend(db_messages)

    # 更新最后读取时间
    last_read = load_last_read()
    last_read["last_scan"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    last_read["message_count"] = len(messages)
    save_last_read(last_read)

    return messages
