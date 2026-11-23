# 第II報への追記案：実験4の要点と参照先

2026-11-23 · 草案作成：Claude。**第II報（実験2・3の統合改訂稿）に加える文面の案である。** 第II報の本文は ChatGPT 側で管理されているので、ここには挿入する文面だけを置く。英語正本と日本語副本の両方に入れる。

## 入れる場所

- **§7 結論の後、付記の前**に、新しい節「8. 実験4への接続」として入れる。
- **「関連文書」**に、実験4の報告への参照を1行加える。
- §1〜§7 の本文と数値は変えない。

## 日本語副本に入れる文面

> ## 8. 実験4への接続
>
> 本報の後、同じ問いを、英語の新しい固定140ケースと別の送り手（`gpt-oss:20b`）で測った。条件は、通常の注意指示、M-Anchor の認識状態ブロック、入力フィルター（Llama Prompt Guard 2）の三つで、すべて共通の決定ゲートの下に置いた。結果は独立した報告にまとめた。
>
> 要点は四つである。第一に、認識状態ブロックは、未決の原因を確定として引き継いだ試行を 143/360 から 12/360 に減らした。減少は、確定に使える種類の資料が見えていない層（129/300 → 0/300）に集中し、見えている層（14/60 → 12/60）では小さかった。本報の実験2と同じ形である。第二に、同じ条件で、承認済みの支払いが台帳に入らなかった試行は 51/360 から 145/360 に増えた。事前に定めた両立は 99/300 から 142/300 に増えたが、この幅は、引き継ぎの「否定」をどう採点するかに依存する。第三に、差が生じた段階が分かった。初期評価は両条件で同じで、誤伝播の減少は、モデルの外に置いた状態ブロックの規則から来ていた。支払いの減少の多くは、モデルが有効な承認を有効と認めなかった試行で、ブロックがその判断を固定したところで生じた。第四に、入力フィルターは、この試験の140件の非信頼資料を1件も検出しなかった。
>
> 実験4には、実行の後に分かった不備がある。モデルに現在の日付を渡していなかった。支払いの減少は、この不備と切り離して読めない。実験4の対象は、1つの送り手、英語、模擬業務、新しく書いた140ケースである。数値を本報の実験2・3と直接つないで比べることはしない。
>
> [実験4報告](https://github.com/iseyan/M-Anchor-challenge/blob/exp4-plan-draft/experiments/llm03-m-anchor/exp4-benchmark/report/report-exp4.ja.md)

「関連文書」に加える行：

> - [実験4報告（耐性ベンチマーク）](https://github.com/iseyan/M-Anchor-challenge/blob/exp4-plan-draft/experiments/llm03-m-anchor/exp4-benchmark/report/report-exp4.ja.md) · [英語版](https://github.com/iseyan/M-Anchor-challenge/blob/exp4-plan-draft/experiments/llm03-m-anchor/exp4-benchmark/report/report-exp4.en.md) · [記録と集計](https://github.com/iseyan/M-Anchor-challenge/pull/17)

## 英語正本に入れる文面

> ## 8. Link to Experiment 4
>
> After this report, the same question was measured on 140 new fixed English cases with a different sender (`gpt-oss:20b`). Three conditions were placed under the common decision gate: ordinary caution, the M-Anchor epistemic-state block, and an input filter (Llama Prompt Guard 2). The results are given in a separate report.
>
> Four points stand out. First, the epistemic-state block reduced the trials that handed an unresolved cause downstream as confirmed from 143/360 to 12/360. The reduction was concentrated where no document of an admissible kind was visible (129/300 → 0/300) and was small where one was visible (14/60 → 12/60), the same pattern as in Experiment 2 of this report. Second, under the same condition the trials in which an approved payment did not reach the ledger rose from 51/360 to 145/360. Joint success as defined in advance rose from 99/300 to 142/300, but the size of that change depends on how a handoff of "refuted" is scored. Third, the step at which the difference arises could be identified. The first assessment was the same under both conditions; the drop in false confirmation came from the rule of the state block, placed outside the model. Most of the drop in payments arose where the model did not accept a valid approval as valid and the block fixed that judgment. Fourth, the input filter detected none of the 140 untrusted documents in that study.
>
> Experiment 4 has a flaw found after the run: the model was not given the current date. The drop in payments cannot be read apart from it. Experiment 4 covers one sender, English, a simulated workflow and 140 newly written cases. Its numbers are not joined to those of Experiments 2 and 3 in this report for direct comparison.
>
> [Report on Experiment 4](https://github.com/iseyan/M-Anchor-challenge/blob/exp4-plan-draft/experiments/llm03-m-anchor/exp4-benchmark/report/report-exp4.en.md)

Line to add under "Related documents":

> - [Report on Experiment 4 (robustness benchmark)](https://github.com/iseyan/M-Anchor-challenge/blob/exp4-plan-draft/experiments/llm03-m-anchor/exp4-benchmark/report/report-exp4.en.md) · [Japanese edition](https://github.com/iseyan/M-Anchor-challenge/blob/exp4-plan-draft/experiments/llm03-m-anchor/exp4-benchmark/report/report-exp4.ja.md) · [Records and tabulation](https://github.com/iseyan/M-Anchor-challenge/pull/17)

## 注意

- 用語は第II報に合わせた（日本語は「誤伝播」「両立」、英語は "joint success"）。実験4の報告では、同じ指標を「同時成功」と呼んでいる。定義は同じである。
- リンクは、いまはブランチ `exp4-plan-draft` を指している。PR #17 をマージした後は、マージ先のコミットを指すように直す必要がある。
- 実験4の報告は公開前の草稿である。公開の判断は著者が行う。
