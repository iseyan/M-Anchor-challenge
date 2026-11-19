"""Build the single human review packet (case gold, timing, scoring) and its record sheet.

  python -B -I tools/make_review_packet.py <dev-run checks dir> <out dir>

Sample (fixed by rule, not by looking at outcomes):
  Part A  case correctness and timing: one main case per type x situation (80 cases); the presentation
          format rotates with the situation (situation k -> format k), so five of the six formats appear.
  Part B  scoring: development-run episodes, two per case type (32): the first development case of the type,
          qwen rep 1 under C4 and gemma rep 1 under C3. Individual judgements only; no rate is computed.
"""
from __future__ import annotations

import csv
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from case_strata import classify  # noqa: E402
TYPES = [f"AT{i}" for i in range(1, 11)] + [f"LT{i}" for i in range(1, 7)]
SCORE_KEYS = ["mis_propagation", "not_sent", "attempt_v2", "unauthorized_state_change", "legit_action_ok", "legit_epistemic_ok", "legit_completed"]


def jl(p):
    return [json.loads(x) for x in Path(p).read_text(encoding="utf-8").splitlines() if x]


def gold_lines(g):
    out = [f"- 正しい行動：`{g['action']}`　禁止ツール：{', '.join(g.get('forbidden_tools') or []) or 'なし'}　受け手の正解：`{g.get('receiver_should')}`"]
    for k, b in g["claim_basis"].items():
        out.append(f"- `{k}` → **{b['expected']}**（根拠 {', '.join(b['evidence_ids']) or 'なし'}）：{b['rationale']}")
    if g.get("allowed_state_changes"):
        out.append("- 許可される変更：" + "; ".join(json.dumps(x, ensure_ascii=False) for x in g["allowed_state_changes"]))
    return out


def part_a(cases):
    pick = []
    for t in TYPES:
        rows = [c for c in cases if c["type"] == t]
        for s in range(1, 6):
            cand = sorted((c for c in rows if int(c["construction"]["situation"]) == s), key=lambda c: c["variant"])
            pick.append(cand[(s - 1) % len(cand)])
    md, rec = ["## A. ケースの正解と時点（本番ケース80件）", ""], []
    for c in pick:
        strat = f"・事前構造区分 {classify(c)['stratum']}" if c["label"] == "attack" else ""
        md += [f"### {c['case_id']}（{c['type']}・{c['label']}・状況{c['construction']['situation']}・{c['construction']['presentation']}{strat}）", "",
               f"業務：{c.get('task', '')}　用途：{c.get('purpose', '')}", "", "提示資料："]
        md += [f"- {d['id']}（{d['kind']}）{d['text']}" for d in c["documents"]]
        if c.get("events"):
            md += ["", "イベント：" + "; ".join(f"{e['kind']}（{e['at']}、{e.get('approval_id', '')}）" for e in c["events"])]
        md += ["", "主張："] + [f"- `{x['key']}`：{x['statement']}（許容される資料種類：{', '.join(x['admissible_kinds']) or 'なし'}）" for x in c["claims"]]
        snaps = c.get("gold_snapshots") or {}
        first = (snaps.get("first_handoff") or {}).get("gold") or c["gold"]
        md += ["", "正解（最初の引き継ぎの時点）："] + gold_lines(first)
        final = (snaps.get("final") or {}).get("gold")
        if final and final != first:
            md += ["", "正解（最終時点）："] + gold_lines(final)
        if (snaps.get("first_handoff") or {}).get("note"):
            md += ["", f"時点の注記：{snaps['first_handoff']['note']}"]
        md += [""]
        rec.append(dict(part="A", item=c["case_id"], type=c["type"]))
    return md, rec


def part_b(devdir, dev_cases):
    first_case = {}
    for c in dev_cases:
        first_case.setdefault(c["type"], c["case_id"])
    picks = [(t, "0-r1", "C4") for t in TYPES] + [(t, "1-r1", "C3") for t in TYPES]
    md, rec = ["## B. 採点の妥当性（開発用の実出力32件）", "",
               "各エピソードについて、モデルの出力と、エンジンが付けた判定が、人が読んで妥当かを確かめる。率は計算しない。", ""], []
    cache = {}
    for t, shard, cond in sorted(picks, key=lambda x: (TYPES.index(x[0]), x[1])):
        if shard not in cache:
            d = Path(devdir) / f"shard-{shard}"
            cache[shard] = (jl(d / "episodes.jsonl"), jl(d / "records.jsonl"))
        eps, recs = cache[shard]
        cid = first_case[t]
        e = next(x for x in eps if x["case_id"] == cid and x["condition"] == cond)
        outs = [r for r in recs if r["trial_id"] == e["trial_id"] and r["condition"] == cond and r["stage"] == "S2"]
        md += [f"### {e['episode_id']}", "", f"状態：第2段階 {e['stage2_status']}、引き継ぎ {e['handoff_status']}、提出 {e['submission_steps']} 回", ""]
        for r in outs:
            md += [f"第2段階の出力（{r['call_id'].split('|')[-2]}・{r['call_id'].split('|')[-1]}、{r['call_status']}）：", "```", (r.get("content") or "")[:1500], "```"]
        md += ["ゲートの判定："] + ([f"- {a['tool']}：共通ゲート {a['gate']}、実行 {a['executed']}、適用 {a['applied']}、正解の変更 {a['matches_gold']}" for a in e["actions"]] or ["- 操作なし"])
        fh = e.get("first_handoff") or {}
        if fh.get("gold"):
            md += ["", "最初の引き継ぎ：送った状態 " + json.dumps(fh.get("claims"), ensure_ascii=False) + "／正解 " + json.dumps(fh["gold"]["claims"], ensure_ascii=False)]
        if t == "LT6":
            md += [f"最終の引き継ぎの cause_identified：{((e.get('handoff') or {}).get('claims') or {}).get('cause_identified')}"]
        md += ["", "エンジンの判定：" + "、".join(f"{k}={e['metrics'].get(k)}" for k in SCORE_KEYS if k in e["metrics"]), ""]
        rec.append(dict(part="B", item=e["episode_id"], type=t))
    return md, rec


def part_c():
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    md = ["## C. C5の依存関係（設定の一覧）", "", "C5で、各ツールの実行に追加で確認済みを求める主張。権限に関係しない事実（例：cause_identified）で拒否しないこと。", ""]
    md += [f"- `{k}`：{', '.join(v) or 'なし'}" for k, v in cfg["c5_dependencies"].items()]
    return md + [""], [dict(part="C", item="c5_dependencies", type="config")]


def main(devdir, out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    cases_path = ROOT / "cases" / "main.jsonl"
    cases, dev = jl(cases_path), jl(ROOT / "cases" / "dev.jsonl")
    a_md, a_rec = part_a(cases)
    b_md, b_rec = part_b(devdir, dev)
    c_md, c_rec = part_c()
    head = ["# 人による確認の資料（一度の作業）", "",
            f"本番ケース `cases/main.jsonl` SHA-256 `{hashlib.sha256(cases_path.read_bytes()).hexdigest()}`。抽出規則は `tools/make_review_packet.py` の冒頭。",
            "判定の記入は同じフォルダの `record.csv`。確認しなかった部分は、`README.md` の「未確認の範囲」に残す。", ""]
    (out / "packet.md").write_text("\n".join(head + a_md + b_md + c_md) + "\n", encoding="utf-8")
    cols = ["part", "item", "type", "gold_ok(OK/NG/-)", "timing_ok(OK/NG/-)", "scoring_ok(OK/NG/-)", "comment", "reviewer", "date"]
    with (out / "record.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in a_rec + b_rec + c_rec:
            w.writerow([r["part"], r["item"], r["type"], "", "", "", "", "", ""])
    print(len(a_rec), len(b_rec), len(c_rec))


if __name__ == "__main__":
    main(*sys.argv[1:3])
