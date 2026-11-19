"""Pre-output case strata; presence of an admissible kind does not guarantee state retention."""
from collections import Counter
from pathlib import Path
import argparse
import hashlib
import json


EXPOSED = "admissible_kind_present_for_unconfirmed"
FILTERED = "no_admissible_kind_for_any_unconfirmed"


def classify(case):
    snap = case.get("gold_snapshots", {}).get("first_handoff", {})
    gold = snap.get("gold", case["gold"])
    visible = set(snap.get("evidence_ids", [d["id"] for d in case["documents"]]))
    witnesses = []
    for claim in case["claims"]:
        if gold["claims"].get(claim["key"]) != "unconfirmed": continue
        docs = [d["id"] for d in case["documents"] if d["id"] in visible
                and d["kind"] in claim["admissible_kinds"]]
        if docs: witnesses.append(dict(claim=claim["key"],document_ids=docs))
    return dict(case_id=case["case_id"],type=case["type"],label=case["label"],
                stratum=EXPOSED if witnesses else FILTERED,witnesses=witnesses)


def load_cases(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line]


def case_manifest(rows):
    classified = [classify(c) for c in rows if c["label"] == "attack"]
    return dict(attack_cases=len(classified),counts=dict(Counter(c["stratum"] for c in classified)),
                exposed_by_type=dict(Counter(c["type"] for c in classified if c["stratum"] == EXPOSED)),
                cases=classified)


def pilot_differences(rows, episodes):
    strata = {c["case_id"]:classify(c)["stratum"] for c in rows if c["label"] == "attack"}
    output = []
    for sender in ["all"]+sorted({e["sender"] for e in episodes}):
        for stratum in (EXPOSED,FILTERED):
            cells = {}
            paired = {}
            for condition in ("C3","C4"):
                selected = [e for e in episodes if strata.get(e["case_id"]) == stratum
                            and e["condition"] == condition and (sender == "all" or e["sender"] == sender)]
                cells[condition] = dict(events=sum(bool(e["metrics"]["mis_propagation"]) for e in selected),
                                        episodes=len(selected))
                paired[condition] = Counter((e["case_id"],e["sender"],e["rep"]) for e in selected)
                if any(n != 1 for n in paired[condition].values()): raise ValueError("duplicate pilot episode")
                if sender == "all":
                    sizes=Counter(e["sender"] for e in selected)
                    if len(set(sizes.values())) != 1: raise ValueError("pilot sender counts not balanced")
            if not all(cells[c]["episodes"] for c in cells): raise ValueError("missing pilot stratum")
            if paired["C3"] != paired["C4"]: raise ValueError("pilot C3/C4 pairs do not match")
            difference = cells["C4"]["events"]/cells["C4"]["episodes"]-cells["C3"]["events"]/cells["C3"]["episodes"]
            output.append(dict(sender=sender,stratum=stratum,cells=cells,c4_minus_c3=difference))
    return output


def main():
    p = argparse.ArgumentParser()
    p.add_argument("output",type=Path)
    p.add_argument("--initial-cases",type=Path)
    p.add_argument("--initial-episodes",type=Path)
    args = p.parse_args()
    if args.output.exists(): raise SystemExit("refuse overwrite: "+str(args.output))
    if bool(args.initial_cases) != bool(args.initial_episodes): raise SystemExit("provide both initial files")
    here = Path(__file__).resolve().parent
    paths = dict(main=here/"cases/main.jsonl",dev=here/"cases/dev.jsonl")
    result = dict(status="pre_output_case_classification_not_outcome_prediction",
                  definition="ANY gold-unconfirmed claim has a visible document of an admissible kind; no semantic or actual-citation check.",
                  warning="Does not imply confirmed will be emitted or retained, or that C4 will fail.",
                  main=case_manifest(load_cases(paths["main"])),dev=case_manifest(load_cases(paths["dev"])))
    if args.initial_cases:
        paths.update(initial_cases=args.initial_cases,initial_episodes=args.initial_episodes)
        initial = load_cases(args.initial_cases)
        result["initial"] = case_manifest(initial)
        result["pilot_mis_propagation"] = pilot_differences(initial,load_cases(args.initial_episodes))
    result["input_sha256"] = {k:hashlib.sha256(v.read_bytes()).hexdigest() for k,v in paths.items()}
    result["classifier_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open("x",encoding="utf-8",newline="\n") as f:
        json.dump(result,f,ensure_ascii=False,indent=2,allow_nan=False);f.write("\n")
    print(json.dumps({k:{x:v for x,v in result[k].items() if x != "cases"} for k in ("main","dev","initial") if k in result},ensure_ascii=False))


if __name__ == "__main__": main()
