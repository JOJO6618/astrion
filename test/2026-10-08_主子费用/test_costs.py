"""Offline regressions: synthetic models, temporary runtime, no real conversations."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
import json
import os
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
_RUNTIME = tempfile.TemporaryDirectory(dir=Path(__file__).parent, prefix='isolated-')
RUNTIME = Path(_RUNTIME.name)
os.environ.update(ASTRION_IGNORE_DOTENV='1', ASTRION_DATA_ROOT=str(RUNTIME),
                  DATA_DIR=str(RUNTIME / 'data'), LOGS_DIR=str(RUNTIME / 'logs'),
                  DEPLOY_CONFIG_DIR=str(RUNTIME / 'deploy'), TERMINAL_SANDBOX_MODE='host',
                  USER_SPACE_DIR=str(RUNTIME / 'users'), API_USER_SPACE_DIR=str(RUNTIME / 'api'))

from modules.api_pricing import calculate_cost, capture_price, clean_rates
from modules.conversation_costs import record_request, summarize, actor_statistics
from modules import exchange_rates, modelsdev_registry
from modules.sub_agent.usage import apply_usage, restore_statistics
from utils.context_manager.token_mixin import TokenMixin

PRICE = {'model_key': 'synthetic/a', 'billing': 'metered',
         'rates': {'input': 2, 'cache_read': 0.2, 'output': 8}}
USAGE = {'prompt_tokens': 1000, 'completion_tokens': 100,
         'prompt_tokens_details': {'cached_tokens': 400}}


class PricingTests(unittest.TestCase):
    def test_three_categories_no_double_counting(self):
        result = calculate_cost(USAGE, PRICE)
        self.assertEqual(Decimal(result['cost_usd']), Decimal('0.00208'))
        self.assertEqual(result['cached_input_tokens'], 400)

    def test_deepseek_and_responses_shapes(self):
        for usage in ({'prompt_tokens': 1000, 'completion_tokens': 100, 'prompt_cache_hit_tokens': 400},
                      {'input_tokens': 1000, 'output_tokens': 100, 'input_tokens_details': {'cached_tokens': 400}}):
            self.assertEqual(calculate_cost(usage, PRICE)['cost_usd'], '0.00208')

    def test_zero_prices_are_not_missing(self):
        price = {**PRICE, 'rates': dict(input=0, cache_read=0, output=0)}
        self.assertEqual(calculate_cost(USAGE, price)['status'], 'priced')
        self.assertEqual(calculate_cost(USAGE, price)['cost_usd'], '0.0')

    def test_missing_cache_rate_not_silently_free(self):
        price = {**PRICE, 'rates': dict(input=2, output=8)}
        self.assertEqual(calculate_cost(USAGE, price)['status'], 'unpriced')
        self.assertIsNone(calculate_cost(USAGE, price)['cost_usd'])
        self.assertEqual(calculate_cost({'prompt_tokens': 100, 'completion_tokens': 1}, price)['status'], 'priced')

    def test_cache_write_price_ignored(self):
        self.assertEqual(calculate_cost(USAGE, PRICE), calculate_cost(USAGE, {**PRICE, 'rates': {**PRICE['rates'], 'cache_write': 900}}))

    def test_invalid_prices_rejected(self):
        self.assertEqual(clean_rates(dict(input=-1, output=float('nan'), cache_read=True)), {})

    def test_missing_usage_is_unpriced_not_zero_cost(self):
        self.assertEqual(calculate_cost(None, PRICE)['status'], 'unpriced')
        self.assertIsNone(calculate_cost(None, PRICE)['cost_usd'])

    def test_currency_setting_persists_and_invalid_value_defaults(self):
        from modules.personalization_manager import save_personalization_config, load_personalization_config
        with tempfile.TemporaryDirectory(dir=RUNTIME) as path:
            saved = save_personalization_config(path, {'display_currency': 'CNY'})
            self.assertEqual(saved['display_currency'], 'CNY')
            self.assertEqual(load_personalization_config(path)['display_currency'], 'CNY')
            self.assertEqual(save_personalization_config(path, {'display_currency': 'invalid'})['display_currency'], 'USD')

    def test_subscription_has_no_fabricated_api_charge(self):
        profiles = {'synthetic/sub': {'responses_auth': 'codex_oauth'}}
        with patch('config.model_profiles.get_registered_model_profiles', return_value=profiles):
            price = capture_price('synthetic/sub', refresh=False)
        self.assertEqual(calculate_cost(USAGE, price)['status'], 'subscription')
        self.assertIsNone(calculate_cost(USAGE, price)['cost_usd'])

    def test_price_is_provider_specific(self):
        profiles = {'synthetic/a': {'pricing': {'rates': PRICE['rates']}}}
        with patch('config.model_profiles.get_registered_model_profiles', return_value=profiles):
            self.assertEqual(capture_price('other/a', refresh=False)['rates'], {})

    def test_slim_snapshot_retains_prices_and_timestamp(self):
        full = {'synthetic': {'models': {'a': {'cost': {**PRICE['rates'], 'cache_write': 9}}}}}
        slim = modelsdev_registry.slim_full_dump(full, {'synthetic'})
        self.assertEqual(slim['version'], 2)
        self.assertEqual(slim['providers']['synthetic']['models']['a']['cost'], PRICE['rates'])
        with patch.object(modelsdev_registry, '_current_data', return_value=slim):
            meta = modelsdev_registry.get_model_meta({'modelsdev_key': 'synthetic'}, 'a')
        self.assertEqual(meta['pricing']['fetched_at'], slim['fetched_at'])


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=RUNTIME)
        self.data = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def record(self, request, actor='main', price=PRICE, historical=False):
        return record_request(self.data, 'conv_1', request, actor, actor, 'main' if actor == 'main' else 'sub_agent',
                              calculate_cost(USAGE, price), historical_unpriced=historical)

    def test_main_plus_each_child_and_duplicate_commit(self):
        self.record('a')
        self.record('b', 'child1')
        self.record('c', 'child2')
        self.assertFalse(self.record('b', 'child1'))
        summary = summarize(self.data, 'conv_1')
        self.assertEqual(Decimal(summary['total_usd']), Decimal('0.00624'))
        self.assertEqual(len(summary['actors']), 3)

    def test_parallel_writers_preserve_all_entries(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda i: self.record(str(i), f'child{i % 3}'), range(24)))
        summary = summarize(self.data, 'conv_1')
        self.assertEqual(Decimal(summary['total_usd']), Decimal('0.00208') * 24)
        self.assertEqual(sum(a['requests'] for a in summary['actors']), 24)

    def test_switch_model_keeps_old_price_snapshot(self):
        self.record('a')
        self.record('b', price={**PRICE, 'model_key': 'synthetic/b', 'rates': dict(input=4, cache_read=0.4, output=16)})
        ledger = json.loads((self.data / 'api_costs/conv_1.json').read_text())
        self.assertEqual(ledger['requests']['a']['price']['rates']['input'], 2)
        self.assertEqual(Decimal(summarize(self.data, 'conv_1')['total_usd']), Decimal('0.00624'))

    def test_old_history_and_unpriced_requests_are_partial(self):
        self.record('a', historical=True)
        self.record('b', 'child', price={**PRICE, 'rates': {}})
        self.assertTrue(summarize(self.data, 'conv_1')['partial'])
        self.assertEqual(summarize(self.data, 'conv_1')['total_usd'], '0.00208')

    def test_untouched_legacy_history_not_reported_as_free(self):
        result = summarize(self.data, 'conv_1', main_input=1000)
        self.assertTrue(result['partial'])
        self.assertFalse(result['has_priced'])

    def test_new_child_without_usage_is_not_legacy_history(self):
        result = summarize(self.data, 'conv_1', sub_agents=[{'task_id': 'child', 'cost_tracking_version': 1}])
        self.assertFalse(result['partial'])

    def test_conversation_isolation_and_invalid_path(self):
        self.record('a')
        self.assertEqual(summarize(self.data, 'conv_2')['total_usd'], '0')
        with self.assertRaises(ValueError):
            summarize(self.data, '../other')

    def agent(self):
        return SimpleNamespace(
            task_id='child', task_record={'conversation_id': 'conv_1'}, model_key='synthetic/a', display_name='Worker_1',
            manager=SimpleNamespace(data_dir=self.data),
            stats={'token_usage': {'prompt': 0, 'completion': 0, 'total': 0, 'cached_input': 0},
                   'current_context_tokens': 0, 'compress_round': 0},
            stats_file=self.data / 'stats.json', conversation_file=self.data / 'conversation.json',
            output_file=self.data / 'output.json', current_context_tokens=0, _compress_round=0,
            _request_price=PRICE, _cost_request_id='request-child')

    def test_child_stream_extracts_nested_actual_usage(self):
        import asyncio
        from modules.sub_agent.task import SubAgentTask
        class Client:
            async def chat(self, *args, **kwargs):
                yield {'choices': [{'delta': {'content': 'synthetic response'}}]}
                yield {'response_metadata': {'token_usage': {
                    'input_tokens': 1000, 'output_tokens': 100,
                    'input_tokens_details': {'cached_tokens': 400}}}}
        agent = SimpleNamespace(_soft_stop=False, _cancelled=False, messages=[])
        result = asyncio.run(SubAgentTask._call_model(agent, Client(), 'synthetic/a', []))
        self.assertEqual(result[3]['cached_input_tokens'], 400)
        self.assertEqual(result[3]['prompt_tokens'], 1000)

    def test_main_missing_usage_does_not_reset_context_or_fake_free_cost(self):
        manager = SimpleNamespace(get_token_statistics=lambda _: {'total_input_tokens': 200})
        context = SimpleNamespace(data_dir=self.data, current_conversation_id='conv_1',
                                  conversation_manager=manager, safe_broadcast_token_update=lambda: None)
        TokenMixin.record_unavailable_usage(context, price=PRICE, request_id='missing')
        summary = summarize(self.data, 'conv_1')
        self.assertTrue(summary['partial'])
        self.assertFalse(summary['has_priced'])
        self.assertEqual(summary['actors'][0]['unpriced_requests'], 1)

    def test_child_usage_persisted_and_restored(self):
        agent = self.agent()
        apply_usage(agent, USAGE)
        restored = self.agent()
        restore_statistics(restored)
        self.assertEqual(restored.stats['token_usage'], dict(prompt=1000, completion=100, total=1100, cached_input=400))
        self.assertEqual(restored.current_context_tokens, 1000)
        self.assertEqual(restored.stats['cost_usd'], '0.00208')
        apply_usage(agent, USAGE)
        self.assertEqual(agent.stats['token_usage']['prompt'], 1000)

    def test_child_missing_usage_preserves_context_and_marks_partial(self):
        agent = self.agent()
        agent.current_context_tokens = 300
        agent.stats['current_context_tokens'] = 300
        apply_usage(agent, None)
        self.assertEqual(agent.current_context_tokens, 300)
        self.assertTrue(summarize(self.data, 'conv_1')['partial'])

    def test_legacy_child_usage_is_preserved_and_new_cost_is_partial(self):
        agent = self.agent()
        agent.stats['token_usage'].update(prompt=200, completion=50, total=250)
        apply_usage(agent, USAGE)
        restored = self.agent()
        restore_statistics(restored)
        self.assertEqual(restored.stats['token_usage']['prompt'], 1200)
        self.assertEqual(restored.stats['token_usage']['total'], 1350)
        self.assertTrue(summarize(self.data, 'conv_1')['partial'])

    def test_ledger_repairs_child_after_stats_write_interrupted(self):
        agent = self.agent()
        with patch('modules.sub_agent.usage.atomic_write_json', side_effect=OSError('synthetic interruption')):
            with self.assertRaises(OSError):
                apply_usage(agent, USAGE)
        restored = self.agent()
        restore_statistics(restored)
        self.assertEqual(restored.stats['token_usage']['total'], 1100)
        self.assertEqual(restored.current_context_tokens, 1000)

    def test_compression_zero_does_not_reset_cumulative_usage(self):
        agent = self.agent()
        apply_usage(agent, USAGE)
        stats = json.loads(agent.stats_file.read_text())
        stats['current_context_tokens'] = 0
        agent.stats_file.write_text(json.dumps(stats))
        restored = self.agent()
        restore_statistics(restored)
        self.assertEqual(restored.current_context_tokens, 0)
        self.assertEqual(restored.stats['token_usage']['prompt'], 1000)

    def test_main_duplicate_and_ledger_only_commit_repair(self):
        counts = dict(total_input_tokens=0, total_output_tokens=0, total_tokens=0, total_cached_input_tokens=0)
        fail = [False]
        def update(_, inp, out, total, **kwargs):
            if fail[0]:
                return False
            counts['total_input_tokens'] += inp
            counts['total_output_tokens'] += out
            counts['total_tokens'] += total
            counts['total_cached_input_tokens'] += kwargs['cached_input_tokens']
            return True
        manager = SimpleNamespace(get_token_statistics=lambda _: dict(counts), update_token_statistics=update)
        context = SimpleNamespace(data_dir=self.data, current_conversation_id='conv_1', conversation_manager=manager,
                                  _increment_workspace_token_totals=lambda *args: None, safe_broadcast_token_update=lambda: None)
        self.assertTrue(TokenMixin.apply_usage_statistics(context, USAGE, price=PRICE, request_id='m1'))
        self.assertTrue(TokenMixin.apply_usage_statistics(context, USAGE, price=PRICE, request_id='m1'))
        self.assertEqual(counts['total_input_tokens'], 1000)
        fail[0] = True
        self.assertFalse(TokenMixin.apply_usage_statistics(context, USAGE, price=PRICE, request_id='m2'))
        fail[0] = False
        self.assertTrue(TokenMixin.apply_usage_statistics(context, USAGE, price=PRICE, request_id='m2'))
        self.assertEqual(counts['total_input_tokens'], 2000)
        self.assertEqual(Decimal(summarize(self.data, 'conv_1')['total_usd']), Decimal('0.00416'))


class ExchangeRateTests(unittest.TestCase):
    def setUp(self):
        self.path = RUNTIME / 'fx.json'
        if self.path.exists():
            self.path.unlink()
        self.patcher = patch.object(exchange_rates, '_path', return_value=self.path)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_success_saves_currency_date_and_source(self):
        response = SimpleNamespace(raise_for_status=lambda: None, json=lambda: {'base': 'USD', 'date': '2026-10-07', 'rates': {'CNY': 7.1}})
        with patch('httpx.get', return_value=response) as get:
            exchange_rates.refresh_rate()
        data = exchange_rates.get_rate(refresh=False)
        self.assertEqual(data['usd_cny'], 7.1)
        self.assertEqual(data['rate_date'], '2026-10-07')
        self.assertFalse(data['stale'])
        self.assertIn('base=USD&symbols=CNY', get.call_args.args[0])

    def test_failed_or_invalid_refresh_preserves_prior_rate(self):
        self.path.write_text(json.dumps({'usd_cny': 7, 'fetched_at': '2020-01-01', 'rate_date': '2020-01-01'}))
        for response in (RuntimeError('offline'), SimpleNamespace(raise_for_status=lambda: None, json=lambda: {'base': 'EUR', 'rates': {'CNY': 8}})):
            with patch('httpx.get', side_effect=response if isinstance(response, Exception) else None,
                       return_value=response), patch.object(exchange_rates.logger, 'warning'):
                exchange_rates.refresh_rate()
            self.assertEqual(exchange_rates.get_rate(refresh=False)['usd_cny'], 7)

    def test_daily_cache_does_not_start_another_fetch(self):
        from datetime import datetime, timezone
        self.path.write_text(json.dumps({'usd_cny': 7, 'fetched_at': datetime.now(timezone.utc).isoformat()}))
        with patch.object(exchange_rates.threading, 'Thread') as thread:
            exchange_rates.get_rate()
            thread.assert_not_called()

    def test_no_initial_rate_is_explicit(self):
        self.assertNotIn('usd_cny', exchange_rates.get_rate(refresh=False))


if __name__ == '__main__':
    try:
        unittest.main()
    finally:
        _RUNTIME.cleanup()
