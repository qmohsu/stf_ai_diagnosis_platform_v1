# STF Repository Commit and Pull Request Documentation Rule

## Repository Scope

These instructions apply only to code developed in the canonical
`qmohsu/stf_ai_diagnosis_platform_v1` repository and to downstream checkouts or
forks derived from that repository. This includes the current
`stf_ai_diagnosis_platform_v3` workspace, whose `origin` is the downstream
`SpaceDX666/stf_ai_diagnosis_platform_v1` repository.

These are repository-scoped instructions. Do not install or copy them to a
user-level or global `~/.codex/AGENTS.md` file, and do not apply them to
unrelated repositories.

The following rule is mandatory for every workflow in this repository that is
expected to create a local Git commit or open or update a pull request. Treat it
as a pre-implementation, pre-commit, and pre-PR gate. More specific
instructions deeper in this repository may add stricter requirements, but they
must not weaken this rule.

If a task is initially not expected to create a commit or PR but the user asks
for one later, apply this gate before making further changes and audit the
existing diff against it.

## 文档与代码同步

1. 任何改变功能、接口、配置、部署方式或架构的代码改动，都要在同一个
   commit 里同步更新开发文档，不允许“代码先合、文档以后补”。

2. 动手前先定位仓库根目录，并读取根目录 `CLAUDE.md` 的
   “Documentation Update Rule” 一节，按其中的路由表判断改动属于 V1、
   V2 还是 V3。`stf_v3/` 和 V3 前端的改动属于 V3。如果该文件或该章节
   缺失，停止提交或开 PR，并向维护者报告，不要自行猜测文档路由。

3. V3 改动必须更新：
   - `docs/v3_design_doc.md`：修改对应章节；在开头“文档控制”表里提升
     版本号、更新日期；在 §4 变更记录增加一条。
   - `docs/v3_dev_plan.md`：更新对应 `PROD-XX` ticket 的状态和验收情况，
     明确已完成和未完成内容；范围有变化时更新 §4“暂缓事项与回头条件”；
     在 §6 变更记录增加一条。
   - 改动影响架构时（新增服务或数据来源、新的部署方式、模块之间新的调用
     关系），在同一个 commit 里更新
     `docs/diagrams/stf_v3_final_architecture.excalidraw`，再运行：

     ```bash
     python docs/diagrams/render_excalidraw.py docs/diagrams/stf_v3_final_architecture.excalidraw docs/diagrams/stf_v3_final_architecture_preview.svg
     ```

     重新生成预览图。不得手工修改 SVG，并在变更记录里注明“图已同步”。

4. 文档只写代码里真实实现的内容，不写计划中或尚未完成的功能。写完后
   逐条对照本次完整 diff：每处需要记录的代码改动在文档中都有对应，文档
   中也没有代码之外的内容。

5. 如果发现代码和文档里已有的决策不一致（例如目录名、接口、数据来源、
   部署方式），不要自行选择一边并改掉另一边。在 PR 描述中单独列出不一致
   之处，交由维护者决定。

6. PR 描述必须列出修改了哪些代码，以及对应更新了哪些文档章节。纯排版、
   注释或错别字改动可以不更新文档，但必须在 PR 描述中说明免更新理由。

## Gate Checklist

Before `git commit`:

- Read the repository root `CLAUDE.md` “Documentation Update Rule”.
- Inspect the complete staged and unstaged diff.
- Confirm every required documentation and generated diagram update is present
  in the same commit as the implementation.
- Confirm documentation describes only behavior present in that diff.
- Do not commit while a required documentation update is missing.

Before opening or updating a PR:

- Re-check the complete branch diff, not only the latest commit.
- Include a code-to-documentation-section mapping in the PR description.
- List any code/document decision mismatch for maintainer resolution.
- For a documentation-exempt formatting, comment-only, or typo-only change,
  state the exemption reason explicitly.
