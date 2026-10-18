"""Bounded model view. Never mutate execution evidence or provider reasoning chains."""
from copy import deepcopy
import json
from maoer_agent.model.client import ModelFailure


def size(messages, tools, output_tokens):
    # A deliberately conservative byte budget, NOT a provider tokenizer.
    return len(json.dumps({'messages': messages, 'tools': tools}, ensure_ascii=False).encode('utf-8')) + output_tokens * 4


def fit_context(messages, tools, limit=240000, output_tokens=4096):
    view = deepcopy(messages)
    before = size(view, tools, output_tokens)
    removed = 0
    # Drop whole old turns only. Current turn and its reasoning/tool pairs stay intact.
    while size(view, tools, output_tokens) > limit:
        starts = [i for i, m in enumerate(view) if m['role'] == 'user']
        if len(starts) < 2: break
        del view[starts[0]:starts[1]]
        removed += 1
    compacted = 0
    can_restore_web = any(t['function']['name'] == 'read_web_page' for t in tools)
    if size(view, tools, output_tokens) > limit and can_restore_web:
        # Only evidence already followed by an assistant response was consumed.
        # Keep all identity/order/status fields; documents restore via existing URL reads.
        last_assistant = max((i for i, m in enumerate(view) if m['role'] == 'assistant'), default=-1)
        for i, message in enumerate(view):
            if size(view, tools, output_tokens) <= limit: break
            if message['role'] != 'tool' or i >= last_assistant: continue
            result = json.loads(message['content'])
            changed = False
            for item in result.get('items') or []:
                # RSS ID reads can refresh to a different snapshot; don't claim they're recoverable.
                if item.get('kind') == 'web' and item.get('document_id') and item.get('source_url') and len(item.get('evidence', '')) > 800:
                    item['evidence'] = item['evidence'][:800]
                    item['evidence_truncated'] = True
                    item['context_excerpt_only'] = True
                    item['restore_note'] = '本模型视图节选；需要原段落时用read_web_page按原URL和offset读取本轮已保存正文。'
                    changed = True
            if changed:
                message['content'] = json.dumps(result, ensure_ascii=False)
                compacted += 1
    if removed:
        view.insert(1, {'role': 'system', 'content': '输入预算移出了部分旧轮次；引用身份仍不代表展示顺序，缺少原列表时不得猜测序号。用户最新要求优先。'})
    after = size(view, tools, output_tokens)
    if after > limit or len(view) > 160 or any(len(m.get('content') or '') > 16000 for m in view):
        raise ModelFailure('context_budget_exhausted', details={'input_bytes': after, 'limit_bytes': limit})
    return view, {'before_bytes': before, 'after_bytes': after, 'limit_bytes': limit,
                  'removed_turns': removed, 'compacted_results': compacted, 'estimate': 'utf8_bytes_with_output_reserve'}
