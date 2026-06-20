"""微信求购监控系统 - Telegram Bot 推送通知

通过 Telegram Bot API 推送求购信息到指定用户。
支持:
  - 文本消息 (HTML 格式)
  - 图片推送 (sendPhoto / sendMediaGroup)
  - 代理访问 (HTTP/SOCKS5)
  - 消息去重 + 频率控制
"""
import json
import os
import time
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime
from collections import defaultdict

import config


# ============================================================
# Telegram API
# ============================================================
API_BASE = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}"

# 推送频率控制
_notified_hashes = set()
_last_push_time = 0
MIN_PUSH_INTERVAL = 5  # 两次推送最少间隔5秒


def _build_opener():
    """构建支持代理的 URL opener"""
    proxy = getattr(config, 'TELEGRAM_PROXY', '')
    if proxy:
        proxy_handler = urllib.request.ProxyHandler({
            'http': proxy,
            'https': proxy,
        })
        return urllib.request.build_opener(proxy_handler)
    return urllib.request.build_opener()


def _msg_hash(purchase):
    """生成消息去重 hash"""
    key = f"{purchase.group_name}:{purchase.sender_name}:{purchase.raw_text[:30]}"
    return hash(key)


def tg_send_text(text, parse_mode="HTML", silent=False):
    """
    发送文本消息到 Telegram

    Args:
        text: 消息内容 (支持 HTML 标签)
        parse_mode: "HTML" 或 "Markdown"
        silent: 静默发送 (不响通知)

    Returns:
        bool: 是否成功
    """
    chat_id = config.TELEGRAM_CHAT_ID
    if not chat_id or not config.TELEGRAM_BOT_TOKEN:
        print("[Telegram] 未配置 BOT_TOKEN 或 CHAT_ID，跳过推送")
        return False

    # Telegram 限制 4096 字符
    if len(text) > 4000:
        text = text[:3997] + "..."

    data = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    if silent:
        data["disable_notification"] = True

    try:
        opener = _build_opener()
        req = urllib.request.Request(
            f"{API_BASE}/sendMessage",
            data=json.dumps(data).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with opener.open(req, timeout=20) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result.get("ok", False)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:300]
        print(f"[Telegram] HTTP {e.code}: {body}")
        return False
    except Exception as e:
        print(f"[Telegram] 发送失败: {e}")
        return False


def tg_send_photo(image_path, caption="", silent=False):
    """
    发送带图片的消息到 Telegram

    Args:
        image_path: 本地图片路径
        caption: 图片说明 (支持 HTML, 限 1024 字符)
        silent: 静默发送

    Returns:
        bool: 是否成功
    """
    chat_id = config.TELEGRAM_CHAT_ID
    if not os.path.exists(image_path):
        return False

    # 检查文件大小 (Telegram 限制 10MB)
    file_size = os.path.getsize(image_path)
    if file_size > 9 * 1024 * 1024:
        print(f"[Telegram] 图片过大 ({file_size // 1024}KB)，跳过: {image_path}")
        return False

    if len(caption) > 1000:
        caption = caption[:997] + "..."

    try:
        # multipart/form-data 上传
        boundary = "----PythonTelegramBoundary"
        body_parts = []

        # chat_id
        body_parts.append(f"--{boundary}\r\n")
        body_parts.append(f'Content-Disposition: form-data; name="chat_id"\r\n\r\n')
        body_parts.append(f"{chat_id}\r\n")

        # caption
        if caption:
            body_parts.append(f"--{boundary}\r\n")
            body_parts.append(f'Content-Disposition: form-data; name="caption"\r\n\r\n')
            body_parts.append(f"{caption}\r\n")

        # parse_mode
        body_parts.append(f"--{boundary}\r\n")
        body_parts.append(f'Content-Disposition: form-data; name="parse_mode"\r\n\r\n')
        body_parts.append("HTML\r\n")

        # disable_notification
        if silent:
            body_parts.append(f"--{boundary}\r\n")
            body_parts.append(f'Content-Disposition: form-data; name="disable_notification"\r\n\r\n')
            body_parts.append("true\r\n")

        # photo file
        filename = os.path.basename(image_path)
        body_parts.append(f"--{boundary}\r\n")
        body_parts.append(
            f'Content-Disposition: form-data; name="photo"; filename="{filename}"\r\n'
        )
        body_parts.append("Content-Type: image/jpeg\r\n\r\n")

        with open(image_path, "rb") as f:
            file_data = f.read()

        # 拼接 body
        body = b""
        for part in body_parts:
            body += part.encode("utf-8")
        body += file_data
        body += f"\r\n--{boundary}--\r\n".encode("utf-8")

        opener = _build_opener()
        req = urllib.request.Request(
            f"{API_BASE}/sendPhoto",
            data=body,
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
        )
        with opener.open(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result.get("ok", False)

    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:300]
        print(f"[Telegram] sendPhoto HTTP {e.code}: {body}")
        return False
    except Exception as e:
        print(f"[Telegram] sendPhoto 失败: {e}")
        return False


def tg_send_media_group(image_paths, caption="", silent=False):
    """
    发送多张图片 (合并为一条消息)

    Args:
        image_paths: 图片路径列表 (2-10张)
        caption: 第一张图片的说明
        silent: 静默发送

    Returns:
        bool: 是否成功
    """
    chat_id = config.TELEGRAM_CHAT_ID
    valid_paths = [p for p in image_paths if os.path.exists(p) and os.path.getsize(p) < 9 * 1024 * 1024]

    if len(valid_paths) < 2:
        # 不够2张，退化为单张发送
        if valid_paths:
            return tg_send_photo(valid_paths[0], caption, silent)
        return False

    valid_paths = valid_paths[:10]  # Telegram 限制最多10张

    try:
        boundary = "----PythonTelegramBoundary"
        body_parts = []

        # chat_id
        body_parts.append(f"--{boundary}\r\n")
        body_parts.append(f'Content-Disposition: form-data; name="chat_id"\r\n\r\n')
        body_parts.append(f"{chat_id}\r\n")

        # media JSON
        media = []
        for i, path in enumerate(valid_paths):
            filename = os.path.basename(path)
            item = {
                "type": "photo",
                "media": f"attach://photo{i}",
            }
            if i == 0 and caption:
                item["caption"] = caption[:1000]
                item["parse_mode"] = "HTML"
            media.append(item)

        body_parts.append(f"--{boundary}\r\n")
        body_parts.append(f'Content-Disposition: form-data; name="media"\r\n\r\n')
        body_parts.append(json.dumps(media) + "\r\n")

        if silent:
            body_parts.append(f"--{boundary}\r\n")
            body_parts.append(f'Content-Disposition: form-data; name="disable_notification"\r\n\r\n')
            body_parts.append("true\r\n")

        # 图片文件
        file_datas = []
        for i, path in enumerate(valid_paths):
            body_parts.append(f"--{boundary}\r\n")
            body_parts.append(
                f'Content-Disposition: form-data; name="photo{i}"; filename="{os.path.basename(path)}"\r\n'
            )
            body_parts.append("Content-Type: image/jpeg\r\n\r\n")
            with open(path, "rb") as f:
                file_datas.append(f.read())

        # 拼接
        body = b""
        part_idx = 0
        file_idx = 0
        for part in body_parts:
            encoded = part.encode("utf-8")
            body += encoded
            # 在图片 Content-Type 行后插入文件数据
            if "Content-Type: image/jpeg" in part:
                body += file_datas[file_idx]
                body += b"\r\n"
                file_idx += 1

        body += f"--{boundary}--\r\n".encode("utf-8")

        opener = _build_opener()
        req = urllib.request.Request(
            f"{API_BASE}/sendMediaGroup",
            data=body,
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
        )
        with opener.open(req, timeout=60) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result.get("ok", False)

    except Exception as e:
        print(f"[Telegram] sendMediaGroup 失败: {e}")
        return False


# ============================================================
# 求购推送
# ============================================================

def _build_purchase_text(purchase):
    """构建单条求购的简洁 HTML 文本"""
    # 紧急标记
    badge = "🔥急单" if purchase.urgency == "高" else "📌求购"

    # 核心信息一行：面料 + 规格 + 数量 + 颜色
    parts = []
    if purchase.fabric_type:
        parts.append(purchase.fabric_type)
    if purchase.specification and purchase.specification not in (purchase.fabric_type or ""):
        parts.append(purchase.specification)
    if purchase.quantity:
        parts.append(purchase.quantity)
    if purchase.color:
        parts.append(purchase.color)
    info_line = " / ".join(parts) if parts else ""

    # 原文截取（去掉换行，限制长度）
    raw = (purchase.raw_text or "").replace("\n", " ").strip()
    if len(raw) > 100:
        raw = raw[:97] + "..."

    lines = [f"{badge} | {purchase.sender_name}"]
    if info_line:
        lines.append(f"<b>{info_line}</b>")
    lines.append(f"<i>{raw}</i>")
    lines.append(f"📍 {purchase.group_name}  {purchase.timestamp}")

    return "\n".join(lines)


def _build_batch_text(purchases, max_items=10):
    """构建批量求购的简洁 HTML 文本"""
    now_str = datetime.now().strftime("%H:%M")
    urgent = [p for p in purchases if p.urgency == "高"]

    if urgent:
        header = f"🔥 <b>{len(urgent)}急单</b> + {len(purchases)-len(urgent)}求购 · {now_str}"
    else:
        header = f"📋 <b>{len(purchases)}条新求购</b> · {now_str}"

    lines = [header, ""]

    for p in purchases[:max_items]:
        badge = "🔥" if p.urgency == "高" else "·"

        # 核心信息
        parts = []
        if p.fabric_type:
            parts.append(p.fabric_type)
        if p.specification and p.specification not in (p.fabric_type or ""):
            parts.append(p.specification)
        if p.quantity:
            parts.append(p.quantity)
        info = " / ".join(parts) if parts else ""

        # 原文（精简）
        raw = (p.raw_text or "").replace("\n", " ").strip()
        if len(raw) > 80:
            raw = raw[:77] + "..."

        lines.append(f"{badge} <b>{p.sender_name}</b> {info}")
        lines.append(f"  <i>{raw}</i>")
        lines.append(f"  📍{p.group_name}")
        lines.append("")

    return "\n".join(lines)


def send_notification(purchase, force=False):
    """
    推送一条求购信息到 Telegram

    Args:
        purchase: PurchaseRequest 对象
        force: 是否强制推送（跳过去重）

    Returns:
        bool: 是否推送成功
    """
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        print("[Telegram] 未配置，跳过推送")
        return False

    # 去重
    h = _msg_hash(purchase)
    if not force and h in _notified_hashes:
        print(f"[Telegram] 重复消息，跳过: {purchase.summary()}")
        return False

    # 频率控制
    global _last_push_time
    elapsed = time.time() - _last_push_time
    if elapsed < MIN_PUSH_INTERVAL:
        time.sleep(MIN_PUSH_INTERVAL - elapsed)

    # 收集有效图片
    valid_images = []
    for img_path in (purchase.images or []):
        if img_path and os.path.exists(img_path):
            valid_images.append(img_path)

    # 构建文本
    text = _build_purchase_text(purchase)

    # 推送策略：有图发图，无图发文
    success = False
    if len(valid_images) >= 2:
        # 多张图 → media group
        caption = f"🛒 {purchase.sender_name} @ {purchase.group_name}\n{purchase.raw_text[:200]}"
        success = tg_send_media_group(valid_images[:4], caption)
        if success:
            # 图片发了，再发文字详情
            tg_send_text(text, silent=True)
    elif len(valid_images) == 1:
        # 单张图 → sendPhoto
        caption = f"🛒 {purchase.sender_name} @ {purchase.group_name}\n{purchase.raw_text[:200]}"
        success = tg_send_photo(valid_images[0], caption)
        if success:
            tg_send_text(text, silent=True)
    else:
        # 无图 → 纯文本
        success = tg_send_text(text)

    if success:
        _notified_hashes.add(h)
        _last_push_time = time.time()
        purchase.notified = True
        img_info = f" (含{len(valid_images)}张图)" if valid_images else ""
        print(f"[Telegram] 推送成功{img_info}: {purchase.summary()}")

    return success


def send_batch_notification(purchases, max_items=10):
    """
    批量推送求购信息到 Telegram

    Args:
        purchases: list of PurchaseRequest
        max_items: 最多合并多少条

    Returns:
        bool: 是否推送成功
    """
    if not purchases:
        return False

    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        print("[Telegram] 未配置，跳过推送")
        return False

    # 过滤已推送的
    new_purchases = [p for p in purchases if not p.notified]
    if not new_purchases:
        return False

    new_purchases = new_purchases[:max_items]

    # 频率控制
    global _last_push_time
    elapsed = time.time() - _last_push_time
    if elapsed < MIN_PUSH_INTERVAL:
        time.sleep(MIN_PUSH_INTERVAL - elapsed)

    # 收集所有图片
    all_images = []
    for p in new_purchases:
        for img_path in (p.images or []):
            if img_path and os.path.exists(img_path):
                all_images.append(img_path)

    # 先发图片（如果有的话）
    if len(all_images) >= 2:
        first = new_purchases[0]
        caption = (
            f"📋 {len(new_purchases)}条新求购\n"
            f"{first.sender_name} @ {first.group_name}"
        )
        tg_send_media_group(all_images[:4], caption)
    elif len(all_images) == 1:
        first = new_purchases[0]
        caption = f"📋 {first.sender_name} @ {first.group_name}"
        tg_send_photo(all_images[0], caption)

    # 发送文字汇总
    text = _build_batch_text(new_purchases, max_items)
    success = tg_send_text(text)

    if success:
        for p in new_purchases:
            _notified_hashes.add(_msg_hash(p))
            p.notified = True
        _last_push_time = time.time()
        total_images = len(all_images)
        img_info = f" (含{total_images}张图)" if total_images else ""
        print(f"[Telegram] 批量推送成功: {len(new_purchases)} 条{img_info}")

    return success


def send_daily_report(report_text):
    """推送每日汇总报告到 Telegram"""
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        return False

    header = f"📊 <b>微信求购日报</b> ({datetime.now().strftime('%Y-%m-%d')})\n\n"
    text = header + report_text[:3500]

    success = tg_send_text(text)
    if success:
        print("[Telegram] 日报推送成功")
    return success


def send_moments_notification(moment, purchase=None):
    """
    推送一条朋友圈求购信息到 Telegram

    Args:
        moment: dict from sns_reader.get_new_moments()
        purchase: PurchaseRequest 对象 (可选，有则附带检测结果)

    Returns:
        bool: 是否推送成功
    """
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        return False

    # 频率控制
    global _last_push_time
    elapsed = time.time() - _last_push_time
    if elapsed < MIN_PUSH_INTERVAL:
        time.sleep(MIN_PUSH_INTERVAL - elapsed)

    # 构建推送文本
    badge = "🔥急单" if (purchase and purchase.urgency == "高") else "📢求购"
    source = "朋友圈"

    # 发布人信息
    display_name = moment.get("display_name", "未知")
    wxid = moment.get("wxid", "")
    alias = moment.get("alias", "")
    nick_name = moment.get("nick_name", "")
    time_str = moment.get("time_str", "")

    # 身份行：显示名 + 微信号
    identity_parts = [f"<b>{display_name}</b>"]
    if alias:
        identity_parts.append(f"微信号:{alias}")
    elif wxid and not wxid.startswith("wxid_"):
        identity_parts.append(f"ID:{wxid}")

    identity_line = " | ".join(identity_parts)

    # 内容
    raw_text = (moment.get("text", "") or "").replace("\n", " ").strip()
    if len(raw_text) > 120:
        raw_text = raw_text[:117] + "..."

    # 检测结果（如果有）
    detail_lines = []
    if purchase:
        parts = []
        if purchase.fabric_type:
            parts.append(purchase.fabric_type)
        if purchase.specification and purchase.specification not in (purchase.fabric_type or ""):
            parts.append(purchase.specification)
        if purchase.quantity:
            parts.append(purchase.quantity)
        if parts:
            detail_lines.append(f"<b>{' / '.join(parts)}</b>")

    lines = [
        f"{badge} | {source}",
        identity_line,
    ]
    if nick_name and nick_name != display_name:
        lines.append(f"昵称: {nick_name}")
    lines.extend(detail_lines)
    lines.append(f"<i>{raw_text}</i>")
    lines.append(f"🕐 {time_str}")

    text = "\n".join(lines)

    # 推送
    success = tg_send_text(text)
    if success:
        _last_push_time = time.time()
        print(f"[Telegram] 朋友圈推送: {display_name} - {raw_text[:40]}")

    return success


def send_moments_batch(moments_with_purchases, max_items=10):
    """
    批量推送朋友圈求购信息

    Args:
        moments_with_purchases: list of (moment_dict, PurchaseRequest)
        max_items: 最多合并多少条

    Returns:
        bool: 是否推送成功
    """
    if not moments_with_purchases:
        return False

    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        return False

    items = moments_with_purchases[:max_items]

    # 频率控制
    global _last_push_time
    elapsed = time.time() - _last_push_time
    if elapsed < MIN_PUSH_INTERVAL:
        time.sleep(MIN_PUSH_INTERVAL - elapsed)

    urgent_count = sum(1 for _, p in items if p and p.urgency == "高")
    now_str = datetime.now().strftime("%H:%M")

    if urgent_count:
        header = f"🔥 <b>朋友圈{urgent_count}急单</b> + {len(items)-urgent_count}求购 · {now_str}"
    else:
        header = f"📢 <b>朋友圈{len(items)}条求购</b> · {now_str}"

    lines = [header, ""]

    for moment, purchase in items:
        display_name = moment.get("display_name", "未知")
        alias = moment.get("alias", "")
        wxid = moment.get("wxid", "")

        # 身份
        id_str = f"<b>{display_name}</b>"
        if alias:
            id_str += f" ({alias})"

        # 内容摘要
        raw = (moment.get("text", "") or "").replace("\n", " ").strip()
        if len(raw) > 70:
            raw = raw[:67] + "..."

        badge = "🔥" if (purchase and purchase.urgency == "高") else "·"

        # 检测详情
        info = ""
        if purchase:
            parts = []
            if purchase.fabric_type:
                parts.append(purchase.fabric_type)
            if purchase.quantity:
                parts.append(purchase.quantity)
            if parts:
                info = f" {' / '.join(parts)}"

        lines.append(f"{badge} {id_str}{info}")
        lines.append(f"  <i>{raw}</i>")
        lines.append(f"  🕐{moment.get('time_str', '')}")
        lines.append("")

    text = "\n".join(lines)
    success = tg_send_text(text)

    if success:
        _last_push_time = time.time()
        print(f"[Telegram] 朋友圈批量推送: {len(items)} 条")

    return success


def test_connection():
    """测试 Telegram 连接是否正常"""
    if not config.TELEGRAM_BOT_TOKEN:
        print("[Telegram] 未配置 BOT_TOKEN")
        return False

    try:
        opener = _build_opener()
        req = urllib.request.Request(f"{API_BASE}/getMe")
        with opener.open(req, timeout=15) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            if result.get("ok"):
                bot_info = result.get("result", {})
                bot_name = bot_info.get("first_name", "unknown")
                print(f"[Telegram] 连接正常: {bot_name} (@{bot_info.get('username', '')})")
                return True
            else:
                print(f"[Telegram] getMe 失败: {result}")
                return False
    except Exception as e:
        print(f"[Telegram] 连接测试失败: {e}")
        return False
