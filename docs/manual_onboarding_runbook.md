# 新手册接入 Runbook(HARNESS-30 S4.2)

| | |
|---|---|
| 作者 | Xiangzhu Yan |
| 日期 | 2026-07-29 |
| 验证案例 | Corolla E11 Haynes(第二本手册,英文,277 页)按本文步骤接入 |
| 前置阅读 | `docs/manual_index_spec.md`(架构与门禁定义) |

把一本新的 PDF 维修手册接入平台 = 产出两个工件(`content.md` +
`index.yaml`)并部署到 manuals 卷。全程约 30-60 分钟机时,人工
操作五条命令。**任何门禁红灯 = 不许部署**,按 §5 处置。

## 0. 前置条件

- 构建主机(PolyU 服务器)`~/bakeoff/` 环境:`venv-mineru`
  (MinerU 3.4.4)、`venv-audit`(pymupdf + pydantic + pyyaml)、
  `manual_pipeline/` 包(与仓库同步)。
- PDF 为**原生文字版**。扫描版(无文字层)按 issue #220 的触发
  流程处理,本 runbook 不适用。
- 手册身份信息:manufacturer / vehicle_model(将写入
  frontmatter 与 applicability)。

## 1. 转换(几何层,GPU,~15-30 分钟)

```bash
cd ~/bakeoff
CUDA_VISIBLE_DEVICES=1 ./venv-mineru/bin/mineru \
  -p <manual>.pdf -o out_mineru_<name> -b hybrid-engine
# 中文手册加:-l ch;英文/拉丁语系省略 -l
```

产物:`out_mineru_<name>/<stem>/hybrid_auto/` 下的
`*_content_list_v2.json` + `images/`。

## 2. 存储构建(文字权威 + 救援 + I0 对账,~2 分钟)

```bash
# frontmatter 模板(新手册手写一个即可):
printf -- '---\nmanufacturer: Toyota\nvehicle_model: Corolla E11\n---\n' > fm.md

PYTHONPATH=. ./venv-audit/bin/python -m manual_pipeline.build \
  --pdf <manual>.pdf \
  --mineru-dir out_mineru_<name>/<stem>/hybrid_auto \
  --out out_<name> --frontmatter-from fm.md
```

**放行标准:输出行显示 `missing=0 char_recall=100.0000%`。**
差一行都会非零退出;此时看 `out_<name>/build_report.json` 的
`missing_samples` 定位,通常是扫描页混入(转 #220 流程)。
`rescues` 数量只是质量参考(TRICITY=48,Corolla=0),不拦截。

## 3. 索引构建(树 + 实体卡 + 摘要 + 门禁 I1-I8;时长≈节点数×3.5s,Corolla 1,264 节点实测 75 分钟)

```bash
KEY=$(podman exec stf-diagnostic-api printenv PREMIUM_LLM_API_KEY)
OPENROUTER_API_KEY=$KEY PYTHONPATH=. \
./venv-audit/bin/python -m manual_pipeline.index_build \
  --mineru-dir out_mineru_<name>/<stem>/hybrid_auto \
  --content-md out_<name>/<stem>.md \
  --manual-id <stem> \
  --out out_<name> \
  --item-lines out_<name>/item_lines.json \
  --manufacturer Toyota --models 'Corolla E11,Corolla' \
  --summaries --model deepseek/deepseek-v3.2
```

**放行标准:`gates=ALL-GREEN` 且报告 `publishable: true`。**
重建时追加 `--reuse-summaries-from <旧index.yaml>`(按稳定
node_id 复用摘要,零重付)。有 golden 迁移映射时追加
`--alias-map slug_map.yaml`。

## 4. 部署(进 manuals 卷,~1 分钟)

```bash
M=/app/data/manuals/"<Model Dir>"
podman exec stf-diagnostic-api mkdir -p "$M/index"
podman cp out_<name>/<stem>.md          stf-diagnostic-api:"$M/index/"
podman cp out_<name>/<stem>.index.yaml  stf-diagnostic-api:"$M/index/"
podman cp out_<name>/images             stf-diagnostic-api:"$M/index/"
```

无需重启:运行时按 mtime 缓存自动加载(日志出现
`manual_index_loaded`)。冒烟:容器内调 `get_manual_toc`,应出现
`index-driven TOC`。回退:删除 `index/` 目录即回旧轨(若有旧
md),或设 `MANUAL_INDEX_TRACK=off`。

## 5. 门禁红灯处置(重要:这是流程的一部分,不是事故)

| 红灯 | 常见原因 | 处置 |
|---|---|---|
| I0(存储) | 扫描页 / 新的引擎丢失模式 | 看 missing_samples;扫描 → #220;新丢失模式 → 救援规则评估 |
| I1/I2(覆盖/空壳) | 新版式的标题怪癖 | 在 `tree_builder.py` 加/调修复规则,**逐条计数并写入 PR**(M4 纪律) |
| I5(垃圾标题) | 新噪音家族 | 扩 `_NOISE_RES`,配 fixture 测试 |
| I6(词表) | 新手册的章节叫法不在别名表 | `vocab.yaml` 加别名,`vocab_version` +1(别名不算规则) |
| I3(DTC) | 码有出现但无归属 | 检查 R2 正则是否覆盖该手册的码表述格式 |

**纪律**:规则改动必须带单测;改完全量重跑第 2、3 步;已发布的
其他手册索引不受词表扩展影响(版本化隔离)。

## 6. 历史经验速查

- 表格增强阶梯**休眠不删除**:`find_tables` 不够时按
  pdfplumber → camelot → Docling 逐级评估,触发条件见规范 §1.3。
- MinerU `--effort high` 在共享 GPU 上会内部超时,**永远用默认
  medium**。
- 页眉区是多碎片的(品牌/年份/编码/章节名混在一起),R4 只认
  "像章节名"的碎片——新语言若失效,先查 `_headerish`。
- 摘要语言偶发英文(CJK 手册):已知瑕疵,不拦截,规范 §10.4。

## 7. 每本手册的边际成本记录(M4 度量,持续追加)

| 手册 | 新增规则 | 泛化规则 | 词表 | 其他 |
|---|---|---|---|---|
| TRICITY155(首本,管线共建) | — | — | v1 | — |
| Corolla E11(第二本) | 1(R6 无头标题提升,兼修 TRICITY 遗留) | 1(R4 拉丁页眉) | v2(英文别名) | CLI 补 applicability 参数;大树 TOC 自适应降深 |

**Corolla 实测结果(2026-07-29)**:存储层首跑即 I0 全绿(23,810 行,缺失 0,零救援触发);索引 1,264 节点 / 186 章,unclassified 0,摘要 1,263 生成 + 1 回退,门禁 I1-I8 全绿,`publishable: true`;schema 与不变量**零修改**。
