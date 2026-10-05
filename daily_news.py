"""Daily tech news digest - auto fetch, summarize and email."""

import os, sys, csv, json, re, time, logging, threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse
from xml.etree import ElementTree as ET
import requests

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"), override=False)
except ImportError:
    pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
ANTHROPIC_AUTH_TOKEN = os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY") or ""
_base = os.environ.get("ANTHROPIC_BASE_URL") or os.environ.get("LLM_BASE_URL") or "https://pool.autelrobotics.com"
_base = _base.rstrip("/")
# 不同厂商端点路径不同：智谱是 /api/paas/v4，SiliconFlow/OpenAI 是 /v1
# 如果 base_url 已含版本路径就不再加 /v1
if re.search(r"/v\d+$", _base) or "/api/paas/" in _base:
    LLM_BASE_URL = _base
else:
    LLM_BASE_URL = _base + "/v1"
LLM_MODEL      = os.environ.get("LLM_MODEL", "claude-opus-4-6")
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
TO_EMAIL       = [e.strip() for e in os.environ.get("TO_EMAIL", "yimingjiang216@gmail.com").split(",") if e.strip()]
FROM_EMAIL     = os.environ.get("FROM_EMAIL", "onboarding@resend.dev")
CSV_PATH       = os.environ.get("CSV_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "媒体洞察调研看板_素材库_表格.csv"))
RSSHUB_URL     = os.environ.get("RSSHUB_URL", "https://rsshub.bestblogs.dev")  # 公共RSSHub镜像
FEISHU_WEBHOOK = os.environ.get("FEISHU_WEBHOOK", "https://open.feishu.cn/open-apis/bot/v2/hook/f2db3751-5063-4969-a732-54694a70731e")
# 测试模式：只推飞书，不发邮件、不写 last_sent.json，手动试跑不污染正式去重库也不重复发信。
# 测试模式绝不回退到正式群——没配 FEISHU_WEBHOOK_TEST 就一条都不发。
TEST_MODE           = os.environ.get("TEST_MODE", "").strip() == "1"
FEISHU_WEBHOOK_TEST = os.environ.get("FEISHU_WEBHOOK_TEST", "")
if TEST_MODE:
    FEISHU_WEBHOOK = FEISHU_WEBHOOK_TEST
FEISHU_SECRET  = os.environ.get("FEISHU_SECRET", "")



CSV_HEADERS = ["序号","素材链接","来源平台","领域主题","核心厂家/产品","核心观点","主要结论","内容倾向","聚类标签","调研报告","关联历史报告","素材标题","日期"]

RSS_SOURCES = [
    ("OpenAI Blog",          "https://openai.com/blog/rss.xml",                                        5),
    ("Google DeepMind",      "https://deepmind.google/blog/rss.xml",                                   5),
    ("Hugging Face Blog",    "https://huggingface.co/blog/feed.xml",                                   5),
    ("Mistral Blog",         "https://mistral.ai/news/rss",                                            5),
    ("Google AI Blog",       "https://blog.google/technology/ai/rss/",                                 4),
    ("Apple ML Research",    "https://machinelearning.apple.com/rss.xml",                              4),
    ("Microsoft Research",   "https://www.microsoft.com/en-us/research/feed/",                         4),
    ("Sam Altman",           "http://blog.samaltman.com/posts.atom",                                   5),
    ("Andrej Karpathy",      "https://karpathy.github.io/feed.xml",                                    5),
    ("Francois Chollet",     "https://medium.com/feed/@francois.chollet",                              4),
    ("Stratechery",          "https://stratechery.com/feed/",                                          5),
    ("Benedict Evans",       "https://www.ben-evans.com/benedictevans/rss.xml",                        4),
    ("Y Combinator",         "https://www.ycombinator.com/blog/rss",                                   4),
    ("量子位",               "https://www.qbitai.com/feed",                                            5),
    ("极客公园",             "https://www.geekpark.net/rss",                                           5),
    ("虎嗅",                 f"{RSSHUB_URL}/huxiu/article",                                           4),
    ("少数派",               "https://sspai.com/feed",                                                 4),
    ("InfoQ",                "https://www.infoq.cn/feed",                                              4),
    ("钛媒体",               "https://www.tmtpost.com/rss",                                            4),
    ("爱范儿",               "https://www.ifanr.com/feed",                                             3),
    ("雷峰网",               "https://www.leiphone.com/feed",                                          3),
    ("新浪科技",             "https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=2509&k=&num=50&page=1", 3),
    ("IT之家",               "https://www.ithome.com/rss/",                                            4),
    ("cnBeta",               "https://www.cnbeta.com.tw/backend.php",                                  3),
    ("TechCrunch AI",        "https://techcrunch.com/category/artificial-intelligence/feed/",          4),
    ("Engadget",             "https://www.engadget.com/rss.xml",                                       3),
    ("The Decoder",          "https://the-decoder.com/feed/",                                          4),
    ("Interesting Engineering","https://interestingengineering.com/rss",                               3),
    ("MIT Tech Review",      "https://www.technologyreview.com/feed/",                                 4),
    ("Wired AI",             "https://www.wired.com/feed/category/artificial-intelligence/rss",        3),
    ("Ars Technica AI",      "https://feeds.arstechnica.com/arstechnica/technology-lab",               3),
    ("NVIDIA Blog",          "https://blogs.nvidia.com/feed/",                                         5),
    ("IEEE Spectrum AI",     "https://spectrum.ieee.org/feeds/topic/artificial-intelligence.rss",      4),
    ("Science Daily AI",     "https://www.sciencedaily.com/rss/computers_math/artificial_intelligence.xml", 3),
    ("Towards Data Science", "https://towardsdatascience.com/feed",                                    3),

    # == 无人机 ==
    ("Drone DJ",             "https://dronedj.com/feed/",                                             5),
    ("Drone Life",           "https://dronelife.com/feed/",                                           4),
    ("The War Zone",         "https://www.thedrive.com/the-war-zone/feed",                             4),
    ("DroneXL",              "https://dronexl.co/feed/",                                               4),

    # == 具身机器人 / 人形机器人 ==
    ("The Robot Report",     "https://www.therobotreport.com/feed/",                                  5),
    ("IEEE Spectrum Robotics","https://spectrum.ieee.org/feeds/topic/robotics.rss",                   5),
    ("Robotics Business",    "https://www.roboticsbusinessreview.com/feed/",                          4),

    # == 算力芯片 / 半导体 ==
    ("SemiAnalysis",         "https://www.semianalysis.com/feed",                                     5),
    ("EE Times",             "https://www.eetimes.com/feed/",                                         4),
    ("Semiconductor Digest", "https://www.semiconductor-digest.com/feed/",                            4),
    ("Tom's Hardware",       "https://www.tomshardware.com/feeds/all",                                3),
    ("ServeTheHome",         "https://www.servethehome.com/feed/",                                    4),
    ("Blocks & Files",       "https://blocksandfiles.com/feed/",                                      3),

    # == 新型电池 / 硬科技 ==
    ("Electrek",             "https://electrek.co/feed/",                                             4),
    ("CleanTechnica",        "https://cleantechnica.com/feed/",                                       4),
    ("Ars Technica Hardware","https://feeds.arstechnica.com/arstechnica/gadgets",                     3),
    ("Energy Storage News",  "https://www.energy-storage.news/feed/",                                 4),
    ("PV Magazine",          "https://www.pv-magazine.com/feed/",                                     3),
    ("InsideEVs",            "https://www.insideevs.com/feed/",                                       3),
    ("The Drive",            "https://www.thedrive.com/feed/",                                        3),

    # == AI 巨头官方 Blog ==
    ("The Information",      "https://www.theinformation.com/feed",                                  5),
    ("Platformer",           "https://www.platformer.news/rss/",                                     4),
    ("Anthropic Blog",       "https://rsshub.bestblogs.dev/anthropic/news",                          5),
    ("Meta Engineering",     "https://engineering.fb.com/feed/",                                     5),
    ("AWS AI Blog",          "https://aws.amazon.com/blogs/machine-learning/feed/",                  4),
    ("ARM Blog",             "https://community.arm.com/arm-community-blogs/rss",                    4),
    ("Interconnects",        "https://www.interconnects.ai/feed",                                    4),

    # == 芯片深度分析 ==
    ("Semiconductor Engineering", "https://semiengineering.com/feed/",                               4),

    # == 机器人学术 ==
    ("IEEE RAS",             "https://www.ieee-ras.org/rss",                                         4),

    # == AI 聚合日报（通过 RSS，主力用 fetch_aihot_api 补充）==
    ("AIHOT AI日报",         "https://aihot.virxact.com/rss",                                         5),

    # == 一手源补强（2026-10-05 本地批量探测，确认能拉到 RSS 且发布节奏在 48h 窗口内）==
    ("NVIDIA Newsroom",     "https://nvidianews.nvidia.com/rss.xml?site=1",                            6),
    ("Google Research",     "https://research.google/blog/rss/",                                        6),
    ("Microsoft OnTheIssues","https://blogs.microsoft.com/on-the-issues/feed/",                         6),
    ("Samsung Newsroom",    "https://news.samsung.com/global/rss",                                      5),
    ("SK hynix Newsroom",   "https://news.skhynix.com/feed/",                                           5),
    ("The Verge AI",        "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",         4),
    ("FT Technology",       "https://www.ft.com/technology?format=rss",                                 4),  # 正文可能有付费墙，取标题+摘要
    ("36氪快讯",            f"{RSSHUB_URL}/36kr/newsflashes",                                           3),

    # == RSSHub 关键词搜索（覆盖五大赛道；权重压低，让一手源在同窗口内优先占名额）==
    ("36氪-大模型",          f"{RSSHUB_URL}/36kr/search/items/大模型",                               3),
    ("36氪-具身机器人",      f"{RSSHUB_URL}/36kr/search/items/具身机器人",                           3),
    ("36氪-人形机器人",      f"{RSSHUB_URL}/36kr/search/items/人形机器人",                           3),
    ("36氪-无人机",          f"{RSSHUB_URL}/36kr/search/items/无人机",                               3),
    ("36氪-算力芯片",        f"{RSSHUB_URL}/36kr/search/items/算力芯片",                             3),
    ("36氪-新型电池",        f"{RSSHUB_URL}/36kr/search/items/固态电池",                             3),
    ("虎嗅-AI",              f"{RSSHUB_URL}/huxiu/search/AI",                                        3),
    ("虎嗅-机器人",          f"{RSSHUB_URL}/huxiu/search/机器人",                                    3),
    ("虎嗅-无人机",          f"{RSSHUB_URL}/huxiu/search/无人机",                                    3),
    ("虎嗅-芯片",            f"{RSSHUB_URL}/huxiu/search/芯片",                                      3),
]

JINA_MAX_CHARS = 3000   # Jina 抓取正文的上限
LLM_CONTENT_CHARS = 3000  # 送 LLM 时每篇截断上限（与抓取一致，不额外截断）
RSS_PER_SOURCE = 6  # 每天跑一次，每源取6条确保覆盖充分
JINA_DELAY_SEC = 0.2  # 每线程抓取前的小睡；并行5路下0.2秒足够防429
FRESH_WINDOW_DAYS = 2   # 采样阶段就丢弃超过这个天数的文章，避免旧文占满名额

# 一手新闻室的发布节奏是每周1~2条（实测最新一条距今 64~93 小时），统一按 48h 筛
# 等于把它们全部筛掉——而这批源恰恰是唯一不能被二手报道替代的。
# 给它们单独放宽到 7 天；媒体/聚合源保持 2 天，速度是它们唯一的优势。
SLOW_FRESH_WINDOWS = {
    "NVIDIA Newsroom": 7,
    "Google Research": 7,
    "Microsoft OnTheIssues": 7,
    "Samsung Newsroom": 7,
    "SK hynix Newsroom": 7,
}


def _fresh_cutoffs():
    """返回 (默认截止日, {一手源名: 截止日})，格式 YYYY-MM-DD（UTC）。
    采样阶段和素材兜底筛必须用同一套，否则一边放宽一边砍回原样。"""
    now = datetime.now(timezone.utc)
    default = (now - timedelta(days=FRESH_WINDOW_DAYS)).strftime("%Y-%m-%d")
    slow = {name: (now - timedelta(days=d)).strftime("%Y-%m-%d")
            for name, d in SLOW_FRESH_WINDOWS.items()}
    return default, slow
RSS_CANDIDATE_MAX = 40  # 送 Jina/LLM 的 RSS 素材上限（全部为窗口内新鲜文章）
AIHOT_MATERIAL_MAX = 10 # aihot 聚合源补充的素材上限
LLM_EVENT_MAX = 22      # 单次 LLM 输出的事件条数上限，防正文生成被 max_tokens 截断

# 自动驾驶：2026-10-05 定「不并进五大赛道，除非有跨行业意义上的突破」。
# 突破与否交给阶段A的分数门槛裁定，不用关键词白名单——开城/路测/地方法规这类常规新闻按
# 【打分规则】（结构性影响、三个月后是否还被引用）本来就上不了8分，取消安全员、监管批准、
# 订单量破纪录这类才到8+。放行的条目仍要和赛道内条目一起过比较趟，不是保送。
AD_KWS = ["自动驾驶", "无人驾驶", "无人车", "无人卡车", "无人重卡",
          "robotaxi", "智驾", "辅助驾驶", "fsd", "waymo", "self-driving"]
AD_LANDMARK_MIN = 8
AD_MAX_PER_ISSUE = 1    # 每期最多留几条赛道外的标志性事件：超过1条就等于变相给它开了赛道
LLM_READ_TIMEOUT = 300   # 流式下两次收到数据之间的最大空档（含首token等待）
LLM_RETRY_DEADLINE = 900 # 单次 LLM 调用（含全部重试）的时间预算，超时直接失败

CATEGORY_COLORS = {
    # 赛道标签
    "AI大模型":   "#6C63FF",
    "算力芯片":   "#FF6584",
    "具身机器人": "#FF9A3C",
    "无人机":     "#43CFAE",
    "新型储能":   "#26C6DA",
    # 信息性质标签
    "技术突破":   "#AB47BC",
    "产业动态":   "#8BC34A",

    "其他":       "#90A4AE",
}

_HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


# ── 1. Fetch ──────────────────────────────────────────────────────────────────

def _is_digest_title(title):
    """判断标题是否是"资讯汇总"条目（如极客公园"极客早知道"、36氪快讯汇总）。

    特征（已用真实 feed 验证）：标题超长(>50字) 且含 ≥2 个新闻分隔符（；;｜|）。
    这类条目是一篇包含多条新闻的合集，不能当单一新闻送 LLM，直接跳过。
    独立文章标题短(通常<35字)且分隔符数为0，不受影响。
    """
    if not title:
        return False
    seps = title.count("；") + title.count(";") + title.count("｜") + title.count("|")
    # 也统计顿号密集（多个"、"可能也是并列新闻），但顿号在正常标题常见，仅作为辅助
    if len(title) > 50 and seps >= 2:
        return True
    return False


# 同一 host 最多同时在途请求数（防 rsshub 镜像这类公共实例被并发打挂返回429）
PER_HOST_LIMIT = 2
_host_semaphores = {}
_host_semaphores_lock = threading.Lock()

def _host_semaphore(url):
    host = urlparse(url).netloc or url
    with _host_semaphores_lock:
        sem = _host_semaphores.get(host)
        if sem is None:
            sem = _host_semaphores[host] = threading.Semaphore(PER_HOST_LIMIT)
    return sem


def _xml_root(content):
    """严格解析；仅在失败时宽松重试一次（常见坏源：正文里裸 & 没转义）"""
    try:
        return ET.fromstring(content)
    except ET.ParseError:
        text = content.decode("utf-8", "ignore")
        fixed = re.sub(r"&(?!(?:[A-Za-z][A-Za-z0-9]{1,10}|#\d{1,5}|#x[0-9A-Fa-f]{1,4});)",
                       "&amp;", text)
        return ET.fromstring(fixed.encode("utf-8"))


def _to_utc(pub_str):
    """把 RSS/Atom 的发布时间统一成 UTC 字符串，避免源所在时区让文章看起来提前一天过期"""
    try:
        dt = parsedate_to_datetime(pub_str)
    except Exception:
        return ""
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M")


def _pub_utc_str(art):
    """取文章的 UTC 发布时间（%Y-%m-%d %H:%M），取不到返回 ''"""
    for k in ("pub_utc", "pub", "pub_date", "published"):
        v = (str(art.get(k) or "")).strip()[:16].replace("T", " ").replace("/", "-")
        if re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$", v):
            return v
        v2 = v[:10]
        if re.match(r"^\d{4}-\d{2}-\d{2}$", v2):
            return v2 + " 00:00"
    return ""


def _age_hours(art):
    """距今小时数；无时间信息的给一个极大值，排在最后而不是被丢掉"""
    v = _pub_utc_str(art)
    if not v:
        return 1e9
    try:
        dt = datetime.strptime(v, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
    except ValueError:
        return 1e9
    return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 3600)


def _fetch_rss(source_name, rss_url, max_items):
    try:
        resp = None
        for attempt in range(2):  # 429/503限流时睡3秒重试一次
            with _host_semaphore(rss_url):
                r = requests.get(rss_url, headers=_HTTP_HEADERS, timeout=15)
            if r.status_code in (429, 503) and attempt == 0:
                log.warning(f"RSS 限流 [{source_name}]，3秒后重试")
                time.sleep(3)
                continue
            resp = r
            break
        if resp is None:
            return []
        resp.raise_for_status()
        text = resp.text
        # 新浪等接口返回JSON而非RSS，检测"{"开头
        stripped = text.strip()
        if stripped.startswith("{"):
            return _fetch_rss_json(source_name, stripped, max_items)
        root = _xml_root(resp.content)
        results = []
        # 兼容 RSS(<item>) 和 Atom(<entry>)，忽略命名空间（Atom 带 xmlns，直接用 tag 名匹配不到）
        def _localname(tag):
            return tag.split("}")[-1] if "}" in tag else tag
        all_entries = [e for e in root.iter() if _localname(e.tag) in ("item", "entry")]
        for item in all_entries[:max_items]:
            # 每个子元素按 local-name 取值（Atom/RSS 字段名不同）
            def _get(name):
                for child in item:
                    if _localname(child.tag) == name:
                        return (child.text or "").strip()
                return ""
            title = _get("title")
            # 资讯汇总条目（极客早知道等）直接跳过，不作为单条新闻
            if _is_digest_title(title):
                continue
            link = _get("link")
            if not link:
                # link 可能是 <link href="url"/>（Atom 用 href 属性，且可能在摘要里）
                for child in item:
                    if _localname(child.tag) == "link":
                        link = (child.get("href") or "").strip()
                        if link:
                            break
            pub_str = _get("pubDate") or _get("published") or _get("updated")
            desc = _get("description") or _get("summary") or _get("content")
            desc = re.sub(r"<[^>]+>", "", desc)
            if not title or not link:
                continue
            try:
                pub_readable = parsedate_to_datetime(pub_str).strftime("%Y-%m-%d %H:%M")
            except Exception:
                pub_readable = pub_str[:16]
            results.append({"title": title, "url": link, "rss_summary": desc[:300],
                             "content": "", "pub": pub_readable, "pub_utc": _to_utc(pub_str),
                             "source": source_name, "author": ""})
        return results
    except Exception as e:
        log.warning(f"RSS fetch failed [{source_name}]: {e}")
        return []


def _fetch_rss_json(source_name, json_text, max_items):
    """解析新浪科技等返回JSON的接口"""
    try:
        import json as _json
        data = _json.loads(json_text)
        # 新浪: result.data[]，每条含 title/url/intro/ctime/media_name
        items = data.get("result", {}).get("data", []) if isinstance(data, dict) else []
        results = []
        for it in items[:max_items]:
            title = (it.get("title") or it.get("stitle") or "").strip()
            link  = (it.get("url") or "").strip()
            summary = (it.get("intro") or it.get("summary") or "").strip()
            ctime = it.get("ctime") or it.get("intime") or ""
            author = (it.get("media_name") or it.get("author") or "").strip()
            if not title or not link:
                continue
            # 时间戳转可读
            pub_readable = ""
            pub_utc = ""
            try:
                pub_readable = datetime.fromtimestamp(int(ctime), timezone.utc).strftime("%Y-%m-%d %H:%M")
                pub_utc = pub_readable
            except Exception:
                pub_readable = str(ctime)[:16]
            results.append({"title": title, "url": link, "rss_summary": summary[:300],
                             "content": "", "pub": pub_readable, "pub_utc": pub_utc,
                             "source": source_name, "author": author})
        return results
    except Exception as e:
        log.warning(f"JSON feed failed [{source_name}]: {e}")
        return []


# 没有 RSS 的博客，用 Jina Reader 直接抓列表页
BLOG_SOURCES = [
    ("Figure AI",       "https://www.figure.ai/news",                   5),
    ("Boston Dynamics", "https://bostondynamics.com/blog/",              5),
    ("a16z AI",         "https://a16z.com/topic/artificial-intelligence/", 5),
    ("Intel Newsroom",  "https://newsroom.intel.com/",                   4),
    ("AMD News",        "https://community.amd.com/t5/blogs/bg-p/technicalarticlesblog", 4),
    ("Qualcomm Blog",   "https://www.qualcomm.com/news/onq",             4),
    ("Tesla AI Blog",   "https://www.tesla.com/en_us/blog",              4),
]

def _fetch_fulltext_jina(url):
    try:
        with _host_semaphore("https://r.jina.ai"):  # 与其他源共用按host限流，防免费额度被并发打爆
            resp = requests.get(
                f"https://r.jina.ai/{url}",
                headers={**_HTTP_HEADERS, "Accept": "text/plain", "X-Return-Format": "text", "X-Timeout": "15"},
                timeout=25,
            )
        if resp.status_code == 200:
            lines = resp.text.strip().splitlines()
            body, author, skip = [], "", True
            for line in lines:
                if skip and re.match(r"^(Title|URL|Published|Description):", line):
                    continue
                if skip and re.match(r"^Author:", line):
                    author = line[len("Author:"):].strip()
                    continue
                skip = False
                body.append(line)
            return "\n".join(body).strip()[:JINA_MAX_CHARS], author
    except Exception:
        pass
    return "", ""


def search_news():
    all_articles, seen = [], set()
    # 采样阶段就按发布时间筛掉旧文：官方博客几天不更新是常态，
    # 让它们占满名额会导致最终素材只剩十几条
    cutoff, slow_cutoffs = _fresh_cutoffs()
    dropped_stale = 0
    kept_slow = 0
    # 8路并行拉取；按 RSS_SOURCES 原始顺序收结果，保证与串行版输出完全一致
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = [ex.submit(_fetch_rss, name, url, RSS_PER_SOURCE)
                   for name, url, weight in RSS_SOURCES]
        for (source_name, rss_url, weight), fut in zip(RSS_SOURCES, futures):
            log.info(f"拉取 RSS: {source_name}")
            try:
                arts = fut.result()
            except Exception:
                arts = []
            my_cutoff = slow_cutoffs.get(source_name, cutoff)
            for art in arts:
                pub_day = _pub_utc_str(art)[:10]
                if pub_day and pub_day < my_cutoff:
                    dropped_stale += 1
                    continue
                key = re.sub(r"\s+", "", art["title"])[:30]
                if key and key not in seen:
                    seen.add(key)
                    art["weight"] = weight
                    art["_age_h"] = _age_hours(art)
                    if source_name in slow_cutoffs and art["_age_h"] > FRESH_WINDOW_DAYS * 24:
                        kept_slow += 1
                    all_articles.append(art)

    log.info(f"RSS 共拉取 {len(all_articles)} 篇（去重后，丢弃{dropped_stale}篇 {FRESH_WINDOW_DAYS} 天以外的旧文；"
             f"其中一手新闻室按7天窗口多留了{kept_slow}篇）")
    if not all_articles:
        return [], []

    # 分桶取样：确保每个赛道都有代表，而不是被AI新闻淹没
    track_keywords = {
        "无人机":   ["drone", "dji", "无人机", "evtol", "uav", "unmanned"],
        "机器人":   ["robot", "机器人", "humanoid", "具身", "robotics"],
        "芯片":     ["chip", "semi", "芯片", "anandtech", "nvidia blog", "gpu", "算力", "eetimes", "tom's hardware"],
        "电池储能": ["electrek", "cleantech", "电池", "储能", "battery", "ofweek"],
    }
    buckets = {t: [] for t in track_keywords}
    buckets["AI通用"] = []
    for art in all_articles:
        src = (art.get("source", "") or "").lower()
        title = (art.get("title", "") or "").lower()
        matched = False
        for track, kws in track_keywords.items():
            if any(k in src or k in title for k in kws):
                buckets[track].append(art)
                matched = True
                break
        if not matched:
            buckets["AI通用"].append(art)

    # 每个非AI赛道按weight排序取最多5条，AI通用取剩余名额
    # 同权重内按「文章更新」排序，确保名额给最新的那批而不是几天前的；
    # 单源最多贡献 PER_SOURCE_CAP 条，避免一个聚合媒体吃满名额、一手源进不来
    candidates = []
    TRACK_QUOTA = 5
    PER_SOURCE_CAP = 3

    def _take(bucket, n):
        picked, per = [], {}
        for art in bucket:
            s = art.get("source", "")
            if per.get(s, 0) >= PER_SOURCE_CAP:
                continue
            per[s] = per.get(s, 0) + 1
            picked.append(art)
            if len(picked) >= n:
                break
        return picked

    for track in ["无人机", "机器人", "芯片", "电池储能"]:
        bucket = sorted(buckets[track], key=lambda x: (-x.get("weight", 1), x.get("_age_h", 1e9)))
        taken = _take(bucket, TRACK_QUOTA)
        candidates.extend(taken)
        if taken:
            log.info(f"  赛道[{track}] 取 {len(taken)} 篇（共 {len(bucket)} 篇可选）")

    # AI通用补足到 RSS_CANDIDATE_MAX
    remaining_quota = max(RSS_CANDIDATE_MAX - len(candidates), 10)
    ai_sorted = sorted(buckets["AI通用"], key=lambda x: (-x.get("weight", 1), x.get("_age_h", 1e9)))
    ai_taken = _take(ai_sorted, remaining_quota)
    candidates.extend(ai_taken)
    log.info(f"  赛道[AI通用] 取 {len(ai_taken)} 篇（共 {len(ai_sorted)} 篇可选）")
    log.info(f"开始 Jina 抓取 {len(candidates)} 篇正文（5路并行）...")

    def _fill_fulltext(art):
        time.sleep(JINA_DELAY_SEC)  # 每线程发起前小睡，礼貌限速防429
        text, author = _fetch_fulltext_jina(art["url"])
        art["content"] = text if text else art.get("rss_summary", "")
        art["author"]  = author
        return art, bool(text)

    # map按提交顺序返回，日志编号与原串行版一致
    with ThreadPoolExecutor(max_workers=5) as ex:
        for i, (art, got_text) in enumerate(ex.map(_fill_fulltext, candidates)):
            log.info(f"  ({i+1}/{len(candidates)}): {art['title'][:50]}")
            log.info(f"    {'✓' if got_text else '↓'} {len(art['content'])} 字")

    # 合集已在 _fetch_rss 入口用 _is_digest_title 过滤，无需在此拆分正文

    # 返回(送LLM的30条, 全量原始清单)
    return candidates, all_articles




def web_search_company(company_name: str) -> str:
    """搜索公司主营业务，返回关键词摘要，用于分类核查兜底。"""
    try:
        q = requests.utils.quote(f"{company_name} company main business products")
        resp = requests.get(
            f"https://api.duckduckgo.com/?q={q}&format=json&no_html=1&skip_disambig=1",
            timeout=8, headers={"User-Agent": "Mozilla/5.0"})
        data = resp.json()
        text = data.get("AbstractText", "") or data.get("Answer", "") or ""
        if not text:
            text = " ".join(t.get("Text", "") for t in data.get("RelatedTopics", [])[:3])
        return text[:600].lower()
    except Exception:
        return ""

# 分类核查：根据搜索结果判断公司属于哪个赛道
TRACK_KEYWORDS = {
    "算力芯片": ["芯片","gpu","npu","半导体","晶圆","chip","semiconductor","processor","foundry"],
    "具身机器人": ["机器人","humanoid","robot","仿人","robotics"],
    "无人机": ["无人机","drone","evtol","uav","飞行器","unmanned"],
    "新型储能": ["电池","储能","battery","固态","energy storage"],
}

# 常见公司/机构名（跨期去重时剔除，避免"同一公司开头"的标题被误判重复）
# 注意：只保留中文名 + 足够长的英文名，避免短英文(meta/ibm/amd等)误删普通英文单词里的字母串
COMPANY_NAMES = [
    "openai", "anthropic", "google", "谷歌", "deepmind", "microsoft", "微软",
    "英伟达", "nvidia", "apple", "苹果", "amazon", "aws", "tesla", "特斯拉",
    "华为", "intel", "英特尔", "百度", "阿里", "阿里巴巴", "腾讯",
    "字节跳动", "bytedance", "京东", "美团", "小米", "xiaomi", "oppo", "vivo",
    "大疆", "dji", "宇树", "unitree", "figure ai", "软银", "softbank",
    "三星", "samsung", "sony", "索尼", "uber", "lyft", "netflix", "奈飞",
    "智谱", "zhipu", "月之暗面", "moonshot", "阶跃星辰", "stepfun", "kimi",
    "deepseek", "minimax", "零一万物", "百川", "讯飞", "iflytek",
]

def _strip_companies(text):
    """剔除标题中的公司名，用于跨期去重时避免"同公司开头"误判重复。"""
    if not text:
        return text
    t = text.lower()
    for c in COMPANY_NAMES:
        if c in t:
            t = t.replace(c, "")
    # 去掉剔除后可能残留的空格
    return re.sub(r"\s+", "", t)


def fetch_aihot_api(limit=30) -> list[dict]:
    """
    调用 aihot.virxact.com 公开 API 获取 AI 精选动态
    返回与 RSS 格式兼容的 dict 列表，权重 5
    """
    url = "https://aihot.virxact.com/api/public/items"
    ua  = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0"
    try:
        resp = requests.get(url,
            params={"mode": "selected", "take": limit},
            headers={"User-Agent": ua},
            timeout=15)
        if resp.status_code != 200:
            log.warning(f"aihot API 返回 {resp.status_code}")
            return []
        items = resp.json().get("items", [])
        results = []
        for item in items:
            title   = (item.get("title") or "").strip()
            summary = (item.get("summary") or item.get("description") or "").strip()
            link    = item.get("url") or item.get("link") or ""
            src     = item.get("source", {})
            src_name = src.get("name", "AIHOT") if isinstance(src, dict) else str(src)
            pub     = item.get("publishedAt") or item.get("createdAt") or ""
            if not title:
                continue
            results.append({
                "title":       title,
                "url":         link,
                "rss_summary": summary,
                "content":     summary,
                "pub":         pub[:16],
                "source":      f"AIHOT·{src_name}",
                "author":      "",
                "weight":      5,
            })
        log.info(f"aihot API 获取 {len(results)} 条")
        return results
    except Exception as e:
        log.warning(f"aihot API 失败: {e}")
        return []





# ── 2. LLM ────────────────────────────────────────────────────────────────────

def call_llm(messages):
    """
    调用大模型。为跨国网络做稳定性处理：
    - 每次重试都新建独立 Session（旧连接池可能已被污染/半死）
    - 流式(SSE)读取：长生成时连接始终有数据，不会被读超时掐断（实测非流式
      在34篇素材下两次挂满600秒读超时，直接吃光 job 时限）
    - 重试受总时长上限约束，避免一个坏时段把整条流水线拖死
    """
    if not ANTHROPIC_AUTH_TOKEN:
        raise ValueError("ANTHROPIC_AUTH_TOKEN not set")

    max_retries = 8
    t_start = time.time()
    payload = {
        "model": LLM_MODEL,
        "max_tokens": 16384,
        "messages": messages,
        "stream": True,
    }
    headers = {"Authorization": f"Bearer {ANTHROPIC_AUTH_TOKEN}", "Content-Type": "application/json"}
    url = f"{LLM_BASE_URL}/chat/completions"
    last_err = None

    for attempt in range(max_retries):
        session = None
        try:
            # 每次重试新建 session，避免复用已失效的连接
            session = requests.Session()
            adapter = requests.adapters.HTTPAdapter(
                max_retries=0,              # 重试由本函数控制，避免自动重试掩盖问题
                pool_connections=1,
                pool_maxsize=1,
            )
            session.mount("https://", adapter)
            session.mount("http://", adapter)

            resp = session.post(
                url, headers=headers, json=payload,
                timeout=(30, LLM_READ_TIMEOUT),   # 连接30秒；流式下单次读取空档上限
                stream=True,
            )
            resp.raise_for_status()
            ctype = resp.headers.get("Content-Type", "")
            if "event-stream" in ctype or "stream" in ctype:
                resp.encoding = "utf-8"   # 不显式设置时 requests 可能按 ISO-8859-1 解，中文标题会乱码
                parts, err = [], None
                for line in resp.iter_lines(decode_unicode=True):
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        obj = json.loads(data)
                    except ValueError:
                        continue
                    if obj.get("error"):
                        err = obj["error"]
                        break
                    for ch in obj.get("choices", []) or []:
                        frag = (ch.get("delta") or {}).get("content")
                        if frag:
                            parts.append(frag)
                resp.close()
                text = "".join(parts)
                if err:
                    raise RuntimeError(f"LLM 流式返回错误: {str(err)[:120]}")
                if text.strip():
                    return text
                raise RuntimeError("LLM 流式返回空内容")
            # 服务端忽略 stream 参数时按普通JSON解析
            return resp.json()["choices"][0]["message"]["content"]

        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else 0
            last_err = e
            if status in (429, 500, 502, 503, 504) and attempt < max_retries - 1 \
                    and time.time() - t_start < LLM_RETRY_DEADLINE:
                wait = 20
                log.warning(f"LLM HTTP {status} (第{attempt+1}/{max_retries}次，"
                            f"已用{time.time()-t_start:.0f}s)，{wait}秒后重试")
                time.sleep(wait)
            else:
                raise

        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout,
                RuntimeError) as e:
            last_err = e
            if attempt < max_retries - 1 and time.time() - t_start < LLM_RETRY_DEADLINE:
                wait = 20
                log.warning(f"LLM 连接失败 (第{attempt+1}/{max_retries}次，"
                            f"已用{time.time()-t_start:.0f}s)，{wait}秒后重试: {str(e)[:80]}")
                time.sleep(wait)
            else:
                raise

        finally:
            if session is not None:
                try:
                    session.close()
                except Exception:
                    pass

    if last_err:
        raise last_err


def _resolve_url(item, url_index, raw_results, log_match=True):
    """
    为一条新闻条目回填 source_url。统一 URL 解析逻辑，消除重复代码。
    优先级：1) source_index 精确反查  2) 标题 bigram 相似度匹配(阈值0.5)
    返回 (url, source_index_str)。配不到返回 ("", 原source_index)。
    """
    idx = str(item.get("source_index", "") or "").strip()
    # 1. source_index 精确反查
    if idx and idx in url_index and url_index[idx]:
        return url_index[idx], idx

    # 2. 标题全文 bigram 相似度匹配（不配置错的URL）
    item_title_full = re.sub(r"\s+", "", item.get("title", "")).lower()
    item_bgs = set(item_title_full[k:k+2] for k in range(len(item_title_full)-1)) if len(item_title_full) > 2 else set()
    best_url, best_idx, best_ratio = "", idx, 0.0
    for ri, r in enumerate(raw_results):
        raw_t = re.sub(r"\s+", "", r.get("title", "")).lower()
        if not raw_t or not r.get("url"):
            continue
        raw_bgs = set(raw_t[k:k+2] for k in range(len(raw_t)-1))
        if not item_bgs or not raw_bgs:
            continue
        overlap = len(item_bgs & raw_bgs)
        ratio = overlap / min(len(item_bgs), len(raw_bgs)) if min(len(item_bgs), len(raw_bgs)) else 0
        if ratio > best_ratio:
            best_ratio = ratio
            best_url = r.get("url")
            best_idx = str(ri + 1)

    # 只有相似度足够高（>0.5）才配，否则宁可缺失也不配错
    if best_url and best_ratio > 0.5:
        if log_match:
            log.info(f"  URL相似度匹配: [{item.get('title','')[:30]}] → {best_url[:50]} ({best_ratio:.2f})")
        return best_url, best_idx
    else:
        if log_match:
            log.warning(f"  URL缺失(相似度不足{best_ratio:.2f}): [{item.get('title','')[:30]}]")
        return "", idx


def summarize_news(raw_results):
    if not raw_results:
        return []

    # 素材编号→URL，覆盖全部送LLM的素材
    url_index = {str(i+1): r.get("url", "") for i, r in enumerate(raw_results)}

    materials = "\n\n".join(
        f"[{i+1}] 来源:{r.get('source','')} 作者:{r.get('author','')}\nURL:{r.get('url','')}\n标题:{r['title']}\n正文:\n{(r.get('content') or r.get('rss_summary',''))[:LLM_CONTENT_CHARS]}"
        for i, r in enumerate(raw_results)
    )

    user_prompt = f"""以下是精选科技媒体最新文章，请对每个独立事件打分并生成简报。

【你的身份】
你是一位见过太多风浪的科技产业洞察者。你不会被PR稿打动，只关心结构性变化。

【打分规则】
对每条新闻做三层审视：
1. **穿透表面**：去掉所有PR形容词，只看"谁做了什么、产生了什么可验证的结果"
2. **追问护城河**：是否改变了竞争壁垒？常规迭代不值高分
3. **时间尺度检验**：3个月后还会被人提起吗？当天热闹降权

必须加权（≥8分）的情形——这些是你的读者最看重的：
- 技术里程碑：数学证明、世界纪录、benchmark冠军、首次实现某能力（如形式化证明、通过图灵测试等）
- 重大产品发布：头部公司（OpenAI/Anthropic/Google/Meta/英伟达）发布新一代核心产品
- 行业格局变化：千亿级投资/并购、重要IPO、核心人物重大发言
- 安全事故：AI系统失控、逃逸、造成实际损害的事件
- **中央级重磅政策（≥8分）**：国务院/工信部/民航局等中央部委发布的、影响整个行业格局的政策——全城/全域禁飞、重大技术出口禁令、千亿级国家补贴、行业准入重大调整、核心产业管制清单。这类政策影响面广、涉及整个行业，必须高分入选，绝不因"政策类"而降权

降权情形（降低评分或不收录）：
- 宏大愿景掩盖执行细节的PR稿
- 常规融资（10亿以下）/常规合作
- "生态系统""全面赋能"等空洞话术
- 信息量低的简讯、快报、评测文章
- 地方性/常规政策文件：省级以下的规章、普通征求意见稿、常规监管条文解读 → ≤4分，不收录
- 量子位/虎嗅等媒体的解读分析文章（非一手事件报道），降2-3分

【合并与覆盖规则——极其重要】
- 多条素材报道完全相同的事件 → 合并为一条，source_index写最详细那条的编号
- 但：不同事件绝对不得合并，即使涉及同一公司
- 每个独立事件都必须输出一条，即使你认为不重要也要打低分(1-3分)输出，不得遗漏任何独立事件
- 输出条目数通常在10-20条之间。如果你只输出了不到10条，说明你合并过度，请回头检查是否漏掉了独立事件
- 例：OpenAI发布模型 vs OpenAI首席科学家发文 vs OpenAI承认安全事件 = 3个不同事件，必须输出3条
- 硬上限：最多输出{LLM_EVENT_MAX}条。若独立事件多于{LLM_EVENT_MAX}个，按重要度保留前{LLM_EVENT_MAX}个、其余整条丢弃；
  绝不允许为了压到{LLM_EVENT_MAX}条而把不同事件合并成一条（合并只适用于同一事件的多篇报道）

【输出要求】
对合并后的每个独立事件输出：
1. title: 标题严格控制在18-22个中文字符以内（绝对不能超过22字！超过必须精简），直接说事，包含核心主语和关键事实，不要写成两个分句。专有名词（公司名、产品名、人名）必须写完整，宁可超字数也绝对不得缩写（如"特斯拉"不得写成"特斯"）
2. summary: 130-170字，说清是什么、关键数据/背景、为什么重要/产业影响，每句以句号结尾，不用"本文""该公司"等套话
3. key_point: ≤25字结论
4. category: 两层标签，用顿号连接：

   【第一层·赛道】判断核心主角：
   - 模型/训练/推理 → AI大模型
   - 芯片/硬件公司 → 算力芯片
   - 机器人/机器人公司 → 具身机器人
   - 无人机/低空飞行器 → 无人机
   - 电池/储能 → 新型储能
   - 以上都不是 → 只贴性质标签
   - 中央级重磅政策（全城禁飞/重大禁令/千亿补贴）→ 按受影响行业归到对应赛道（如禁飞→无人机，芯片管制→算力芯片）
   - 地方性/常规政策解读 → 不收录

   【第二层·性质】必选一个：
   - 技术突破：有数据/benchmark，能力质变
   - 产业动态：发布/合作/收购/战略/资本

   严禁自创标签，只能来自：AI大模型/算力芯片/具身机器人/无人机/新型储能/技术突破/产业动态
5. sentiment: 正面/负面/中性
6. cluster_tag: 厂商动态类/技术突破类/行业趋势类/产品评测类/市场情绪类
7. companies: 涉及的公司/产品名称（重要！代码用此字段做同公司去重，务必准确填写主要公司名）
8. source_index: 原始素材编号（**极其重要**，必须是上方素材清单里 [数字] 的那个编号，用于回填原文链接。每条必须准确对应其素材编号，写错会导致链接指向错误文章。合并多条时填最详细那条的编号）
9. source_url: 留空，代码自动回填
10. source_name: 来源媒体名
11. author: 作者，无则留空
12. score: 重要程度评分(1-10)
13. selected: 全部填false（由代码决定最终入选）
14. 字段值不得含英文双引号，改用书名号《》

严格输出JSON数组，按score降序排列，无其他内容：
[{{"index":1,"score":9,"selected":false,"category":"AI大模型、技术突破","title":"标题","summary":"摘要","key_point":"核心观点","companies":"公司","sentiment":"正面","cluster_tag":"厂商动态类","source_index":"1","source_url":"","source_name":"来源","author":""}}]

"""
    # 读取上期已发标题，追加跨期去重规则
    last_sent_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "last_sent.json")
    exclude_block = ""
    try:
        if os.path.exists(last_sent_path):
            with open(last_sent_path, encoding="utf-8") as f:
                last_data = json.load(f)
            prev_titles = last_data.get("titles", [])
            prev_date   = last_data.get("date", "")
            if prev_titles:
                titles_str = "\n".join(f"  - {t}" for t in prev_titles)
                exclude_block = f"""
【跨期去重·硬性规则】以下是上期（{prev_date}）已发送的标题，本期绝对不得再选相同或高度相似的事件：
{titles_str}

判断标准（关键）：不是看标题文字像不像，而是看**核心事件是不是同一件事**。
- 哪怕措辞完全不同，只要讲的是同一件事，就必须判为重复，直接跳过。
- 典型陷阱：上期"概括式标题"（如"OpenAI解决世界100道数学难题"）与本期"具体式标题"（如"OpenAI用AI证明纳维-斯托克斯方程"）——这是同一件事的两种说法，绝对属于重复。
- 反过来，同一公司、同一领域但确实是**不同事件**的，不算重复（如"OpenAI发模型"和"OpenAI发安全报告"是两件事）。
请对每篇文章在内心做这个语义判断：它和上面哪条是不是同一件事？是的话打 0 分或 1 分并跳过。
"""
    except Exception:
        pass

    user_prompt += f"""
{exclude_block}
--- 原始文章（共{len(raw_results)}篇）---
{materials}
"""

    log.info("调用 LLM 生成简报...")
    try:
        raw_text = call_llm([
            {"role": "system", "content": "你是一位极度博学、极度怀疑的科技产业洞察者。你读过所有财报和创始人传记，见过无数次泡沫与崩盘的循环。你不会被PR稿打动，只关心结构性变化——谁的护城河加深了，谁的定价权被动摇了，什么技术突破是真实的而非营销噪音。只输出严格JSON数组，不含任何markdown标记或说明文字。"},
            {"role": "user",   "content": user_prompt},
        ])
        # 保存调试文件
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "llm_raw.txt"), "w", encoding="utf-8") as f:
            f.write(raw_text)
    except Exception as e:
        log.error(f"LLM 调用失败: {e}")
        return []

    # 解析 JSON（去掉可能的 ```json 标记）
    text = raw_text.strip().replace("```json", "").replace("```", "").strip()
    start, end = text.find("["), text.rfind("]")
    if start != -1 and end > start:
        json_text = text[start:end+1]
        # 第一次尝试直接解析
        parsed = None
        try:
            parsed = json.loads(json_text)
        except json.JSONDecodeError:
            # 修复：转义值内的非法双引号
            # 策略：在JSON结构符号之间的双引号前加反斜杠
            fixed = re.sub(
                r'(?<=:[\s])"((?:[^"\\]|\\.)*?)"(.*?)"',
                lambda m: '"' + m.group(1) + "\\'" + m.group(2) + '"',
                json_text
            )
            try:
                parsed = json.loads(fixed)
                log.info("JSON修复成功（转义值内引号）")
            except json.JSONDecodeError:
                # 最终兜底：逐行去掉值中的双引号
                lines = json_text.split("\n")
                cleaned = []
                for line in lines:
                    # 如果一行有3个以上双引号，说明值内有非法引号
                    if line.count('"') > 4:
                        # 保留前两个和最后一个双引号（key: "value"），中间的替换为单引号
                        parts = line.split('"')
                        if len(parts) > 5:
                            # parts[0]前缀 parts[1]key parts[2]: parts[3+]value含非法引号
                            rebuilt = '"'.join(parts[:3]) + '"' + "'".join(parts[3:-1]) + '"' + parts[-1]
                            cleaned.append(rebuilt)
                        else:
                            cleaned.append(line)
                    else:
                        cleaned.append(line)
                try:
                    parsed = json.loads("\n".join(cleaned))
                    log.info("JSON修复成功（逐行清理引号）")
                except json.JSONDecodeError:
                    # 第四级修复：补全缺失的 key 名（LLM 偶尔漏写 "title": 之类的键名）
                    # 特征：某行只有字符串值（"xxx",），且上一行是 "index": N,
                    repaired = []
                    for idx2, line in enumerate(cleaned):
                        st = line.strip()
                        # 判断：只有值没有key 的行（以引号开头结尾，且不含 "): 这种key分隔）
                        if (st.startswith('"') and
                                (st.endswith('",') or st.endswith('"')) and
                                '":' not in st):
                            # 看上一行是不是 "index": N,
                            prev = cleaned[idx2-1].strip() if idx2 > 0 else ""
                            if re.match(r'^"index"\s*:\s*\d+,?$', prev):
                                # 补上 title key
                                indent = line[:len(line) - len(line.lstrip())]
                                repaired.append(f'{indent}"title": {st}')
                                log.info(f"  JSON修复: 补全缺失的 title key → {st[:40]}")
                                continue
                        repaired.append(line)
                    try:
                        parsed = json.loads("\n".join(repaired))
                        log.info("JSON修复成功（补全缺失key）")
                    except json.JSONDecodeError:
                        pass
        if parsed:
                for i, item in enumerate(parsed):
                    # URL修正：统一用 _resolve_url 回填
                    resolved_url, resolved_idx = _resolve_url(item, url_index, raw_results, log_match=False)
                    item["source_url"] = resolved_url
                    item["source_index"] = resolved_idx
                    # ── 标签白名单过滤（只保留合法标签，去掉LLM自创的） ──
                    VALID_CATS = {"AI大模型","算力芯片","具身机器人","无人机","新型储能","技术突破","产业动态"}
                    cat = item.get("category","")
                    parts = cat.replace("/","、").replace("，","、").replace(",","、").split("、")
                    # 去掉所有空格后匹配白名单（Qwen会输出"AI 大模型"带空格）
                    clean_cats = list(dict.fromkeys(p.strip().replace(" ","") for p in parts if p.strip().replace(" ","") in VALID_CATS))  # 去重且保序
                    if clean_cats:
                        item["category"] = "、".join(clean_cats)
                    else:
                        # 白名单一个都不匹配（LLM给了自创标签或空）→ 给默认值，避免飞书显示【】或【非法标签】
                        item["category"] = "其他"

                    # 联网核查移到组稿之后（只对最终selected的5条做）

                # （二次补充打分机制已彻底移除，LLM一轮打分即可）

                # ── 统一组稿：质量优先 + 多样性平衡 ──
                # 读取上期标题（用bigram做模糊匹配）
                prev_titles_raw = []
                prev_bigrams_list = []  # 每条上期标题的bigram集合
                try:
                    if os.path.exists(last_sent_path):
                        with open(last_sent_path, encoding="utf-8") as f:
                            _last_data = json.load(f)
                        prev_titles_raw = _last_data.get("titles", [])
                        for pt in prev_titles_raw:
                            t = _strip_companies(pt)
                            bgs = set(t[k:k+2] for k in range(len(t)-1)) if len(t) > 1 else set()
                            prev_bigrams_list.append(bgs)
                except Exception:
                    pass

                # 清除所有selected标记，由代码统一决定
                for item in parsed:
                    item["selected"] = False

                # ── 代码级赛道关键词硬校验：无关新闻强制降分 ──
                # 不赌LLM自觉，用关键词判断是否属于五大赛道
                TRACK_KWS = {
                    "AI大模型": ["ai", "人工智能", "大模型", "模型", "gpt", "llm", "agent", "智能体", "deepseek", "chatgpt", "claude", "gemini", "qwen", "通义", "智谱", "openai", "anthropic", "谷歌", "google", "meta", "微软", "算法", "机器学习", "深度学习", "生成式", "推理"],
                    "算力芯片": ["芯片", "gpu", "npu", "半导体", "晶圆", "算力", "封测", "光刻", "英伟达", "nvidia", "昇腾", "晶圆", "制程", "处理器", "chip", "semiconductor", "foundry"],
                    "具身机器人": ["机器人", "具身", "humanoid", "人形", "机械臂", "robotics", "robots", "麦肯", "宇树", "figure", "特斯拉optimus", "digit", "机械狗", "协作机器人"],
                    "无人机": ["无人机", "drone", "uav", "evtol", "dji", "大疆", "无人系统", "低空", "飞行器", "unmanned", "多旋翼", "配送机"],
                    "新型储能": ["电池", "储能", "固态", "锂", "battery", "储能", "充电", "光伏", "新能源", "燃料电池", "钠离子", "energy storage", "超级快充", "换电"],
                }
                # AI大模型 的词面最宽（"模型/算法/推理"什么都能套），所以判定顺序放最后兜底
                TRACK_PRIORITY = ["无人机", "具身机器人", "算力芯片", "新型储能", "AI大模型"]

                def _derive_track(text_blob):
                    """按优先级返回关键词命中的赛道，判不出来返回空串"""
                    for track in TRACK_PRIORITY:
                        if any(k in text_blob for k in TRACK_KWS[track]):
                            return track
                    return ""

                # 低信息量题材：事件本身不构成产业变化，只出现在标题里才封顶（避免误伤正文顺带提到的正经事件）
                LOW_VALUE_KWS = ["课程", "培训", "招聘", "月薪", "招人", "展会", "预告",
                                 "盘点", "评测", "体验"]
                LOW_VALUE_CAP = 4
                # 但标题里同时出现这些词，说明「评测」是交付物而不是测评稿：
                # 微软+Hugging Face 发布 ThinkingBox 智能体评测基准被误封顶（分9→4），
                # 发基准/数据集/框架本身是产业事件，不该按软文压。
                LOW_VALUE_EXCLUDE = ["基准", "数据集", "benchmark", "框架", "标准",
                                     "开发者体验"]

                def _cap_low_value(item):
                    title_blob = (item.get("title", "") or "").lower()
                    low_hit = next((k for k in LOW_VALUE_KWS if k in title_blob), "")
                    # 「评测/体验」词面有歧义：标题同时带基准/数据集/框架这类交付物时，
                    # 说的是发布了被评测的东西，不是有人写了篇测评，所以让位给其它硬关键词
                    if low_hit in ("评测", "体验") and any(x in title_blob for x in LOW_VALUE_EXCLUDE):
                        low_hit = next((k for k in LOW_VALUE_KWS
                                        if k not in ("评测", "体验") and k in title_blob), "")
                    if not low_hit:
                        return
                    try:
                        cur = int(item.get("score", 0) or 0)
                    except (ValueError, TypeError):
                        cur = 0
                    if cur > LOW_VALUE_CAP:
                        item["score"] = LOW_VALUE_CAP
                        log.info(f"  低信息量封顶: [{item.get('title','')[:24]}] 分{cur}→{LOW_VALUE_CAP}（标题命中「{low_hit}」）")

                for item in parsed:
                    title_blob0 = (item.get("title", "") or "").lower()
                    text_blob = title_blob0 + " " + (item.get("summary", "") or "").lower()
                    kw_track = _derive_track(text_blob)
                    kw_title = _derive_track(title_blob0)
                    try:
                        score0 = int(item.get("score", 0) or 0)
                    except (ValueError, TypeError):
                        score0 = 0
                    # 标题主角是自动驾驶（赛道词只落到「AI大模型」这个宽桶、或压根没落）时先过自动驾驶裁定，
                    # 不让正文里顺带出现的「AI/算法」把它悄悄当成赛道内新闻收走
                    if kw_title in ("", "AI大模型") and any(k in title_blob0 for k in AD_KWS):
                        if score0 < AD_LANDMARK_MIN:
                            item["score"] = 0
                            item["_irrelevant"] = True
                            log.info(f"  无关新闻过滤: [{item.get('title','')[:24]}] → 强制0分"
                                     f"（自动驾驶非常规赛道，分{score0}<{AD_LANDMARK_MIN}）")
                        else:
                            item["_offtopic_landmark"] = True
                            item["_kw_track"] = ""
                            log.info(f"  赛道外放行: [{item.get('title','')[:24]}] 自动驾驶 分{score0}"
                                     f"≥{AD_LANDMARK_MIN}，交比较趟裁定")
                            _cap_low_value(item)
                        continue
                    if not kw_track:
                        # 标题摘要完全不含任何赛道关键词 → 判无关新闻
                        item["score"] = 0
                        item["_irrelevant"] = True
                        log.info(f"  无关新闻过滤: [{item.get('title','')[:30]}] → 强制0分")
                        continue
                    # 赛道判定：只有标题里带赛道词才允许覆盖 LLM 写的 category。
                    # 上一版用「标题+摘要」的关键词去改判，线上跑出了反例：
                    # 「行业评论文章探讨智能体AI超级周期」因摘要顺带提到"机器人"被改成具身机器人，
                    # 「傅里叶智能音频芯片港股大涨」因摘要提到"机器人"被从算力芯片改成具身机器人。
                    # 正文提到 ≠ 这条新闻是关于它的；标题带词才是主题。
                    # 摘要关键词退居两个位置：判相关性（上面的0分闸）+ LLM没给赛道时兜底补标。
                    cats = [c.strip() for c in (item.get("category", "") or "").split("、") if c.strip()]
                    llm_track = cats[0] if cats and cats[0] in TRACK_KWS else ""
                    specific = {"算力芯片", "具身机器人", "无人机", "新型储能"}
                    chosen = kw_title or (kw_track if not llm_track else "")
                    if chosen and chosen != llm_track:
                        # 例外：关键词只落到「AI大模型」这个宽桶而 LLM 给了具体赛道时不覆盖——
                        # 宽桶词面太泛（模型/算法/推理什么都能套），不如正文语义判断精确
                        if chosen == "AI大模型" and llm_track in specific:
                            chosen = llm_track
                        else:
                            old = cats[0] if cats else ""
                            cats = [chosen] + [c for c in cats[1:] if c not in TRACK_KWS]
                            item["category"] = "、".join(cats)
                            log.info(f"  {'赛道改判' if llm_track else '赛道补标'}: "
                                     f"[{item.get('title','')[:24]}] {old or '无'} → {chosen}")
                    item["_kw_track"] = chosen or llm_track or kw_track

                    _cap_low_value(item)

                # 按分数降序排列（int排序，确保类型一致）
                for item in parsed:
                    try:
                        item["score"] = int(item.get("score", 0))
                    except (ValueError, TypeError):
                        item["score"] = 0
                stage_a_sorted = sorted(parsed, key=lambda x: x.get("score", 0), reverse=True)

                # ── 阶段B：比较式终审 ──
                # LLM打绝对分尺度漂移（同日可能给十几条9分），但相对比较稳定。
                # 用高分shortlist做「谁胜过谁」的排序，再与阶段A的分数名次合并开榜；
                # 调用失败或格式异常则退回阶段A的分数排序，不影响出报。
                COMPARE_N = 20
                shortlist = [it for it in stage_a_sorted if int(it.get("score", 0) or 0) > 0][:COMPARE_N]
                all_sorted = stage_a_sorted
                if len(shortlist) >= 3:
                    try:
                        sl_prompt_items = json.dumps(
                            [{"index": i + 1, "title": it.get("title", ""),
                              "summary": (it.get("summary", "") or "")[:200],
                              "key_point": (it.get("key_point", "") or "")[:150]}
                             for i, it in enumerate(shortlist)],
                            ensure_ascii=False)
                        cmp_text = call_llm([
                            {"role": "system", "content": "你是科技产业简报的终审编辑，擅长相对比较。只输出严格JSON数组，不含任何说明文字。"},
                            {"role": "user", "content": f"""下面是本期评分靠前的 {len(shortlist)} 条候选（index 为候选序号）。

{sl_prompt_items}

请做**相对比较**，不要重新打分：决出最值得发给读者的前5条，并给出全部 {len(shortlist)} 条的完整排序。

判断口径（按优先级）：
1. 事实强度：有可验证数据/首次实现/官方文件 > 有具体动作 > 只有愿景和形容词
2. 结构性影响：改变成本曲线、竞争壁垒、行业规则的程度
3. 三个月后是否仍会被引用

同一公司的多条，只保留其中最强的一条排在前面，其余往后排。

严格输出JSON数组，覆盖全部 {len(shortlist)} 条候选，按推荐度降序：
[{{"index":3,"final_rank":1,"reason":"胜过第7条：给出可验证的成功率而非PR口号"}}]
不要输出任何其他文字。"""},
                        ])
                        ct = cmp_text.strip().replace("```json", "").replace("```", "").strip()
                        cs, ce = ct.find("["), ct.rfind("]")
                        ranked = []
                        if cs != -1 and ce > cs:
                            for v in json.loads(ct[cs:ce + 1]):
                                try:
                                    vi = int(v.get("index", 0)) - 1
                                except (ValueError, TypeError):
                                    continue
                                if 0 <= vi < len(shortlist) and all(vi != r[0] for r in ranked):
                                    ranked.append((vi, v.get("reason", "")))
                        if len(ranked) >= len(shortlist) // 2:  # 至少排掉一半才采用，否则视为输出不可信
                            # 模型没排到的，比较名次记在末尾，一条不丢
                            missed = [i for i in range(len(shortlist)) if all(i != r[0] for r in ranked)]
                            rank_b = {idx: p for p, (idx, _) in enumerate(ranked)}
                            for j, idx in enumerate(missed):
                                rank_b[idx] = len(ranked) + j
                            # 名次Borda合并：shortlist 按阶段A顺序构建，故分数名次就是下标 i。
                            # 两趟各出一半票，任何一趟单独跑偏都掀不动榜单——
                            # 分3要顶到分7前面，必须在比较排序里反超对方位次之和以上。
                            merged = sorted(
                                range(len(shortlist)),
                                key=lambda i: (i + rank_b[i], rank_b[i],
                                               -int(shortlist[i].get("score", 0) or 0)),
                            )
                            ordered_shortlist = []
                            for i in merged:
                                it = shortlist[i]
                                it["_rank_a"], it["_rank_b"] = i + 1, rank_b[i] + 1
                                ordered_shortlist.append(it)
                            in_shortlist = {id(it) for it in ordered_shortlist}
                            leftovers = [it for it in stage_a_sorted if id(it) not in in_shortlist]
                            all_sorted = ordered_shortlist + leftovers
                            log.info(f"  比较式终审采用：Borda合并 shortlist {len(shortlist)} 条，前3条 "
                                     f"[{' / '.join(it.get('title','')[:20] for it in ordered_shortlist[:3])}]")
                        else:
                            log.warning(f"  比较式终审输出不完整（{len(ranked)}/{len(shortlist)}），退回分数排序")
                    except Exception as e:
                        log.warning(f"  比较式终审失败（退回分数排序）: {e}")

                # 调试：输出排序后前8条（附两趟原始名次，便于核对合并结果）
                for _di, _d in enumerate(all_sorted[:8]):
                    _co = (_d.get("companies","") or "").split("、")[0].split(",")[0].strip().lower()
                    _rk = (f" A{_d.get('_rank_a', '-')}B{_d.get('_rank_b', '-')}"
                           if "_rank_a" in _d else "")
                    log.info(f"  排序#{_di+1}: 分{_d.get('score','')}{_rk} 公司[{_co}] {_d.get('title','')[:25]}")

                def _get_company(item):
                    # 提取所有公司（不只第一个），做同公司去重时更准确
                    comps = (item.get("companies", "") or "")
                    # 拆分成公司列表
                    parts = [p.strip().lower() for p in re.split(r"[、,，;；]", comps) if p.strip()]
                    # 合并大小写和别名归一，返回全部公司集合
                    return tuple(parts) if parts else ("",)

                def _get_track(item):
                    """从category中提取赛道"""
                    if item.get("_offtopic_landmark"):
                        return "赛道外·自动驾驶"
                    cat = item.get("category", "")
                    for t in ["算力芯片", "具身机器人", "无人机", "新型储能"]:
                        if t in cat:
                            return t
                    return "AI大模型"

                def _is_dup(item):
                    """检查跨期重复：bigram重合率>0.5才判重复（语义重复已交给LLM，这里只拦字面几乎一样的）"""
                    t = _strip_companies(item.get("title", ""))
                    if len(t) < 4:
                        return False
                    item_bgs = set(t[k:k+2] for k in range(len(t)-1))
                    # 同时检查摘要的bigram（防止标题完全不同但内容相同）
                    s = re.sub(r"\s+", "", item.get("summary", "")).lower()
                    summ_bgs = set(s[k:k+2] for k in range(len(s)-1)) if len(s) > 1 else set()
                    for pi, prev_bgs in enumerate(prev_bigrams_list):
                        if not prev_bgs:
                            continue
                        # 标题bigram匹配（已剔除公司名，阈值提到0.5避免误杀）
                        overlap = len(item_bgs & prev_bgs)
                        ratio = overlap / min(len(item_bgs), len(prev_bgs))
                        if ratio > 0.5:
                            return True
                        # 摘要也包含上期标题的关键词（兜底）
                        if summ_bgs:
                            s_overlap = len(summ_bgs & prev_bgs)
                            s_ratio = s_overlap / min(len(summ_bgs), len(prev_bgs)) if min(len(summ_bgs), len(prev_bgs)) > 0 else 0
                            if s_ratio > 0.6:
                                return True
                    return False

                def _is_same_event(item, selected_list):
                    """检查同一批结果内是否有高度相似的条目（同一事件多篇报道）"""
                    t = re.sub(r"\s+", "", item.get("title", "")).lower()
                    if len(t) < 4:
                        return False
                    item_bgs = set(t[k:k+2] for k in range(len(t)-1))
                    for sel in selected_list:
                        st = re.sub(r"\s+", "", sel.get("title", "")).lower()
                        if len(st) < 4:
                            continue
                        sel_bgs = set(st[k:k+2] for k in range(len(st)-1))
                        overlap = len(item_bgs & sel_bgs)
                        ratio = overlap / min(len(item_bgs), len(sel_bgs))
                        if ratio > 0.35:
                            return True
                    return False

                # === 阶段1：质量优先——按分数降序选 ===
                # 同公司限制：最多2条，且第2条必须≥8分（重磅）；普通重复只给1条
                # URL 去重：同一链接最多1条（防止合集拆分出的子新闻共用URL）
                final = []
                company_count = {}  # {公司: 出现次数}
                used_urls = set()   # 已选URL，防止重复链接
                ad_used = 0         # 赛道外（自动驾驶）已入选条数
                for item in all_sorted:
                    if len(final) >= 5:
                        break
                    # 0分条目（无关新闻/LLM弃选）彻底排除，不进选择
                    if int(item.get("score", 0) or 0) <= 0:
                        continue
                    # 赛道外的标志性事件每期只留 AD_MAX_PER_ISSUE 条，多了会挤掉五大赛道
                    if item.get("_offtopic_landmark") and ad_used >= AD_MAX_PER_ISSUE:
                        log.info(f"  赛道外限额: [{item.get('title','')[:30]}] 本期最多{AD_MAX_PER_ISSUE}条，跳过")
                        continue
                    if _is_dup(item):
                        log.info(f"  跨期去重: [{item.get('title','')}]")
                        continue
                    if _is_same_event(item, final):
                        log.info(f"  同次去重: [{item.get('title','')[:30]}]")
                        continue
                    # URL去重：合集拆分出的子新闻共用同一URL，只取最高分那条
                    item_url = (item.get("source_url", "") or "").strip()
                    if item_url and item_url in used_urls:
                        log.info(f"  URL重复跳过: [{item.get('title','')[:30]}] (链接已选)")
                        continue
                    companies = _get_company(item)  # tuple of company names
                    score = int(item.get("score", 0) or 0)
                    # 检查该条目涉及的每个公司，是否已达上限
                    blocked = False
                    for c in companies:
                        cnt = company_count.get(c, 0)
                        # 同公司：已有1条，第2条必须≥8分；已有2条，拒绝
                        if cnt >= 2:
                            blocked = True
                            break
                        if cnt >= 1 and score < 8:
                            blocked = True
                            break
                    if blocked:
                        log.info(f"  同公司限制: [{item.get('title','')[:30]}] (分{score}, 公司{companies})")
                        continue
                    item["selected"] = True
                    final.append(item)
                    if item.get("_offtopic_landmark"):
                        ad_used += 1
                    for c in companies:
                        company_count[c] = company_count.get(c, 0) + 1
                    if item_url:
                        used_urls.add(item_url)
                    log.info(f"  质量入选: [{item.get('title','')}] (公司:{companies}, 分{score}, 赛道:{_get_track(item)})")

                # === 阶段2：兜底补满（普通事件，同公司尽量不重复，URL不重复） ===
                # 凑不满5条时，放宽跨期去重：允许≥6分的高分旧闻（昨天发过但有持续重要性）补位
                if len(final) < 5:
                    for item in all_sorted:
                        if len(final) >= 5:
                            break
                        if item.get("selected"):
                            continue
                        if _is_same_event(item, final):
                            log.info(f"  同次去重: [{item.get('title','')[:30]}]")
                            continue
                        score = int(item.get("score", 0) or 0)
                        # 0分条目（无关新闻）不进阶段2
                        if score <= 0:
                            continue
                        if item.get("_offtopic_landmark") and ad_used >= AD_MAX_PER_ISSUE:
                            log.info(f"  赛道外限额: [{item.get('title','')[:30]}] 本期最多{AD_MAX_PER_ISSUE}条，跳过")
                            continue
                        is_dup = _is_dup(item)
                        # 放宽逻辑：跨期重复但分数≥6的，允许进入补位（重要旧闻不丢）
                        if is_dup and score < 6:
                            continue
                        # URL去重
                        item_url = (item.get("source_url", "") or "").strip()
                        if item_url and item_url in used_urls:
                            continue
                        companies = _get_company(item)
                        # 兜底阶段：同公司已有1条就尽量跳过（除非实在没得选）
                        overlap_any = any(company_count.get(c, 0) >= 1 for c in companies)
                        if overlap_any:
                            continue
                        item["selected"] = True
                        final.append(item)
                        if item.get("_offtopic_landmark"):
                            ad_used += 1
                        for c in companies:
                            company_count[c] = company_count.get(c, 0) + 1
                        if item_url:
                            used_urls.add(item_url)
                        mark = "放宽旧闻" if is_dup else "兜底"
                        log.info(f"  {mark}入选: [{item.get('title','')[:30]}] (分{score})")

                # === 阶段3：终极兜底（阶段2仍凑不满，放掉跨期+同公司限制，只保URL不重复） ===
                if len(final) < 5:
                    log.info(f"  阶段2后仍缺 {5 - len(final)} 条，进入终极兜底...")
                    for item in all_sorted:
                        if len(final) >= 5:
                            break
                        if item.get("selected"):
                            continue
                        # 至少不做同次去重（避免同一次简报出现两条相似内容）
                        if _is_same_event(item, final):
                            log.info(f"  终极兜底同次skip: [{item.get('title','')[:30]}]")
                            continue
                        # 0分条目（无关新闻）不参与兜底，否则上面那道0分闸在这条路径上被绕过
                        if int(item.get("score", 0) or 0) <= 0:
                            continue
                        if item.get("_offtopic_landmark") and ad_used >= AD_MAX_PER_ISSUE:
                            continue
                        # 只保URL不重复，放掉跨期和同公司限制
                        item_url = (item.get("source_url", "") or "").strip()
                        if item_url and item_url in used_urls:
                            continue
                        if item_url:
                            used_urls.add(item_url)
                        item["selected"] = True
                        final.append(item)
                        if item.get("_offtopic_landmark"):
                            ad_used += 1
                        log.info(f"  终极兜底: [{item.get('title','')[:30]}] (分{item.get('score','?')})")

                # === 阶段4：跨期语义去重复核（LLM终审） ===
                # bigram只能拦字面几乎一样的重复；同一事件换个说法连发三天的情况
                # （如"OpenAI承认智能体入侵政府网站"→"OpenAI每日超50万美元调查智能体攻击"）
                # 只能靠LLM识别，选完后再做一轮强制核对，命中即剔除并补位。
                if final and prev_titles_raw:
                    try:
                        prev_list_str = json.dumps(
                            [{"序号": i + 1, "标题": t} for i, t in enumerate(prev_titles_raw)],
                            ensure_ascii=False)
                        cand_list_str = json.dumps(
                            [{"index": i + 1, "title": it.get("title", ""), "summary": it.get("summary", "")}
                             for i, it in enumerate(final)],
                            ensure_ascii=False)
                        verdict_text = call_llm([
                            {"role": "system", "content": "你是新闻去重审核员，只判断两期内容是否报道同一事件。只输出严格JSON，不含任何说明文字。"},
                            {"role": "user", "content": f"""以下是往期已发送的新闻标题，以及本期刚选出的候选条目（含摘要）。

【往期已发送】
{prev_list_str}

【本期候选】
{cand_list_str}

判断标准：只要核心事件是同一件事就算重复——即使措辞、角度、详略完全不同。例如往期"OpenAI承认智能体入侵政府网站并高额投入调查"与本期"OpenAI每日超50万美元调查智能体攻击事件"是同一件事（都是OpenAI智能体攻击事件及其调查），必须判重复。反过来，同一公司的不同事件（发新模型 vs 发安全报告）不算重复。

对每条疑似重复的，标注置信度：
- "确定"：就是同一件事，无悬念（典型：同一事件的不同措辞/角度/跟进报道）
- "疑似"：有点像但不敢肯定（典型：同公司同领域、时间接近，但无法确认是同一事件）

拿不准时一律标"疑似"，只有无悬念时才标"确定"。

严格输出JSON数组：本期哪些 index 与往期重复，形如 [{{"index":1,"dup_of":2,"level":"确定"}}]。没有重复则输出 []。"""},
                        ])
                        vt = verdict_text.strip().replace("```json", "").replace("```", "").strip()
                        vs, ve = vt.find("["), vt.rfind("]")
                        dup_indices = set()
                        if vs != -1 and ve > vs:
                            for verdict in json.loads(vt[vs:ve + 1]):
                                try:
                                    vi = int(verdict.get("index", 0)) - 1
                                except (ValueError, TypeError):
                                    continue
                                if not (0 <= vi < len(final)):
                                    continue
                                if str(verdict.get("level", "")).strip() == "确定":
                                    dup_indices.add(vi)
                                else:
                                    log.info(f"  跨期复核疑似(保留): [{final[vi].get('title','')[:30]}]")
                        if dup_indices:
                            removed = [final[i] for i in sorted(dup_indices) if 0 <= i < len(final)]
                            final = [it for i, it in enumerate(final) if i not in dup_indices]
                            removed_ids = {id(it) for it in removed}
                            for it in removed:
                                for c in _get_company(it):
                                    company_count[c] = max(0, company_count.get(c, 0) - 1)
                                u = (it.get("source_url", "") or "").strip()
                                if u and u in used_urls:
                                    used_urls.discard(u)
                                it["selected"] = False
                                log.info(f"  跨期复核剔除: [{it.get('title','')[:30]}]")
                            # 补位：先只用非跨期重复条目；实在凑不满再允许旧闻补位
                            for allow_dup in (False, True):
                                for item in all_sorted:
                                    if len(final) >= 5:
                                        break
                                    if id(item) in removed_ids:
                                        continue
                                    if item.get("selected"):
                                        continue
                                    if _is_same_event(item, final):
                                        continue
                                    if not allow_dup and _is_dup(item):
                                        continue
                                    item["selected"] = True
                                    final.append(item)
                                    for c in _get_company(item):
                                        company_count[c] = company_count.get(c, 0) + 1
                                    iu = (item.get("source_url", "") or "").strip()
                                    if iu:
                                        used_urls.add(iu)
                                    log.info(f"  复核补位: [{item.get('title','')[:30]}]")
                    except Exception as e:
                        log.warning(f"  跨期复核失败（保留原选择）: {e}")

                # 重组：selected在前，其余在后
                rest = [it for it in all_sorted if not it.get("selected")]
                parsed = final + rest

                # ── 联网核查兜底（仅最终selected的5条，含"AI大模型"且公司不明确时纠正赛道标签） ──
                for item in parsed:
                    if item.get("selected") is True and "AI大模型" in item.get("category", ""):
                        company = (item.get("companies", "") or "").split("、")[0].split(",")[0].strip()
                        if company:
                            search_text = web_search_company(company)
                            if search_text:
                                for track, keywords in TRACK_KEYWORDS.items():
                                    if any(k in search_text for k in keywords):
                                        cats = [c.strip() for c in item["category"].split("、") if c.strip()]
                                        if track not in cats:
                                            cats.insert(0, track)
                                            if track != "AI大模型":
                                                cats = [c for c in cats if c != "AI大模型"]
                                            item["category"] = "、".join(cats)
                                            log.info(f"  联网核查: {company} → {item['category']}")
                                        break

                # ── 出口质量检查：强制修正标题/摘要/URL ──
                seen_urls = set()
                for item in parsed:
                    if not item.get("selected"):
                        continue

                    # 标题：不再截断。飞书 post 消息的 text 元素超长会自动换行，
                    # 之前 22字截断反而产生半截词（中英混排时砍断英文单词）。
                    # 标题保持完整，靠飞书自动换行。

                    # 摘要：保证完整句子，上限180字（A4一页5条放得下）
                    summary = item.get("summary", "")
                    if len(summary) > 180:
                        cut = summary[:180]
                        # 只在句号处截断（分号不是句末标点，会导致结尾是分号）
                        best_pos = cut.rfind("。")
                        if best_pos >= 100:
                            cut = cut[:best_pos + 1]
                        else:
                            for ch in ["，", ","]:
                                pos = cut.rfind(ch)
                                if pos >= 120:
                                    cut = cut[:pos] + "。"
                                    break
                            else:
                                cut = cut[:180] + "。"
                        item["summary"] = cut
                    elif len(summary) < 30:
                        # 摘要太短（补全条目），用标题补
                        item["summary"] = item.get("title", "") + "。" + summary if summary else item.get("title", "")

                    # 摘要清理HTML残留
                    item["summary"] = re.sub(r"<[^>]+>", "", item["summary"]).strip()

                    # URL验证：统一用 _resolve_url
                    url = item.get("source_url", "")
                    if not url or "http" not in url:
                        resolved_url, resolved_idx = _resolve_url(item, url_index, raw_results, log_match=True)
                        item["source_url"] = resolved_url
                        item["source_index"] = resolved_idx
                        url = resolved_url
                    if url in seen_urls:
                        log.warning(f"  URL重复: [{item.get('title','')}] → {url[:50]}")
                    seen_urls.add(url)

                    # 公司名清理：去掉RSS源名称
                    companies = item.get("companies", "") or ""
                    if "AIHOT" in companies or "RSS" in companies:
                        # 从标题中重新提取
                        _known = {"openai":"OpenAI","anthropic":"Anthropic","claude":"Anthropic",
                                  "nvidia":"英伟达","英伟达":"英伟达","github":"GitHub",
                                  "google":"Google","meta":"Meta","microsoft":"Microsoft",
                                  "dji":"DJI","tesla":"Tesla"}
                        tl = item.get("title","").lower()
                        detected = [v for k,v in _known.items() if k in tl]
                        item["companies"] = "、".join(dict.fromkeys(detected)) if detected else ""

                log.info(f"LLM 返回 {len(parsed)} 条简报")
                # 保存代码处理后的最终结果（供调试）
                try:
                    final_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "final_result.json")
                    with open(final_path, "w", encoding="utf-8") as f:
                        json.dump(parsed, f, ensure_ascii=False, indent=2)
                except Exception:
                    pass
                return parsed

    log.warning("JSON 解析失败，使用兜底内容")
    return [{"index": 1, "category": "其他", "title": "科技前沿简报", "summary": raw_text[:500],
             "source_url": "", "source_name": "AI汇总", "author": "", "comments": "",
             "selected": False, "score": 0}]


# ── 3. Render HTML ────────────────────────────────────────────────────────────

def render_html(news_items, report_date):
    def badge(category):
        color = CATEGORY_COLORS.get(category, CATEGORY_COLORS["其他"])
        return f'<span style="display:inline-block;padding:2px 10px;border-radius:12px;background:{color};color:#fff;font-size:12px;font-weight:600;margin-bottom:8px;">{category}</span>'

    def card(item, idx):
        index    = item.get("index", idx+1)
        category = item.get("category", "其他")
        title    = item.get("title", "（无标题）")
        summary  = item.get("summary", "")
        url      = item.get("source_url", "")
        src_name = item.get("source_name", "来源")
        color    = CATEGORY_COLORS.get(category, CATEGORY_COLORS["其他"])
        link     = (f'<a href="{url}" style="display:inline-block;margin-top:10px;padding:5px 14px;'
                    f'background:#f0f0f0;color:#444;border-radius:6px;text-decoration:none;font-size:13px;">'
                    f'阅读原文 → {src_name}</a>') if url else ""
        return f"""
        <tr><td style="padding:0 0 16px 0;">
          <table width="100%" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:12px;box-shadow:0 2px 8px rgba(0,0,0,0.07);border-left:4px solid {color};overflow:hidden;">
            <tr><td style="padding:20px 24px;">
              {badge(category)}
              <p style="margin:4px 0 10px;font-size:16px;font-weight:700;color:#1a202c;line-height:1.5;">
                <span style="color:{color};font-weight:800;margin-right:6px;">#{index}</span>{title}
              </p>
              <p style="margin:0;font-size:14px;color:#4a5568;line-height:1.8;">{summary}</p>
              {link}
            </td></tr>
          </table>
        </td></tr>"""

    cards = "\n".join(card(item, i) for i, item in enumerate(news_items))

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>科技前沿简报 · {report_date}</title></head>
<body style="margin:0;padding:0;background:#f0f2f8;font-family:'PingFang SC','Microsoft YaHei',sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f0f2f8;padding:24px 0;">
<tr><td align="center">
<table width="640" cellpadding="0" cellspacing="0" style="max-width:640px;width:100%;">
  <tr><td style="padding:0 0 20px 0;">
    <table width="100%" cellpadding="0" cellspacing="0" style="background:linear-gradient(135deg,#6C63FF 0%,#4A90D9 100%);border-radius:16px;overflow:hidden;">
      <tr><td style="padding:32px 36px;">
        <p style="margin:0 0 6px;font-size:13px;color:rgba(255,255,255,0.8);letter-spacing:2px;">DAILY TECH DIGEST</p>
        <h1 style="margin:0 0 8px;font-size:28px;font-weight:800;color:#fff;letter-spacing:1px;">📡 科技前沿简报</h1>
        <p style="margin:0;font-size:14px;color:rgba(255,255,255,0.85);">{report_date} &nbsp;·&nbsp; {len(news_items)} 条行业动态</p>
      </td></tr>
    </table>
  </td></tr>
  <table width="100%" cellpadding="0" cellspacing="0">{cards}</table>
</table>
</td></tr>
</table>
</body></html>"""


# ── 4. CSV ────────────────────────────────────────────────────────────────────

def append_to_csv(news_items, report_date):
    date_tag  = datetime.now().strftime("%Y%m%d")
    today_csv = datetime.now().strftime("%Y/%m/%d")
    base, ext = os.path.splitext(CSV_PATH)
    csv_path  = f"{base}_{date_tag}{ext}"

    max_seq = 0
    file_exists = os.path.isfile(csv_path)
    if file_exists:
        try:
            with open(csv_path, "r", encoding="utf-8-sig") as f:
                for row in csv.DictReader(f):
                    try:
                        max_seq = max(max_seq, int(row.get("序号", 0) or 0))
                    except (ValueError, TypeError):
                        pass
        except Exception as e:
            log.warning(f"CSV read failed: {e}")

    rows = []
    for i, item in enumerate(news_items):
        rows.append({
            "序号":         str(max_seq + i + 1),
            "素材链接":     item.get("source_url", ""),
            "来源平台":     item.get("source_name", ""),
            "领域主题":     f"{item.get('category','')}·{item.get('title','')}",
            "核心厂家/产品": item.get("companies", ""),
            "核心观点":     item.get("key_point", ""),
            "主要结论":     item.get("summary", ""),
            "内容倾向":     item.get("sentiment", "中性"),
            "聚类标签":     item.get("cluster_tag", ""),
            "调研报告":     "",
            "关联历史报告": "",
            "素材标题":     item.get("title", ""),
            "日期":         today_csv,
        })

    try:
        with open(csv_path, "a", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=CSV_HEADERS)
            if not file_exists:
                w.writeheader()
            w.writerows(rows)
        log.info(f"✅ 已追加 {len(rows)} 行到 CSV: {csv_path}")
        return len(rows)
    except Exception as e:
        log.error(f"CSV write failed: {e}")
        return 0


# ── 5. Email ──────────────────────────────────────────────────────────────────

def send_email(html_content, subject):
    if not RESEND_API_KEY:
        log.error("RESEND_API_KEY not set")
        return False
    log.info(f"发送邮件至 {', '.join(TO_EMAIL)} ...")
    try:
        resp = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
            json={"from": FROM_EMAIL, "to": TO_EMAIL, "subject": subject, "html": html_content},
            timeout=30,
        )
        if resp.status_code in (200, 201):
            log.info(f"✅ 邮件发送成功！Message ID: {resp.json().get('id','')}")
            return True
        else:
            log.error(f"❌ 邮件发送失败 HTTP {resp.status_code}: {resp.text}")
            return False
    except Exception as e:
        log.error(f"Email error: {e}")
        return False


# ── 5b. 飞书推送 ─────────────────────────────────────────────────────────────

def _feishu_sign(secret):
    """生成飞书机器人签名"""
    import hashlib, base64, hmac
    timestamp = str(int(time.time()))
    string_to_sign = f"{timestamp}\n{secret}"
    hmac_code = hmac.new(string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
    sign = base64.b64encode(hmac_code).decode("utf-8")
    return timestamp, sign


def send_feishu(news_items, report_date):
    if not FEISHU_WEBHOOK:
        log.warning("FEISHU_WEBHOOK not set, skipping")
        return False

    # 取selected的5条
    selected = [item for item in news_items if item.get("selected") is True]
    # 没有任何真正选中的条目时，不推送（避免发兜底垃圾内容）
    if not selected:
        log.error("没有选中任何条目，跳过飞书推送")
        return False
    items = selected[:5]

    # 构建富文本内容
    content_lines = []
    for i, item in enumerate(items, 1):
        cat = item.get("category", "") or "其他"
        title = item.get("title", "") or "（无标题）"
        summary = item.get("summary", "")
        url = item.get("source_url", "")

        cat_elem = {"tag": "text", "text": f"{i}. 【{cat}】"}
        title_elem = {"tag": "a", "text": title, "href": url} if url else {"tag": "text", "text": title}
        # 分类和标题分两行，避免挤在一行导致标题被飞书截断
        content_lines.append([cat_elem])
        content_lines.append([title_elem])
        content_lines.append([{"tag": "text", "text": summary}])
        content_lines.append([{"tag": "text", "text": ""}])  # 空行分隔

    payload = {
        "msg_type": "post",
        "content": {
            "post": {
                "zh_cn": {
                    "title": f"📡 科技前沿简报 · {report_date}",
                    "content": content_lines
                }
            }
        }
    }

    # 加签名
    if FEISHU_SECRET:
        timestamp, sign = _feishu_sign(FEISHU_SECRET)
        payload["timestamp"] = timestamp
        payload["sign"] = sign

    try:
        resp = requests.post(FEISHU_WEBHOOK, json=payload, timeout=15)
        if resp.status_code == 200 and resp.json().get("code") == 0:
            log.info("✅ 飞书推送成功！")
            return True
        else:
            log.error(f"❌ 飞书推送失败: {resp.text}")
            return False
    except Exception as e:
        log.error(f"飞书推送异常: {e}")
        return False


# ── 6. Brief PDF ─────────────────────────────────────────────────────────────

def generate_brief(news_items, report_date):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log.warning("playwright not installed, skipping")
        return

    date_tag   = datetime.now().strftime("%Y%m%d")
    script_dir = os.path.dirname(os.path.abspath(__file__))
    pdf_path   = os.path.join(script_dir, f"今日简报_{date_tag}.pdf")

    # 取LLM标记为selected的条目，兜底取前5条
    selected = [item for item in news_items if item.get("selected") is True]
    items_to_show = selected[:5] if len(selected) >= 5 else news_items[:5]

    cards_html = ""
    for i, item in enumerate(items_to_show):
        cat_raw = item.get("category", "其他")
        # 支持多标签，用顿号或/分隔
        cats  = [c.strip() for c in cat_raw.replace("/", "、").split("、") if c.strip()]
        color = CATEGORY_COLORS.get(cats[0] if cats else "其他", "#90A4AE")
        url   = item.get("source_url", "")
        src   = item.get("source_name", "")
        title = item.get("title", "")
        summ  = item.get("summary", "")

        # 多标签渲染
        tags_html = " ".join(
            f'<span style="background:{CATEGORY_COLORS.get(c,"#90A4AE")};color:#fff;font-size:11px;font-weight:700;padding:3px 10px;border-radius:6px;white-space:nowrap;">{c}</span>'
            for c in cats
        )

        link_html = f'<a href="{url}" style="position:absolute;top:12px;right:14px;font-size:11px;color:{color};text-decoration:none;font-weight:600;white-space:nowrap;">↗ {src or "原文"}</a>' if url else ""

        cards_html += f"""
        <div style="position:relative;padding:12px 18px 12px 18px;background:#fff;border-radius:12px;
                    box-shadow:0 1px 5px rgba(0,0,0,0.07);border-left:4px solid {color};">
          <div style="display:flex;align-items:center;gap:6px;margin-bottom:5px;flex-wrap:wrap;padding-right:72px;">
            {tags_html}
          </div>
          <div style="font-size:15px;font-weight:700;color:#1a202c;line-height:1.4;margin-bottom:6px;
                      padding-right:72px;word-break:break-word;">{title}</div>
          <div style="font-size:14.5px;color:#4a5568;line-height:1.75;">{summ}</div>
          {link_html}
        </div>"""

    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  html, body {{ height:1123px; overflow:hidden; }}
  body {{
    font-family: "Microsoft YaHei","PingFang SC","Noto Sans CJK SC",sans-serif;
    background: #f0f2f8;
    padding: 14px 24px;
    width: 794px;
    height: 1123px;
    display: flex;
    flex-direction: column;
    overflow: hidden;
    -webkit-print-color-adjust: exact;
  }}
  .header {{
    background: linear-gradient(135deg,#6C63FF 0%,#4A90D9 100%);
    border-radius: 12px;
    padding: 12px 24px;
    color: white;
    margin-bottom: 8px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-shrink: 0;
  }}
  .cards {{
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    flex: 1;
    min-height: 0;
  }}
  .footer {{
    margin-top: 6px;
    font-size: 11px;
    color: #bbb;
    text-align: right;
    flex-shrink: 0;
  }}
</style>
</head><body>
  <div class="header">
    <div>
      <div style="font-size:21px;font-weight:800;letter-spacing:1px;">📡 科技前沿简报</div>
      <div style="font-size:13px;opacity:0.85;margin-top:4px;">{report_date} · 精选 {len(items_to_show)} 条</div>
    </div>
    <div style="text-align:right;font-size:12px;opacity:0.8;line-height:2;">
      AI大模型 · 算力芯片<br>机器人 · 无人机 · 硬科技
    </div>
  </div>
  <div class="cards">{cards_html}</div>
  <div class="footer">{report_date}</div>
</body></html>"""

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 794, "height": 1123})
            page.set_content(html, wait_until="networkidle")
            page.wait_for_timeout(600)
            page.pdf(
                path=pdf_path,
                format="A4",
                print_background=True,
                margin={"top": "0", "bottom": "0", "left": "0", "right": "0"},
            )
            log.info(f"✅ PDF: {pdf_path}")
            browser.close()
    except Exception as e:
        log.error(f"Brief generation failed: {e}")


# ── 7. Main ───────────────────────────────────────────────────────────────────

# 送达标记：一次运行失败后 Actions 会重跑一次，靠这个文件记住"哪些外部动作已经做过"，
# 避免重跑时给同一个群/同一个邮箱发两遍。按天命名，第二天自然失效。
_DELIVER_DIR = os.path.dirname(os.path.abspath(__file__))


def _delivery_mark_path():
    return os.path.join(_DELIVER_DIR,
                        f"_delivered_{datetime.now(timezone.utc).strftime('%Y%m%d')}.mark")


def _already_delivered(kind):
    try:
        with open(_delivery_mark_path(), encoding="utf-8") as f:
            return kind in f.read().split()
    except FileNotFoundError:
        return False
    except Exception as e:
        log.warning(f"  读取送达标记失败，按未送达处理: {e}")
        return False


def _mark_delivered(kind):
    try:
        with open(_delivery_mark_path(), "a", encoding="utf-8") as f:
            f.write(kind + " ")
    except Exception as e:
        log.warning(f"  写送达标记失败: {e}")


def main():
    log.info("=" * 60)
    log.info("科技前沿简报 开始运行")
    log.info("=" * 60)

    report_date = datetime.now().strftime("%Y年%m月%d日")
    log.info(f"报告日期: {report_date}")
    subject = f"📡 科技前沿简报 · {report_date}"

    log.info("【Step 1】拉取 RSS 新闻...")
    raw_results, _ = search_news()

    log.info("【Step 1b】调用 aihot API 获取 AI 精选动态...")
    aihot_results = fetch_aihot_api(limit=AIHOT_MATERIAL_MAX)
    if aihot_results:
        raw_results = raw_results + aihot_results
        log.info(f"合并 aihot 后共 {len(raw_results)} 篇素材")



    # Step 1c: 无 RSS 博客爬取（JS渲染网站暂无效，由其他源间接覆盖）

    if not raw_results:
        log.error("无结果，退出")
        sys.exit(1)

    # ── 过滤旧文章：默认只保留最近 FRESH_WINDOW_DAYS 天，一手新闻室按其窗口（RSS 采样阶段已筛过一遍，这里兜底 aihot 等补充源）──
    cutoff, slow_cutoffs = _fresh_cutoffs()
    fresh_results = []
    dropped_unknown = 0
    dropped_old = 0
    for item in raw_results:
        # 统一从 pub_utc / pub / pub_date / published 拿时间，并按 UTC 归一
        pub_clean = _pub_utc_str(item)[:10]
        # 时间缺失/格式异常的，谨慎处理：丢弃，避免把旧闻当新闻
        if not pub_clean:
            dropped_unknown += 1
            continue
        # 兜底筛也必须用同一套窗口，否则采样阶段给一手新闻室放宽的7天在这里又被砍掉
        item_cutoff = slow_cutoffs.get(item.get("source", ""), cutoff)
        if pub_clean < item_cutoff:
            dropped_old += 1
            continue
        item.setdefault("_age_h", _age_hours(item))
        fresh_results.append(item)
    log.info(f"过滤旧文章: {len(raw_results)} → {len(fresh_results)} 篇（丢弃{dropped_old}条过期、{dropped_unknown}条无时间）")
    raw_results = fresh_results
    if not raw_results:
        log.error("过滤后无有效素材（所有文章时间缺失或过期），退出")
        sys.exit(1)

    # ── 保存精选素材清单到 dp3 文件夹，方便溯源 ──
    try:
        dp3_dir = os.environ.get("DP3_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "dp3"))
        os.makedirs(dp3_dir, exist_ok=True)
        date_tag = datetime.now().strftime("%Y%m%d")
        raw_list_path = os.path.join(dp3_dir, f"素材清单_{date_tag}.csv")

        with open(raw_list_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["序号", "来源", "标题", "链接", "发布时间"])
            for idx, item in enumerate(raw_results, 1):
                pub = item.get("pub", "") or item.get("pub_date", "")
                pub_clean = pub[:16].replace("T", " ") if pub else "未知"
                title = item.get("title", "")
                if len(title) > 80:
                    title = title[:80] + "…"
                writer.writerow([idx, item.get("source", ""), title, item.get("url", ""), pub_clean])
        log.info(f"✅ 素材清单已保存: {raw_list_path}（共 {len(raw_results)} 篇）")
    except Exception as e:
        log.warning(f"保存素材清单失败: {e}")

    log.info("【Step 2】AI 汇总...")
    news_items = summarize_news(raw_results)
    if not news_items:
        log.error("AI 汇总失败，退出")
        sys.exit(1)

    log.info("【Step 3】渲染 HTML...")
    html_content = render_html(news_items, report_date)

    log.info("【Step 4】发送邮件...")
    if TEST_MODE:
        success = True
        log.info("  测试模式：跳过邮件发送")
    elif _already_delivered("email"):
        success = True
        log.info("  本期邮件已发过（重跑），跳过避免重复发送")
    else:
        success = send_email(html_content, subject)
        if success:
            _mark_delivered("email")

    log.info("【Step 5】写入 CSV...")
    if _already_delivered("csv"):
        log.info("  本期 CSV 已写过（重跑），跳过避免重复追加")
    else:
        append_to_csv(news_items, report_date)
        _mark_delivered("csv")

    log.info("【Step 6】生成 PDF...")
    generate_brief(news_items, report_date)

    log.info("【Step 7】推送飞书...")
    if _already_delivered("feishu"):
        log.info("  本期飞书已推送过（重跑），跳过避免重复推送")
    else:
        if send_feishu(news_items, report_date):
            _mark_delivered("feishu")

    # ── 保存本期标题，供跨期去重（保留最近3期=15条） ──
    if TEST_MODE:
        log.info("  测试模式：跳过 last_sent.json 写入，去重库保持不变")
    elif _already_delivered("last_sent"):
        # 重跑时不覆盖：上期第一次尝试已经发出去并写库了，用那一版才对得上读者实际看到的内容
        log.info("  本期去重库已写入（重跑），跳过避免用未发送的版本覆盖")
    else:
        try:
            selected_items = [item for item in news_items if item.get("selected") is True]
            # 只用真正选中的条目；没选中就空列表，绝不拿未选中的凑数进去重库
            valid_final = [it for it in selected_items if it.get("title", "") != "科技前沿简报" and it.get("score", 0) > 0]
            new_titles = [item.get("title", "") for item in valid_final[:5]]
            last_sent_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "last_sent.json")
            # 读取旧标题，合并保留最近3期
            old_titles = []
            try:
                if os.path.exists(last_sent_path):
                    with open(last_sent_path, encoding="utf-8") as f:
                        old_data = json.load(f)
                    old_titles = old_data.get("titles", [])
            except Exception:
                pass
            # 新标题在前，旧标题在后，最多保留15条（3期×5条，每天跑需要覆盖更长去重窗口）
            all_titles = new_titles + [t for t in old_titles if t not in new_titles]
            all_titles = all_titles[:15]
            with open(last_sent_path, "w", encoding="utf-8") as f:
                json.dump({"titles": all_titles, "date": report_date}, f, ensure_ascii=False, indent=2)
            log.info(f"✅ 已保存去重库到 last_sent.json（{len(all_titles)} 条，含上期）")
            _mark_delivered("last_sent")
        except Exception as e:
            log.warning(f"保存 last_sent.json 失败: {e}")

    if success:
        log.info("🎉 全流程完成！")
        sys.exit(0)
    else:
        log.error("邮件发送失败")
        sys.exit(1)


if __name__ == "__main__":
    main()
