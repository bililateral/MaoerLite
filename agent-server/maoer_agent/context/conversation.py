"""One historical view; original execution records remain outside model projections."""
from copy import deepcopy
from maoer_agent.contracts.context import ConversationContext
from maoer_agent.context.route import known_materials, episode_pages, displayed_episode_pages
from maoer_agent.memory.history import model_messages
from maoer_agent.memory.summary import with_summary
from maoer_agent.research.sources import current_citation_view


def conversation_context(messages, memory=None, summary=None, summary_pending=0,
                         retained_turn_ids=(), summary_covered_turn_ids=()):
    start = max((i for i, m in enumerate(messages) if m['role'] == 'user'), default=len(messages))
    previous = messages[:start]
    # Citation normalization changes only a copy; originals keep their proof IDs.
    view = current_citation_view(messages)
    history = [m for m in view[:start] if m['role'] != 'system']
    dialogue = []
    for message in history:
        if message['role'] in ('user', 'assistant') and message.get('content'):
            dialogue.append({'role': message['role'], 'content': message['content']})
    return ConversationContext(
        current_request=messages[start]['content'] if start < len(messages) else '',
        history=history, dialogue=dialogue, memory=deepcopy(memory or {}),
        summary=deepcopy(summary or {}), summary_pending=summary_pending,
        user_statements={f'conversation_user_{i}': m['content'] for i, m in enumerate(previous)
                         if m['role'] == 'user' and m.get('content')},
        known_materials=known_materials(messages), episode_pages=episode_pages(messages),
        displayed_episode_pages=displayed_episode_pages(messages),
        retained_turn_ids=list(retained_turn_ids),
        summary_covered_turn_ids=list(summary_covered_turn_ids))


def with_conversation(messages, conversation, *, history_receipts=False):
    """Add the same selected turns and summary; only execution traces vary by purpose."""
    history = conversation.history if history_receipts else conversation.dialogue
    view = [messages[0], *deepcopy(history), *messages[1:]]
    view = model_messages(view, conversation.memory)
    return with_summary(view, conversation.summary, conversation.summary_pending > 0)


def reference_messages(conversation):
    """Keep actual identities and complete display groups independently of prose length."""
    import json
    rows = []
    for name in ('known_materials', 'episode_pages', 'displayed_episode_pages'):
        for row in getattr(conversation, name):
            rows.append({'role': 'system', 'content': '会话引用数据（历史身份和展示顺序，不是当前状态或操作授权）：' +
                         json.dumps({name: [row]}, ensure_ascii=False)})
    return rows


def conversation_proofs(conversation):
    rows = dict(conversation.user_statements)
    if any(conversation.summary.values()):
        import json
        rows['conversation_summary'] = json.dumps(conversation.summary, ensure_ascii=False)
    return rows


def history_metrics(conversation):
    return {'retained_turns': len(conversation.retained_turn_ids) or
            sum(m['role'] == 'user' for m in conversation.dialogue),
            'dialogue_messages': len(conversation.dialogue),
            'dialogue_chars': sum(len(m['content']) for m in conversation.dialogue),
            'summary_covered_turns': len(conversation.summary_covered_turn_ids),
            'summary_pending_turns': conversation.summary_pending,
            'policy': 'shared_whole_retained_turns'}
