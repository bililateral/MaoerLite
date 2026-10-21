"""Remaining control scope after recovered effects, never compensating playback."""
from collections import Counter
from maoer_agent.tools.definitions import TOOLS


def recovery_scope(run, route, available, allowed):
    if not run.get('recovered_playback_calls'):
        return available, allowed
    changing = {name for name, spec in TOOLS.items() if spec.changes_state}
    names = {c['id']: c['name'] for c in run.get('tool_ledger', [])}
    completed = Counter(names[r['call_id']] for r in run.get('results', [])
                        if r['call_id'] in names and r['result'].get('status') == 'ok')
    remaining = Counter(route.get('control_actions', [])) - completed
    allowed = (allowed - changing) | (set(remaining) & allowed)
    return [d for d in available if d['function']['name'] in allowed], allowed
