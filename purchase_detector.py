"""微信求购监控系统 - 求购信息 AI 识别引擎"""
import re
import os
from datetime import datetime, timedelta
import config


# ===== 通用面料品种（不作为话题切换依据） =====
_GENERIC_FABRICS = {"面料", "布", "织物", "纺织品", "胚布", "坯布", "白坯", "色布"}

# ===== 分隔词（显式表示新话题） =====
_SEPARATOR_WORDS = [
    "还有", "另外", "第二个", "还要", "再要", "别的",
    "另一种", "下一个", "这个也", "顺便", "还想",
]

# ============================================================
#  数据结构
# ============================================================

class PurchaseRequest:
    """一条求购信息"""
    def __init__(self):
        self.group_name = ""        # 来源群
        self.sender_name = ""       # 发送人
        self.sender_wxid = ""       # 发送人微信ID
        self.timestamp = ""         # 时间
        self.raw_text = ""          # 原始文本
        self.fabric_type = ""       # 面料品种
        self.specification = ""     # 规格
        self.quantity = ""          # 数量
        self.color = ""             # 颜色
        self.urgency = "中"         # 紧急程度：高/中/低
        self.price_mentioned = ""   # 提及的价格
        self.contact_info = ""      # 联系方式
        self.extra_notes = ""       # 补充说明
        self.confidence = 0         # 置信度 0-100
        self.images = []            # 相关图片路径
        self.notified = False       # 是否已推送通知
        self.request_id = ""        # 需求编号（用于区分同一人的不同需求）

    def to_dict(self):
        return {
            "group_name": self.group_name,
            "sender_name": self.sender_name,
            "timestamp": self.timestamp,
            "raw_text": self.raw_text,
            "fabric_type": self.fabric_type,
            "specification": self.specification,
            "quantity": self.quantity,
            "color": self.color,
            "urgency": self.urgency,
            "price_mentioned": self.price_mentioned,
            "contact_info": self.contact_info,
            "extra_notes": self.extra_notes,
            "confidence": self.confidence,
            "images": self.images,
            "request_id": self.request_id,
        }

    def summary(self):
        """生成简短摘要（用于推送通知）"""
        parts = []
        if self.fabric_type:
            parts.append(f"【{self.fabric_type}】")
        if self.sender_name:
            parts.append(self.sender_name)
        if self.quantity:
            parts.append(self.quantity)
        if self.urgency == "高":
            parts.append("⚡急单")
        return " ".join(parts) if parts else self.raw_text[:50]

    def detail(self):
        """生成详细文本"""
        lines = [f"📢 {self.group_name} - {self.sender_name}"]
        lines.append(f"⏰ {self.timestamp}")
        lines.append(f"💬 {self.raw_text}")
        if self.fabric_type:
            lines.append(f"🧵 面料: {self.fabric_type}")
        if self.specification:
            lines.append(f"📐 规格: {self.specification}")
        if self.quantity:
            lines.append(f"📦 数量: {self.quantity}")
        if self.urgency == "高":
            lines.append(f"🔥 紧急度: {self.urgency}")
        if self.price_mentioned:
            lines.append(f"💰 价格: {self.price_mentioned}")
        if self.contact_info:
            lines.append(f"📞 联系: {self.contact_info}")
        return "\n".join(lines)


# ============================================================
#  时间解析
# ============================================================

def _parse_time(ts):
    """解析时间字符串为 datetime 对象"""
    if not ts:
        return None
    if isinstance(ts, datetime):
        return ts
    for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
                "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M"]:
        try:
            return datetime.strptime(str(ts), fmt)
        except (ValueError, TypeError):
            continue
    try:
        ts_int = int(ts)
        if ts_int > 1000000000:
            return datetime.fromtimestamp(ts_int)
    except (ValueError, TypeError, OSError):
        pass
    return None


# ============================================================
#  求购判定 & 信息提取
# ============================================================

def is_purchase_request(text, sender_name=""):
    """
    判断一条消息是否为求购信息

    Returns:
        (bool, int): (是否求购, 置信度0-100)
    """
    if not text or len(text.strip()) < 2:
        return False, 0

    text_lower = text.lower().strip()

    # 排除明显不是求购的消息
    exclude_patterns = [
        r"^(哈哈|呵呵|嗯嗯|好的|收到|谢谢|感谢|ok|OK|👍|666)",
        r"^@\S+\s*$",
        r"^(早上好|晚上好|早安|晚安|大家好)",
        r"(广告|推广|加盟|代理招商)",
        r"(红包|拼团|砍价|助力)",
    ]
    for pat in exclude_patterns:
        if re.match(pat, text_lower):
            return False, 0

    score = 0
    matched_keywords = []

    # 1. 核心求购关键词 (权重高)
    for kw in config.PURCHASE_KEYWORDS:
        if kw in text_lower:
            score += 20
            matched_keywords.append(kw)

    # 2. 面料品种关键词 (权重中)
    for kw in config.FABRIC_KEYWORDS:
        if kw.lower() in text_lower:
            score += 10
            matched_keywords.append(kw)

    # 3. 数量提及 (权重中)
    for pat in config.QUANTITY_PATTERNS:
        if re.search(pat, text_lower):
            score += 15
            break

    # 4. 紧急程度 (权重低)
    for level, keywords in config.URGENCY_KEYWORDS.items():
        for kw in keywords:
            if kw in text_lower:
                score += 5
                break

    # 5. 价格提及
    price_patterns = [r"\d+\.?\d*\s*[元块]", r"\d+\.?\d*\s*/[米m]", r"报价", r"价格"]
    for pat in price_patterns:
        if re.search(pat, text_lower):
            score += 5
            break

    confidence = min(100, score)
    is_purchase = confidence >= 30
    return is_purchase, confidence


def _detect_urgency(text):
    """
    从文本检测紧急程度（修复版）

    将所有级别的关键词按长度从长到短排序后检查，
    长的先匹配，防止"不急"（低级别，2字）被"急"（高级别，1字）的子串匹配抢先。
    同一长度时按级别优先级 高→中→低 排序。
    """
    if not text:
        return "中"
    text_lower = text.lower()

    # 收集所有关键词及其级别
    all_keywords = []
    level_priority = {"高": 0, "中": 1, "低": 2}
    for level, keywords in config.URGENCY_KEYWORDS.items():
        for kw in keywords:
            all_keywords.append((kw, level))

    # 按长度降序排列，同长度按级别优先级排列
    all_keywords.sort(key=lambda x: (-len(x[0]), level_priority.get(x[1], 9)))

    for kw, level in all_keywords:
        if kw in text_lower:
            return level

    return "中"


def _extract_fabric_types(text):
    """提取文本中所有面料品种（返回 set）"""
    if not text:
        return set()
    text_lower = text.lower()
    found = set()
    for kw in config.FABRIC_KEYWORDS:
        if kw.lower() in text_lower:
            found.add(kw)
    return found


def _extract_specific_fabrics(text):
    """提取具体面料品种（排除'布'、'面料'等通用词）"""
    return _extract_fabric_types(text) - _GENERIC_FABRICS


def extract_purchase_info(text, group_name="", sender_name="", timestamp=""):
    """
    从求购消息中提取结构化信息

    Returns:
        PurchaseRequest 对象
    """
    pr = PurchaseRequest()
    pr.group_name = group_name
    pr.sender_name = sender_name
    pr.timestamp = timestamp or datetime.now().strftime("%Y-%m-%d %H:%M")
    pr.raw_text = text

    if not text:
        return pr

    text_lower = text.lower()

    # 提取面料品种（可能有多个，用 / 分隔）
    fabric_types = _extract_specific_fabrics(text)
    if fabric_types:
        pr.fabric_type = " / ".join(sorted(fabric_types))
    else:
        # 回退到通用面料词
        for kw in config.FABRIC_KEYWORDS:
            if kw.lower() in text_lower:
                pr.fabric_type = kw
                break

    # 提取规格 (如 228T, 300T, 75D*75D 等)
    spec_patterns = [
        r"(\d{2,3}[TtDd])",
        r"(\d+[Dd]\s*[×x*]\s*\d+[Dd])",
        r"(\d+[Dd]/\d+[Ff])",
        r"(\d+[gG][/\s][mM²]+)",
        r"(克重\s*\d+)",
        r"(幅宽\s*\d+)",
        r"(\d+[cC][mM])",
    ]
    specs = []
    for pat in spec_patterns:
        matches = re.findall(pat, text)
        specs.extend(matches)
    if specs:
        pr.specification = " | ".join(specs[:3])

    # 提取数量
    for pat in config.QUANTITY_PATTERNS:
        match = re.search(pat, text)
        if match:
            pr.quantity = match.group(0)
            break

    # 提取颜色
    color_keywords = [
        "白色", "黑色", "灰色", "红色", "蓝色", "绿色", "黄色",
        "粉色", "紫色", "橙色", "咖啡色", "米色", "卡其色",
        "藏青", "酒红", "军绿", "驼色", "杏色", "雾蓝", "烟灰",
        "本白", "漂白", "象牙白", "宝蓝", "天蓝", "湖蓝",
    ]
    for c in color_keywords:
        if c in text:
            pr.color = c
            break

    # 提取紧急程度（使用修复后的函数）
    pr.urgency = _detect_urgency(text)

    # 提取价格
    price_match = re.search(r"(\d+\.?\d*)\s*[元块/]", text)
    if price_match:
        pr.price_mentioned = price_match.group(0)

    # 提取联系方式
    phone_match = re.search(r"(1[3-9]\d{9})", text)
    if phone_match:
        pr.contact_info = phone_match.group(0)

    # 提取置信度
    _, pr.confidence = is_purchase_request(text)

    return pr


# ============================================================
#  消息分浪 & 话题切换检测
# ============================================================

def _group_into_bursts(indexed_msgs, gap_seconds=300):
    """
    将同一发送人的消息按时间间隔分成多个"浪"（burst）。

    同一浪内的消息是连续发送的（间隔 < gap_seconds），
    不同浪之间有明显停顿，通常代表不同时间段的不同需求。

    Args:
        indexed_msgs: list of (index, msg)，已按时间排序
        gap_seconds: 间隔阈值，默认5分钟

    Returns:
        list of list of (index, msg)
    """
    if not indexed_msgs:
        return []

    bursts = []
    current_burst = [indexed_msgs[0]]

    for i in range(1, len(indexed_msgs)):
        prev_time = _parse_time(indexed_msgs[i - 1][1].get("timestamp", ""))
        curr_time = _parse_time(indexed_msgs[i][1].get("timestamp", ""))

        gap = None
        if prev_time and curr_time:
            gap = (curr_time - prev_time).total_seconds()

        # 同一个人连续发 → 同一浪；间隔太长 → 新浪
        if gap is not None and gap <= gap_seconds:
            current_burst.append(indexed_msgs[i])
        else:
            bursts.append(current_burst)
            current_burst = [indexed_msgs[i]]

    if current_burst:
        bursts.append(current_burst)

    return bursts


def _has_topic_shift(text1, text2):
    """
    检测两段文字之间是否发生了话题切换（不同的购买需求）。

    判定条件（满足任一即为切换）:
      1. text2 以分隔词开头（"还有"、"另外"、"第二个"等）
      2. 两段文字提到了不同的具体面料品种
    """
    # 条件 1: 显式分隔词
    for word in _SEPARATOR_WORDS:
        if text2.strip().startswith(word):
            return True

    # 条件 2: 不同的具体面料品种
    fabrics1 = _extract_specific_fabrics(text1)
    fabrics2 = _extract_specific_fabrics(text2)

    if fabrics1 and fabrics2:
        # 两边都有具体面料，看是否出现了新品种
        new_in_text2 = fabrics2 - fabrics1
        # 如果 text2 的面料完全包含 text1 的面料（超集），说明在补充细节，不是切换
        if new_in_text2 and not fabrics1.issubset(fabrics2):
            return True

    return False


def _split_into_topics(burst):
    """
    在一个消息浪内，把文字消息按话题切换点拆分成多段。

    每段代表一个独立的购买需求。
    图片不在这里处理，后续由 _associate_images 就近绑定。

    Returns:
        list of dict:
            {"text_items": [(index, msg), ...],
             "image_items": [(index, msg), ...]}
    """
    text_items = [(i, m) for i, m in burst if m.get("msg_type") == "text"]
    image_items = [(i, m) for i, m in burst if m.get("msg_type") == "image"]

    if not text_items:
        # 全是图片（或空），整浪作为一段
        return [{"text_items": [], "image_items": image_items}]

    # 按话题切换点拆分文字
    segments = []
    current_texts = [text_items[0]]

    for k in range(1, len(text_items)):
        prev_text = text_items[k - 1][1].get("text", "")
        curr_text = text_items[k][1].get("text", "")

        if _has_topic_shift(prev_text, curr_text):
            segments.append(current_texts)
            current_texts = [text_items[k]]
        else:
            current_texts.append(text_items[k])

    segments.append(current_texts)

    # 构建结果
    result = []

    # 计算各文字段之间的中点，用作图片分配的分界线
    # 例：段1的文字在 index 0~1, 段2在 index 3~4 → 分界线 = 2
    seg_boundaries = []  # [(lower_bound, upper_bound, seg_index)]
    for si, seg_texts in enumerate(segments):
        first_idx = seg_texts[0][0]
        last_idx = seg_texts[-1][0]
        seg_boundaries.append((first_idx, last_idx, si))

    # 为每个图片找到最近的文字段
    result = [{"text_items": st, "image_items": []} for st in segments]

    for img_item in image_items:
        img_idx = img_item[0]
        img_time = _parse_time(img_item[1].get("timestamp", ""))

        best_si = 0
        best_dist = float("inf")

        for first_idx, last_idx, si in seg_boundaries:
            if img_time:
                # 优先用时间距离：找该段内时间最近的文字
                for ti, tmsg in segments[si]:
                    t_time = _parse_time(tmsg.get("timestamp", ""))
                    if t_time:
                        dist = abs((img_time - t_time).total_seconds())
                        if dist < best_dist:
                            best_dist = dist
                            best_si = si
            else:
                # 回退到 index 距离：到该段中心点的距离
                center = (first_idx + last_idx) / 2
                dist = abs(img_idx - center)
                if dist < best_dist:
                    best_dist = dist
                    best_si = si

        result[best_si]["image_items"].append(img_item)

    return result


# ============================================================
#  批量检测（核心入口）
# ============================================================

def batch_detect(messages):
    """
    批量检测求购信息（话题感知图文关联版）

    核心逻辑：
    1. 同一群、同一人的消息按时间排列
    2. 连续发的消息归为"一浪"（间隔 < 5分钟）
    3. 浪内检测话题切换：
       - 面料品种变了 → 不同需求
       - 出现"还有""另外"等分隔词 → 不同需求
    4. 图片就近绑定最近的那段文字
    5. 不同浪 = 不同时间段 = 不同需求

    典型场景：
    - 张三: [图片][图片] "需要这两种布各5000米" → 1条求购（含2张图）
    - 李四: [图片] "找这种春亚纺" → [图片] "还有这种塔丝隆" → 2条求购
    - 王五: [图片]  →  [5分钟后] [图片] "这个也要" → 2条求购（分两浪）

    Args:
        messages: list of dict, 每个包含:
            - text: 消息文本
            - group_name: 群名
            - sender_name: 发送人
            - sender_wxid: 发送人微信ID (可选)
            - timestamp: 时间
            - msg_type: 消息类型 (text/image/video/...)
            - image_path: 图片路径 (msg_type=image时)

    Returns:
        list of PurchaseRequest
    """
    # ===== 第一步：按"群+发送人"分组 =====
    sender_groups = {}
    for i, msg in enumerate(messages):
        gn = msg.get("group_name", "") or "私聊"
        sender = msg.get("sender_name", "") or msg.get("sender_wxid", "unknown")
        key = (gn, sender)
        if key not in sender_groups:
            sender_groups[key] = []
        sender_groups[key].append((i, msg))

    purchases = []
    request_counter = {}  # 用于生成 request_id

    # ===== 第二步：对每个 (群+发送人) 做分浪 + 话题拆分 + 图文关联 =====
    for (gn, sender), indexed_msgs in sender_groups.items():
        indexed_msgs.sort(key=lambda x: _parse_time(x[1].get("timestamp", "")))

        bursts = _group_into_bursts(indexed_msgs, gap_seconds=300)

        for burst in bursts:
            segments = _split_into_topics(burst)
            req_seq = 0

            for seg in segments:
                text_items = seg["text_items"]
                image_items = seg["image_items"]

                # 合并文字
                all_text_parts = [m.get("text", "") for _, m in text_items]
                combined_text = " ".join(t for t in all_text_parts if t)

                # 收集图片路径
                image_paths = [
                    m.get("image_path", "") for _, m in image_items
                    if m.get("image_path")
                ]

                # ---------- 情况 A: 有文字 → 检测是否求购 ----------
                if combined_text.strip():
                    is_pur, confidence = is_purchase_request(combined_text)

                    if is_pur:
                        # 取最有信息量的那条文字做结构化提取
                        best_text = max(
                            all_text_parts,
                            key=lambda t: len(t) if t else 0
                        )
                        ts = text_items[0][1].get("timestamp", "")
                        pr = extract_purchase_info(best_text, gn, sender, ts)
                        pr.sender_wxid = text_items[0][1].get("sender_wxid", "")
                        pr.images = image_paths

                        # 多条文字合并时，保留完整原文
                        if len(all_text_parts) > 1:
                            pr.raw_text = combined_text

                        # 有图片 → 置信度 +10
                        if image_paths:
                            pr.confidence = min(100, pr.confidence + 10)

                        # 生成需求编号
                        req_seq += 1
                        req_key = f"{gn}:{sender}"
                        request_counter[req_key] = request_counter.get(req_key, 0) + 1
                        pr.request_id = f"{req_key}#{request_counter[req_key]}"

                        purchases.append(pr)
                        continue

                # ---------- 情况 B: 纯图片（无文字或文字不是求购） ----------
                if image_items and not text_items:
                    # 检查浪内是否有面料相关文字（非求购型）
                    other_texts = [
                        m.get("text", "") for _, m in burst
                        if m.get("msg_type") == "text" and m.get("text")
                    ]
                    all_other = " ".join(other_texts)
                    has_fabric = any(
                        kw.lower() in all_other.lower()
                        for kw in config.FABRIC_KEYWORDS
                    )

                    if has_fabric:
                        pr = PurchaseRequest()
                        pr.group_name = gn
                        pr.sender_name = sender
                        pr.timestamp = image_items[0][1].get("timestamp", "")
                        pr.raw_text = (
                            f"[发送了{len(image_items)}张面料图片] "
                            f"{all_other[:100]}"
                        )
                        pr.images = image_paths
                        pr.confidence = 25

                        if all_other:
                            _, conf = is_purchase_request(all_other)
                            if conf > 0:
                                pr.confidence = min(100, 25 + conf)
                                extracted = extract_purchase_info(
                                    all_other, gn, sender, pr.timestamp
                                )
                                pr.fabric_type = extracted.fabric_type
                                pr.quantity = extracted.quantity
                                pr.specification = extracted.specification

                        req_seq += 1
                        req_key = f"{gn}:{sender}"
                        request_counter[req_key] = request_counter.get(req_key, 0) + 1
                        pr.request_id = f"{req_key}#{request_counter[req_key]}"

                        purchases.append(pr)

    # 按置信度排序（急单和高置信度排前面）
    purchases.sort(key=lambda x: (
        0 if x.urgency == "高" else (1 if x.urgency == "中" else 2),
        -x.confidence,
    ))

    return purchases
