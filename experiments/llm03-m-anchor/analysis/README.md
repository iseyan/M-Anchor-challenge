# 初回試験の分析スクリプト（分析計画 v1）

`../analysis-plan-v1.md` を実行するスクリプトです。計画をmainにマージした後に、本番run `main-20261118-01` に当てます。

## 実行順

`experiments/llm03-m-anchor/` で実行します。出力先 `analysis/out-main-20261118-01/` は新しいフォルダにします。

```sh
python -I analysis/prep.py main-runs/main-20261118-01 analysis/out-main-20261118-01
Rscript analysis/glmm.R analysis/out-main-20261118-01
python -I analysis/boot_desc.py main-runs/main-20261118-01 analysis/out-main-20261118-01
```

| スクリプト | 計画の章 | 出力 |
| --- | --- | --- |
| `prep.py` | 2 | 入力のSHA-256を計画の値と照合し（違えば停止）、凍結した採点コード（v0.2.3）で全エピソードの指標を計算し直して保存値と比べる（違えば停止）。`episodes.csv`、`prep_info.json` |
| `glmm.R` | 3、4、6.5、6.6 | GLMM（`lme4::glmer`、ラプラス近似、bobyqa）。2モデル平均の対比、Holm補正、推定がうまくいかない場合の手順（3.3）、感度分析、送り手別の対比、交互作用の尤度比検定、分散成分とプロファイル信頼区間。`glmm_results.json` |
| `boot_desc.py` | 3.4、4、5、6 | ケース単位のクラスター・ブートストラップ（B = 10,000、シード 20261119）による率と率の差、H5の非劣性、未送信の内訳、補助列、H1〜H3の記述。`boot_desc_results.json` |

## 実行に必要なもの

- Python 3.10以上、numpy、scipy
- R と lme4、jsonlite。作成時の動作確認は R 4.3.3、lme4 1.1-35.1、Matrix 1.6-5（Ubuntu 24.04 のパッケージ `r-base-core`、`r-cran-lme4`）。実際に使った版は `glmm_results.json` に記録されます。

## 計画に書いていない実行上の決まり

- 送り手別の対比（探索的）で、その送り手の比べる2条件のどちらかの値がすべて0またはすべて1のときは、ワルド推定の代わりに、その送り手の中で同じ正確検定（3.3の手順3）を使う。主検定の手順は計画どおり。
- 分散成分のプロファイル信頼区間が計算できない場合（推定値が境界にあるときなど）は、計算できなかった理由をそのまま `glmm_results.json` に記録する。
- 率はすべて、予定したエピソード全体を分母にする（計画4章）。

## 動作確認

開発用の動作確認の記録（`operation-checks/dev-smoke-20261118-03/`）で、3つのスクリプトが最後まで動くことを確かめた（`prep.py` は `--no-hash-check` を付けて実行。付けないと照合で正しく停止する）。手順3（完全分離での正確検定）、ブートストラップ、H1・H3の集計の経路も、この記録で動いた。開発用の結果は保存していない。本番runにはまだ当てていない。
