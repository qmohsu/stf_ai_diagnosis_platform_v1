# Golden Q&A Set — Expert Review / 黃金問答集 — 專家審查

**Vehicle / 車輛**: Yamaha MWS-150-A (Tricity 155) service manual / 維修手冊
**Document version**: v0.2 (pilot, 2 entries — bilingual)
**Prepared / 準備日期**: 2026-05-05
**Authors / 作者**: Li-Ta Hsu et al.

---

## Purpose of this document

This document contains question-and-answer pairs we have authored as a **reference set** for evaluating an AI diagnostic assistant. Before we use these pairs to grade our system, we would like a domain expert to validate that each pair is reliable.

For every Q&A below, please grade three dimensions:

1. **Question realism** — would a working technician actually phrase the question this way? Is the level of detail appropriate?
2. **Answer correctness** — based on the cited source quotes from the manual, is the proposed answer accurate? Is anything missing or wrong?
3. **Citation faithfulness** — do the quoted source passages actually support the answer's claims? Or is the answer making a leap?

Each Q&A appears in **both Chinese and English**. Please grade based on whichever version you prefer — the two versions should say the same thing, and any discrepancy between them is itself a finding worth flagging.

At the end of each Q&A is a grading box. Please tick **one** of: **Accept** / **Needs revision** / **Reject**, and add free-text notes.

## 本文件之目的

本文件包含我們撰寫的問答對，作為評估 AI 診斷助手的**參考集**。在使用這些問答對評分前，我們希望由領域專家確認每組問答的可靠性。

針對下列每組問答，請從三個面向評分：

1. **問題擬真度** — 真實技師是否會這樣提問？問題的詳細程度是否恰當？
2. **答案正確性** — 根據手冊引用的原文，所提出的答案是否準確？有無遺漏或錯誤？
3. **引用的忠實度** — 引用的原文是否真的支持答案中的主張？或是答案做出了原文沒有支持的跳躍推論？

每組問答提供**中英文兩個版本**，請依您偏好的語言評分。兩個版本應表達相同的內容，若兩者不一致，本身即為值得指出的問題。

每組問答下方有評分欄。請勾選 **Accept**（採用）／ **Needs revision**（需修訂）／ **Reject**（拒絕）其中一項，並可在備註欄留下意見。

---

## Bucket: lookup (single-fact retrieval / 單一事實查詢)

### Q-lookup-1 (difficulty / 難度: easy)

**Question (中文)**:
> MWS-150-A 引擎在冷機狀態下，進氣門和排氣門的汽門間隙規格是多少？

**Question (English)**:
> What is the valve clearance specification for the intake and exhaust valves on a cold MWS-150-A engine?

**Proposed correct answer (中文)**:
> MWS-150-A 引擎在冷機狀態 (引擎冷機) 下，汽門間隙規格如下：
>
> - **進氣 (intake)**: 0.10–0.14 mm (0.0039–0.0055 in)
> - **排氣 (exhaust)**: 0.21–0.25 mm (0.0083–0.0098 in)
>
> 這些規格出現在引擎規格表中，是調整汽門間隙時所使用的數值。測量程序要求引擎處於冷機狀態，且活塞位於壓縮行程的上死點 (TDC)。

**Proposed correct answer (English)**:
> On a cold MWS-150-A engine (冷機狀態), the valve clearance specifications are:
> - **進氣 (intake)**: 0.10–0.14 mm (0.0039–0.0055 in)
> - **排氣 (exhaust)**: 0.21–0.25 mm (0.0083–0.0098 in)
>
> These specifications appear in the engine specifications table and are the values used when adjusting valve clearance — the measurement procedure requires the engine to be in cold state and the piston at top dead centre (TDC) of the compression stroke.

**Source quotes from the manual / 手冊原文引用**:

1. *Section / 章節: 冷卻系統*
   > 汽門間隙 (冷機狀態)

2. *Section / 章節: 冷卻系統*
   > 0.10–0.14 mm (0.0039–0.0055 in)

3. *Section / 章節: 冷卻系統*
   > 0.21–0.25 mm (0.0083–0.0098 in)

#### Expert grading / 專家評分

- [ ] **Accept** / **採用** — Q+A is reliable as written / 問答可靠，可直接採用
- [ ] **Needs revision** / **需修訂** — substantive concerns / 有具體疑慮（請說明）
- [ ] **Reject** / **拒絕** — fundamentally wrong / 根本錯誤（請說明）

**Expert notes / 專家備註**:
> _(your feedback here / 請填入您的意見)_

---

## Bucket: procedural (multi-step diagnostic flow / 多步驟診斷程序)

### Q-procedural-1 (difficulty / 難度: medium)

**Question (中文)**:
> MWS-150-A 出現故障代碼 P0117。這個代碼代表什麼意思？逐步診斷程序是什麼？

**Question (English)**:
> I'm getting DTC P0117 on the MWS-150-A. What does this code mean, and what is the step-by-step diagnostic procedure?

**Proposed correct answer (中文)**:
> MWS-150-A 上的故障代碼 P0117 表示 **冷卻液溫度感知器 (coolant temperature sensor)** 偵測到 **搭鐵短路 (ground short)**。同系列的 P0118 代碼則涵蓋同一感知器的開路與電源短路情形。
>
> 相關的診斷代碼為 06，所有檢查必須在 **引擎冷機 (engine cold)** 狀態下進行——程序依賴將工具顯示溫度與環境溫度進行比較。
>
> **7 個診斷步驟為**：
>
> 1. 檢查冷卻液溫度感知器接頭，確認固定狀況、各端子是否彎曲或破損；連接不當時，請牢固連接接頭或更換電線總成。
> 2. 以同樣方式檢查 ECU 接頭。
> 3. 拆離兩端接頭後，測試電線總成的開路與短路——P0117 須檢查冷卻液溫度感知器接頭與搭鐵之間 **綠色 / 紅色** 線的導通；如有導通，即表示搭鐵短路，必須更換電線總成。
> 4. 檢查感知器的安裝狀況——確認既不鬆動也不過緊，必要時重新安裝或更換。
> 5. 在引擎冷機狀態下執行診斷模式 06，確認顯示的溫度接近環境溫度；若不接近，請更換冷卻液溫度感知器。
> 6. 若上述步驟均無異狀，則 ECU 本身故障——請依第 8-101 頁更換。
> 7. 使用 **山葉診斷工具 (Yamaha diagnostic tool)** 清除故障代碼，並確認引擎警告燈熄滅。

**Proposed correct answer (English)**:
> DTC P0117 on the MWS-150-A indicates the **冷卻液溫度感知器 (coolant temperature sensor)** reported a **搭鐵短路 (ground short)**. Its sibling code P0118 covers open-circuit and power-short variants of the same sensor.
>
> The associated diagnostic code is 06, and all checks must be performed in **引擎冷機 (engine cold)** state — the procedure relies on comparing the tool's displayed temperature against ambient.
>
> **The 7-step diagnosis is**:
>
> 1. Inspect the coolant temperature sensor connector for proper seating, bent or broken pins; secure or replace the wire harness if a connection fault is found.
> 2. Inspect the ECU connector the same way.
> 3. Test the wire harness for opens and shorts with both connectors disconnected — for P0117 specifically, check continuity between the coolant temperature sensor connector and ground on the **綠色 / 紅色 (green/red)** wire; any continuity indicates a ground short and the harness must be replaced.
> 4. Check the sensor's installation — confirm it is neither loose nor over-tightened, reinstalling or replacing as needed.
> 5. Execute diagnostic mode 06 with the engine cold and confirm the displayed temperature is close to ambient air temperature; if it is not, replace the coolant temperature sensor.
> 6. If all earlier steps come back clean, the ECU itself is at fault — replace per page 8-101.
> 7. Erase the DTC using the **山葉診斷工具 (Yamaha diagnostic tool)** and verify the engine warning lamp extinguishes.

**Source quotes from the manual / 手冊原文引用**:

1. *Section / 章節: 故障代碼編號 P0117、P0118*
   > [P0117] 冷卻液溫度感知器：偵測到搭鐵短路。

2. *Section / 章節: 故障代碼編號 P0117、P0118*
   > 在引擎冷機狀態進行此程序。

3. *Section / 章節: 故障代碼編號 P0117、P0118*
   > [P0117] 搭鐵短路

4. *Section / 章節: 故障代碼編號 P0117、P0118*
   > 冷卻液溫度感知器接頭與搭鐵之間: 綠色/紅色－搭鐵

5. *Section / 章節: 故障代碼編號 P0117、P0118*
   > 更換 ECU。

#### Expert grading / 專家評分

- [ ] **Accept** / **採用** — Q+A is reliable as written / 問答可靠，可直接採用
- [ ] **Needs revision** / **需修訂** — substantive concerns / 有具體疑慮（請說明）
- [ ] **Reject** / **拒絕** — fundamentally wrong / 根本錯誤（請說明）

**Expert notes / 專家備註**:
> _(your feedback here / 請填入您的意見)_

---

## Buckets not yet authored / 尚未撰寫的桶別

The following buckets are planned but not yet drafted. Please ignore for this round; we will request review on them in a follow-up document once they are written.

下列桶別已規劃但尚未撰寫，本輪請忽略；待撰寫後將以後續文件再次請求審查。

- **cross-section** (questions requiring synthesis across ≥2 manual sections / 需綜合 2 個以上章節資訊的問題) — 0 of 6 drafted / 已撰寫 0/6
- **image-required** (questions whose correct answer depends on a wiring diagram or figure / 答案依賴接線圖或圖示的問題) — 0 of 4 drafted / 已撰寫 0/4
- **adversarial** (questions the manual cannot answer; the system should refuse / 手冊無法回答、系統應拒絕作答的問題) — 0 of 4 drafted / 已撰寫 0/4

---

## Overall feedback / 整體意見

After reviewing the entries above, do you have general comments about:

- The question style — too leading, too vague, about the right level of specificity?
- The answer style — too verbose, too terse, missing context a technician would expect?
- The citation format — useful, sufficient, or do you need more (page numbers, figure references)?
- The bilingual presentation — preferred direction (Chinese first vs English first), or any translation issues?
- Anything else worth flagging before we author the remaining ~28 entries?

審閱上述問答後，您對下列項目是否有整體意見？

- 問題風格——過於引導、過於模糊、或詳細程度恰當？
- 答案風格——過於冗長、過於簡略、或缺少技師會期望的上下文？
- 引用格式——是否有用、足夠，或需要更多資訊（頁碼、圖示編號）？
- 雙語呈現方式——偏好中文先或英文先，或翻譯有無問題？
- 在我們撰寫其餘約 28 組問答之前，是否還有其他值得提出的事項？

> _(your feedback here / 請填入您的意見)_

---

**Thank you for your time. / 感謝您撥冗審閱。**
