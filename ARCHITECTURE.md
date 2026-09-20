# daily_news.py 全链路梳理

> 本文件是代码结构的诚实记录，用于后续改动时"先看这里、再动手"。
> 最后更新：2026-09-20

## 一、整条数据流（8 步）

```
1. search_news()        抓 RSS + Jina 全文 + 合集拆分
        ↓ 返回 candidates（送 LLM 的素材列表）
2. fetch_aihot_api()    补充 AIHOT 精选（在 main 里合并）
        ↓
3. 【日期过滤】main 里按 pub 时间过滤 3 天内的
        ↓
4. summarize_news()     组 prompt → LLM 打分 → 解析 JSON → URL回填 → 选择5条
        ↓ 返回 parsed（selected 标记好的列表）
5. render_html()        生成邮件 HTML
6. send_email()         Resend 发邮件
7. append_to_csv()      CSV 落盘
8. send_feishu()        飞书推送 + last_sent.json 更新
```

## 二、关键字段约定（分散在各处，必须一致）

| 字段 | 来源/值 | 注意 |
|------|---------|------|
| `pub` | RSS=pubDate转`%Y-%m-%d %H:%M`；新浪JSON=ctime转；aihot=publishedAt[:16] | 日期过滤只认前10位`YYYY-MM-DD` |
| `url` | RSS=link；aihot=url/link；新浪JSON=url | 可能为空，URL回填靠它 |
| `content` | Jina全文，失败则 rss_summary | 送 LLM 用 `content or rss_summary` |
| `source_index` | LLM 输出的素材编号（字符串） | URL 回填第一优先级 |
| `source_url` | LLM留空，代码回填 | 最终链接 |
| `companies` | LLM输出，用`[、,，;；]`分隔 | 同公司去重靠它 |
| `score` | LLM 打分 1-10 | 选择阶段强转 int |
| `selected` | 代码在选择阶段设为 True | 飞书/last_sent 只认 True |

## 三、三个来源的字段产出（易错点）

1. **RSS（_fetch_rss）**：产 `pub`(YYYY-MM-DD HH:MM)、`url`(link，Atom 有 href 回退)
2. **新浪JSON（_fetch_rss_json）**：产 `pub`(ctime 转 YYYY-MM-DD HH:MM)、`url`
3. **aihot（fetch_aihot_api）**：产 `pub`(publishedAt[:16]=YYYY-MM-DDTHH:MM)、`url`(url or link)

⚠️ 三者都用 `pub` 字段，日期过滤统一取 `item["pub"][:10]`。

## 四、URL 回填（已统一为 _resolve_url）

- 优先级：`source_index` 精确反查 → 标题 bigram 相似度(阈值0.5)
- **只允许这两条路径**，不用原来的"前10字子串匹配"（那会配错链接）
- 相似度 <0.5 就保持空，宁可链接缺失也不配错
- ⚠️ 若 URL 为空且 source_index 反查也空，最终飞书那条会没有超链接（可接受，比配错强）

## 五、选择 5 条的规则（阶段1+阶段2）

**阶段1（质量优先，按分数降序）：**
1. 跨期去重 `_is_dup`：标题 bigram 重叠 >35% 或 摘要 bigram >60%
2. 同次去重 `_is_same_event`：和已选条目标题 bigram >35%
3. URL 去重：同 URL 只取最高分那条
4. 同公司限制：每公司最多2条，第2条必须 ≥8分

**阶段2（兜底）：** 补满5条，同公司已有1条就跳过、URL不重复

⚠️ **顺序不可乱**：去重 → 分数 → 选5，任何一步写错都会漏选或错选。

## 六、跨期去重（last_sent.json）

- 存 `{"titles": [...15条...], "date": "..."}`，只存标题
- 每期保存：本期5条 + 上期旧标题，去重后保留最多15条（3期）
- ⚠️ **只靠标题 bigram**，没有公司级/事件级兜底（试过但会误杀，已撤销）
- ⚠️ 旧闻主要靠"日期过滤"拦截（见下），跨期 bigram 只是最后一道

## 七、日期过滤（旧闻根治的关键）

位置：main 里、summarize_news 之前。
规则：
- 只认 `YYYY-MM-DD` 格式（前10位）
- `pub` 缺失 / None / 格式异常 → **直接丢弃**（这是关键，之前会放行导致旧闻混入）
- 3 天前的 → 丢弃
- 过滤后为空 → sys.exit(1)

## 八、合集拆分（search_news 内）

- 检测正文 ≥2 个 `## 标题`（markdown 二级标题）
- 拆成多条独立素材，每条继承原始 url（相同）
- ⚠️ 拆分后 URL 相同 → 靠"URL去重"保证一篇合集最多进1条

## 九、飞书推送（send_feishu）

- 只取 `selected=True` 的条目，取前5条
- **无 selected（空）→ 不推送，直接 return False**（不发兜底垃圾）
- 标题 >22 字在出口检查阶段已截断（词边界，无半截字）
- summary 上限 180 字，只在句号截断

## 十、已知的"糙点"（诚实记录，暂不动）

1. `summarize_news` 一个函数几百行，嵌套深，难读难维护
2. 选择逻辑靠一堆嵌套 if，变量作用域（company_count/used_urls）在函数内闭包
3. `web_search_company` 用 DuckDuckGo，云端可能被墙/限流（联网核查兜底，非关键路径）
4. `for i, item in enumerate(parsed)` 的 `i` 未使用（无害冗余）

## 十一、改动前的检查清单（重要！）

后续任何改动，先回答：
1. 这个改动，数据的**字段名/格式**会不会变？（影响日期过滤、URL回填、去重）
2. 会不会影响 **selected 标记**？（飞书和 last_sent 都依赖它）
3. 会不会引入**重复**（跨期/同次/同URL/同公司）的漏判或误判？
4. 边界情况：URL为空、时间为空、标题超长、合集格式 → 都覆盖了吗？
5. 改完必须**针对边界情况做 dry-run 验证**，不能只看语法通过。
