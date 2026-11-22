"""Experiment 4 runner (draft, before freeze).

  python harness/run.py --cases cases/dev-cases.jsonl --out runs/dev-01 --reps 1 --conditions A,M

One trial = one case in one repetition. Calls per trial: one first assessment (S1) shared by the
conditions, then one submission (S2) per condition. Every call is appended to calls.jsonl with the request
body as sent, its SHA-256 and the full response. A call that already has a saved result is never sent
again, so an interrupted run continues where it stopped. Ledgers are rebuilt from the saved outputs.
episodes.jsonl and summary.json are derived files and are rewritten on every run.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import statistics
import sys
import time
from collections import Counter
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import anchor                      # noqa: E402
from gate import STATUSES, parse_json          # noqa: E402
from score import FOCUS, score     # noqa: E402

CONFIG = {       # draft values; fixed in the freeze record before the main run
    'model': 'gpt-oss:20b', 'think': 'low', 'seed_base': 20461122, 'timeout_seconds': 300, 'max_retries': 2,
    'options': {'temperature': 0.7, 'top_p': 0.9, 'num_ctx': 4096, 'num_predict': 1024},
}
UNTRUSTED = ('business_memo', 'email')
KIND = anchor.KIND_LABEL
STATUS = {'enum': list(STATUSES)}
TOOLS_HELP = None


def text(name):
    return (ROOT / 'prompts' / name).read_text(encoding='utf-8').strip()


def sha(value):
    return hashlib.sha256(value if isinstance(value, bytes) else value.encode('utf-8')).hexdigest()


# ---- prompts ----------------------------------------------------------------------------------
def documents_block(case, withheld=()):
    lines = []
    for d in case['documents']:
        body = text('filter_notice.txt') if d['id'] in withheld else d['text']
        if d['kind'] in UNTRUSTED:
            lines.append(f'{d["id"]} [{KIND[d["kind"]]}] [untrusted]: <untrusted_document>{body}</untrusted_document>')
        else:
            lines.append(f'{d["id"]} [{KIND[d["kind"]]}]: {body}')
    return '\n'.join(lines)


def stage1_messages(case, withheld=()):
    user = (text('stage1_user_template.txt').replace('{task}', case['task']).replace('{documents}', documents_block(case, withheld))
            .replace('{claims_block}', '\n'.join(f'- {c["key"]}: {c["statement"]}' for c in case['claims'])))
    return [{'role': 'system', 'content': text('system.txt')}, {'role': 'user', 'content': user}]


def stage2_base(case):
    keys = [c['key'] for c in case['claims']]
    return (text('stage2_base.txt').replace('{claim_keys}', ', '.join(keys))
            .replace('{claims_example}', '{' + ', '.join(f'"{k}": "status"' for k in keys) + '}'))


def stage2_messages(stage1, stage1_content, case, block=None):
    instruction = (block + '\n\n' if block else '') + stage2_base(case)
    return stage1 + [{'role': 'assistant', 'content': stage1_content}, {'role': 'user', 'content': instruction}]


def schemas(case):
    keys = [c['key'] for c in case['claims']]
    s1 = {'type': 'object', 'required': ['claims', 'note'], 'properties': {'note': {'type': 'string'}, 'claims': {
        'type': 'object', 'required': keys, 'properties': {k: {'type': 'object', 'required': ['status', 'evidence'], 'properties': {
            'status': STATUS, 'evidence': {'type': 'array', 'items': {'type': 'string'}}}} for k in keys}}}}
    s2 = {'type': 'object', 'required': ['actions', 'handoff'], 'properties': {
        'actions': {'type': 'array', 'items': {'type': 'object', 'required': ['tool', 'args'], 'properties': {'tool': {'type': 'string'}, 'args': {'type': 'object'}}}},
        'handoff': {'type': 'object', 'required': ['claims'], 'properties': {'claims': {'type': 'object', 'required': keys, 'properties': {k: STATUS for k in keys}}}}}}
    return s1, s2


def request_body(messages, schema, seed):
    return {'model': CONFIG['model'], 'think': CONFIG['think'], 'stream': False, 'format': schema,
            'options': {**CONFIG['options'], 'seed': seed}, 'messages': messages}


# ---- transport --------------------------------------------------------------------------------
class Ollama:
    def __init__(self, host):
        self.host, self.client = host.rstrip('/'), build_opener(ProxyHandler({}))

    def get(self, path):
        with self.client.open(self.host + path, timeout=10) as response:
            return json.load(response)

    def chat(self, body_bytes):
        request = Request(self.host + '/api/chat', body_bytes, {'Content-Type': 'application/json'})
        with self.client.open(request, timeout=CONFIG['timeout_seconds']) as response:
            return json.load(response)


class TransportDown(RuntimeError):
    pass


class Calls:
    """calls.jsonl: one line per call, appended and flushed. A call with a saved line is not sent again."""
    def __init__(self, path, chat):
        self.path, self.chat, self.saved = path, chat, {}
        if path.exists():
            for line in path.read_text(encoding='utf-8').splitlines():
                if line.strip():
                    row = json.loads(line)
                    self.saved[row['call_id']] = row
        self.sent = 0

    def call(self, call_id, body):
        if call_id in self.saved:
            return self.saved[call_id]
        raw = json.dumps(body, ensure_ascii=False).encode('utf-8')
        row = {'call_id': call_id, 'request': body, 'request_sha256': sha(raw), 'attempts': [], 'response': None, 'status': 'failed'}
        for attempt in range(CONFIG['max_retries'] + 1):       # retried only on transport, HTTP or timeout errors, never on content
            started, wall = time.time(), time.monotonic()
            try:
                response = self.chat(raw)
                row['attempts'].append({'started_at': started, 'wall_seconds': round(time.monotonic() - wall, 3), 'error': None})
                row.update(response=response, status='ok')
                break
            except HTTPError as error:
                row['attempts'].append({'started_at': started, 'wall_seconds': round(time.monotonic() - wall, 3), 'error': f'HTTP_ERROR {error.code}'})
            except (URLError, TimeoutError, ConnectionError, OSError) as error:
                kind = 'TIMEOUT' if isinstance(error, TimeoutError) or 'timed out' in str(error) else 'TRANSPORT_ERROR'
                row['attempts'].append({'started_at': started, 'wall_seconds': round(time.monotonic() - wall, 3), 'error': f'{kind} {error}'})
        with self.path.open('a', encoding='utf-8', newline='\n') as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + '\n')
            handle.flush()
        self.saved[call_id] = row
        self.sent += 1
        if row['status'] == 'failed' and all(a['error'].startswith('TRANSPORT_ERROR') for a in row['attempts']):
            raise TransportDown(call_id)        # the failed call stays recorded; check the model server, then run again to continue
        return row


def content_of(row):
    if row is None or row['status'] != 'ok':
        return None
    message = row['response'].get('message') or {}
    return message.get('content') if isinstance(message.get('content'), str) else None


# ---- one trial --------------------------------------------------------------------------------
def run_trial(case, index, rep, conditions, calls, filter_scores):
    seed = CONFIG['seed_base'] + 1000 * rep + index
    s1_schema, s2_schema = schemas(case)
    trial = f'{case["case_id"]}|r{rep}'
    detected = sorted(d for d, s in filter_scores.items() if s['detected']) if filter_scores is not None else []
    episodes, views = [], {}

    def assessment(tag, withheld):
        if tag not in views:
            messages = stage1_messages(case, withheld)
            row = calls.call(f'S1|{trial}|{tag}', request_body(messages, s1_schema, seed))
            content = content_of(row)
            parsed = parse_json(content) if content is not None else None
            visible = [d['id'] for d in case['documents'] if d['id'] not in withheld]
            views[tag] = {'messages': messages, 'row': row, 'content': content, 'parsed': parsed, 'visible': visible,
                          'state': anchor.build_state(case, parsed, visible)}
        return views[tag]

    for condition in conditions:
        if condition == 'G' and filter_scores is None:
            continue                                            # the input filter is not installed for this run
        changed = condition == 'G' and bool(detected)
        view = assessment('filtered' if changed else 'orig', detected if changed else ())
        store = view['state'] if condition == 'M' else None
        shared = None
        if view['content'] is None:
            s2_row, output = None, None                         # S1 did not return: the submission is not run
        else:
            block = anchor.render_block(text('stage2_m_header.txt'), store) if condition == 'M' else None
            # G with nothing withheld sends exactly the request of A; it is sent once and shared.
            tag = 'A' if (condition == 'G' and not changed and 'A' in conditions) else condition
            shared = 'A' if tag != condition else None
            s2_row = calls.call(f'S2|{trial}|{tag}', request_body(stage2_messages(view['messages'], view['content'], case, block), s2_schema, seed))
            output = content_of(s2_row)
        result = score(case, output)
        s1_claim = view['state'][FOCUS]
        admissible_ids = [d['id'] for d in case['documents'] if d['kind'] in next(c for c in case['claims'] if c['key'] == FOCUS)['admissible_kinds']]
        episodes.append({
            'trial': trial, 'case_id': case['case_id'], 'situation_id': case['situation_id'], 'type': case['type'], 'variant': case['variant'],
            'rep': rep, 'seed': seed, 'condition': condition,
            # Strata are fixed before filtering; what the model saw after filtering is recorded separately.
            'admissible_document': case['gold']['admissible_document'], 'e7_subtype': case['gold']['e7_subtype'],
            'filter': {'scores': filter_scores, 'detected': detected, 'input_changed': changed, 'visible_documents': view['visible']} if condition == 'G' else None,
            's1': {'status': view['row']['status'], 'readable': isinstance(view['parsed'], dict), 'cause_stated': s1_claim['stated'], 'cause_cited': s1_claim['cited'],
                   'done_reason': (view['row']['response'] or {}).get('done_reason')},
            'store': {'cause_status': store[FOCUS]['status'], 'cause_reason': store[FOCUS]['reason'], 'states': {k: v['status'] for k, v in store.items()}} if store else None,
            # Counted independently of the final output: was an admissible-kind document shown, cited for the cause, and passed by the store rule?
            'admissible_visible': any(d in view['visible'] for d in admissible_ids),
            'admissible_cited_for_cause': bool(s1_claim['admitted']),
            'store_rule_passes_confirmed': s1_claim['stated'] == 'confirmed' and bool(s1_claim['admitted']),
            's2': {'status': s2_row['status'] if s2_row else 'not_run', 'shared_with': shared, 'done_reason': (s2_row['response'] or {}).get('done_reason') if s2_row else None},
            'final_cause': result['cause_state'], 'score': result,
        })
    return episodes


# ---- summary ----------------------------------------------------------------------------------
def frac(rows, key):
    rows = [r for r in rows if r['score'][key] is not None]
    return [sum(1 for r in rows if r['score'][key]), len(rows)]


def path_of(e):
    """a-e of plan section 2.2, from the first assessment, the store and the final handoff."""
    stated, final = e['s1']['cause_stated'], e['final_cause']
    stored = e['store']['cause_status'] if e['store'] else None
    if e['store'] is None:
        return f'S1 {stated} -> final {final}'
    if stated != 'confirmed':
        return 'a' if (stored == 'unconfirmed' and final == 'confirmed') else f'S1 {stated} -> store {stored} -> final {final}'
    if stored == 'unconfirmed':
        return 'b' if final == 'unconfirmed' else 'c' if final == 'confirmed' else f'S1 confirmed -> store unconfirmed -> final {final}'
    return 'd' if final == 'confirmed' else 'e' if final == 'unconfirmed' else f'S1 confirmed -> store confirmed -> final {final}'


def summarize(episodes, calls, planned):
    out = {'planned_trials_per_condition': planned, 'conditions': {}}
    for condition in sorted({e['condition'] for e in episodes}):
        rows = [e for e in episodes if e['condition'] == condition]
        unconf = [e for e in rows if e['score']['reference_cause'] == 'unconfirmed']
        e7 = [e for e in rows if e['type'] == 'E7']
        out['conditions'][condition] = {
            'episodes': len(rows),
            'main': {'mispropagation': frac(rows, 'mispropagation'), 'handoff_missing_or_malformed': frac(rows, 'handoff_missing_or_malformed'), 'joint_success': frac(rows, 'joint_success')},
            'appendix': {
                'unauthorized_applied': [sum(1 for r in rows if r['score']['unauthorized_applied'] > 0), len(rows)],
                'payment_unmet': frac(rows, 'payment_unmet'), 'payment_unmet_reasons': dict(Counter(r['score']['payment_unmet_reason'] for r in rows if r['score']['payment_unmet_reason'])),
                'stop_case_payment_proposed': frac(rows, 'stop_case_payment_proposed'), 'confirmed_not_kept': frac(rows, 'confirmed_not_kept'),
                'paths_reference_unconfirmed': dict(Counter(path_of(e) for e in unconf)),
                'by_type': {t: {k: frac([r for r in rows if r['type'] == t], k) for k in ('mispropagation', 'handoff_missing_or_malformed', 'joint_success')} for t in sorted({r['type'] for r in rows})},
                'e7': {'episodes': len(e7), 'admissible_visible': sum(e['admissible_visible'] for e in e7), 'admissible_cited_for_cause': sum(e['admissible_cited_for_cause'] for e in e7),
                       'store_rule_passes_confirmed': sum(e['store_rule_passes_confirmed'] for e in e7), 'final_confirmed': sum(e['final_cause'] == 'confirmed' for e in e7)},
            },
            'format': {'s1_not_returned': sum(e['s1']['status'] != 'ok' for e in rows), 's1_unreadable': sum(e['s1']['status'] == 'ok' and not e['s1']['readable'] for e in rows),
                       's2_not_run_or_failed': sum(e['s2']['status'] != 'ok' for e in rows), 's2_unreadable': sum(e['s2']['status'] == 'ok' and not e['score']['readable'] for e in rows),
                       's2_done_reason': dict(Counter(str(e['s2']['done_reason']) for e in rows))},
            's1_cause_by_type': {t: dict(Counter(str(e['s1']['cause_stated']) for e in rows if e['type'] == t)) for t in sorted({r['type'] for r in rows})},
            'filter': {'input_changed': sum(bool(e['filter'] and e['filter']['input_changed']) for e in rows),
                       'documents_detected': sum(len(e['filter']['detected']) for e in rows if e['filter'])} if condition == 'G' else None,
        }
    ok = [r for r in calls.saved.values() if r['status'] == 'ok']
    ns = lambda r, k: (r['response'].get(k) or 0) / 1e9      # noqa: E731
    infer = sorted(ns(r, 'total_duration') - ns(r, 'load_duration') for r in ok)
    out['calls'] = {'saved': len(calls.saved), 'ok': len(ok), 'failed': len(calls.saved) - len(ok), 'sent_this_run': calls.sent,
                    'errors': dict(Counter(a['error'].split()[0] for r in calls.saved.values() for a in r['attempts'] if a['error'])),
                    'done_reason': dict(Counter(str(r['response'].get('done_reason')) for r in ok))}
    if infer:
        out['timing_seconds'] = {
            'load_max': round(max(ns(r, 'load_duration') for r in ok), 2), 'calls_with_load_over_1s': sum(ns(r, 'load_duration') > 1 for r in ok),
            'inference_median': round(statistics.median(infer), 2), 'inference_p90': round(infer[int(0.9 * (len(infer) - 1))], 2), 'inference_max': round(infer[-1], 2),
            'output_tokens_median': statistics.median((r['response'].get('eval_count') or 0) for r in ok), 'output_tokens_max': max((r['response'].get('eval_count') or 0) for r in ok),
            'projected_hours_for_1680_calls_at_median': round(1680 * statistics.median(infer) / 3600, 1),
            # Basis for scheduling: the observed wall time per call, including loading and slow calls.
            'wall_seconds_per_call': round(sum(a['wall_seconds'] for r in calls.saved.values() for a in r['attempts']) / len(calls.saved), 2),
            'projected_hours_for_1680_calls_at_observed_pace': round(1680 * sum(a['wall_seconds'] for r in calls.saved.values() for a in r['attempts']) / len(calls.saved) / 3600, 1)}
    return out


def print_summary(summary):
    for condition, c in summary['conditions'].items():
        m, f = c['main'], c['format']
        print(f'[{condition}] episodes {c["episodes"]} | mis-propagation {m["mispropagation"][0]}/{m["mispropagation"][1]} | handoff missing or malformed '
              f'{m["handoff_missing_or_malformed"][0]}/{m["handoff_missing_or_malformed"][1]} | joint success {m["joint_success"][0]}/{m["joint_success"][1]}')
        print(f'      format: S1 not returned {f["s1_not_returned"]}, S1 unreadable {f["s1_unreadable"]}, S2 not run or failed {f["s2_not_run_or_failed"]}, S2 unreadable {f["s2_unreadable"]}, done_reason {f["s2_done_reason"]}')
        print(f'      S1 cause by type: {c["s1_cause_by_type"]}')
        print(f'      E7: {c["appendix"]["e7"]} | paths: {c["appendix"]["paths_reference_unconfirmed"]}')
    print('calls:', summary['calls'])
    print('timing (seconds):', summary.get('timing_seconds'))


def main(argv=None, chat=None, info=None, classify=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--cases', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--reps', type=int, default=1)
    parser.add_argument('--conditions', default='A,M')
    parser.add_argument('--host', default='http://127.0.0.1:11434')
    parser.add_argument('--expect-digest', help='stop before any inference if the model digest differs')
    parser.add_argument('--prompt-guard', help='local path or ID of Llama Prompt Guard 2 (needed for condition G)')
    parser.add_argument('--limit', type=int, help='only the first N cases (for a quick check)')
    args = parser.parse_args(argv)
    conditions = [c for c in args.conditions.split(',') if c]
    assert set(conditions) <= {'A', 'M', 'G'}
    cases = [json.loads(line) for line in args.cases.read_text(encoding='utf-8').splitlines() if line.strip()][:args.limit]
    args.out.mkdir(parents=True, exist_ok=True)

    if chat is None:
        server = Ollama(args.host)
        models = server.get('/api/tags')['models']
        model = next((m for m in models if m['name'] == CONFIG['model']), None)
        if model is None:
            sys.exit(f'{CONFIG["model"]} is not available on {args.host}')
        info = {'ollama_version': server.get('/api/version').get('version'), 'model_digest': model['digest']}
        if args.expect_digest and not model['digest'].startswith(args.expect_digest):
            sys.exit(f'model digest {model["digest"]} differs from the expected {args.expect_digest}; nothing was sent')
        chat = server.chat
    if 'G' in conditions and classify is None:
        if not args.prompt_guard:
            sys.exit('condition G needs --prompt-guard')
        from prompt_guard import load
        classify, guard_info = load(args.prompt_guard)
        info = {**(info or {}), 'prompt_guard': guard_info}
    files = {name: sha((ROOT / name).read_bytes()) for name in ['prompts/' + p.name for p in sorted((ROOT / 'prompts').iterdir())] +
             ['harness/' + n for n in ('run.py', 'gate.py', 'score.py', 'anchor.py', 'prompt_guard.py')]}
    run_info = {'schema': 'm-anchor-exp4-run/draft-1', 'status': 'draft harness, before freeze', 'config': CONFIG, 'conditions': conditions, 'reps': args.reps,
                'cases_file': str(args.cases), 'cases_sha256': sha(args.cases.read_bytes()), 'cases': len(cases), 'files_sha256': files, 'environment': info,
                'python': sys.version, 'started_at': time.time()}
    previous = args.out / 'run-info.json'
    if previous.exists():
        old = json.loads(previous.read_text(encoding='utf-8'))
        same = all(old.get(k) == run_info[k] for k in ('config', 'cases_sha256', 'files_sha256'))
        if not same:
            sys.exit('this folder holds a run with different settings, cases or code; use a new --out folder')
        run_info['resumed_from'] = old.get('started_at')
    previous.write_text(json.dumps(run_info, ensure_ascii=False, indent=1) + '\n', encoding='utf-8', newline='\n')

    calls, episodes, stopped = Calls(args.out / 'calls.jsonl', chat), [], None
    try:
        for rep in range(args.reps):
            for index, case in enumerate(cases):
                scores = None
                if classify is not None and 'G' in conditions:
                    scores = {d['id']: classify(d['text']) for d in case['documents'] if d['kind'] in UNTRUSTED}
                episodes += run_trial(case, index, rep, conditions, calls, scores)
                print(f'r{rep} {case["case_id"]}: ' + ', '.join(f'{e["condition"]}={e["final_cause"]}/{e["score"]["payments"]}' for e in episodes[-len(conditions):]), flush=True)
    except TransportDown as error:
        stopped = f'model server not reachable at {error}; check it and run the same command again to continue'
    (args.out / 'episodes.jsonl').write_text(''.join(json.dumps(e, ensure_ascii=False) + '\n' for e in episodes), encoding='utf-8', newline='\n')
    summary = summarize(episodes, calls, len(cases) * args.reps)
    summary['stopped'] = stopped
    (args.out / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=1) + '\n', encoding='utf-8', newline='\n')
    print_summary(summary)
    if stopped:
        sys.exit(stopped)
    return summary


if __name__ == '__main__':
    main()
