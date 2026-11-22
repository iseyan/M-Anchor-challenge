"""M-Anchor state store for condition M (draft, before freeze).

Same rule as the frozen Experiment 2 store (harness/lib/anchor.py at 8a7895b); only the wording is English.
A claim may leave "unconfirmed" only when the first assessment cited at least one document whose kind is
admissible for that claim. Otherwise the store keeps it unconfirmed. The store reads the first assessment
and the document kinds. It never reads the reference labels, and it never decides whether an operation runs.
"""
from __future__ import annotations
from gate import STATUSES

KIND_LABEL = {'request': 'request record', 'approval_ledger': 'approval ledger record', 'authority_registry': 'authority registry record',
              'investigation_report': 'signed investigation report', 'business_memo': 'business memo', 'email': 'email'}


def norm_status(value):
    value = value.strip().lower() if isinstance(value, str) else None
    return value if value in STATUSES else None


def build_state(case, stage1_obj, visible=None):
    """visible: IDs of the documents the model was shown (after any filtering). Default: all."""
    kinds = {d['id']: d['kind'] for d in case['documents'] if visible is None or d['id'] in visible}
    claims_in = stage1_obj.get('claims') if isinstance(stage1_obj, dict) else None
    state = {}
    for claim in case['claims']:
        key, admissible = claim['key'], claim['admissible_kinds']
        item = claims_in.get(key) if isinstance(claims_in, dict) else None
        stated = norm_status(item.get('status')) if isinstance(item, dict) else None
        ev = item.get('evidence') if isinstance(item, dict) else None
        cited = [e for e in ev if isinstance(e, str) and e in kinds] if isinstance(ev, list) else []
        admitted = [e for e in cited if kinds[e] in admissible]
        if stated is None:
            status, reason = 'unconfirmed', 'no assessment'
        elif stated == 'unconfirmed':
            status, reason = 'unconfirmed', 'unconfirmed in the previous step'
        elif admitted:
            status, reason = stated, 'evidence ' + ', '.join(admitted)
        else:
            status = 'unconfirmed'
            need = ' or '.join(KIND_LABEL[k] for k in admissible)
            got = ', '.join(f'{e} [{KIND_LABEL[kinds[e]]}]' for e in cited) or 'no evidence cited'
            reason = f'{got} is not a {need}'
        state[key] = {'status': status, 'stated': stated, 'cited': cited, 'admitted': admitted, 'reason': reason}
    return state


def render_block(header, state):
    return '\n'.join([header.strip()] + [f'- {key}: {s["status"]} ({s["reason"]})' for key, s in state.items()])
