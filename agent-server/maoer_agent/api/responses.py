"""Version-one public task projection; internal fields do not become API by default."""
RUN_FIELDS = frozenset((
    'id', 'conversation_id', 'status', 'text', 'calls', 'results', 'claimed',
    'error', 'error_detail', 'retry_after', 'retry_at', 'version', 'created_at',
    'summary_turns', 'summary_pending', 'phase', 'search_hint', 'route', 'sources',
    'batch_completed', 'batch_total', 'control_blocked',
))


def run_response(run):
    return {key: value for key, value in run.items() if key in RUN_FIELDS}
