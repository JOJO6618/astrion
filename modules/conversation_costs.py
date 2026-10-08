"""One atomic ledger per conversation, shared by main and sub-agent request writers.

Independent of context/history compaction. Request IDs make repeated commits harmless.
The existing token counters retain their main-agent scope.
"""
from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path
import re
from typing import Any

from utils.atomic_io import atomic_write_json
from utils.conversation_manager.locking import directory_lock


def _path(data_dir: str | Path, conversation_id: str) -> Path:
    if not re.fullmatch(r'[A-Za-z0-9_-]+', conversation_id):
        raise ValueError('Invalid conversation identity for cost ledger')
    return Path(data_dir) / 'api_costs' / f'{conversation_id}.json'


def _read(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {'version': 1, 'actors': {}, 'requests': {}}
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data.get('actors'), dict) or not isinstance(data.get('requests'), dict):
        raise ValueError('Invalid conversation cost ledger')
    return data


def record_request(data_dir: str | Path, conversation_id: str, request_id: str,
                   actor_id: str, name: str, kind: str, entry: dict[str, Any],
                   *, historical_unpriced: bool = False,
                   baseline: dict[str, int] | None = None) -> bool:
    from datetime import datetime, timezone
    path = _path(data_dir, conversation_id)
    with directory_lock(path.parent):
        data = _read(path)
        if request_id in data['requests']:
            return False
        actor = data['actors'].setdefault(actor_id, {
            'id': actor_id, 'name': name, 'kind': kind, 'cost_usd': '0',
            'input_tokens': (baseline or {}).get('input_tokens', 0),
            'output_tokens': (baseline or {}).get('output_tokens', 0),
            'cached_input_tokens': (baseline or {}).get('cached_input_tokens', 0),
            'total_tokens': (baseline or {}).get('total_tokens', 0),
            'requests': 0, 'priced_requests': 0, 'unpriced_requests': 0,
            'subscription_requests': 0, 'local_requests': 0,
            'historical_unpriced': historical_unpriced,
        })
        actor['name'] = name
        for key in ('input_tokens', 'output_tokens', 'cached_input_tokens', 'total_tokens'):
            actor[key] = actor.get(key, 0) + entry[key]
        actor['current_context_tokens'] = entry.get('current_context_tokens', entry['input_tokens'])
        actor['requests'] += 1
        actor[f"{entry['status']}_requests"] += 1
        if entry['cost_usd'] is not None:
            actor['cost_usd'] = str(Decimal(actor['cost_usd']) + Decimal(entry['cost_usd']))
        data['requests'][request_id] = {
            'actor_id': actor_id, 'recorded_at': datetime.now(timezone.utc).isoformat(), **entry,
        }
        atomic_write_json(path, data)
        return True


def actor_statistics(data_dir: str | Path, conversation_id: str, actor_id: str) -> dict[str, Any] | None:
    path = _path(data_dir, conversation_id)
    with directory_lock(path.parent):
        return _read(path)['actors'].get(actor_id)


def summarize(data_dir: str | Path, conversation_id: str, *, main_input: int = 0,
              sub_agents: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    path = _path(data_dir, conversation_id)
    with directory_lock(path.parent):
        actors = list(_read(path)['actors'].values())
    if not any(a['id'] == 'main' for a in actors):
        actors.insert(0, {'id': 'main', 'name': '', 'kind': 'main', 'cost_usd': '0',
                         'requests': 0, 'priced_requests': 0, 'unpriced_requests': 0,
                         'historical_unpriced': main_input > 0})
    present = {a['id'] for a in actors}
    for task in sub_agents or []:
        task_id = task.get('task_id')
        if task_id and task_id not in present:
            actors.append({'id': task_id, 'name': task.get('display_name') or task.get('summary') or task_id,
                           'kind': 'sub_agent', 'cost_usd': '0', 'requests': 0,
                           'priced_requests': 0, 'unpriced_requests': 0,
                           'historical_unpriced': not bool(task.get('cost_tracking_version'))})
    actors.sort(key=lambda a: a['kind'] != 'main')
    total = sum((Decimal(a['cost_usd']) for a in actors), Decimal(0))
    partial = any(a.get('unpriced_requests') or a.get('historical_unpriced') for a in actors)
    has_priced = any(a.get('priced_requests') for a in actors)
    non_metered = any(a.get('subscription_requests') or a.get('local_requests') for a in actors)
    return {'currency': 'USD', 'total_usd': str(total), 'partial': partial,
            'has_priced': has_priced, 'non_metered': non_metered, 'actors': actors}
