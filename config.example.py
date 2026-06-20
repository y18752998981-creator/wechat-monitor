"""微信求购监控系统 - 配置文件

使用前请修改下面 marked with <<< 的配置项
"""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
EXPORTS_DIR = os.path.join(DATA_DIR, "exports")
IMAGES_DIR = os.path.join(DATA_DIR, "images")
REPORTS_DIR = os.path.join(DATA_DIR, "reports")

# ===== Telegram 推送配置 ===== <<<
# Bot Token: 在 Telegram 搜索 @BotFather，发送 /newbot 创建
# Chat ID: 在 Telegram 搜索 @userinfobot 获取你的 ID
TELEGRAM_BOT_TOKEN = ""          # <<< 填入你的 Bot Token
TELEGRAM_CHAT_ID = ""            # <<< 填入你的 Chat ID
# HTTP 代理（WARP 等系统级 VPN 不需要配，留空即可）
TELEGRAM_PROXY = ""

# ===== DeepSeek AI 配置 ===== <<<
# 获取 API Key: https://platform.deepseek.com/api_keys
DEEPSEEK_API_KEY = ""            # <<< 填入你的 DeepSeek API Key

# ===== wechat-decrypt 配置 ===== <<<
# 项目地址: https://github.com/ylytdeng/wechat-decrypt
# 支持微信 4.x（SQLCipher 4 加密），通过扫描进程内存获取密钥
# 安装步骤:
#   git clone https://github.com/ylytdeng/wechat-decrypt.git
#   cd wechat-decrypt && pip install -r requirements.txt
WECHAT_DECRYPT_PATH = r"C:\Users\你的用户名\wechat-decrypt"  # <<< 修改为你的实际路径
# wechat-decrypt 解密后的数据库目录（运行 main.py decrypt 后生成）
WECHAT_DECRYPT_DB_DIR = os.path.join(WECHAT_DECRYPT_PATH, "decrypted")
# wechat-decrypt 导出的消息文件目录（JSON/CSV）
WECHAT_DECRYPT_EXPORT_DIR = os.path.join(WECHAT_DECRYPT_PATH, "exports")
# 旧版兼容路径
WX_EXPORT_DIR = os.path.join(EXPORTS_DIR, "wx_messages")


# ===== 多微信账号配置（可选） ===== <<<
# 自动扫描本机所有已登录过的微信号，每个账号独立解密、独立监控
# 不需要多账号监控就保持空列表: WECHAT_ACCOUNTS = []
WECHAT_ACCOUNTS_DIR = r"C:\Users\你的用户名\Documents\xwechat_files"  # <<< 微信账号数据根目录
WECHAT_ACCOUNTS = [
    # {
    #     "name": "你的微信号（用于日志显示）",
    #     "account_dir": r"C:\Users\你的用户名\Documents\xwechat_files\你的微信号_xxx",
    #     "db_dir": r"...\db_storage",
    #     "keys_file": r"<wechat-decrypt目录>\keys_你的微信号.json",
    #     "decrypted_dir": r"<wechat-decrypt目录>\decrypted_你的微信号",
    # },
    # 添加更多账号按同样格式追加...
]
# ===== 扫描频率配置 =====
# 工作时段（每N分钟扫一次）
BUSY_HOURS_START = 8   # 早上8点
BUSY_HOURS_END = 22    # 晚上10点
BUSY_INTERVAL = 30     # 工作时段每30分钟扫一次
IDLE_INTERVAL = 60     # 非工作时段每60分钟扫一次

# ===== 求购信息识别关键词 =====
# 核心关键词（命中即判定为求购）
PURCHASE_KEYWORDS = [
    "求购", "急求", "需要", "找布", "找货", "有没有", "要货",
    "急需", "收布", "收面料", "寻", "找一下", "帮我找",
    "谁家有", "哪家有", "有货吗", "能做吗", "能发吗",
    "要下单", "想订", "报个价", "发个价格", "怎么卖",
    "要采购", "采购", "询价", "要买",
    "还要", "也要", "还需要", "想要", "也要找", "顺便找",
]

# 面料品种关键词
FABRIC_KEYWORDS = [
    "塔丝隆", "春亚纺", "雪纺", "桃皮绒", "涤塔夫", "尼丝纺",
    "仿真丝", "真丝", "涤纶", "锦纶", "尼龙", "氨纶", "阳离子",
    "牛津布", "帆布", "牛仔", "针织", "梭织", "提花", "印花",
    "涂层", "复合", "贴膜", "防水", "阻燃", "抗静电",
    "T400", "T800", "DTY", "FDY", "POY",
    "228T", "290T", "300T", "380T",
    "大力马", "Dyneema", "Cordura", "考杜拉",
    "色丁", "缎面", "乔其", "双绉", "绉布",
    "弹力", "四面弹", "经编", "纬编", "网布", "蕾丝",
    "摇粒绒", "珊瑚绒", "法兰绒", "灯芯绒",
    "胚布", "坯布", "白坯", "色布", "染色",
    "面料", "布", "织物", "纺织品",
]

# 数量关键词模式
QUANTITY_PATTERNS = [
    r"(\d+)\s*[米m]",
    r"(\d+)\s*米",
    r"(\d+)\s*[公斤kg]",
    r"(\d+)\s*[吨t]",
    r"(\d+)\s*[匹]",
    r"(\d+)\s*[卷]",
    r"(\d+)\s*[件]",
    r"(\d+)\s*万米",
    r"(\d+)\s*万",
    r"(\d+)\s*[条]",
    r"(\d+)\s*[码]",
]

# 紧急程度关键词
URGENCY_KEYWORDS = {
    "高": ["急", "急单", "马上要", "立刻", "今天", "本周", "赶货", "催", "火急"],
    "中": ["尽快", "近期", "这几天", "月底前", "下周"],
    "低": ["不急", "慢慢来", "有空看看", "了解一下"],
}

# ===== 活跃群判定 =====
# 一个群在N天内有M条以上求购信息 → 标记为"重点群"
ACTIVE_GROUP_THRESHOLD_DAYS = 7
ACTIVE_GROUP_THRESHOLD_PURCHASES = 3

# ===== 图片识别 =====
# 面料相关图片的判断（后续用 AI 视觉模型）
FABRIC_IMAGE_KEYWORDS = ["色卡", "样布", "面料", "布", "纹路", "花型", "色样"]
