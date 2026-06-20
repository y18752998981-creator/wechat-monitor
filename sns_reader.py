"""微信求购监控系统 - 朋友圈读取模块

从 wechat-decrypt 解密后的 sns.db 读取朋友圈数据，
解析 XML 内容，提取文字、发布人信息。

前置条件:
  1. wechat-decrypt 已安装并执行过 decrypt
  2. sns/sns.db 存在
"""
import os
import sqlite3
import xml.etree.ElementTree as ET
import time
from datetime import datetime, timedelta
import config

# 朋友圈数据库路径
SNS_DB = os.path.join(config.WECHAT_DECRYPT_DB_DIR, "sns", "sns.db")
CONTACT_DB = os.path.join(config.WECHAT_DECRYPT_DB_DIR, "contact", "contact.db")

# 已处理记录持久化
SEEN_FILE = os.path.join(config.DATA_DIR, "sns_seen.json")

# 联系人缓存
_contact_cache = {"map": {}, "ts": 0}

# 已处理的 tid 集合
_seen_tids = set()
_seen_loaded = False


def _load_seen_tids():
    """加载已处理的 tid 集合"""
    global _seen_tids, _seen_loaded
    if _seen_loaded:
        return
    _seen_loaded = True
    if os.path.exists(SEEN_FILE):
        try:
            import json
            with open(SEEN_FILE, "r") as f:
                data = json.load(f)
                _seen_tids = set(data.get("tids", []))
        except Exception:
            pass


def _save_seen_tids():
    """持久化已处理的 tid"""
    import json
    os.makedirs(os.path.dirname(SEEN_FILE), exist_ok=True)
    # 只保留最近 2000 条，防止文件膨胀
    tids = list(_seen_tids)[-2000:]
    with open(SEEN_FILE, "w") as f:
        json.dump({"tids": tids}, f)


def _load_contacts():
    """加载 username → {alias, remark, nick_name} 映射"""
    now = time.time()
    if now - _contact_cache["ts"] < 60 and _contact_cache["map"]:
        return _contact_cache["map"]

    cmap = {}
    if not os.path.exists(CONTACT_DB):
        return cmap

    try:
        conn = sqlite3.connect(f"file:{CONTACT_DB}?mode=ro", uri=True)
        cur = conn.cursor()
        cur.execute("SELECT username, alias, remark, nick_name FROM contact")
        for username, alias, remark, nick_name in cur.fetchall():
            if username:
                cmap[username] = {
                    "alias": alias or "",
                    "remark": remark or "",
                    "nick_name": nick_name or "",
                }
        conn.close()
    except Exception as e:
        print(f"[SNS] 加载联系人失败: {e}")

    _contact_cache["map"] = cmap
    _contact_cache["ts"] = now
    return cmap


def _parse_sns_xml(xml_content):
    """
    解析朋友圈 XML，提取关键字段

    Returns:
        dict: {text, create_time, wxid, sns_nickname, media_type, title}
    """
    result = {
        "text": "",
        "create_time": 0,
        "wxid": "",
        "sns_nickname": "",
        "media_type": "",
        "title": "",
    }

    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError:
        return result

    for elem in root.iter():
        tag = elem.tag
        text = (elem.text or "").strip()

        if tag == "contentDesc" and text and not result["text"]:
            result["text"] = text
        elif tag == "createTime" and text and not result["create_time"]:
            try:
                result["create_time"] = int(text)
            except ValueError:
                pass
        elif tag == "username" and text and not result["wxid"]:
            # 取第一个 username（发帖人）
            if text.startswith("wxid_") or not text.startswith("v"):
                result["wxid"] = text
        elif tag == "nickname" and text and not result["sns_nickname"]:
            result["sns_nickname"] = text
        elif tag == "type" and text and not result["media_type"]:
            result["media_type"] = text
        elif tag == "title" and text and not result["title"]:
            result["title"] = text

    return result


def get_new_moments(hours=1):
    """
    读取最近 N 小时的朋友圈新数据

    Args:
        hours: 读取最近多少小时的数据

    Returns:
        list[dict]: 每条朋友圈包含:
            - text: 文字内容
            - wxid: 发布人 wxid
            - alias: 微信号
            - remark: 备注名
            - nick_name: 昵称
            - sns_nickname: 朋友圈显示的昵称
            - display_name: 最佳显示名 (备注 > 昵称 > 微信号)
            - create_time: 发布时间戳
            - time_str: 格式化时间
            - media_type: 类型 (文字/图片/链接/视频号)
            - source: "朋友圈"
    """
    _load_seen_tids()

    if not os.path.exists(SNS_DB):
        print(f"[SNS] 数据库不存在: {SNS_DB}")
        return []

    since_ts = int(time.time()) - hours * 3600
    contacts = _load_contacts()

    results = []

    try:
        conn = sqlite3.connect(f"file:{SNS_DB}?mode=ro", uri=True)
        cur = conn.cursor()

        # 按 tid 降序取最近的朋友圈
        # tid 是负数，绝对值越大越新
        cur.execute("""
            SELECT tid, user_name, content
            FROM SnsTimeLine
            ORDER BY tid DESC
        """)

        for tid, user_name, content in cur.fetchall():
            # 跳过已处理
            if tid in _seen_tids:
                continue

            # 解析 XML
            parsed = _parse_sns_xml(content)

            # 时间过滤
            if parsed["create_time"] and parsed["create_time"] < since_ts:
                continue

            # 没有文字内容就跳过（纯图/视频不带文字的不处理）
            text = parsed["text"] or parsed["title"]
            if not text or len(text.strip()) < 5:
                # 标记为已处理但跳过
                _seen_tids.add(tid)
                continue

            # 解析发布人信息
            wxid = parsed["wxid"] or user_name
            contact_info = contacts.get(wxid, {})

            alias = contact_info.get("alias", "")
            remark = contact_info.get("remark", "")
            nick_name = contact_info.get("nick_name", "")
            sns_nick = parsed["sns_nickname"]

            # 最佳显示名
            display_name = remark or nick_name or sns_nick or alias or wxid

            # 类型名
            type_map = {
                "1": "文字", "2": "图片", "3": "链接",
                "4": "视频", "15": "图文", "28": "视频号",
            }
            media_name = type_map.get(parsed["media_type"], f"类型{parsed['media_type']}")

            # 格式化时间
            time_str = ""
            if parsed["create_time"]:
                time_str = datetime.fromtimestamp(
                    parsed["create_time"]
                ).strftime("%m-%d %H:%M")

            results.append({
                "text": text.strip(),
                "wxid": wxid,
                "alias": alias,
                "remark": remark,
                "nick_name": nick_name or sns_nick,
                "display_name": display_name,
                "create_time": parsed["create_time"],
                "time_str": time_str,
                "media_type": media_name,
                "source": "朋友圈",
                "tid": tid,
            })

            _seen_tids.add(tid)

        conn.close()

    except Exception as e:
        print(f"[SNS] 读取异常: {e}")
        import traceback
        traceback.print_exc()

    # 持久化已处理
    if results:
        _save_seen_tids()

    return results
