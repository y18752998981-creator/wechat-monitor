"""微信求购监控系统 - 全文搜索引擎

基于微信的 message_fts.db 全文搜索索引，支持：
  - 关键词搜索聊天记录
  - 图片 OCR 文字搜索
  - 群名/联系人过滤
  - 时间范围限定
"""
import os
import sqlite3
import time
import config

# ============================================================
# FTS 数据库路径
# ============================================================
FTS_DB = os.path.join(config.WECHAT_DECRYPT_DB_DIR, "message", "message_fts.db")
CONTACT_DB = os.path.join(config.WECHAT_DECRYPT_DB_DIR, "contact", "contact.db")

# FTS 分片表（微信把全文索引拆成4个分片）
FTS_TABLES = [f"message_fts_v4_{i}_content" for i in range(4)]
IMG_OCR_TABLES = [f"ImgFtsAux{i}V0" for i in range(4)]

# ============================================================
# 联系人缓存（60秒TTL）
# ============================================================
_contact_cache = {"map": {}, "ts": 0}
_session_cache = {"map": {}, "ts": 0}


def _load_contacts():
    """加载 username → 显示名 映射（带缓存）"""
    now = time.time()
    if now - _contact_cache["ts"] < 60 and _contact_cache["map"]:
        return _contact_cache["map"]

    cmap = {}
    if not os.path.exists(CONTACT_DB):
        return cmap

    try:
        conn = sqlite3.connect(f"file:{CONTACT_DB}?mode=ro", uri=True)
        cur = conn.cursor()
        cur.execute("""
            SELECT username,
                   COALESCE(NULLIF(remark,''), NULLIF(nick_name,''), NULLIF(alias,''), username)
            FROM contact
        """)
        for username, display in cur.fetchall():
            if username and display:
                cmap[username] = display
        conn.close()
    except Exception as e:
        print(f"[Search] 加载联系人失败: {e}")

    _contact_cache["map"] = cmap
    _contact_cache["ts"] = now
    return cmap


def _load_session_map():
    """加载 session_id → username 映射（name2id 表）"""
    now = time.time()
    if now - _session_cache["ts"] < 60 and _session_cache["map"]:
        return _session_cache["map"]

    smap = {}
    if not os.path.exists(FTS_DB):
        return smap

    try:
        conn = sqlite3.connect(f"file:{FTS_DB}?mode=ro", uri=True)
        cur = conn.cursor()
        cur.execute("SELECT rowid, username FROM name2id")
        for rowid, username in cur.fetchall():
            smap[rowid] = username
        conn.close()
    except Exception as e:
        print(f"[Search] 加载 session map 失败: {e}")

    _session_cache["map"] = smap
    _session_cache["ts"] = now
    return smap


# ============================================================
# 搜索函数
# ============================================================

def search_messages(keyword, hours=24, limit=20, group_only=False):
    """
    搜索微信聊天记录

    Args:
        keyword: 搜索关键词
        hours: 搜索最近N小时的消息（0=不限）
        limit: 最多返回多少条
        group_only: 是否只搜群消息

    Returns:
        list of dict: {
            'content': 消息内容,
            'chat_name': 群名/联系人名,
            'username': 微信ID,
            'timestamp': Unix时间戳,
            'time_str': 可读时间,
            'source': 'text' | 'ocr'
        }
    """
    if not os.path.exists(FTS_DB):
        return []

    session_map = _load_session_map()
    contact_map = _load_contacts()

    min_ts = 0
    if hours > 0:
        min_ts = int(time.time()) - hours * 3600

    results = []
    like_pattern = f"%{keyword}%"

    try:
        conn = sqlite3.connect(f"file:{FTS_DB}?mode=ro", uri=True)
        cur = conn.cursor()

        # 搜索文本 FTS
        for tbl in FTS_TABLES:
            try:
                cur.execute(f"""
                    SELECT c0, c4, c6 FROM [{tbl}]
                    WHERE c0 LIKE ? AND c6 >= ?
                    ORDER BY c6 DESC LIMIT ?
                """, (like_pattern, min_ts, limit * 2))
                for content, session_id, ts in cur.fetchall():
                    username = session_map.get(session_id, "")
                    chat_name = contact_map.get(username, username)

                    if group_only and "@" not in username:
                        continue

                    results.append({
                        "content": content[:200] if content else "",
                        "chat_name": chat_name,
                        "username": username,
                        "timestamp": ts,
                        "time_str": _ts_to_str(ts),
                        "source": "text",
                    })
            except Exception:
                continue

        # 搜索图片 OCR
        for tbl in IMG_OCR_TABLES:
            try:
                cur.execute(f"""
                    SELECT acontent, session_id, create_time FROM [{tbl}]
                    WHERE acontent LIKE ? AND create_time >= ?
                    ORDER BY create_time DESC LIMIT ?
                """, (like_pattern, min_ts, limit))
                for content, session_id, ts in cur.fetchall():
                    username = session_map.get(session_id, "")
                    chat_name = contact_map.get(username, username)
                    results.append({
                        "content": f"[图片文字] {content[:150]}",
                        "chat_name": chat_name,
                        "username": username,
                        "timestamp": ts,
                        "time_str": _ts_to_str(ts),
                        "source": "ocr",
                    })
            except Exception:
                continue

        conn.close()
    except Exception as e:
        print(f"[Search] 搜索失败: {e}")

    # 去重 + 排序
    seen = set()
    unique = []
    for r in results:
        key = (r["chat_name"], r["content"][:50], r["timestamp"])
        if key not in seen:
            seen.add(key)
            unique.append(r)

    unique.sort(key=lambda x: x["timestamp"], reverse=True)
    return unique[:limit]


def search_purchase_related(hours=24, limit=30):
    """搜索与求购相关的消息（使用配置中的关键词）"""
    all_results = []
    seen = set()

    for kw in config.PURCHASE_KEYWORDS[:10]:  # 取前10个核心关键词
        results = search_messages(kw, hours=hours, limit=limit)
        for r in results:
            key = (r["chat_name"], r["content"][:50], r["timestamp"])
            if key not in seen:
                seen.add(key)
                r["matched_keyword"] = kw
                all_results.append(r)

    all_results.sort(key=lambda x: x["timestamp"], reverse=True)
    return all_results[:limit]


def get_available_groups():
    """获取所有有聊天记录的群列表"""
    session_map = _load_session_map()
    contact_map = _load_contacts()

    groups = []
    for session_id, username in session_map.items():
        if "@" in username:  # 群聊
            name = contact_map.get(username, username)
            groups.append({"username": username, "name": name})

    return groups


# ============================================================
# 工具函数
# ============================================================

def _ts_to_str(ts):
    """Unix 时间戳 → 可读字符串"""
    if not ts:
        return ""
    try:
        from datetime import datetime
        return datetime.fromtimestamp(int(ts)).strftime("%m-%d %H:%M")
    except Exception:
        return str(ts)
