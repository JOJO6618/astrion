"""Normalized, durable sub-agent usage and cost commits; safe across restoration."""
from __future__ import annotations

from copy import deepcopy
import json
import logging
from typing import Any

from modules.api_pricing import calculate_cost
from modules.conversation_costs import actor_statistics, record_request
from utils.atomic_io import atomic_write_json
from utils.token_usage import normalize_usage_payload

logger = logging.getLogger(__name__)


def restore_statistics(agent: Any) -> None:
    """Restore the newest saved statistics before the restored task can run."""
    candidates = []
    for path in (agent.conversation_file, agent.output_file, agent.stats_file):
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            stats = data if path == agent.stats_file else data.get('stats', {})
            if isinstance(stats, dict) and isinstance(stats.get('token_usage'), dict):
                candidates.append((int(stats['token_usage'].get('total', 0)), stats))
        except (OSError, ValueError, TypeError):
            continue
    if candidates:
        saved = max(candidates, key=lambda item: item[0])[1]
        agent.stats.update(deepcopy(saved))
        agent.stats['token_usage'].setdefault('cached_input', 0)
        agent.current_context_tokens = int(agent.stats.get('current_context_tokens', 0))
        agent._compress_round = int(agent.stats.get('compress_round', 0))
    # Ledger is committed before the stats file. Recover a costed response if the
    # process died between those writes. Legacy history stays explicitly unpriced.
    conversation_id = agent.task_record.get('conversation_id')
    if conversation_id:
        actor = actor_statistics(agent.manager.data_dir, conversation_id, agent.task_id)
        if actor:
            usage = agent.stats['token_usage']
            usage.update(prompt=actor['input_tokens'], completion=actor['output_tokens'],
                         total=actor['total_tokens'], cached_input=actor['cached_input_tokens'])
            agent.stats['cost_usd'] = actor['cost_usd']
            # Preserve a saved post-compression zero; otherwise repair a response
            # whose ledger commit preceded the last stats-file save.
            if not candidates or actor['total_tokens'] > max(c[0] for c in candidates):
                agent.current_context_tokens = actor.get('current_context_tokens', 0)
                agent.stats['current_context_tokens'] = agent.current_context_tokens


def apply_usage(agent: Any, usage: Any) -> None:
    tokens = normalize_usage_payload(usage)
    from uuid import uuid4

    price = getattr(agent, '_request_price', {'model_key': agent.model_key, 'rates': {}})
    request_id = getattr(agent, '_cost_request_id', None) or str(uuid4())
    totals = agent.stats['token_usage']
    conversation_id = agent.task_record.get('conversation_id')
    entry = calculate_cost(tokens, price)
    if conversation_id:
        committed = record_request(
            agent.manager.data_dir, conversation_id, request_id, agent.task_id,
            agent.display_name or agent.task_record.get('summary') or agent.task_id,
            'sub_agent', entry, historical_unpriced=bool(totals.get('prompt')),
            baseline={'input_tokens': totals.get('prompt', 0),
                      'output_tokens': totals.get('completion', 0),
                      'total_tokens': totals.get('total', 0),
                      'cached_input_tokens': totals.get('cached_input', 0)},
        )
        if not committed:
            return
    if not tokens:
        atomic_write_json(agent.stats_file, agent.stats)
        return
    totals['prompt'] += tokens['prompt_tokens']
    totals['completion'] += tokens['completion_tokens']
    totals['total'] += tokens['total_tokens']
    totals['cached_input'] = totals.get('cached_input', 0) + entry['cached_input_tokens']
    agent.current_context_tokens = tokens['current_context_tokens']
    agent.stats['current_context_tokens'] = agent.current_context_tokens
    agent.stats['cost_usd'] = actor_statistics(agent.manager.data_dir, conversation_id, agent.task_id)['cost_usd'] if conversation_id else '0'
    atomic_write_json(agent.stats_file, agent.stats)
