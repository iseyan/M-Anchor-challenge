# 動作確認 dev-smoke-20261118-01（開発用8ケース、本集計に含めない）

- ハーネス：v0.2、コミット `c0671fd`（`manifest.json` に全ファイルのSHA-256）
- 送信経路：V1と同じブラウザ経路（Playwright、`http://localhost:11434` のページから `fetch /api/chat`）
- 対象：開発用8ケース × 送り手2種 × 3反復 = 48試行、num_predict=300
- 実行：2026-11-18 12:44:59Z〜12:55:36Z（636.7秒）
- 照合：405件すべてで、Pythonで作り直したリクエストとSHA-256が一致。重複・計画外・欠落なし、送信順はV1順。Ollama 0.40.16 と全モデルのdigestが一致

| ファイル | 内容 |
| --- | --- |
| `state_raw.json` | ブラウザの `window.__llm03` をそのまま保存したもの |
| `records.json` | 呼び出しごとの記録（405件） |
| `verification.json` | 照合結果と環境の照合 |
| `operation_report.json` | 段階別・モデル別の切断・形式誤り・タイムアウト・所要時間 |
| `episodes.jsonl`, `summary.json` | ゲート処理と採点の結果。開発用の配線確認であり、条件間の比較には使わない |
