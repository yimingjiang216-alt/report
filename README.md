# 科技前沿简报

每天早上 8:30 自动推送一份科技新闻速报到飞书和邮箱。每天只发 5 条。

## 它做什么

从 **83 个信息源**（OpenAI、谷歌、英伟达的官方博客，加上量子位、虎嗅、36氪这些中文媒体）抓新闻，按五个赛道分类（无人机、具身机器人、算力芯片、新型储能、AI 大模型），交给大模型筛选，挑出 5 条最值得看的，生成摘要，推送到飞书，同时发邮件、存 CSV、出 PDF。

跑在 GitHub Actions 上，不需要自己的服务器，也不用花钱。

## 筛选逻辑

不是按热度排序。prompt 里要求模型做三件事：

1. 去掉 PR 词，只看这件事到底做了什么、有没有可验证的结果
2. 判断护城河是否变化，普通的小迭代不打高分
3. 想一下三个月后还会不会有人提这件事

举个例子：一篇是创业公司发模型，通篇"三段范式全部落地"这种话；另一篇是 Figure AI 公布机器人实测数据，56% 成功率，同行公开质疑。后一条入选，前一条被降权。

## 结构

```
daily_news.py          核心逻辑：抓取、打分、选稿、推送
requirements.txt       依赖
.github/workflows      GitHub Actions 定时任务
last_sent.json         去重库，自动更新
ARCHITECTURE.md        设计说明：为什么这么分步、失败怎么兜底
外部定时触发配置.md     Actions 定时不稳时的替代方案
```

几处工程兜底：

- **双层去重**：先用 `last_sent.json` 过滤已发过的条目，再用 URL 指纹去重，防止同一新闻换个标题再次入选。
- **赛道校验**：模型输出的分类标签先过白名单，不匹配就按关键词回退，避免标签写错导致整批内容错位。
- **JSON 四级容错**：模型返回不合法 JSON 时依次尝试直接解析、截取代码块、补全括号、正则提取，四步都失败才丢弃该条。
- **定时与执行解耦**：GitHub Actions 的 cron 经常延迟，改用外部定时服务调 API 触发，Actions 只负责执行，保证每天准点。
- **LLM 重试**：遇到 429 / 5xx 用指数退避重试，避免限流导致当天中断。

## 怎么用

Fork 之后在 Actions 里配这几个 Secrets：

- ANTHROPIC_AUTH_TOKEN：大模型 API key（SiliconFlow 免费）
- ANTHROPIC_BASE_URL：https://api.siliconflow.cn
- LLM_MODEL：Pro/deepseek-ai/DeepSeek-V3.2
- RESEND_API_KEY：邮件 API key
- TO_EMAIL / FROM_EMAIL：收件、发件邮箱
- FEISHU_WEBHOOK：飞书机器人 webhook

配完就每天自动跑了，也可以在 Actions 页面手动触发。

本地跑：

```
pip install -r requirements.txt
cp .env.example .env
python daily_news.py
```

## 输出

- 飞书：5 条速报，标签、标题链接、摘要
- 邮件：HTML 版式
- CSV：完整打分数据
- PDF：一页五条

改一下 RSS 源和打分规则，能套到别的领域，比如医疗、金融。