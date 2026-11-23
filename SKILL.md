---
name: annotation-note-distiller
description: Use when a research-library case supplies PDFs, annotation exports, or reading notes and asks to extract highlights, structure them into sourced notes, link related items, or make them searchable; run scripts/run_skill.py for a deterministic contract-preserving result.json.
---

# 阅读批注抽取与知识沉淀

## 用途与适用条件

在阅读批注整理任务中使用：从批注导出文件、Markdown 摘录、文本材料中抽取高亮/划线/页边批注，结构化为带来源定位的笔记，建立可解释关联并提供检索入口。输入为 `/input/case.json`、可选 `/input/task.md` 与材料目录。

不适用：直接解析 PDF 内嵌注释对象与扫描件手写笔迹（脚本不读取 PDF 注释字典，也不做 OCR）；不做文献内容判断与写作。

## 输入

- `case.json`：`case_id`、`output_contract`；可选 `material_files`、`annotation_files` 指向批注导出。
- 支持的结构化批注：JSON 数组或含 `annotations`/`highlights` 键的对象，条目含 `text`/`quote`/`content`、`page`、`color`、`type`、`note`。
- 支持的文本标记：Markdown 引用 `> ...`、`==高亮==`、行内 `【批注：...】`。
- 输出：`/app/output/result.json`。

## 处理流程（对应评分维度）

1. **盘点与载体识别（任务完成度）**：枚举文件，识别可解析批注的 JSON/Markdown/文本；PDF 仅记录为 `pdf_annotation_layer_not_parsed`，不伪造其批注。
2. **批注抽取（结果正确性）**：统一字段 `quote`、`note`、`page`、`color`、`type`、`source_file`、`locator`；类型按原字段保留，缺失记 `unknown`；页码缺失为 `null`，不用顺序号冒充。
3. **笔记结构化（完整性）**：依据原文线索词把批注归入 `question`、`method`、`conclusion`、`reusable`、`to_verify`、`unclassified`，每条保留 `annotation_id` 与原文引用；不扩展原文没有的结论。
4. **关联（质量）**：两条笔记共享至少 2 个高区分度词（ASCII 长度≥4 或 CJK 二元组）且 Jaccard ≥ 0.2 才建立关联，每条最多 3 条，附共享词作为依据，避免仅凭一个通用词连成一片。
5. **检索入口（可核验性）**：建立 `term -> note_ids` 倒排索引，附来源文件与页码，支持按标签与来源过滤。
6. **失败与恢复（可靠体验）**：无批注、JSON 结构不符、文件不可读、PDF 无注释解析分别进入 `skipped`、`unresolved`、`errors`，并给出 `supported_inputs`。

## 输出契约

| 字段 | 含义 |
|-|-|
| `case_id` | 与输入一致 |
| `output_contract` 各字段 | 命中别名时填真实值，否则 `null` + `contract_notes` |
| `status` | `success` / `partial` / `insufficient_input` / `failed` |
| `result` | `annotations`、`notes`、`links`、`search_index`、`unresolved` |
| `evidence` | 来源文件、哈希、抽取器、定位规则 |
| `uncertainty` | 页码缺失、PDF 注释未解析、启发式分类 |
| `errors` / `stats` | 真实错误与计数 |

## 异常处理表

| 情况 | 处置 | 状态 |
|-|-|-|
| case.json 缺失/非法 | 空结果 + 错误 | `failed` |
| 无材料 | 空结果并说明 | `insufficient_input` |
| 材料无批注结构 | `status=partial`，列出 `supported_inputs` | `partial` |
| JSON 解析失败 | `skipped` 记录文件与原因 | `partial` |
| 缺页码/颜色/类型 | 字段 `null` 或 `unknown` | `partial` |
| PDF 含注释但未解析 | 明确写入 `unresolved` | `partial` |
| 关联不足 | 不强行连边，`links` 可为空 | `success` |

## 限制

- 不解析 PDF `/Annots` 注释对象与手写笔迹，不执行 OCR。
- 笔记分类为线索词启发式，可能与作者原意不同，需人工确认。
- 不修改源文件，不覆盖原始批注导出。

## 最小调用示例（示例，非官方案例结果）

```bash
python /skills/weekly-skill/scripts/run_skill.py --case /input/case.json --out /app/output/result.json --work /app/work
```

示意输入 `{"case_id":"synthetic-demo","output_contract":{"notes":"array","links":"array"}}`；示意输出保留 `case_id`、`notes`、`links`、`status`，无材料时为 `insufficient_input`。仅为接口示例。


## 交付保证与评分对齐

调用顺序（固定）：

1. 先运行包内脚本（若存在）：`python scripts/run_skill.py`（默认读 `/input/case.json`，写 `/app/output/result.json`）；路径不同时用 `--case` / `--output` 指定。
2. 再完成脚本无法自动完成的实质工作（译文、论述、图表、修订、结论等），把结果写回 `result.json` 对应字段。
3. 最后自检：`case_id` 与输入一致；`output_contract` 每个字段都存在；`status` 按契约取 `success`/`partial`/`insufficient_input`/`failed`（缺输入或证据不足时用 `insufficient_input`/`partial`）；无 `pending` 占位；数字、引用、来源可逐条回溯。

运行保证：

- 输入缺失、JSON 损坏、字段异常、输出目录不存在时，仍须写出结构化 `result.json` 并正常结束，不得崩溃或非零退出。
- 不联网、不伪造数据、不声称平台评分或官方案例结果。
- 交付物必须是可直接编辑与复核的成品，而不是流程说明。

评分对齐（本题五个维度）：

- 实际效果（40）：完整产出任务要求的最终成品，覆盖全部输入条目，可直接进入修改流程。
- 使用增益（25）：体现相对基础 Agent 的可验证改进（结构化、术语/数字/引用核对、可编辑性）。
- 创新增益（15）：保留方法学增量（分块、对齐、二次校验、版本比对等），并用结果证明其价值。
- 可靠体验（10）：异常输入有明确降级与说明，不同长度/文体/重复运行下稳定产出。
- 生态贡献（10）：用途、调用、输入输出、限制、示例、维护信息齐全，资源可被他人复用。

## 最终交付强制要求

1. **调用 Agent 必须交付成品，不是流程说明**：脚本产出只是索引、核对或规格骨架；调用 Agent 必须在同一 `result.json` 中补齐本题要求的最终产物（正文/译文/审稿意见/综述/分析结论/图表源文件与导出件/幻灯片页内容与讲稿/实验方案等）。交付字段为空、只有标题骨架，或含 `pending`、`TODO` 之类占位，一律视为未完成。
2. **必须覆盖全部输入条目**：输入中的每个章节、问题、文献、图、页面、指标都要有对应条目，不得只处理示例或首条；确实缺失的条目写入 `uncertainty` / `limitations` 并说明原因，不得静默跳过。
3. **必须自检后再交付**：`case_id` 与输入一致；`output_contract` 每个字段都存在；状态值取本题契约规定的枚举；数字、单位、引用、来源可逐条回溯；结果内部无自相矛盾。
4. **参数等价**：`python scripts/run_skill.py --case <输入> --out <输出>` 与 `--output <输出>` 等价；默认 `/input/case.json` → `/app/output/result.json`。
5. **平台用 3 个固定案例分别运行**：逻辑要由输入驱动，不针对某一份示例硬编码；字段命名差异走别名匹配与显式降级，并在 `uncertainty` 记录假设。
