# 项目协作规则

## 错误会话审核

当用户要求整理情绪识别、工单意图识别或其他对话分类任务的错误样本时，必须先阅读并遵循：

- `references/conversation_error_review_template.md`

输出必须至少包含：

1. 错误样本汇总表。
2. Ground truth、模型预测和置信度。
3. 按原始顺序展示的完整对话。
4. 基于对话证据的审核点。
5. 对 Prompt 问题、Ground truth 口径问题和模型执行问题的区分。

不得只展示局部 evidence，不得改写、缩写或润色原始对话。如 Ground truth 与对话证据存在冲突，应明确标记为待业务审核，不得为迎合既有标签而忽略反证。

## Prompt 版本管理

当新增、修改、评测、回滚或对比 Prompt 时，必须先阅读并遵循：

- `references/prompt_version_management.md`

必须保证“同一个 Prompt 版本号只对应唯一份内容”。修改 Prompt 前先给出变更方案并获得用户确认；修改时必须升级版本号、存档旧版本，且不得覆盖历史评测结果。
