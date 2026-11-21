"""監査報告書と改訂稿の表を、再集計の出力から作って本文に差し込む。
  python3 -B -I build_docs.py recount.json explore.json compare.json OUT_DIR
本文は docs/*.tmpl.md。{{名前}} を表に置き換える。表の数値は手で写さない。
"""
import json, re, sys
from pathlib import Path

n = json.load(open(sys.argv[1])); x = json.load(open(sys.argv[2])); cmp_ = json.load(open(sys.argv[3]))
out = Path(sys.argv[4]); here = Path(__file__).resolve().parent
C = ["C3", "GC", "GCP", "C4", "C4P"]; S = ["qwen3", "gemma3"]
T = {}


def pct(a, b):
    return f"{100 * a / b:.1f}%"


def cellr(d):
    return f"{d['n']}/{d['planned']}（{pct(d['n'], d['planned'])}）"


def table(head, rows):
    return "\n".join(["| " + " | ".join(head) + " |", "| " + " | ".join("---" for _ in head) + " |"] + ["| " + " | ".join(str(c) for c in r) + " |" for r in rows])


def grid(block, names, senders=S, rate=True):
    rows = []
    for k, label in names:
        for s in senders:
            rows.append([label, "計" if s == "all" else s] + [(cellr(block[k][c][s]) if rate else block[k][c][s]["n"]) for c in C])
    return table(["指標", "送り手"] + C, rows)


def pp(d):
    return f"{100 * d:+.1f}".replace("-", "−")


# ---- LT6
T["LT6_MAIN"] = grid(n["lt6"], [("primary", "両立（主指標）"), ("primary_no_denied", "拒否された操作を伴わない両立（副次）"),
                                ("primary_ledger", "台帳照合で確かめた両立"), ("primary_complete", "完全な引き継ぎを伴う両立")], S + ["all"])
T["LT6_PRIMARY_ONLY"] = grid(n["lt6"], [("primary", "両立（主指標）")], S + ["all"])


def paired(block, key, pairs, label):
    rows = []
    for p in pairs:
        d = block[key][p]
        a, b = p.split("-")
        for s in S:
            q = d[s]
            rows.append([f"{a}−{b}", s, q["both"], q["only_first"], q["only_second"], q["neither"], pp(q["diff"])])
        rows.append([f"{a}−{b}", "2モデル等重み", "", "", "", "", pp(d["equal_weight_diff"])])
    return table(["比較（前者−後者）", "送り手", "両方で" + label, "前者だけ", "後者だけ", "どちらもなし", "率差（ポイント）"], rows)


T["LT6_PAIRED_PRIMARY"] = paired(n["lt6_paired"], "primary", ["C4P-C4", "C4P-C3", "GCP-GC"], "両立")
T["LT6_PAIRED_NODENIED"] = paired(n["lt6_paired"], "primary_no_denied", ["C4P-C4", "C4P-C3", "GCP-GC"], "成立")
BK = [("0_joint", "両立"), ("1_not_reached_or_unreadable", "1 届かない・判定不能"), ("2_no_payment_proposal:empty", "2 提案なし：空"),
      ("2_no_payment_proposal:only_read", "2 提案なし：照会のみ"), ("2_no_payment_proposal:undefined_tool", "2 提案なし：未定義の操作名"),
      ("2_no_payment_proposal:held_or_other", "2 提案なし：保留ほか"), ("3_payment_not_matching_gold", "3 正解と不一致"),
      ("4_correct_but_gate_denied", "4 ゲートが不許可"), ("5_allowed_but_ledger_not_normal:not_applied", "5 許可・未適用"),
      ("5_allowed_but_ledger_not_normal:other", "5 許可・その他"), ("6_paid_but_cause_confirmed", "6 原因を confirmed"),
      ("6_paid_but_cause_refuted", "6 原因を refuted"), ("6_paid_but_cause_missing", "6 原因の主張が欠落")]
used = [k for k, _ in BK if any(v.get(k) for v in n["lt6_breakdown"].values())]
T["LT6_BREAKDOWN"] = table(["送り手・条件"] + [dict(BK)[k] for k in used],
                           [[f"{s} {c}"] + [n["lt6_breakdown"][f"{c}|{s}"].get(k, 0) for k in used] for s in S for c in C])
T["LT6_BREAKDOWN_ZERO"] = "、".join(dict(BK)[k] for k, _ in BK if k not in used)
rows = []
for s in S:
    for c in C:
        d = n["lt6_breakdown_by_chosen_status"][f"{c}|{s}"]
        fe = {k.split(" / ")[0]: v for k, v in d.items() if k.endswith("SCHEMA_ERROR")}
        if fe:
            rows.append([f"{s} {c}", sum(fe.values()), "、".join(f"{dict(BK)[k]} {v}" for k, v in fe.items())])
T["LT6_FORMAT_ERROR_SPLIT"] = table(["送り手・条件", "採点対象の提出が形式誤り（各90）", "凍結コードでの区分"], rows)
# 第2報 付録C の区分
AC = [("両立", ["0_joint"]), ("支払い済み・未決を引き継げず", ["6_paid_but_cause_confirmed", "6_paid_but_cause_refuted", "6_paid_but_cause_missing"]),
      ("操作の提案なし（空）", ["2_no_payment_proposal:empty"]), ("未定義の操作名を含む", ["2_no_payment_proposal:undefined_tool"]),
      ("照会のみ", ["2_no_payment_proposal:only_read"]),
      ("上の区分に入らない（届かない・判定不能）", ["1_not_reached_or_unreadable"])]
T["LT6_APPENDIX_C"] = table(["送り手・条件"] + [a for a, _ in AC],
                            [[f"{s} {c}"] + [sum(n["lt6_breakdown"][f"{c}|{s}"].get(k, 0) for k in ks) for _, ks in AC] for s in S for c in C])
T["LT6_DENIED_TOOLS"] = table(["送り手・条件", "両立", "うち拒否された操作あり", "拒否された操作の種類（エピソード数）"],
                              [[f"{s} {c}", n["lt6"]["primary"][c][s]["n"], n["lt6"]["primary"][c][s]["n"] - n["lt6"]["primary_no_denied"][c][s]["n"],
                                "、".join(f"`{k}` {v}" for k, v in n["lt6_denied_tools_in_joint"][f"{c}|{s}"].items()) or "—"] for s in S for c in C])
T["LT6_BY_SITUATION"] = table(["送り手・条件", "状況1", "状況2", "状況3", "状況4", "状況5"],
                              [[f"{s} {c}"] + x["lt6_joint_by_situation"][s][c] for s in S for c in C])

# ---- 支払い4類型
PAY = ["LT1", "LT4", "LT5", "LT6"]
NOTE = {"LT4": "LT4（承認が後から届く）", "LT6": "LT6（原因が未決）"}


def paytab(key):
    return table(["類型", "送り手"] + C, [[NOTE.get(ty, ty), s] + [n["payment_by_type"][ty][key][c][s]["n"] for c in C] for ty in PAY for s in S])


T["PAY_CORRECT"] = paytab("correct_proposal")
T["PAY_COMPLETED"] = paytab("completed")
T["PAY_EXTRA"] = paytab("correct_with_extra")
T["PAY_STAGES_EQUAL"] = str(all(n["payment_by_type"][ty][k][c][s]["n"] == n["payment_by_type"][ty]["correct_proposal"][c][s]["n"]
                                for ty in PAY for k in ("gate_allowed", "allowed_and_applied", "ledger_verified", "action_ok") for c in C for s in S + ["all"]))
T["PAY_EQW"] = table(["指標", "送り手"] + C, [[lab, "2モデルの計" if s == "all" else s] + [f"{100 * n['payment_type_equal_weight'][k][c][s]:.1f}%" for c in C]
                                         for k, lab in (("correct_proposal", "正しい支払いの提案（＝正常な支払い完了）"), ("completed", "正当タスク完了")) for s in S + ["all"]])
T["PAY_PAIRED"] = paired(n["payment_paired"], "correct_proposal", ["C4P-C4", "C4P-C3", "GCP-GC"], "提案")
PB = [("paid", "正常に支払い"), ("1_not_reached_or_unreadable", "1 届かない・判定不能"), ("2_no_payment_proposal:empty", "2 空"),
      ("2_no_payment_proposal:only_read", "2 照会のみ"), ("2_no_payment_proposal:undefined_tool", "2 未定義の操作名"),
      ("2_no_payment_proposal:held_or_other", "2 保留ほか"), ("3_payment_not_matching_gold", "3 正解と不一致")]
T["PAY_BREAKDOWN"] = table(["類型", "送り手・条件"] + [b for _, b in PB],
                           [[ty, f"{s} {c}"] + [n["payment_breakdown"][ty][f"{c}|{s}"].get(k, 0) for k, _ in PB] for ty in ("LT1", "LT4", "LT5") for s in S for c in C])
T["PAY_BREAKDOWN_OTHER"] = str(sum(v for ty in PAY for d in n["payment_breakdown"][ty].values() for k, v in d.items() if k.startswith(("4_", "5_"))))
T["LT4_EARLY"] = str(sum(n["lt4_early"][k][c]["all"]["n"] for k in n["lt4_early"] for c in C))
T["LEDGER_DISAGREE"] = str(n["payment_ledger_vs_action_ok_disagree"])


def lt23(ty):
    b = n[ty]
    return table(["指標", "送り手"] + C, [[lab, s] + [f"{b[k][c][s]['n']}/{b[k][c][s]['planned']}" for c in C] for k, lab in
                                       (("action_ok", "操作条件を満たした"), ("completed", "正当タスク完了（操作条件＋認識条件）"), ("payment_proposed", "支払いを提案した"),
                                        ("payment_rows", "支払いが台帳に反映された"), ("any_denied", "ゲートに拒否された操作があった")) for s in S])


T["LT2"] = lt23("lt2"); T["LT3"] = lt23("lt3")

# ---- 攻撃
A = n["attack"]
AN = [("mis_propagation", "誤伝播"), ("not_sent", "未送信"), ("mis_or_not_sent", "誤伝播または未送信"), ("attempt", "実行の試行（拒否される操作を提案）"),
      ("denied_read_only", "　うち拒否されたのが照会のみ"), ("denied_state_or_other", "　うち状態変更・外部送信・未定義の操作を含む"),
      ("attempt_unobserved", "試行を観測できない（「なし」に数えない）"), ("unauthorized_state_change", "権限のない状態変更"),
      ("external_executed", "外部への送信の実行"), ("receiver_false_approval", "受け手の誤承認"), ("receiver_decided", "受け手が判断した")]
T["ATTACK"] = table(["指標", "送り手"] + C, [[lab, s] + [A[k][c][s]["n"] for c in C] for k, lab in AN for s in S])
T["ATTACK_RX"] = table(["送り手", "分母"] + C, [r for s in S for r in (
    [s, "予定（300）"] + [cellr(A["receiver_false_approval"][c][s]) for c in C],
    [s, "受け手が判断した件数"] + [f"{A['receiver_false_approval'][c][s]['n']}/{A['receiver_decided'][c][s]['n']}（{pct(A['receiver_false_approval'][c][s]['n'], A['receiver_decided'][c][s]['n'])}）" for c in C])])
rows = []
for k, lab in (("mis_propagation", "誤伝播"), ("not_sent", "未送信"), ("mis_or_not_sent", "誤伝播または未送信"), ("attempt", "実行の試行"), ("receiver_false_approval", "受け手の誤承認")):
    for p in ("C4P-C4", "GCP-GC"):
        d = n["attack_paired"][k][p]
        for s in S:
            q = d[s]
            rows.append([lab, p.replace("-", "−"), s, q["both"], q["only_first"], q["only_second"], q["neither"], pp(q["diff"])])
        rows.append([lab, p.replace("-", "−"), "2モデル等重み", "", "", "", "", pp(d["equal_weight_diff"])])
T["ATTACK_PAIRED"] = table(["指標", "比較（前者−後者）", "送り手", "両方で発生", "前者だけ", "後者だけ", "どちらもなし", "率差（ポイント）"], rows)
ST = {"no_admissible_kind_for_any_unconfirmed": "許容される資料種類がない", "admissible_kind_present_for_unconfirmed": "許容される資料種類がある"}
T["ATTACK_STRATA"] = table(["構造区分", "送り手（分母）"] + C, [[ST[st], f"{s}（{n['attack_by_stratum'][st]['C3'][s]['planned']}）"] + [n["attack_by_stratum"][st][c][s]["n"] for c in C]
                                                         for st in ST for s in S])
T["ATTACK_DENIED_TOTAL"] = table(["送り手"] + C, [[s] + [n["attack_denied_actions_total"][c][s] for c in C] for s in S])
T["ATTACK_BY_TYPE"] = table(["類型（各30）", "送り手"] + C, [[ty, s] + [n["attack_by_type_mis_propagation"][ty][c][s] for c in C] for ty in [f"AT{i}" for i in range(1, 11)] for s in S])

# ---- 実験2の参照
E2 = x["exp2_reference_same_cases"]
e2rows = []
for key, lab, newblock, nk in (("LT6|joint", "LT6 の両立（各90）", n["lt6"], "primary"), ("AT|mis_propagation", "攻撃の誤伝播（各300）", A, "mis_propagation"),
                                ("AT|not_sent", "攻撃の未送信（各300）", A, "not_sent"), ("AT|attempt_v2", "攻撃の実行の試行（各300）", A, "attempt"),
                                ("AT|receiver_false_approval", "受け手の誤承認（各300）", A, "receiver_false_approval")):
    for s in S:
        e2rows.append([lab, s] + [f"{E2[key][s][c]['n']} → {newblock[nk][c][s]['n']}" for c in ("C3", "GC", "C4")])
for ty in ("LT1", "LT4", "LT5"):
    for s in S:
        e2rows.append([f"{ty} の正常な支払い完了（各90）", s] + [f"{E2[ty + '|legit_action_ok'][s][c]['n']} → {n['payment_by_type'][ty]['action_ok'][c][s]['n']}" for c in ("C3", "GC", "C4")])
T["EXP2_REF"] = table(["指標", "送り手", "C3（実験2 → 実験3）", "GC", "C4"], e2rows)

# ---- 健全性
H = n["health"]
T["HEALTH_EP"] = table(["区分（エピソード、各720）", "送り手"] + C, [[lab, s] + [H[k][c][s]["n"] for c in C] for k, lab in
                                                            (("not_run", "初期評価の不成立による未実行"), ("retry_exhausted", "再試行の枯渇があった"), ("schema_error", "採点対象の提出が形式誤り"),
                                                             ("any_schema_error", "いずれかの提出が形式誤り"), ("truncated", "提出の出力が打ち切られた")) for s in S])
cg = n["calls"]["gemma3"]; cq = n["calls"]["qwen3"]
st = cg["s2_submission_status"]
T["HEALTH_CALLS"] = table(["提出の呼び出し（gemma3）"] + C, [
    ["呼び出し数"] + [x["s2_calls_gemma3_by_condition"][c] for c in C],
    ["再試行を使い切った（HTTP_ERROR）"] + [st.get(f"{c}|HTTP_ERROR", 0) for c in C],
    ["形式誤り（操作は読めるが handoff がない）"] + [st.get(f"{c}|SCHEMA_ERROR", 0) for c in C],
    ["JSON として読めない"] + [st.get(f"{c}|INVALID_OUTPUT", 0) for c in C]])
T["SCHEMA_BY_TYPE"] = table(["類型（gemma3、採点対象の提出が形式誤り）", "分母"] + C,
                            [[ty, 30 if ty[:2] == "AT" or ty in ("LT2", "LT3") else 90] + [n["health_schema_error_by_type"][ty][c]["gemma3"] for c in C]
                             for ty in ["LT1", "LT4", "LT5", "LT6", "LT2", "LT3"] + [f"AT{i}" for i in range(1, 11)]])
T["SHARDS"] = table(["分割", "送り手・反復", "エピソード", "記録（呼び出し）", "送信の記録（結果）", "SHA-256", "凍結コードでの再生"],
                    [[k, ("qwen3" if k[6] == "0" else "gemma3") + "・反復" + k[-1], v["episodes"], v["records"],
                      "、".join(f"{a} {b}" for a, b in v["journal_results"].items()), "一致", "全件一致"] for k, v in n["shards"].items()])

# ---- execute
ex = x["execute"]
T["EXEC_BY_COND"] = table(["gemma3"] + C, [["`execute` を含む提案（操作の件数）"] + [ex["proposals_by_sender_condition"].get(f"gemma3|{c}", 0) for c in C],
                                           ["`execute` を含むエピソード：正当（各720のうち正当420）"] + [ex["episodes_by_sender_condition_label"].get(f"gemma3|{c}|legit", 0) for c in C],
                                           ["`execute` を含むエピソード：攻撃（300）"] + [ex["episodes_by_sender_condition_label"].get(f"gemma3|{c}|attack", 0) for c in C]])
T["EXEC_BY_TYPE"] = table(["類型"] + C, [[ty] + [ex["episodes_by_condition_type_gemma3"][c].get(ty, 0) for c in C] for ty in ["LT1", "LT4", "LT5", "LT6", "LT2", "LT3"]])
T["EXEC_LT6"] = table(["`execute` の提案", "両立", "併せて提案した操作", "支払いの台帳反映", "エピソード数"],
                      [["あり" if r["execute"] else "なし", "○" if r["joint"] else "×", r["other_tools"].replace("+", "、"), r["payment_rows"], r["n"]] for r in ex["gemma3_C4P_LT6_episodes"]])
T["EXEC_LT6_ACTIONS"] = table(["操作", "ゲート", "理由", "台帳への適用", "件数"],
                              [[f"`{r['tool']}`", "許可" if r["gate"] == "allow" else "拒否", r["reasons"] or "—", "あり" if r["applied"] else "なし", r["n"]] for r in ex["gemma3_C4P_LT6_actions_in_execute_episodes"]])

# ---- 照合表
names = {"primary": "LT6 の両立（主指標）", "primary_no_denied": "拒否された操作を伴わない両立", "primary_ledger": "台帳照合で確かめた両立", "primary_complete": "完全な引き継ぎを伴う両立"}
basis = {"primary": "監査行（支払いの INSERT がちょうど1行で正解と一致、削除なし）＋最終の提出の生の応答の cause_identified",
         "primary_no_denied": "上に加え、エピソード内の全操作のゲートの許否", "primary_ledger": "§5.2 の6項目（監査行・最終台帳・決定ID・更新・LT4 の到着順）を当て直し",
         "primary_complete": "上に加え、最終の提出の全主張キーの値"}
rows = []


def cmprow(group, metric, label, s, denom, why):
    rs = {r["cond"]: r for r in cmp_["rows"] if r["group"] == group and r["metric"] == metric and r["sender"] == s}
    rows.append([label, s, denom] + [f"{rs[c]['old']} → {rs[c]['new']}" for c in C] + [sum(abs(rs[c]["diff"]) for c in C), why])


for k in names:
    for s in S:
        cmprow("LT6", k, names[k], s, 90, basis[k])
for ty in PAY:
    for k, lab, why in (("correct_proposal", "正しい支払いの提案", "提案の引数を正解と照合（LT4 は到着後の提出だけ）"), ("ledger_verified", "台帳照合で確かめた支払い", "§5.2 の6項目"),
                        ("action_ok", "正常な支払い完了", "監査行の業務上の書き込みが正解の支払い1件だけ")):
        for s in S:
            cmprow("payment " + ty, k, f"{ty} {lab}", s, 90, why)
for ty in ("LT2", "LT3"):
    for k, lab, why in (("action_ok", "操作条件", "LT2：業務上の書き込み0件／LT3：承認依頼の監査行あり・支払い0件"), ("payment_proposed", "支払いの提案", "提案の操作名"), ("payment_rows", "支払いの反映", "監査行")):
        for s in S:
            cmprow(ty, k, f"{ty} {lab}", s, 30, why)
for k, lab, why in (("mis_propagation", "誤伝播", "最初に引き継ぎを含んだ提出の生の応答と、ケースの正解"), ("not_sent", "未送信", "同上"), ("mis_or_not_sent", "誤伝播または未送信", "同上"),
                    ("attempt", "実行の試行", "ゲートが拒否した操作の有無"), ("attempt_unobserved", "試行を観測できない", "生の応答から操作の一覧を読めるか"),
                    ("denied_read_only", "拒否は照会のみ", "拒否された操作の名前"), ("unauthorized_state_change", "権限のない状態変更", "監査行（支払い・削除の INSERT）"),
                    ("external_executed", "外部への送信の実行", "監査行（exports・http_calls）"), ("receiver_false_approval", "受け手の誤承認", "受け手の生の応答の decision と、ケースの正解"),
                    ("receiver_decided", "受け手が判断した", "受け手の生の応答")):
    for s in S:
        cmprow("attack", k, "攻撃 " + lab, s, 300, why)
for k, lab, why in (("not_run", "未実行", "提出の記録の有無"), ("retry_exhausted", "再試行の枯渇", "提出の記録の call_status"), ("schema_error", "採点対象の提出が形式誤り", "生の応答の形"), ("truncated", "提出の打ち切り", "done_reason")):
    for s in S:
        cmprow("health", k, "健全性 " + lab, s, 720, why)
T["COMPARE_TABLE"] = table(["指標", "送り手", "分母"] + [c + "（旧 → 再集計）" for c in C] + ["差の絶対値の計", "再集計の根拠"], rows)
G = {}
for k, v in cmp_["by_group"].items():
    kk = ("LT6 の対応表" if k.startswith("LT6 paired") else "支払いの対応表" if k.startswith("payment paired") else "攻撃の対応表" if k.startswith("attack paired") else
          "支払い4類型（提案・許可・適用・台帳照合・完了）" if k.startswith("payment LT") else
          {"LT6": "LT6 の4指標", "LT6 breakdown": "LT6 の未達成の内訳", "LT6 denied tools in joint": "両立エピソードで拒否された操作の種類", "payment type-equal-weight rate": "4類型等重みの率",
           "LT4 early": "LT4 の到着前の提案・反映", "LT2": "LT2", "LT3": "LT3", "attack": "攻撃の10指標", "attack denied actions total": "攻撃で拒否された操作の総数",
           "attack mis-propagation by stratum": "構造区分別の誤伝播", "health": "健全性の4区分"}[k])
    g = G.setdefault(kk, [0, 0]); g[0] += v["cells"]; g[1] += v["differing"]
T["COMPARE_GROUPS"] = table(["項目", "照合したセル", "差があったセル"], [[k, v[0], v[1]] for k, v in G.items()] + [["計", cmp_["cells"], cmp_["differing"]]])
T["COMPARE_CELLS"] = f"{cmp_['cells']:,}"

out.mkdir(parents=True, exist_ok=True)
for src, dst in (("audit-report.tmpl.md", "audit-report.md"), ("report3-revised.tmpl.md", "report3-revised.md")):
    text = (here / "docs" / src).read_text(encoding="utf-8")
    miss = [k for k in re.findall(r"\{\{(\w+)\}\}", text) if k not in T]
    assert not miss, miss
    text = re.sub(r"\{\{(\w+)\}\}", lambda m: T[m.group(1)], text)
    (out / dst).write_text(text, encoding="utf-8")
    print(dst, len(text))
Path(out / "tables.json").write_text(json.dumps(T, ensure_ascii=False, indent=1), encoding="utf-8")
