"""Sequential device dispatch and durable receipts, independent of model planning."""
import hashlib
import json
from dataclasses import dataclass
from maoer_agent.agent.state import TaskConflict, READ_ONLY
from maoer_agent.tools.results import checked_result
from maoer_agent.memory.history import PLAYBACK_SNAPSHOT_TOOLS


@dataclass(frozen=True)
class Submission:
    run: dict
    resume: list | None = None


class ToolDispatcher:
    def __init__(self, store, get_run):
        self.store, self.get_run = store, get_run

    def advance_batch(self, run_id, calls):
        """Persist one pending device call, or return a complete ordered batch.

        The graph stays interrupted for the whole model batch. Every receipt
        commits separately, so a restart can reconstruct the cursor without
        asking the phone to execute a completed operation again.
        """
        run = self.get_run(run_id)
        by_id = {r['call_id']: r for r in run['results']}
        run['batch'] = calls
        ledger = {c['id']: c for c in run.get('tool_ledger', [])}
        ledger.update({c['id']: c for c in calls})
        run['tool_ledger'] = list(ledger.values())
        for index, call in enumerate(calls):
            if call['id'] in by_id:
                continue
            if run.get('control_blocked') and call['name'] not in READ_ONLY:
                result = {'call_id': call['id'], 'result': {
                    'status': 'error', 'executed': False, 'reason': 'previous_control_not_confirmed'}}
                run['results'].append(result)
                by_id[call['id']] = result
                continue
            run.update(status='awaiting_tools', calls=[call], text='', retry_after=0,
                       batch_completed=index, batch_total=len(calls))
            self.store.put(run)
            return None
        run.update(status='generating', calls=[], batch_completed=len(calls), batch_total=len(calls))
        self.store.put(run)
        return [by_id[c['id']] for c in calls]


    def claim(self, run_id, call_id):
        run = self.get_run(run_id)
        if run['status'] != 'awaiting_tools' or call_id not in [c['id'] for c in run['calls']]:
            raise TaskConflict('tool_not_pending')
        if call_id not in run['claimed']:
            run['claimed'].append(call_id)
            self.store.put(run)
        return run


    def submit(self, run_id, results, *, busy=False):
        run = self.get_run(run_id)
        if len(results) != 1: raise TaskConflict('single_result_required')
        recovered = results[0].get('recovered', False)
        # Recovery is transport metadata, not part of an immutable execution receipt.
        results = [{'call_id': r['call_id'], 'result': r['result']} for r in results]
        fingerprint = hashlib.sha256(json.dumps(results, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        key = ','.join(r['call_id'] for r in results)
        if key in run['receipts']:
            if run['receipts'][key] != fingerprint: raise TaskConflict('tool_result_changed')
            return Submission(run)
        if run['status'] == 'cancelled':
            # A claimed operation can finish just after cancellation. Preserve
            # its actual receipt, but never resume the graph or dispatch more.
            if key not in run['claimed'] or key not in {c['id'] for c in run.get('batch', [])}:
                raise TaskConflict('tool_not_pending')
            run['receipts'][key] = fingerprint
            call = next(c for c in run['batch'] if c['id'] == key)
            run.setdefault('raw_results', {})[key] = results[0]['result']
            run['results'].append({'call_id': key, 'result': checked_result(call, results[0]['result'])})
            return Submission(self.store.put(run))
        if run['status'] != 'awaiting_tools' or busy: raise TaskConflict('tool_not_pending')
        if [r['call_id'] for r in results] != [c['id'] for c in run['calls']]: raise TaskConflict('tool_id_mismatch')
        if any(r['call_id'] not in run['claimed'] for r in results): raise TaskConflict('tool_not_claimed')
        run['receipts'][key] = fingerprint
        raw = results[0]['result']
        run.setdefault('raw_results', {})[key] = raw
        checked = checked_result(run['calls'][0], raw)
        if run['calls'][0]['name'] in PLAYBACK_SNAPSHOT_TOOLS:
            run['playback_refresh_pending'] = recovered
            if recovered:
                run['recovered_playback_calls'] = list(dict.fromkeys(run.get('recovered_playback_calls', []) + [key]))
        run['results'].append({'call_id': key, 'result': checked})
        if run['calls'][0]['name'] not in READ_ONLY and checked.get('status') != 'ok':
            run['control_blocked'] = True
        self.store.put(run)
        completed = self.advance_batch(run_id, run.get('batch', run['calls']))
        return Submission(self.get_run(run_id), completed)
