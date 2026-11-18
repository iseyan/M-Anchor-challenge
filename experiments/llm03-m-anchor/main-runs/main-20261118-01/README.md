# 本番run main-20261118-01（本番40ケース、初回試験の本集計）

- ハーネス：v0.2.3、コミット `cd7b996`（仕様凍結の記録 `../../spec-freeze-20261118.md`）
- 送信経路：V1と同じブラウザ経路（Playwright で `http://localhost:11434` を開き、ページ内から `fetch /api/chat`）。ケース・設定・シード・順序・num_predict=300 は凍結どおり
- 対象：本番40ケース（攻撃25・正当15）× 送り手2種 × 3反復 = 240試行、条件別エピソード960（攻撃600・正当360）
- バンドル：`bundle_main-20261118-01.json`（SHA-256 `572e2f7a…85a7`、payload `34910115…86ac`）。PC側に置いたファイルと、ブラウザで読み込んだバイト列のSHA-256がどちらも一致することを確認した。バンドル本体はここに置かない（`bundle_info.json` から再生成できる）
- 実行：2026-11-18 14:10:59Z〜14:56:47Z（約45.8分）
- PC側のstate：`C:\Users\ise\AppData\Local\Temp\.playwright-mcp\llm03\state_main-20261118-01.json`（SHA-256 `9606cfcd…a48a`）。`state_raw.json` は取り込み時に書き直したもの
- 照合：2,069件すべてでSHA-256が一致。重複・計画外・欠落なし、V1順、環境一致（Ollama 0.40.16、両モデルのdigest一致）。fatal なし、NOT_RUN 0

## ファイル

| ファイル | 内容 |
| --- | --- |
| `manifest.json` | split・送信経路・ハーネスの全ファイルのSHA-256 |
| `records.json` | 全呼び出しの記録（2,069件） |
| `episodes.jsonl` | 条件別エピソード（960件）とゲート処理・採点の結果 |
| `summary.json` | 送り手・条件・ラベル別の集計（記述統計） |
| `operation_report.json` | 段階別・モデル別の健全性 |
| `verification.json` | リクエストの再構成と照合の結果 |
| `breakdown.json` | `harness/tools/opcheck_breakdown.py` による第2段階の分解（選別用の数で、採点定義ではない） |

信頼区間と GLMM は分析の段階で別に行う。ここには記述統計だけを置く。
