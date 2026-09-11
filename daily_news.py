"""Daily tech news digest - auto fetch, summarize and email."""

import os, sys, csv, json, re, time, logging
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
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
LLM_BASE_URL   = _base.rstrip("/") + "/v1"
LLM_MODEL      = os.environ.get("LLM_MODEL", "claude-opus-4-6")
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
TO_EMAIL       = [e.strip() for e in os.environ.get("TO_EMAIL", "yimingjiang216@gmail.com").split(",") if e.strip()]
FROM_EMAIL     = os.environ.get("FROM_EMAIL", "onboarding@resend.dev")
CSV_PATH       = os.environ.get("CSV_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "媒体洞察调研看板_素材库_表格.csv"))
RSSHUB_URL     = os.environ.get("RSSHUB_URL", "http://localhost:1200")  # 本地或公共RSSHub
FEISHU_WEBHOOK = os.environ.get("FEISHU_WEBHOOK", "https://open.feishu.cn/open-apis/bot/v2/hook/f2db3751-5063-4969-a732-54694a70731e")
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
    ("Marc Andreessen",      "https://pmarca.substack.com/feed",                                       5),
    ("Stratechery",          "https://stratechery.com/feed/",                                          5),
    ("Benedict Evans",       "https://www.ben-evans.com/benedictevans/rss.xml",                        4),
    ("Y Combinator",         "https://www.ycombinator.com/blog/rss",                                   4),
    ("量子位",               "https://www.qbitai.com/feed",                                            5),
    ("极客公园",             "https://www.geekpark.net/rss",                                           5),
    ("机器之心",             "https://jiqizhixin.com/rss",                                             5),
    ("虎嗅",                 f"{RSSHUB_URL}/huxiu/article",                                           5),
    ("晚点LatePost",         "https://feeds.feedburner.com/latepost",                                  5),
    ("36氪",                 "https://36kr.com/feed",                                                  4),
    ("36氪快讯",             "https://36kr.com/newsflashes/rss",                                       4),
    ("少数派",               "https://sspai.com/feed",                                                 4),
    ("InfoQ",                "https://www.infoq.cn/feed",                                              4),
    ("澎湃科技",             "https://www.thepaper.cn/rss_list.jsp?cat=104828",                        4),
    ("AI科技大本营",         "https://blog.csdn.net/dQCFKyQDXYm3F8rB0/rss/list",                      3),
    ("钛媒体",               "https://www.tmtpost.com/rss",                                            4),
    ("爱范儿",               "https://www.ifanr.com/feed",                                             3),
    ("雷峰网",               "https://www.leiphone.com/feed",                                          3),
    ("华尔街见闻",           "https://wallstreetcn.com/rss",                                           4),
    ("财新科技",             "https://weekly.caixin.com/rss/index.xml",                                4),
    ("新浪科技",             "https://feed.sina.com.cn/api/roll/get?pageid=153&lid=2509&k=&num=50&page=1&r=&encode=utf-8&callback=feedCallback", 3),
    ("腾讯科技",             "https://tech.qq.com/rss/tech.xml",                                       3),
    ("DeepTech深科技",       "https://www.mittrchina.com/rss",                                         4),
    ("TechCrunch AI",        "https://techcrunch.com/category/artificial-intelligence/feed/",          4),
    ("VentureBeat",          "https://venturebeat.com/feed/",                                          4),
    ("MIT Tech Review",      "https://www.technologyreview.com/feed/",                                 4),
    ("Wired AI",             "https://www.wired.com/feed/category/artificial-intelligence/rss",        3),
    ("Ars Technica AI",      "https://feeds.arstechnica.com/arstechnica/technology-lab",               3),
    ("NVIDIA Blog",          "https://blogs.nvidia.com/feed/",                                         5),
    ("IEEE Spectrum AI",     "https://spectrum.ieee.org/feeds/topic/artificial-intelligence.rss",      4),
    ("Science Daily AI",     "https://www.sciencedaily.com/rss/computers_math/artificial_intelligence.xml", 3),
    ("Towards Data Science", "https://towardsdatascience.com/feed",                                    3),
    ("Analytics Vidhya",     "https://www.analyticsvidhya.com/blog/feed/",                            3),

    # == 无人机 ==
    ("DJI 新闻",             "https://www.dji.com/cn/newsroom/rss.xml",                               5),
    ("Drone DJ",             "https://dronedj.com/feed/",                                             5),
    ("Drone Life",           "https://dronelife.com/feed/",                                           4),
    ("sUAS News",            "https://www.suasnews.com/feed/",                                        4),
    ("Inside Unmanned",      "https://insideunmannedsystems.com/feed/",                               3),

    # == 具身机器人 / 人形机器人 ==
    ("The Robot Report",     "https://www.therobotreport.com/feed/",                                  5),
    ("IEEE Spectrum Robotics","https://spectrum.ieee.org/feeds/topic/robotics.rss",                   5),
    ("Robotics Business",    "https://www.roboticsbusinessreview.com/feed/",                          4),
    ("Automation World",     "https://www.automationworld.com/rss.xml",                               3),

    # == 算力芯片 / 半导体 ==
    ("AnandTech",            "https://www.anandtech.com/rss/",                                        5),
    ("SemiAnalysis",         "https://www.semianalysis.com/feed",                                     5),
    ("EE Times",             "https://www.eetimes.com/feed/",                                         4),
    ("Semiconductor Digest", "https://www.semiconductor-digest.com/feed/",                            4),
    ("Tom's Hardware",       "https://www.tomshardware.com/feeds/all",                                3),

    # == 新型电池 / 红外 / 硬科技 ==
    ("OFweek 电子工程",      "https://news.ofweek.com/rss/epaper.xml",                                4),
    ("Electrek",             "https://electrek.co/feed/",                                             4),
    ("CleanTechnica",        "https://cleantechnica.com/feed/",                                       4),
    ("New Atlas Tech",       "https://newatlas.com/feed/",                                            3),
    ("Ars Technica Hardware","https://feeds.arstechnica.com/arstechnica/gadgets",                     3),

    # == 新增：AI 巨头官方 Blog ==
    ("Anthropic Blog",       "https://rsshub.bestblogs.dev/anthropic/news",                          5),
    ("Meta Engineering",     "https://engineering.fb.com/feed/",                                     5),
    ("AWS AI Blog",          "https://aws.amazon.com/blogs/machine-learning/feed/",                  4),
    ("DeepMind Substack",    "https://deepmind.substack.com/feed",                                   5),
    ("ARM Blog",             "https://community.arm.com/arm-community-blogs/rss",                    4),

    # == 新增：芯片深度分析 ==
    ("Semiconductor Engineering", "https://semiengineering.com/feed/",                               4),

    # == 新增：机器人学术 ==
    ("IEEE RAS",             "https://www.ieee-ras.org/rss",                                         4),

    # == AI 聚合日报（通过 RSS，主力用 fetch_aihot_api 补充）==
    ("AIHOT AI日报",         "https://aihot.virxact.com/rss",                                         5),

    # == 本地 RSSHub 关键词搜索（覆盖五大赛道）==
    ("36氪-大模型",          f"{RSSHUB_URL}/36kr/search/items/大模型",                               5),
    ("36氪-具身机器人",      f"{RSSHUB_URL}/36kr/search/items/具身机器人",                           5),
    ("36氪-无人机",          f"{RSSHUB_URL}/36kr/search/items/无人机",                               5),
    ("36氪-算力芯片",        f"{RSSHUB_URL}/36kr/search/items/算力芯片",                             5),
    ("36氪-新型电池",        f"{RSSHUB_URL}/36kr/search/items/固态电池",                             4),
    ("虎嗅-AI",              f"{RSSHUB_URL}/huxiu/search/AI",                                        5),
    ("虎嗅-机器人",          f"{RSSHUB_URL}/huxiu/search/机器人",                                    4),
    ("虎嗅-无人机",          f"{RSSHUB_URL}/huxiu/search/无人机",                                    4),
]

JINA_MAX_CHARS = 3000
RSS_PER_SOURCE = 6  # 每天跑一次，每源取6条确保覆盖充分
JINA_DELAY_SEC = 1.0

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

def _fetch_rss(source_name, rss_url, max_items):
    try:
        resp = requests.get(rss_url, headers=_HTTP_HEADERS, timeout=15)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        results = []
        for item in root.findall(".//item")[:max_items]:
            title   = (item.findtext("title") or "").strip()
            link    = (item.findtext("link")  or "").strip()
            pub_str = (item.findtext("pubDate") or "").strip()
            desc    = re.sub(r"<[^>]+>", "", item.findtext("description") or "").strip()
            if not title or not link:
                continue
            try:
                pub_readable = parsedate_to_datetime(pub_str).strftime("%Y-%m-%d %H:%M")
            except Exception:
                pub_readable = pub_str[:16]
            results.append({"title": title, "url": link, "rss_summary": desc[:300],
                             "content": "", "pub": pub_readable, "source": source_name, "author": ""})
        return results
    except Exception as e:
        log.warning(f"RSS fetch failed [{source_name}]: {e}")
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

def _fetch_blog_scraper(source_name, blog_url, max_items=3):
    """通过 Jina Reader 抓取没有 RSS 的博客列表页，提取文章标题和链接"""
    try:
        resp = requests.get(
            f"https://r.jina.ai/{blog_url}",
            headers={**_HTTP_HEADERS, "Accept": "text/plain", "X-Return-Format": "text", "X-Timeout": "15"},
            timeout=25,
        )
        if resp.status_code != 200:
            return []
        text = resp.text
        # 提取 markdown 格式链接 [title](url)
        links = re.findall(r'\[([^\]]{10,120})\]\((https?://[^\)]+)\)', text)
        results = []
        seen = set()
        for title, url in links:
            title = title.strip()
            # 过滤导航菜单等无意义链接
            if any(w in title.lower() for w in ['home', 'about', 'contact', 'menu', 'search', 'login', 'sign']):
                continue
            if url in seen:
                continue
            seen.add(url)
            results.append({
                "title": title, "url": url, "rss_summary": "",
                "content": "", "pub": datetime.now().strftime("%Y-%m-%d"),
                "source": source_name, "author": "",
            })
            if len(results) >= max_items:
                break
        log.info(f"Blog scrape [{source_name}]: {len(results)} items")
        return results
    except Exception as e:
        log.warning(f"Blog scrape failed [{source_name}]: {e}")
        return []


def _fetch_fulltext_jina(url):
    try:
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
    for source_name, rss_url, weight in RSS_SOURCES:
        log.info(f"拉取 RSS: {source_name}")
        for art in _fetch_rss(source_name, rss_url, RSS_PER_SOURCE):
            key = re.sub(r"\s+", "", art["title"])[:30]
            if key and key not in seen:
                seen.add(key)
                art["weight"] = weight
                all_articles.append(art)

    log.info(f"RSS 共拉取 {len(all_articles)} 篇（去重后）")
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
    candidates = []
    TRACK_QUOTA = 5
    for track in ["无人机", "机器人", "芯片", "电池储能"]:
        bucket = sorted(buckets[track], key=lambda x: x.get("weight", 1), reverse=True)
        taken = bucket[:TRACK_QUOTA]
        candidates.extend(taken)
        if taken:
            log.info(f"  赛道[{track}] 取 {len(taken)} 篇（共 {len(bucket)} 篇可选）")

    # AI通用取剩余名额，总数上限40条
    remaining_quota = max(40 - len(candidates), 20)
    ai_sorted = sorted(buckets["AI通用"], key=lambda x: x.get("weight", 1), reverse=True)
    candidates.extend(ai_sorted[:remaining_quota])
    log.info(f"  赛道[AI通用] 取 {min(remaining_quota, len(ai_sorted))} 篇（共 {len(ai_sorted)} 篇可选）")
    log.info(f"开始 Jina 抓取 {len(candidates)} 篇正文...")

    for i, art in enumerate(candidates):
        log.info(f"  ({i+1}/{len(candidates)}): {art['title'][:50]}")
        text, author = _fetch_fulltext_jina(art["url"])
        art["content"] = text if text else art.get("rss_summary", "")
        art["author"]  = author
        log.info(f"    {'✓' if text else '↓'} {len(art['content'])} 字")
        if i < len(candidates) - 1:
            time.sleep(JINA_DELAY_SEC)

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
    if not ANTHROPIC_AUTH_TOKEN:
        raise ValueError("ANTHROPIC_AUTH_TOKEN not set")
    max_retries = 5
    for attempt in range(max_retries):
        try:
            resp = requests.post(
                f"{LLM_BASE_URL}/chat/completions",
                headers={"Authorization": f"Bearer {ANTHROPIC_AUTH_TOKEN}", "Content-Type": "application/json"},
                json={"model": LLM_MODEL, "max_tokens": 16384, "messages": messages},
                timeout=600,
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else 0
            if status in (429, 500, 502, 503, 504) and attempt < max_retries - 1:
                wait = 30 * (2 ** attempt)
                log.warning(f"LLM HTTP {status} (第{attempt+1}次)，{wait}秒后重试")
                time.sleep(wait)
            else:
                raise
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            if attempt < max_retries - 1:
                wait = 30 * (2 ** attempt)
                log.warning(f"LLM 连接失败 (第{attempt+1}次)，{wait}秒后重试: {e}")
                time.sleep(wait)
            else:
                raise


def summarize_news(raw_results):
    if not raw_results:
        return []

    # 素材编号→URL，覆盖全部送LLM的素材
    url_index = {str(i+1): r.get("url", "") for i, r in enumerate(raw_results)}

    materials = "\n\n".join(
        f"[{i+1}] 来源:{r.get('source','')} 作者:{r.get('author','')}\nURL:{r.get('url','')}\n标题:{r['title']}\n正文:\n{r['content'] or r.get('rss_summary','')}"
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

降权情形（降低评分或不收录）：
- 宏大愿景掩盖执行细节的PR稿
- 常规融资（10亿以下）/常规合作
- "生态系统""全面赋能"等空洞话术
- 信息量低的简讯、快报、评测文章
- 政策/法规/监管类新闻不收录
- 量子位/虎嗅等媒体的解读分析文章（非一手事件报道），降2-3分

【合并与覆盖规则——极其重要】
- 多条素材报道完全相同的事件 → 合并为一条，source_index写最详细那条的编号
- 但：不同事件绝对不得合并，即使涉及同一公司
- 每个独立事件都必须输出一条，即使你认为不重要也要打低分(1-3分)输出，不得遗漏任何独立事件
- 输出条目数通常在10-20条之间。如果你只输出了不到10条，说明你合并过度，请回头检查是否漏掉了独立事件
- 例：OpenAI发布模型 vs OpenAI首席科学家发文 vs OpenAI承认安全事件 = 3个不同事件，必须输出3条

【输出要求】
对合并后的每个独立事件输出：
1. title: 标题严格控制在18-22个中文字符以内（绝对不能超过22字！超过必须精简），直接说事，包含核心主语和关键事实，不要写成两个分句
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
   - 政策/监管类 → 不收录

   【第二层·性质】必选一个：
   - 技术突破：有数据/benchmark，能力质变
   - 产业动态：发布/合作/收购/战略/资本

   严禁自创标签，只能来自：AI大模型/算力芯片/具身机器人/无人机/新型储能/技术突破/产业动态
5. sentiment: 正面/负面/中性
6. cluster_tag: 厂商动态类/技术突破类/行业趋势类/产品评测类/市场情绪类
7. companies: 涉及的公司/产品名称（重要！代码用此字段做同公司去重，务必准确填写主要公司名）
8. source_index: 原始素材编号（合并时选最详细那条）
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
【跨期去重·硬性规则】以下是上期（{prev_date}）已发送的标题，本期绝对不得再选相同或高度相似的事件（即使换了角度也不行）：
{titles_str}
如果某篇文章的核心事件与上述任一标题相同，直接跳过，选其他文章。
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
                    pass
        if parsed:
                for i, item in enumerate(parsed):
                    # URL修正：source_index反查 → 模糊标题匹配兜底
                    idx = str(item.get("source_index", ""))
                    if idx in url_index and url_index[idx]:
                        item["source_url"] = url_index[idx]
                    else:
                        # source_index查不到，用标题前10字模糊匹配原始素材
                        llm_title = re.sub(r"\s+", "", item.get("title", ""))[:10]
                        if llm_title:
                            for r in raw_results:
                                raw_title = re.sub(r"\s+", "", r.get("title", ""))
                                if llm_title in raw_title and r.get("url"):
                                    item["source_url"] = r["url"]
                                    break
                    # ── 标签白名单过滤（只保留合法标签，去掉LLM自创的） ──
                    VALID_CATS = {"AI大模型","算力芯片","具身机器人","无人机","新型储能","技术突破","产业动态"}
                    cat = item.get("category","")
                    parts = cat.replace("/","、").replace("，","、").replace(",","、").split("、")
                    # 去掉所有空格后匹配白名单（Qwen会输出"AI 大模型"带空格）
                    clean_cats = list(dict.fromkeys(p.strip().replace(" ","") for p in parts if p.strip().replace(" ","") in VALID_CATS))  # 去重且保序
                    if clean_cats:
                        item["category"] = "、".join(clean_cats)

                    # 联网核查移到组稿之后（只对最终selected的5条做）

                # ── 遗漏检查：找出LLM漏掉的非OpenAI独立事件，二次LLM补充打分 ──
                covered_indices = set()
                for item in parsed:
                    si = str(item.get("source_index", ""))
                    if si:
                        covered_indices.add(si)

                # 收集LLM已覆盖的标题bigram指纹
                llm_bigrams_all = set()
                for item in parsed:
                    t = re.sub(r"\s+", "", item.get("title", "")).lower()
                    for k in range(len(t) - 1):
                        llm_bigrams_all.add(t[k:k+2])

                missed_materials = []
                for idx, r in enumerate(raw_results):
                    si = str(idx + 1)
                    if si in covered_indices:
                        continue
                    raw_t = re.sub(r"\s+", "", r.get("title", "")).lower()
                    raw_bgs = set(raw_t[k:k+2] for k in range(len(raw_t)-1)) if len(raw_t) > 1 else set()
                    overlap = len(raw_bgs & llm_bigrams_all) if raw_bgs else 0
                    ratio = overlap / len(raw_bgs) if raw_bgs else 0
                    if ratio > 0.4:
                        continue
                    # 跳过OpenAI重复报道
                    tl = r.get("title", "").lower()
                    if any(k in tl for k in ["openai", "gpt-6", "gpt6", "astra", "chatgpt", "altman", "奥尔特曼"]):
                        continue
                    missed_materials.append((si, r))

                if missed_materials:
                    log.info(f"  发现 {len(missed_materials)} 条LLM遗漏的非OpenAI素材，二次补充打分...")
                    missed_text = "\n\n".join(
                        f"[{si}] 来源:{r.get('source','')}\n标题:{r['title']}\n正文:\n{(r.get('content') or r.get('rss_summary',''))[:1500]}"
                        for si, r in missed_materials[:10]
                    )
                    patch_prompt = f"""以下素材在第一轮分析中被遗漏，请用同样标准打分并生成简报。

规则：
- title 严格18-22字以内（绝对不能超过22字！），直接说事，包含核心主语和关键事实
- summary 130-170字，说清事件、关键数据/背景、产业影响，每句以句号结尾
- score 1-10分
- 必须加权≥8分：技术里程碑/首次实现某能力/头部公司新产品/千亿级投资并购/重大IPO/安全事故
- 降权：媒体解读文降2-3分，快报降权
- companies 填涉及的主要公司名（不是来源媒体名）
- category 两层标签(赛道、性质)，仅限：AI大模型/算力芯片/具身机器人/无人机/新型储能 + 技术突破/产业动态
- source_url 留空，selected 全部false
- 字段值不得含英文双引号

严格输出JSON数组：
[{{"index":1,"score":8,"selected":false,"category":"具身机器人、产业动态","title":"标题","summary":"摘要","key_point":"核心","companies":"公司","sentiment":"正面","cluster_tag":"厂商动态类","source_index":"1","source_url":"","source_name":"来源","author":""}}]

--- 遗漏素材 ---
{missed_text}
"""
                    try:
                        patch_raw = call_llm([
                            {"role": "system", "content": "你是科技产业洞察者。只输出严格JSON数组，不含markdown标记。"},
                            {"role": "user",   "content": patch_prompt},
                        ])
                        patch_raw = patch_raw.strip()
                        if patch_raw.startswith("```"):
                            patch_raw = re.sub(r"^```\w*\n?", "", patch_raw)
                            patch_raw = re.sub(r"\n?```$", "", patch_raw)
                        patch_items = json.loads(patch_raw)
                        if isinstance(patch_items, list):
                            for pi in patch_items:
                                pidx = str(pi.get("source_index", ""))
                                if pidx in url_index and url_index[pidx]:
                                    pi["source_url"] = url_index[pidx]
                                pi["selected"] = False
                                pi["score"] = int(pi.get("score", 0))  # 确保分数为整数
                                parsed.append(pi)
                                log.info(f"  补充打分: [{pi.get('title','')}] (分{pi.get('score','')}, 公司:{pi.get('companies','')})")
                    except Exception as e:
                        log.warning(f"  二次补充LLM调用失败: {e}")
                else:
                    log.info("  LLM覆盖完整，无需补充")

                # ── 统一组稿：质量优先 + 多样性平衡 ──
                # 读取上期标题（用bigram做模糊匹配）
                prev_titles_raw = []
                prev_bigrams_list = []  # 每条上期标题的bigram集合
                try:
                    if os.path.exists(last_sent_path):
                        with open(last_sent_path, encoding="utf-8") as f:
                            prev_titles_raw = json.load(f).get("titles", [])
                        for pt in prev_titles_raw:
                            t = re.sub(r"\s+", "", pt).lower()
                            bgs = set(t[k:k+2] for k in range(len(t)-1)) if len(t) > 1 else set()
                            prev_bigrams_list.append(bgs)
                except Exception:
                    pass
                prev_set = set()  # 保留精确匹配兼容

                # 清除所有selected标记，由代码统一决定
                for item in parsed:
                    item["selected"] = False

                # 按分数降序排列（int排序，确保类型一致）
                for item in parsed:
                    try:
                        item["score"] = int(item.get("score", 0))
                    except (ValueError, TypeError):
                        item["score"] = 0
                all_sorted = sorted(parsed, key=lambda x: x.get("score", 0), reverse=True)
                # 调试：输出排序后前8条
                for _di, _d in enumerate(all_sorted[:8]):
                    _co = (_d.get("companies","") or "").split("、")[0].split(",")[0].strip().lower()
                    log.info(f"  排序#{_di+1}: 分{_d.get('score','')} 公司[{_co}] {_d.get('title','')[:25]}")

                def _get_company(item):
                    c = (item.get("companies", "") or "").split("、")[0].split(",")[0].strip().lower()
                    return c

                def _get_track(item):
                    """从category中提取赛道"""
                    cat = item.get("category", "")
                    for t in ["算力芯片", "具身机器人", "无人机", "新型储能"]:
                        if t in cat:
                            return t
                    return "AI大模型"

                def _is_dup(item):
                    """检查跨期重复：bigram重合率>35%就认为是同一事件（降低阈值防止换措辞逃过去重）"""
                    t = re.sub(r"\s+", "", item.get("title", "")).lower()
                    if len(t) < 4:
                        return False
                    item_bgs = set(t[k:k+2] for k in range(len(t)-1))
                    # 同时检查摘要的bigram（防止标题完全不同但内容相同）
                    s = re.sub(r"\s+", "", item.get("summary", "")).lower()
                    summ_bgs = set(s[k:k+2] for k in range(len(s)-1)) if len(s) > 1 else set()
                    for pi, prev_bgs in enumerate(prev_bigrams_list):
                        if not prev_bgs:
                            continue
                        # 标题bigram匹配
                        overlap = len(item_bgs & prev_bgs)
                        ratio = overlap / min(len(item_bgs), len(prev_bgs))
                        if ratio > 0.35:
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
                # 同公司硬性限制：每家公司最多1条，不论分数多高
                final = []
                company_count = {}
                seen_tracks = set()
                for item in all_sorted:
                    if len(final) >= 5:
                        break
                    if _is_dup(item):
                        log.info(f"  跨期去重: [{item.get('title','')}]")
                        continue
                    if _is_same_event(item, final):
                        log.info(f"  同次去重: [{item.get('title','')[:30]}]")
                        continue
                    company = _get_company(item)
                    cc = company_count.get(company, 0) if company else 0
                    if cc >= 1 and company:
                        continue  # 同公司硬性最多1条
                    item["selected"] = True
                    final.append(item)
                    if company:
                        company_count[company] = cc + 1
                    seen_tracks.add(_get_track(item))
                    log.info(f"  质量入选: [{item.get('title','')}] (公司:{company}[{cc+1}], 分{item.get('score','')}, 赛道:{_get_track(item)})")

                # === 阶段2：放宽同公司到2条——仍按分数降序 ===
                if len(final) < 5:
                    company_count = {}
                    for item in final:
                        c = _get_company(item)
                        if c:
                            company_count[c] = company_count.get(c, 0) + 1
                    for item in all_sorted:
                        if len(final) >= 5:
                            break
                        if item.get("selected"):
                            continue
                        if _is_dup(item):
                            continue
                        company = _get_company(item)
                        if company and company_count.get(company, 0) >= 2:
                            continue
                        item["selected"] = True
                        final.append(item)
                        if company:
                            company_count[company] = company_count.get(company, 0) + 1
                        seen_tracks.add(_get_track(item))
                        log.info(f"  放宽入选: [{item.get('title','')}] (公司:{company}, 分{item.get('score','')})")

                # === 阶段3：兜底补满 ===
                if len(final) < 5:
                    for item in all_sorted:
                        if len(final) >= 5:
                            break
                        if item.get("selected"):
                            continue
                        item["selected"] = True
                        final.append(item)
                        log.info(f"  兜底入选: [{item.get('title','')}]")

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

                    # 标题：超过35字时在最后一个逗号前截断（保持语义完整）
                    title = item.get("title", "")
                    if len(title) > 35:
                        # 在最后一个逗号/顿号处截断
                        cut = title[:35]
                        for ch in ["，", "、", "："]:
                            pos = cut.rfind(ch)
                            if 15 <= pos:
                                cut = cut[:pos]
                                break
                        item["title"] = cut
                        log.info(f"  标题截断: {title[:45]}... → {item['title']}")

                    # 摘要：保证完整句子，上限180字（A4一页5条放得下）
                    summary = item.get("summary", "")
                    if len(summary) > 180:
                        cut = summary[:180]
                        best_pos = -1
                        for ch in ["。", "；"]:
                            pos = cut.rfind(ch)
                            if pos >= 100:
                                best_pos = max(best_pos, pos)
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

                    # URL验证
                    url = item.get("source_url", "")
                    if not url or "http" not in url:
                        # 重新尝试source_index反查
                        idx = str(item.get("source_index", ""))
                        if idx in url_index and url_index[idx]:
                            item["source_url"] = url_index[idx]
                            url = item["source_url"]
                        else:
                            # 标题模糊匹配原始素材URL
                            item_title = re.sub(r"\s+", "", item.get("title", ""))[:10]
                            for ri, r in enumerate(raw_results):
                                raw_t = re.sub(r"\s+", "", r.get("title", ""))[:10]
                                if item_title and raw_t and item_title in raw_t or raw_t in item_title:
                                    if r.get("url"):
                                        item["source_url"] = r["url"]
                                        url = r["url"]
                                        item["source_index"] = str(ri + 1)
                                        log.info(f"  URL模糊匹配: [{item.get('title','')}] → {url[:50]}")
                                        break
                            else:
                                log.warning(f"  URL缺失: [{item.get('title','')}]")
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
             "source_url": "", "source_name": "AI汇总", "author": "", "comments": ""}]


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
    items = selected[:5] if len(selected) >= 5 else news_items[:5]

    # 构建富文本内容
    content_lines = []
    for i, item in enumerate(items, 1):
        cat = item.get("category", "")
        title = item.get("title", "")
        summary = item.get("summary", "")
        url = item.get("source_url", "")

        cat_elem = {"tag": "text", "text": f"{i}. 【{cat}】"}
        title_elem = {"tag": "a", "text": title, "href": url} if url else {"tag": "text", "text": title}
        content_lines.append([cat_elem, title_elem])
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

def main():
    log.info("=" * 60)
    log.info("科技前沿简报 开始运行")
    log.info("=" * 60)

    report_date = datetime.now().strftime("%Y年%m月%d日")
    log.info(f"报告日期: {report_date}")
    subject = f"📡 科技前沿简报 · {report_date}"

    log.info("【Step 1】拉取 RSS 新闻...")
    raw_results, all_rss_articles = search_news()

    log.info("【Step 1b】调用 aihot API 获取 AI 精选动态...")
    aihot_results = fetch_aihot_api(limit=30)
    if aihot_results:
        raw_results = raw_results + aihot_results
        log.info(f"合并 aihot 后共 {len(raw_results)} 篇素材")



    # Step 1c: 无 RSS 博客爬取（JS渲染网站暂无效，由其他源间接覆盖）

    if not raw_results:
        log.error("无结果，退出")
        sys.exit(1)

    # ── 过滤2天前旧文章（每天跑一次，2天窗口+1天冗余） ──
    cutoff = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
    fresh_results = []
    for item in raw_results:
        pub = item.get("pub", "") or item.get("pub_date", "")
        pub_clean = pub[:16].replace("T", " ") if pub else "未知"
        if pub_clean != "未知" and pub_clean[:10] < cutoff:
            continue
        fresh_results.append(item)
    log.info(f"过滤旧文章: {len(raw_results)} → {len(fresh_results)} 篇（3天内）")
    raw_results = fresh_results

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
    success = send_email(html_content, subject)

    log.info("【Step 5】写入 CSV...")
    append_to_csv(news_items, report_date)

    log.info("【Step 6】生成 PDF...")
    generate_brief(news_items, report_date)

    log.info("【Step 7】推送飞书...")
    send_feishu(news_items, report_date)

    # ── 保存本期+前两期标题，供跨期去重（保留最近3期=15条） ──
    try:
        selected_items = [item for item in news_items if item.get("selected") is True]
        final_5 = selected_items[:5] if len(selected_items) >= 5 else news_items[:5]
        new_titles = [item.get("title", "") for item in final_5]
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
