"""微信求购监控系统 - 活跃群自动识别"""
import json
import os
from datetime import datetime, timedelta
from collections import defaultdict
import config


GROUP_STATS_FILE = os.path.join(config.DATA_DIR, "group_stats.json")


def load_group_stats():
    """加载群统计历史数据"""
    if os.path.exists(GROUP_STATS_FILE):
        with open(GROUP_STATS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_group_stats(stats):
    """保存群统计数据"""
    os.makedirs(os.path.dirname(GROUP_STATS_FILE), exist_ok=True)
    with open(GROUP_STATS_FILE, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)


def update_group_stats(group_name, purchase_count=0, message_count=0):
    """更新某个群的统计"""
    stats = load_group_stats()
    today = datetime.now().strftime("%Y-%m-%d")

    if group_name not in stats:
        stats[group_name] = {
            "total_purchases": 0,
            "total_messages": 0,
            "daily": {},
            "first_seen": today,
            "is_focus": False,
        }

    g = stats[group_name]
    g["total_purchases"] += purchase_count
    g["total_messages"] += message_count

    if today not in g["daily"]:
        g["daily"][today] = {"purchases": 0, "messages": 0}
    g["daily"][today]["purchases"] += purchase_count
    g["daily"][today]["messages"] += message_count

    # 判断是否为重点群
    g["is_focus"] = _check_if_focus_group(g)

    save_group_stats(stats)
    return g


def _check_if_focus_group(group_data):
    """判断一个群是否为重点关注群"""
    threshold_days = config.ACTIVE_GROUP_THRESHOLD_DAYS
    threshold_purchases = config.ACTIVE_GROUP_THRESHOLD_PURCHASES

    today = datetime.now()
    recent_purchases = 0

    for date_str, daily in group_data.get("daily", {}).items():
        try:
            date = datetime.strptime(date_str, "%Y-%m-%d")
            if (today - date).days <= threshold_days:
                recent_purchases += daily.get("purchases", 0)
        except ValueError:
            continue

    return recent_purchases >= threshold_purchases


def get_focus_groups():
    """获取所有重点关注的群"""
    stats = load_group_stats()
    focus = []
    for name, data in stats.items():
        if _check_if_focus_group(data):
            focus.append({
                "name": name,
                "total_purchases": data.get("total_purchases", 0),
                "is_focus": True,
            })

    # 按求购数量排序
    focus.sort(key=lambda x: x["total_purchases"], reverse=True)
    return focus


def get_all_groups_ranking():
    """获取所有群的活跃度排名"""
    stats = load_group_stats()
    ranking = []
    for name, data in stats.items():
        today = datetime.now()
        recent_7d_purchases = 0
        recent_7d_messages = 0

        for date_str, daily in data.get("daily", {}).items():
            try:
                date = datetime.strptime(date_str, "%Y-%m-%d")
                if (today - date).days <= 7:
                    recent_7d_purchases += daily.get("purchases", 0)
                    recent_7d_messages += daily.get("messages", 0)
            except ValueError:
                continue

        ranking.append({
            "name": name,
            "recent_7d_purchases": recent_7d_purchases,
            "recent_7d_messages": recent_7d_messages,
            "total_purchases": data.get("total_purchases", 0),
            "is_focus": _check_if_focus_group(data),
        })

    ranking.sort(key=lambda x: x["recent_7d_purchases"], reverse=True)
    return ranking


def process_new_messages(messages_with_groups):
    """
    处理新消息，更新群统计
    
    Args:
        messages_with_groups: list of dict, 每个包含 group_name 和 is_purchase
    """
    # 按群分组统计
    group_counts = defaultdict(lambda: {"purchases": 0, "messages": 0})

    for msg in messages_with_groups:
        gn = msg.get("group_name", "未知群")
        group_counts[gn]["messages"] += 1
        if msg.get("is_purchase"):
            group_counts[gn]["purchases"] += 1

    # 更新每个群的统计
    for gn, counts in group_counts.items():
        update_group_stats(gn, counts["purchases"], counts["messages"])
