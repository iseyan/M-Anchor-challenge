# 凍結記録（実験2・拡張本試験、2026-11-19 21:45 の利用者の判断）

利用者（伊瀬）は 2026-11-19 21:45 に「今すぐ凍結して本番開始」を選んだ。このファイルを含むコミットが凍結のコミットである。これより後に、本番の採点・プロンプト・ケース・送信経路・分析計画を変えない。

## 固定した内容

| 項目 | 値 |
| --- | --- |
| 分析計画 | `analysis-plan-final.md`（記述的な比較。H4の検定・TOST・H5・区間は行わない。同等性と非劣性は未判定）SHA-256 `3a4ae88fe0331f8f28b955746a9420ed4d52b2ecb6292fe49e0eabfba9418f74` |
| 記述報告のコード | `analysis/report_final.py` SHA-256 `704ad9c95428ee516cd80662413148bcaa7f47521a07fbf927e8b0e921ecf3a3` |
| 本番ケース | `cases/main.jsonl`（480件：攻撃300・正当180）SHA-256 `9e9a9880b88b01239bdabeae4af06a0f8f266cd21bb8d34cfb49e01be2b1d017` |
| 送信・採点の全ソース | `support.sources()` 77ファイル、一覧のSHA-256（キー順のJSON）`623ca2e76fb95c96ec0b10682c6cf7e3c48351b854e91221451c64040b7a048a`。各分割の `manifest.json` に全ファイルのハッシュが残る |
| 設定 | `config.json`（`run_enabled: true`、`harness_version: full-study-1.0`）SHA-256 `ce7d08ebdd5df9cdf3de3f6d7628609b5faae4acaf54ef7d35ab0145e4262a7c` |
| 送り手 | qwen3:4b-q4_K_M（ダイジェスト `2bfd38a7daaf4b1037efe517ccb73d1a3bbd4822cf89f1a82be1569050a114e0`）、gemma3:4b（`68ceab7e468a31c988b44d5a4984a0e21f58c4f7158c6ce29c1804babac38df7`）。受け手は gemma3:4b |
| Ollama | 0.40.16（各分割の開始時に照合し、不一致なら推論の前に止まる） |
| 規模 | 2送り手 × 3反復 × 480ケース × 9条件 = 25,920エピソード、6分割 |
| 推論設定 | 凍結済みの設定のまま（num_ctx 4096、num_predict 300 など） |
| A2 | 「主張キー＋整理行」で確定。gemma では A2 が C4 より中央値約5トークン短い（`checks/dev-tech-20261119-02/`）。報告に併記する |

開発用の確認v2（`ed8c2ec`）からの違いは、`config.json`（凍結の欄）、テスト、`analysis-plan-final.md`、`analysis/report_final.py`、`tools/make_review_packet.py` だけで、エンジン・条件・採点・送信部・ページのループは同じである。

## 凍結時点で済んでいない確認（そのまま記録する）

- **人による確認（`checks/human-review-20261119/`）は、凍結の時点で実施していない。** 利用者の判断で、確認を待たずに凍結した。ケースの意味的な正解、事前構造区分、C5の依存関係、動的イベントの時点、実出力での採点の妥当性、迂回と完全仲介について、人による確認はない。自動の整合確認とAIによる読み取りは行ったが、人の確認の代わりにはしない。
- 確認を本番の後に行う場合も、凍結したケースや採点は変えない。不備が見つかれば、報告に限界として記す。

## 本番の実行

手順は `checks/freeze-and-main-run.md`。実行フォルダは `fullstudy-main-20261119-01`。
