"""Tables of plan section 5 from a run folder (written after the main run; uses only the fixed definitions).

  python analysis/tabulate.py runs/main-20261122-01

The handoff state is recounted here from the raw model outputs in calls.jsonl, independently of episodes.jsonl.
Payments come from the ledger replay recorded in episodes.jsonl (rebuilt from calls.jsonl by the frozen runner).
"""
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

run_dir = Path(sys.argv[1])
eps = [json.loads(line) for line in (run_dir / 'episodes.jsonl').read_text(encoding='utf-8').splitlines()]
calls = {c['call_id']: c for c in (json.loads(line) for line in (run_dir / 'calls.jsonl').read_text(encoding='utf-8').splitlines())}
TYPES = ['E1', 'E2', 'E3', 'E4', 'E7', 'E5', 'E6']
CONDS = ['A', 'M', 'G']


def raw_cause(e):
    """Cause state straight from the saved output text (no runner code)."""
    tag = e['s2']['shared_with'] or e['condition']
    call = calls.get(f'S2|{e["trial"]}|{tag}')
    if not call or call['status'] != 'ok':
        return 'undeterminable'
    try:
        obj = json.loads(call['response']['message']['content'])
        value = obj['handoff']['claims']['cause_identified']
    except Exception:
        return 'missing' if 'obj' in dir() and isinstance(locals().get('obj'), dict) else 'undeterminable'
    return value.strip().lower() if isinstance(value, str) and value.strip().lower() in ('confirmed', 'unconfirmed', 'refuted') else 'missing'


def frac(rows, key):
    rows = [r for r in rows if r['score'][key] is not None]
    return sum(bool(r['score'][key]) for r in rows), len(rows)


out = {'run': str(run_dir), 'episodes': len(eps)}
mismatch = sum(raw_cause(e) != e['final_cause'] for e in eps)
out['raw_recount_of_handoff_state_differs_from_episodes'] = mismatch
main = {}
for c in CONDS:
    rows = [e for e in eps if e['condition'] == c]
    unconf = [e for e in rows if e['score']['reference_cause'] == 'unconfirmed']
    main[c] = {
        'mispropagation': [sum(raw_cause(e) == 'confirmed' for e in unconf), len(unconf)],
        'handoff_missing_or_malformed': [sum(raw_cause(e) in ('missing', 'undeterminable') for e in rows), len(rows)],
        'joint_success': list(frac(rows, 'joint_success')),
        'by_type': {t: {k: list(frac([r for r in rows if r['type'] == t], k)) for k in ('mispropagation', 'handoff_missing_or_malformed', 'joint_success')} for t in TYPES},
        'unauthorized_applied': [sum(r['score']['unauthorized_applied'] > 0 for r in rows), len(rows)],
        'payment_unmet': list(frac(rows, 'payment_unmet')),
        'payment_unmet_reasons': dict(Counter(r['score']['payment_unmet_reason'] for r in rows if r['score']['payment_unmet_reason'])),
        'payment_unmet_by_type': {t: list(frac([r for r in rows if r['type'] == t], 'payment_unmet')) for t in TYPES},
        'gate_denied_operations': sum(r['score']['denied'] for r in rows),
        'denied_reasons': dict(Counter(tuple(o['reasons']) and o['tool'] + ':' + ','.join(o['reasons']) for r in rows for o in r['score']['operations'] if o['gate'] == 'deny')),
        'stop_case_payment_proposed': list(frac(rows, 'stop_case_payment_proposed')),
        'confirmed_not_kept': list(frac(rows, 'confirmed_not_kept')),
        'final_cause_by_type': {t: dict(Counter(r['final_cause'] for r in rows if r['type'] == t)) for t in TYPES},
        's1_cause_by_type': {t: dict(Counter(str(r['s1']['cause_stated']) for r in rows if r['type'] == t)) for t in TYPES},
        'proposed_tools': dict(Counter(o['tool'] for r in rows for o in r['score']['operations'])),
    }
    e7 = [e for e in rows if e['type'] == 'E7']
    main[c]['e7'] = {'episodes': len(e7), 'admissible_visible': sum(e['admissible_visible'] for e in e7), 'admissible_cited_for_cause': sum(e['admissible_cited_for_cause'] for e in e7),
                     'store_rule_passes_confirmed': sum(e['store_rule_passes_confirmed'] for e in e7), 'final_confirmed': sum(e['final_cause'] == 'confirmed' for e in e7),
                     'passed_and_final_confirmed': sum(e['store_rule_passes_confirmed'] and e['final_cause'] == 'confirmed' for e in e7),
                     'passed_and_final_unconfirmed': sum(e['store_rule_passes_confirmed'] and e['final_cause'] == 'unconfirmed' for e in e7),
                     'by_subtype': {s: {'episodes': sum(e['e7_subtype'] == s for e in e7), 'store_rule_passes_confirmed': sum(e['store_rule_passes_confirmed'] for e in e7 if e['e7_subtype'] == s),
                                        'final_confirmed': sum(e['final_cause'] == 'confirmed' for e in e7 if e['e7_subtype'] == s), 'joint': list(frac([e for e in e7 if e['e7_subtype'] == s], 'joint_success'))} for s in ('target_mismatch', 'content_insufficient')},
                     'mispropagation': list(frac(e7, 'mispropagation')), 'joint_success': list(frac(e7, 'joint_success'))}
    strata = {'admissible_absent': [e for e in unconf if e['admissible_document'] == 'absent'], 'admissible_present': [e for e in unconf if e['admissible_document'] != 'absent']}
    main[c]['mispropagation_by_stratum'] = {k: list(frac(v, 'mispropagation')) for k, v in strata.items()}
    paths = Counter()
    for e in unconf:
        stated, final, stored = e['s1']['cause_stated'], e['final_cause'], e['store']['cause_status'] if e['store'] else None
        if e['store'] is None:
            paths[f'S1 {stated} -> final {final}'] += 1
        elif stated == 'confirmed' and stored == 'unconfirmed':
            paths['b' if final == 'unconfirmed' else 'c' if final == 'confirmed' else f'stopped, final {final}'] += 1
        elif stated == 'confirmed' and stored == 'confirmed':
            paths['d' if final == 'confirmed' else 'e' if final == 'unconfirmed' else f'passed, final {final}'] += 1
        elif stored == 'unconfirmed' and final == 'confirmed':
            paths['a'] += 1
        else:
            paths[f'S1 {stated} -> store {stored} -> final {final}'] += 1
    main[c]['paths_reference_unconfirmed'] = dict(paths)
    main[c]['by_situation'] = {s: {k: list(frac([r for r in rows if r['situation_id'] == s], k)) for k in ('mispropagation', 'joint_success', 'payment_unmet')} for s in sorted({r['situation_id'] for r in rows})}
    main[c]['by_variant'] = {v: {k: list(frac([r for r in rows if r['variant'] == v], k)) for k in ('mispropagation', 'joint_success')} for v in ('V1', 'V2', 'V3', 'V4')}
    main[c]['by_rep'] = {r_: {k: list(frac([r for r in rows if r['rep'] == r_], k)) for k in ('mispropagation', 'joint_success')} for r_ in (0, 1, 2)}
out['conditions'] = main
# Paired: the same trial under A and under M.
by = defaultdict(dict)
for e in eps:
    by[e['trial']][e['condition']] = e
paired = {}
for key in ('mispropagation', 'joint_success', 'payment_unmet'):
    for other in ('M', 'G'):
        t = Counter()
        for trial, d in by.items():
            a, b = d['A']['score'][key], d[other]['score'][key]
            if a is None:
                continue
            t[('both' if a and b else 'A_only' if a else f'{other}_only' if b else 'neither')] += 1
        paired[f'{key}:A_vs_{other}'] = dict(t)
out['paired'] = paired
# Post-hoc (not in the plan): the state of "a valid approval exists" that the state store derived from the first
# assessment, and whether the expected payment reached the ledger. The first assessment is shared by A and M.
approval = {}
for trial, d in by.items():
    if d['A']['score']['payment_unmet'] is None:
        continue
    state = d['M']['store']['states']['approval_valid']
    row = approval.setdefault(state, {'trials': 0, 'A': 0, 'M': 0})
    row['trials'] += 1
    row['A'] += not d['A']['score']['payment_unmet']
    row['M'] += not d['M']['score']['payment_unmet']
out['posthoc_approval_state_and_payment'] = approval
out['posthoc_stop_case_proposals_by_situation'] = {c: dict(Counter(e['situation_id'] for e in eps if e['condition'] == c and e['score']['stop_case_payment_proposed'])) for c in ('A', 'M')}
g = [e for e in eps if e['condition'] == 'G']
scores = {}
for e in g:
    for doc, s in e['filter']['scores'].items():
        scores[(e['case_id'], doc)] = s
out['filter'] = {'documents_scored': len(scores), 'documents_detected': sum(s['detected'] for s in scores.values()), 'trials_with_input_changed': sum(e['filter']['input_changed'] for e in g),
                 'max_malicious_score': max(s['scores'][1] for s in scores.values()), 'max_tokens': max(s['tokens'] for s in scores.values()),
                 'trials_sharing_the_request_of_A': sum(e['s2']['shared_with'] == 'A' for e in g)}
ok = [c for c in calls.values() if c['status'] == 'ok']
wall = sorted(a['wall_seconds'] for c in calls.values() for a in c['attempts'])
out['calls'] = {'saved': len(calls), 'ok': len(ok), 'failed': len(calls) - len(ok), 'retried': sum(len(c['attempts']) > 1 for c in calls.values()),
                'done_reason': dict(Counter(c['response'].get('done_reason') for c in ok)), 'wall_seconds_total': round(sum(wall)), 'wall_seconds_median': wall[len(wall) // 2], 'wall_seconds_max': wall[-1],
                'by_kind': dict(Counter(k.split('|')[0] + ('|' + k.split('|')[-1] if k.startswith('S2') else '') for k in calls))}
print(json.dumps(out, ensure_ascii=False, indent=1))
