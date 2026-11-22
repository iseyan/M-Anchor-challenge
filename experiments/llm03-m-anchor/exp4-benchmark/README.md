# 実験4（耐性ベンチマーク）— 凍結前の作業フォルダ

**凍結前である。** 計画書は `plan-draft-v0.4.md`。文面・ケース・設定はまだ固定していない。開発用ケースの結果は、件数に数えない。

| パス | 内容 |
| --- | --- |
| `plan-draft-v0.4.md` | 計画書（設計レビューは v0.4 で閉じた） |
| `cases/base-situations.{json,md}` | 基本状況（本試験35、開発用7） |
| `cases/dev-cases.jsonl` | 開発用14ケース |
| `cases/validate_cases.py` | ケースの検査 |
| `prompts/` | 英語の依頼文・注意指示・状態ブロックの見出し・通知文 |
| `harness/run.py` | 実行部分（呼び出し、記録、再開、採点、集計） |
| `harness/gate.py`・`score.py`・`anchor.py` | ゲートと模擬台帳、採点、状態ストア |
| `harness/prompt_guard.py` | 入力フィルター（条件 G）。**作成者は未実行** |
| `tests/` | 自動テスト（モデルの代役を使う。実モデルは呼ばない） |

## 開発用14ケースの通し運転（PC で実行）

見るのは3つ：**所要時間、出力形式の失敗数、初期評価の出方。**

```
cd experiments\llm03-m-anchor\exp4-benchmark
py -3 -m unittest discover -s tests
py -3 harness\run.py --cases cases\dev-cases.jsonl --out runs\dev-20261122-01 --reps 1 --conditions A,M --expect-digest f38aa0c5
```

- Ollama は `ollama serve` で起動しておく。既定の接続先は `http://127.0.0.1:11434`。別のポートなら `--host http://127.0.0.1:ポート` を付ける。
- 呼び出しは 14（初期評価）＋ 28（提出）＝ 42 回。
- 途中で止まったら、**同じコマンドをもう一度実行する**。保存済みの呼び出しは送り直さない。
- 設定やコードを変えたら、新しい `--out` フォルダを使う（同じフォルダでは始まらない）。
- 終わると、条件ごとの件数、形式の失敗、初期評価の状態、E7 の件数、時間（読み込みと推論を分けたもの、1,680回に換算した時間）が表示される。

**返してほしいもの：** `runs\dev-20261122-01\` の `summary.json`、`episodes.jsonl`、`calls.jsonl`、`run-info.json`。

条件 G（Prompt Guard 2）は、重みを入れた後に `--conditions A,M,G --prompt-guard 重みのフォルダ` で実行する。

## 記録されるもの

- `calls.jsonl`：呼び出しごとに、送信した要求本文、その SHA-256、応答の全体、終了理由、時間、失敗した試行。
- `episodes.jsonl`：試行 × 条件ごとに、初期評価の状態、状態ストアが渡した状態、最終の引き継ぎ、台帳にもとづく採点。E7 の「見えていた／引用された／規則を通過した」は、最終出力と独立に数える。
- `summary.json`：上の表示と同じ集計。
