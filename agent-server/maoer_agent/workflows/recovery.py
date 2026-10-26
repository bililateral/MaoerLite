"""Remaining authorized effects and recovered observations, never replaying controls."""
from collections import Counter
from maoer_agent.tools.definitions import TOOLS
from maoer_agent.memory.history import last_turn
from maoer_agent.workflows.episode_pages import observations


def recovered_selection_reply(route, messages):
    """Keep verified pre-restart execution separate from freshly observed state."""
    actions = route.get('control_actions', [])
    if len(actions) != 1 or actions[0] not in ('next_episode', 'previous_episode'):
        return None
    if any(route.get(k) for k in ('podcast', 'web', 'library')):
        return None
    rows = observations(last_turn(messages))
    effects = [r for c, r in rows if c['name'] == actions[0] and r.get('status') == 'ok'
               and r.get('observation_scope') == 'execution_before_process_restart']
    states = [r for c, r in rows if c['name'] == 'get_playback_state' and r.get('status') == 'ok']
    if len(effects) != 1 or not states:
        return None
    effect, current = effects[0], states[-1]
    if not effect.get('episode_id') or current.get('episode_id') != effect['episode_id']:
        return None
    return {'kind': 'recovered_selection', 'current': current}


def remaining_controls(run, route):
    names = {c['id']: c['name'] for c in run.get('tool_ledger', [])}
    completed = Counter(names[r['call_id']] for r in run.get('results', [])
                        if r['call_id'] in names and r['result'].get('status') == 'ok')
    return Counter(route.get('control_actions', [])) - completed


def recovery_scope(run, route, available, allowed):
    if 'control_actions' not in route and not run.get('recovered_playback_calls'):
        return available, allowed
    changing = {name for name, spec in TOOLS.items() if spec.changes_state}
    remaining = remaining_controls(run, route) if not run.get('control_blocked') else Counter()
    allowed = (allowed - changing) | (set(remaining) & allowed)
    return [d for d in available if d['function']['name'] in allowed], allowed
