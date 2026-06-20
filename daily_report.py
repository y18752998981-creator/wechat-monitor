"""微信求购监控系统 - 每日汇总日报"""
import os
import json
from datetime import datetime, timedelta
from collections import defaultdict
import config
import group_tracker


REPORTS_FILE = os.path.join(config.DATA_DIR, "purchases_history.json")


def load_history():
    """加载历史求购记录"""
    if os.path.exists(REPORTS_FILE):
        with open(REPORTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_history(history):
    """保存历史"""
    os.makedirs(os.path.dirname(REPORTS_FILE), exist_ok=True)
    with open(REPORTS_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def add_purchases(purchases):
    """添加新的求购记录到历史"""
    history = load_history()
    for p in purchases:
        history.append(p.to_dict() if hasattr(p, 'to_dict') else p)
    save_history(history)


def generate_daily_report():
    """
    生成每日汇总日报
    
    Returns:
        str: 日报文本
    """
    today = datetime.now().strftime("%Y-%m-%d")
    history = load_history()

    # 筛选今天的求购
    today_purchases = []
    for p in history:
        ts = p.get("timestamp", "")
        if today in str(ts):
            today_purchases.append(p)

    if not today_purchases:
        return f"📋 求购日报 - {today}\n\n今日暂无求购信息。"

    # 按群分组
    by_group = defaultdict(list)
    for p in today_purchases:
        gn = p.get("group_name", "未知群")
        by_group[gn].append(p)

    # 按面料品种分组
    by_fabric = defaultdict(list)
    for p in today_purchases:
        ft = p.get("fabric_type", "未分类")
        by_fabric[ft].append(p)

    # 统计紧急程度
    urgent = [p for p in today_purchases if p.get("urgency") == "高"]
    normal = [p for p in today_purchases if p.get("urgency") != "高"]

    # 生成报告
    lines = []
    lines.append(f"📋 求购日报 - {today}")
    lines.append(f"{'='*50}")
    lines.append(f"")
    lines.append(f"📊 今日总览")
    lines.append(f"  求购信息: {len(today_purchases)} 条")
    lines.append(f"  🔥 急单: {len(urgent)} 条")
    lines.append(f"  来源群: {len(by_group)} 个")
    lines.append(f"  涉及品种: {len(by_fabric)} 类")
    lines.append(f"")

    # 急单专区
    if urgent:
        lines.append(f"🔥 急单专区 ({len(urgent)}条)")
        lines.append(f"{'─'*40}")
        for i, p in enumerate(urgent, 1):
            lines.append(f"  {i}. {p.get('group_name', '?')} | {p.get('sender_name', '?')}")
            lines.append(f"     {p.get('raw_text', '')[:80]}")
            if p.get('fabric_type'):
                lines.append(f"     面料: {p['fabric_type']} {p.get('quantity', '')}")
            lines.append(f"")

    # 按群列表
    lines.append(f"📱 各群求购明细")
    lines.append(f"{'─'*40}")
    for gn, items in sorted(by_group.items(), key=lambda x: len(x[1]), reverse=True):
        lines.append(f"  [{gn}] ({len(items)}条)")
        for p in items:
            urgency_mark = "🔥" if p.get("urgency") == "高" else "  "
            fabric = p.get("fabric_type", "")
            qty = p.get("quantity", "")
            sender = p.get("sender_name", "?")
            text = p.get("raw_text", "")[:60]
            detail = f"{fabric} {qty}".strip()
            lines.append(f"    {urgency_mark} {sender}: {text}")
            if detail:
                lines.append(f"       → {detail}")
        lines.append(f"")

    # 按面料品种统计
    lines.append(f"🧵 品种热度排名")
    lines.append(f"{'─'*40}")
    for ft, items in sorted(by_fabric.items(), key=lambda x: len(x[1]), reverse=True):
        lines.append(f"  {ft}: {len(items)} 条求购")

    # 活跃群排名
    lines.append(f"")
    lines.append(f"📈 活跃群排名 (近7天)")
    lines.append(f"{'─'*40}")
    ranking = group_tracker.get_all_groups_ranking()[:10]
    for i, g in enumerate(ranking, 1):
        focus_mark = "⭐" if g["is_focus"] else "  "
        lines.append(f"  {focus_mark} {i}. {g['name']}: {g['recent_7d_purchases']}条求购")

    lines.append(f"")
    lines.append(f"{'='*50}")
    lines.append(f"由 信风AI·微信求购监控系统 自动生成")

    return "\n".join(lines)


def get_today_stats():
    """获取今日统计"""
    today = datetime.now().strftime("%Y-%m-%d")
    history = load_history()
    today_purchases = [p for p in history if today in str(p.get("timestamp", ""))]
    return {
        "total": len(today_purchases),
        "urgent": len([p for p in today_purchases if p.get("urgency") == "高"]),
        "groups": len(set(p.get("group_name", "") for p in today_purchases)),
        "fabrics": len(set(p.get("fabric_type", "") for p in today_purchases if p.get("fabric_type"))),
    }
