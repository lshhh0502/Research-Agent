---
name: research
description: 检索文献和本地资料，提取并核验研究证据，编写综述并保存可召回的研究结论。
---
# 研究流程

1. 明确主题、范围、资料截止日期和交付形式。先召回相关研究记忆，标记过期或被替代的记录。
2. 拆分子问题，记录关键词、数据源、过滤条件和未覆盖范围。使用可用 MCP 检索网络，用 grep_search 和 read_file 阅读本地资料。
3. 分配稳定的资料、证据和结论 ID。保存原文短片段、定位及适用条件。无法取得全文时说明证据限制。
4. 比较研究条件与结果，核查反例。把支持和反对证据分别连到结论上。
5. 输出带来源的综述。默认依次写背景、核心进展、对比、结论、参考来源。将适合复用的研究知识保存至项目 Memory。用户对研究流程的反馈用于改进 Skill。

## research 记忆正文格式

正文为纯 JSON，不加代码围栏。以下是字段格式示例，保存前用真实资料替换示例值。未知日期或定位用空字符串，未发现反对证据用空数组，尚未被替代时 superseded_by 用空字符串。每条证据和结论引用的 ID 必须存在于同一记录内。

```json
{
  "scope": "研究范围",
  "conditions": ["适用条件"],
  "coverage_cutoff": "资料覆盖截止日期",
  "last_verified_at": "最后核验时间",
  "open_questions": ["未解决问题"],
  "superseded_by": "",
  "sources": [{"id": "S1", "title": "资料标题", "locator": "URL 或文件路径", "published_at": "", "read_at": "实际读取时间"}],
  "evidence": [{"id": "E1", "source_id": "S1", "excerpt": "实际原文片段", "location": "页码或章节", "conditions": ["适用条件"]}],
  "claims": [{"id": "C1", "statement": "研究结论", "supporting_evidence": ["E1"], "opposing_evidence": [], "status": "支持"}]
}
```

核验状态使用「未核验」「支持」「部分支持」「不支持」。会话折叠时保留研究记忆文件路径与相关结论、证据 ID。
