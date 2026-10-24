"""Offline regressions for bridge slash commands: no network, no model, no QQ send."""
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '_experiments/qqbot/src'))

from qqbot_bridge.app import Service
from qqbot_bridge.commands import Command, CommandWorker, execute, parse_command, render_context
from qqbot_bridge.gateway import GatewayError
from qqbot_bridge.security import private_key


STATS = {
    'conversation_id': 'conv_demo',
    'title': 'QQ 群',
    'model_key': 'demo-model',
    'context_window': 1000000,
    'context_limit': 250000,
    'context_limit_source': 'deep_compress',
    'compression_count': 3,
    'last_compression_at': '2026-10-24T21:03:12.456789',
    'current_context_tokens': 48213,
    'message_count': 128,
    'total_input_tokens': 1234567,
    'total_output_tokens': 56789,
    'total_tokens': 1291356,
    'total_cached_input_tokens': 1100000,
    'cache_exempt_input_tokens': 0,
    'updated_at': '2026-10-24T21:30:12',
    'costs': {'currency': 'USD', 'total_usd': '1.23', 'partial': False, 'has_priced': True,
              'exchange_rate': {'usd_cny': 7.1, 'source': 'test'},
              'actors': [{'id': 'main', 'kind': 'main', 'cost_usd': '1.22'},
                         {'id': 'task_1', 'kind': 'sub_agent', 'cost_usd': '0.01'}]},
}


class ParseTests(unittest.TestCase):
    def test_plain_slash(self):
        command = parse_command('/context')
        self.assertEqual(command.name, 'context')
        self.assertEqual(command.args, [])

    def test_full_width_slash_and_at_suffix(self):
        self.assertEqual(parse_command('／context').name, 'context')
        self.assertEqual(parse_command('/context@astrion_bot').name, 'context')

    def test_leading_space_and_arguments(self):
        command = parse_command('   /context now')
        self.assertEqual((command.name, command.args), ('context', ['now']))

    def test_non_command_text_is_ignored(self):
        self.assertIsNone(parse_command('hello /context'))
        self.assertIsNone(parse_command('/'))
        self.assertIsNone(parse_command(''))
        self.assertIsNone(parse_command(None))


class RenderTests(unittest.TestCase):
    def test_context_report_has_ratio_and_cache_hit(self):
        text = render_context(STATS, 'conv_demo')
        self.assertIn('模型：demo-model · 窗口 1,000,000', text)
        self.assertIn('48,213 / 250,000（19.3% · 自动深度压缩阈值）', text)
        self.assertIn('缓存命中：1,100,000（89.1%）', text)
        self.assertIn('累计输出：56,789', text)
        self.assertIn('$1.23', text)
        self.assertIn('含子智能体 $0.01', text)
        self.assertIn('约 ¥8.73', text)
        self.assertIn('消息：128 条', text)
        self.assertIn('深度压缩：3 次（最近 2026-10-24 21:03）', text)
        self.assertIn('更新：2026-10-24 21:30:12', text)

    def test_cache_denominator_excludes_exempt_input(self):
        stats = dict(STATS, total_input_tokens=1000, total_cached_input_tokens=400,
                     cache_exempt_input_tokens=600)
        self.assertIn('缓存命中：400（100.0%）', render_context(stats, 'conv_demo'))
        stats = dict(STATS, total_input_tokens=600, total_cached_input_tokens=0,
                     cache_exempt_input_tokens=600)
        self.assertIn('（样本不足）', render_context(stats, 'conv_demo'))

    def test_zero_compression_has_no_timestamp(self):
        text = render_context(dict(STATS, compression_count=0), 'conv_demo')
        self.assertIn('深度压缩：0 次', text)
        self.assertNotIn('最近', text)

    def test_missing_limit_falls_back_to_model_window(self):
        stats = {key: value for key, value in STATS.items()
                 if key not in ('context_limit', 'context_limit_source')}
        self.assertIn('48,213 / 1,000,000（4.8%）', render_context(stats, 'conv_demo'))

    def test_missing_window_and_partial_costs_are_explicit(self):
        stats = dict(STATS, context_window=None,
                     costs={'total_usd': '0.5', 'partial': True, 'has_priced': True, 'actors': []})
        text = render_context(stats, 'conv_demo')
        self.assertNotIn('窗口', text)
        self.assertIn('部分请求未计价', text)

    def test_empty_stats_reports_unavailable(self):
        self.assertIn('未取到该对话的上下文统计', render_context({}, 'conv_demo'))

    def test_unknown_command_lists_available(self):
        reply = execute(Command('nope', []), gateway=Mock(), conversation_id='conv_demo')
        self.assertIn('未知指令：/nope', reply)
        self.assertIn('/context', reply)


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.service = Service({'app_secret': 'fake-secret', 'app_id': '123', 'group_id': 'group',
                                'data_dir': str(self.root / 'service'),
                                'astrion_url': 'http://127.0.0.1:1',
                                'token_file': str(self.root / 'fake-token'), 'workspace_id': 'test',
                                'conversation_id': 'conv_demo', 'workspace_path': str(self.root)})
        self.gateway = Mock()
        self.gateway.token_stats.return_value = STATS
        self.service.commands.gateway = self.gateway

    def tearDown(self):
        self.tmp.cleanup()

    def webhook(self, content, qq_id='cmd1'):
        raw = json.dumps({'op': 0, 't': 'GROUP_AT_MESSAGE_CREATE',
                          'd': {'group_openid': 'group', 'id': qq_id, 'content': content,
                                'timestamp': '2026-10-24T21:31:00+08:00',
                                'author': {'member_openid': 'member'}}}).encode()
        timestamp = str(int(time.time()))
        signature = private_key('fake-secret').sign(timestamp.encode() + raw).hex()
        return self.service.process(raw, {'X-Bot-Appid': '123',
                                          'X-Signature-Timestamp': timestamp,
                                          'X-Signature-Ed25519': signature})

    def test_command_is_not_forwarded_to_agent(self):
        self.assertEqual(self.webhook('/context'), (200, {'op': 12}))
        self.assertEqual(self.service.store.counts()['inbox'], {})
        self.assertEqual(self.service.commands.pending(), 1)

    def test_command_reply_goes_to_outbox(self):
        self.webhook('/context')
        command, message = self.service.commands.queue.get_nowait()
        self.service.commands.handle(command, message)
        self.gateway.token_stats.assert_called_once_with()
        row = self.service.store.next_output()
        self.assertEqual(row['kind'], 'text')
        self.assertIn('📊 当前对话上下文', row['content'])
        self.assertIn('缓存命中', row['content'])

    def test_gateway_failure_is_reported_not_raised(self):
        self.gateway.token_stats.side_effect = GatewayError(503, 'backend down')
        self.webhook('/context')
        command, message = self.service.commands.queue.get_nowait()
        self.service.commands.handle(command, message)
        row = self.service.store.next_output()
        self.assertIn('查询失败（Astrion HTTP 503）', row['content'])

    def test_unknown_command_reply_never_reaches_agent(self):
        self.webhook('/unknown')
        command, message = self.service.commands.queue.get_nowait()
        self.service.commands.handle(command, message)
        self.assertIn('可用指令：/context', self.service.store.next_output()['content'])
        self.assertEqual(self.service.store.counts()['inbox'], {})

    def test_plain_message_still_queues_for_agent(self):
        self.assertEqual(self.webhook('你好'), (200, {'op': 12}))
        self.assertEqual(self.service.store.counts()['inbox'], {'queued': 1})
        self.assertEqual(self.service.commands.pending(), 0)


if __name__ == '__main__':
    unittest.main()
