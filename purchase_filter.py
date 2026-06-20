"""微信求购监控系统 - 求购意图过滤器

区分"我要买"（真实求购）和"我有货"（推销广告），
大幅减少误报，只推送真实采购需求。

过滤逻辑：
  1. 检测推销特征（selling patterns）
  2. 检测求购特征（buying patterns）
  3. 综合评分，低于阈值的过滤掉
"""

import re

# ============================================================
# 推销/广告特征（"我有货，你来买"）
# ============================================================

# 强推销信号：命中任一个基本可以确定是广告
STRONG_SELL_PATTERNS = [
    # 现货供应类
    r"现货.{0,5}(供应|出售|批发|出|秒排|接单)",
    r"(供应|出售|批发|秒排|接单).{0,5}现货",
    r"大量.{0,4}(现货|在机|库存)",
    r"(工厂|厂家).{0,4}(直销|直供|供应|现货)",
    r"机台.{0,4}(现货|在机)",
    r"常年.{0,4}(现货|在机|供应|接单)",
    # 推销行为
    r"(清仓|清库).{0,5}(包邮|超低价|处理)",
    r"欢迎.{0,4}(询价|下单|咨询|来电|联系)",
    r"需要.{0,4}(的老板|的来|联系我|私)",
    r"可.{0,2}(随时|立即).{0,3}(接单|排单|发货|安排)",
    # 广告格式
    r"(电话|微信|手机).{0,3}(\d[\d\s\-]{6,}|\d{11})",
    r"(更多规格|详情请).{0,5}(私信|联系|咨询)",
    r"(主营|专营|专业).{0,15}(各种|各类|多种)",
]

# 中等推销信号：加分但不决定
MEDIUM_SELL_PATTERNS = [
    r"现货",                    # 单独的"现货"可能是推销也可能是求购
    r"在机",
    r"库存\s*\d+",              # "库存600件"
    r"成品\s*\d+g",             # "成品102gsm"
    r"(规格|门幅|克重).{0,5}\d+",
    r"(大量|充足|齐全)",
    r"(接单|排单|下单)",
    r"(二批|批发|零售)",
    r"供(二批|市场|客户)",
]

# 弱推销信号
WEAK_SELL_PATTERNS = [
    r"📦",                      # emoji 广告风格
    r"✅.{0,20}✅",
    r"[💰🏭📞]{2,}",           # 多个商业 emoji
]


# ============================================================
# 求购特征（"我要买"）
# ============================================================

# 强求购信号：命中任一个基本可以确定是真实需求
STRONG_BUY_PATTERNS = [
    r"(求购|急求|急需|收布|收面料|收坯布)",
    r"(帮我找|帮我问|帮忙找|帮忙问)",
    r"(谁家有|哪家有|哪家能做|谁家有货|哪里有|谁有|哪家有)",
    r"(有货吗|有吗|有没有|有做吗|能做吗)",
    r"(找布|找货|找样|找一下|寻找)",
    r"(要下单|想订|想订购|要采购|需采购)",
    r"(报个价|发个价|怎么卖|什么价|多少一)",
    r"(询价|问一下|问个价)",
]

# 中等求购信号
MEDIUM_BUY_PATTERNS = [
    r"(需要|要)\s*\d+\s*(米|m|码|匹|卷|公斤|吨|件|条|万)",
    r"(需要|要|想找|想看).{0,5}(面料|布|坯布|胚布|样布|色卡)",
    r"(有没有|可否).{0,5}(推荐|介绍|提供)",
    r"(\d+).{0,3}(米|m|码).{0,5}(左右|以上|起)",
]

# 弱求购信号（需要和其他信号配合）
WEAK_BUY_PATTERNS = [
    r"(需要|要|找|寻|收)",
    r"(怎么卖|什么价|报价)",
    r"(能发吗|能寄吗|能做吗)",
]


# ============================================================
# 无关信息过滤
# ============================================================

IRRELEVANT_PATTERNS = [
    r"(招聘|招工|求职|应聘|岗位|薪资|月薪)",
    r"(物流|快递|发货|运输|专线|货运).{0,10}(联系|电话|价格)",
    r"(租房|出租|出售|二手房|门面)",
    r"(培训|课程|学习|教育)",
    r"(软件|APP|系统|开发|程序)",
]


# ============================================================
# 评分引擎
# ============================================================

def _count_matches(text, patterns):
    """统计文本匹配了多少个模式"""
    count = 0
    for pattern in patterns:
        if re.search(pattern, text, re.IGNORECASE):
            count += 1
    return count


def classify_intent(purchase):
    """
    判断一条检测到的求购是否为真实采购需求

    Args:
        purchase: PurchaseRequest 对象

    Returns:
        dict: {
            'is_real': bool,        # 是否为真实求购
            'buy_score': int,       # 求购得分
            'sell_score': int,      # 推销得分
            'confidence': str,      # 置信度: 高/中/低
            'reason': str,          # 判断理由
        }
    """
    text = purchase.raw_text or ""
    sender = purchase.sender_name or ""
    full_text = f"{sender} {text}"  # 发送人名也可能包含线索

    # 计算推销得分
    sell_score = 0
    sell_score += _count_matches(full_text, STRONG_SELL_PATTERNS) * 10
    sell_score += _count_matches(full_text, MEDIUM_SELL_PATTERNS) * 3
    sell_score += _count_matches(full_text, WEAK_SELL_PATTERNS) * 1

    # 发送人名含推销特征加分
    seller_sell = 0
    seller_sell += _count_matches(sender, [
        r"(批发|供应|工厂|纺织|实业|贸易)",
        r"(客服|推广|推广)",
        r"(清仓|清库|库存)",
    ]) * 5
    sell_score += seller_sell

    # 计算求购得分
    buy_score = 0
    buy_score += _count_matches(text, STRONG_BUY_PATTERNS) * 10
    buy_score += _count_matches(text, MEDIUM_BUY_PATTERNS) * 5
    buy_score += _count_matches(text, WEAK_BUY_PATTERNS) * 2

    # 无关信息检测
    irrelevant = _count_matches(full_text, IRRELEVANT_PATTERNS) > 0

    # 私聊消息过滤（可能是自己发的或软件消息）
    gn = (purchase.group_name or "").strip()
    is_private = (gn == "" or gn == "私聊" or gn == "(私聊)")

    # ---- 综合判断 ----
    result = {
        'buy_score': buy_score,
        'sell_score': sell_score,
        'confidence': '低',
        'is_real': False,
        'reason': '',
    }

    # 无关信息直接过滤
    if irrelevant:
        result['reason'] = '无关信息（招聘/物流/租房等）'
        return result

    # 私聊消息过滤（可能是自己发的或软件消息）
    if is_private:
        result['reason'] = '私聊消息，跳过'
        return result

    # 纯推销：推销得分远高于求购得分
    if sell_score >= 15 and buy_score < 10:
        result['reason'] = f'推销广告（推销分{sell_score}，求购分{buy_score}）'
        return result

    # 推销为主：推销分是求购分的2倍以上，即使求购分不低也过滤
    if sell_score >= 20 and sell_score > buy_score * 2:
        result['reason'] = f'推销为主（推销分{sell_score}远超求购分{buy_score}）'
        return result

    # 强求购信号
    if buy_score >= 10:
        if sell_score < 10:
            result['is_real'] = True
            result['confidence'] = '高'
            result['reason'] = f'明确求购意图（求购分{buy_score}）'
        else:
            # 同时有求购和推销信号，可能是"求购+顺带推销"
            result['is_real'] = True
            result['confidence'] = '中'
            result['reason'] = f'求购为主（求购分{buy_score}，推销分{sell_score}）'
        return result

    # 中等求购信号
    if buy_score >= 5 and sell_score < 8:
        result['is_real'] = True
        result['confidence'] = '中'
        result['reason'] = f'可能求购（求购分{buy_score}，推销分{sell_score}）'
        return result

    # 信号不足
    result['reason'] = f'信号不足（求购分{buy_score}，推销分{sell_score}）'
    return result


def filter_purchases(purchases, min_confidence='低'):
    """
    过滤求购信息，只保留真实采购需求

    Args:
        purchases: list of PurchaseRequest
        min_confidence: 最低置信度 ('高'/'中'/'低')

    Returns:
        list of PurchaseRequest: 过滤后的真实求购列表
    """
    confidence_rank = {'高': 3, '中': 2, '低': 1}
    min_rank = confidence_rank.get(min_confidence, 1)

    filtered = []
    for p in purchases:
        result = classify_intent(p)
        if result['is_real'] and confidence_rank.get(result['confidence'], 0) >= min_rank:
            # 把过滤结果附加到 purchase 对象上
            p.intent_result = result
            filtered.append(p)

    return filtered
