"""Sequential device dispatch and durable receipts, independent of model planning."""
import hashlib
import json
from langgraph.types import Command
from task_state import TaskConflict, READ_ONLY


class ToolDispatcher:
    def __init__(self, store, get_run, jobs, launch):
        self.store, self.get_run, self.jobs, self.launch = store, get_run, jobs, launch

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


    def submit(self, run_id, results):
        run = self.get_run(run_id)
        if len(results) != 1: raise TaskConflict('single_result_required')
        fingerprint = hashlib.sha256(json.dumps(results, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        key = ','.join(r['call_id'] for r in results)
        if key in run['receipts']:
            if run['receipts'][key] != fingerprint: raise TaskConflict('tool_result_changed')
            return run
        if run['status'] == 'cancelled':
            # A claimed operation can finish just after cancellation. Preserve
            # its actual receipt, but never resume the graph or dispatch more.
            if key not in run['claimed'] or key not in {c['id'] for c in run.get('batch', [])}:
                raise TaskConflict('tool_not_pending')
            run['receipts'][key] = fingerprint
            run['results'].extend(results)
            return self.store.put(run)
        if run['status'] != 'awaiting_tools' or run_id in self.jobs: raise TaskConflict('tool_not_pending')
        if [r['call_id'] for r in results] != [c['id'] for c in run['calls']]: raise TaskConflict('tool_id_mismatch')
        if any(r['call_id'] not in run['claimed'] for r in results): raise TaskConflict('tool_not_claimed')
        run['receipts'][key] = fingerprint
        run['results'].extend(results)
        if run['calls'][0]['name'] not in READ_ONLY and results[0]['result'].get('status') != 'ok':
            run['control_blocked'] = True
        self.store.put(run)
        completed = self.advance_batch(run_id, run.get('batch', run['calls']))
        if completed is not None:
            self.launch(run_id, Command(resume=completed))
        return self.get_run(run_id)
