# 科技前沿简报

每天早上 8:30 自动推送一份科技新闻速报到飞书和邮箱。每天只发 5 条。

## 它做什么

从 60 多个信息源（OpenAI、谷歌、英伟达的官方博客，加上量子位、虎嗅、36氪这些中文媒体）抓新闻，交给大模型筛选，挑出 5 条最值得看的，生成一段摘要，推送到飞书，同时发邮件、存 CSV、出 PDF。

跑在 GitHub Actions 上，不需要自己的服务器，也不用花钱。

## 筛选逻辑

不是按热度排序。prompt 里要求模型做三件事：

1. 去掉 PR 词，只看这件事到底做了什么、有没有可验证的结果
2. 判断护城河是否变化，普通的小迭代不打高分
3. 想一下三个月后还会不会有人提这件事

举个例子：一篇是创业公司发模型，通篇"三段范式全部落地"这种话；另一篇是 Figure AI 公布机器人实测数据，56% 成功率，同行公开质疑。后一条入选，前一条被降权。

## 结构

```
daily_news.py          核心逻辑，一个文件
requirements.txt       依赖
.github/workflows      GitHub Actions 定时任务
last_sent.json         去重库，自动更新
```

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