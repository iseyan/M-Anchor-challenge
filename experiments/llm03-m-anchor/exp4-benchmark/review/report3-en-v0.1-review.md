# 査読報告：The AI Interface Problem III（英語版、公開前草稿 v0.1）

2026-11-23 · 査読：Claude。**AI による査読である。人による査読ではない。** 対象は、伊瀬が添付した英語版（2026-11-23 15:08 の添付。14:39 の添付と同じ内容）。査読者は、この実験のケース・実行コード・集計の作成にも関わっている。独立した査読ではない。

## 総合の判断

**数値と記述は記録と一致しており、主張は記録の範囲に収まっている。公開の前に直すべき点が3つ、直したほうがよい点が7つある。** 結果を変える指摘はない。

- 記録と照合した数値：要旨、§3、§4、§5、§7、付記のすべて。食い違いは0件。
- 最も重要な指摘は M1 である。草稿は「基準日を渡さなかった影響は未測定」とするが、保存された推論の文には、その理由がほぼ全件で書かれている。

## 直すべき点

### M1. §4.2・要旨・§5・§6：承認を有効と認めなかった理由は、記録にほぼ全件で書かれている

草稿は、説明文（note）のうち `current date` を含むものが55件、と書いている。記録にはモデルの推論の文（`thinking`）も保存されていて、そこまで見ると次のようになる（支払いが期待される360試行）。

| 初期評価での承認の状態 | 試行数 | 推論の文が current date に触れている | 推論の文に「2026」がある |
| --- | --- | --- | --- |
| confirmed | 235 | 178 | 8 |
| unconfirmed | 95 | 95 | 3 |
| refuted | 30 | 29 | 30 |

- 承認を unconfirmed・refuted とした125試行のうち、124試行で、推論の文が現在の日付に触れている。
- unconfirmed の95試行は、すべて「現在の日付が与えられていない」という趣旨である。
- refuted の30試行は、すべて「2026」を含む。今日を実行日（2026-11-23）と仮定し、2027年2月1日発行の承認を「まだ有効でない」としている。例（`S06-V2|r0`）：`assume today 2026-11-23 earlier than issuance … So not valid yet. So refuted.`
- 送信した要求（システム指示と依頼文）に、日付は入っていない。モデルがその日付をどこから得たかは、確かめていない。

**直し方。** §4.2 にこの表と上の4点を加える。不備の書き方を、「基準日を渡さなかった」から、「基準日を渡さず、かつケースの日付（2027年）が、モデルが仮定した今日より後だった」に改める。要旨と §6 の「未測定」は、「因果としては未測定だが、モデルの推論の記録は、125試行のうち124試行でこの点を理由に挙げている」とする。推論の文はモデルが書いた理由であり、原因の証明ではない、という但し書きを付ける。

挿入文の案（§4.2、表の後）：

> The saved reasoning text points the same way. In 124 of the 125 trials in which the first assessment marked a valid approval "unconfirmed" or "refuted", the model's reasoning refers to the current date. All 95 "unconfirmed" trials say in effect that the current date was not given. All 30 "refuted" trials mention 2026: the model took the run date, 23 November 2026, as today and treated an approval issued on 1 February 2027 as not yet valid. The requests we sent contain no date, and we did not establish where the model obtained one. Reasoning text is the model's own account, not proof of cause.

### M2. §3.2・§5：131件の減少が「規則の性質」であることを、主結果の近くに書く

状態ブロックの規則は、初期評価が何と言っても、確定に使える種類の資料が引用されていなければ unconfirmed を返す。確定に使える種類の資料がない層（300試行）では、この規則が返す値は、ケースが与える資料の種類だけで決まる。したがって 129/300 → 0/300 のうち、観測として意味があるのは次の2点である。

1. 初期評価が、参照状態が未決の360試行のうち143試行で confirmed とした（モデルの振る舞い）。
2. 提出の段階で、モデルがブロックの状態を360試行すべてでそのまま引き継いだ（モデルの振る舞い）。

残りは、規則と、ケースが信頼できる情報として与える資料の種類から、ほぼ決まる。草稿は §6 の Scope でこの前提に触れているが、主結果とつながっていない。読者が「モデルの判断が良くなった」と読まないよう、§3.2 の末尾か §5 の第1段落に一文を入れる。

挿入文の案：

> Where no document of an admissible kind exists, the value the rule returns is fixed by the document kinds the case supplies, whatever the first assessment says. What the run adds is that the first assessment said "confirmed" in 143 trials and that the model carried the block's state into its handoff in all 360.

### M3. §3.3・§5・§7：残った12件は、基本状況1つである。一般化の言い方を弱める

- 12件は S21 の4形式 × 3反復で、独立した12例ではない。A の誤伝播は30の基本状況のうち22に広がっているが、M の誤伝播は1つの基本状況だけにある。
- §5 の "This residual case shows why … need separate checks" は、1つの基本状況からの言い方としては強い。"illustrates" とし、"one base situation" と明記する。
- S21 の資料について、草稿は「申請に請求書番号も対象月もなく、対応を確認できない」と書く。これは正しい。あわせて、報告書の側には `for the previous month`・`on that invoice` と書かれていることも示すと、読者が資料から何を読み取れたかを判断できる。

## 直したほうがよい点

| # | 箇所 | 指摘 | 直し方 |
| --- | --- | --- | --- |
| m1 | 全体 | 同じ指標に3つの呼び名がある：`Approved payment not reaching the ledger`（表）、`Approved-payment noncompletion`（§2・§7）、`missed approved payments`（題名・§5） | 1つに揃える。題名に合わせるなら `missed approved payment` |
| m2 | §2 | シードの扱いが書かれていない。同じ試行の初期評価と両条件の提出は同じシードを使っている。対応のある比較の前提である | Size の行か本文に一文を加える |
| m3 | §3.5 | "scored all 140 untrusted documents" とあるが、標準順とメモ先頭の2形式は同じ文面なので、異なる文面は105である | "140 documents (105 distinct texts)" とする |
| m4 | §3.5 | "A separate check at setup" は、試験の資料ではなく、典型的な注入の一文である | "a typical injection sentence, not a study document" を補う |
| m5 | §5 第4段落 | 著者の判断が、確認の途中で両方の向きに動いたことが、本文から読み取れない（確認記録への参照だけ） | "after moving between two positions during the review" を補う。事前の採点がその動きに左右されていないことも一言 |
| m6 | 参考文献2 | 第II報の題名が *Preserving Epistemic State Without Blocking Authorized Action* になっている。査読者が受け取った第II報の統合改訂稿の副題は *Can Authorized Action Proceed While the Cause Remains Unresolved?* だった | 現行の題名を確かめる（査読者は確かめられない） |
| m7 | 資料のリンク | すべて `4c5b308` を指している。その後、確認記録と結果の記述に追記がある（`ae8c4a0`：推論の文の所見） | M1 を入れるなら、固定するコミットを更新する |

## 確かめられなかった点

- §1 の第I報の記述（実験1が四つを別々に扱った）。第I報は査読者の手元にない。
- 日本語版との逐語の対応。数値と主張の食い違いは見つからなかったが、全文の対訳としては確認していない。

## 草稿が正確にしている点（維持する）

- S21 を「別の請求」と断定せず、「対応を資料から確認できない」とした。
- §4.2 で、「ブロックは原因・承認・承認者の権限を一緒に渡すので、承認状態を渡すことだけの効果は切り分けられない」とした。
- E4 で、事後の集計では条件の順序が逆転すること（A 46/60、M 30/60）を明記した。
- G の列が独立した測定でないことを、表の注と §3.5 の両方に書いた。
- 言語・送り手・ケースが同時に変わっているので、個別の影響は測っていない、と §1 に書いた。

## 照合の一覧（食い違い0件）

| 草稿の記述 | 記録 |
| --- | --- |
| 143/360、12/360、99/300、142/300、51/360、145/360、0/420 | 一致 |
| 移り変わり（131・0・12、32・75・67、8・102・43） | 一致 |
| 層別 129/300 → 0/300、14/60 → 12/60。実験2の 795/1,152 → 26/1,152、402/648 → 390/648 | 一致 |
| S21 で A・M とも 12/12、S22 で M 0/12・A 2/12、内容が不十分な3状況で0件、E7 の同時成功 12/60・3/60、E3 で 60/60 | 一致 |
| 支払い未達の内訳 45・139、6・6。操作の件数 337 → 237、79 → 100、6 → 31。120試行での 99・69 | 一致 |
| 検出0件、最大スコア 0.0018、導入時 0.9994、G の追加の呼び出し0 | 一致 |
| E5 の 22/60・16/60、権限外の操作の提案 22・5 | 一致 |
| §4.1 の 155/300・161/300、E4 の 43・17、16/60・30/60、46/60・30/60 | 一致 |
| §4.2 の表（235・95・30、227・76・6、207・8・0）、55件、360/360 の一致、74と20、S28 の 12/22・11/16 | 一致 |
| 呼び出し1,260回の内訳、失敗・再試行・打ち切りなし | 一致 |
| 参照コミット（`4c5b308`、`e2c0d96`、`e2aeb9f`）と S21 の行番号 | 存在し、内容も合う |
