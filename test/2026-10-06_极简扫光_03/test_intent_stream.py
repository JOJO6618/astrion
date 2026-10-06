from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path
import runpy
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[2]
extract_complete_intent = runpy.run_path(str(ROOT / 'server/tool_intent_stream.py'))['extract_complete_intent']
extract_partial = runpy.run_path(str(ROOT / 'server/chat_flow_runner_helpers.py'))['extract_intent_from_partial']


class CompleteIntentTest(unittest.TestCase):
    def test_waits_for_unescaped_closing_quote(self):
        self.assertIsNone(extract_complete_intent('{"intent":"partial'))
        self.assertIsNone(extract_complete_intent('{"intent":"quoted \\"'))
        self.assertEqual(extract_complete_intent('{"intent":"partial"'), 'partial')

    def test_decodes_unicode_escaped_quotes_and_long_intents(self):
        text = '核对 "内容" 与路径\\文件' + '完整' * 100
        source = json.dumps({'intent': text}, ensure_ascii=True)
        self.assertEqual(extract_complete_intent(source[:-1]), text)
        self.assertEqual(extract_complete_intent('{"intent":""'), '')

    def test_only_top_level_intent_is_used(self):
        self.assertIsNone(extract_complete_intent('{"content":"\\"intent\\":\\"fake\\""'))
        self.assertIsNone(extract_complete_intent('{"nested":{"intent":"fake"}'))
        self.assertEqual(extract_complete_intent('{"nested":{"intent":"fake"}, "intent":"real", "content":'), 'real')

    def test_incomplete_preceding_argument_does_not_fake_completion(self):
        self.assertIsNone(extract_complete_intent('{"content":"unfinished, \\"intent\\": \\"fake'))
        self.assertIsNone(extract_complete_intent('{"intent":null'))
        self.assertIsNone(extract_complete_intent('not JSON'))


class CompleteArgumentsTest(unittest.TestCase):
    def test_complete_arguments_without_intent_release_the_tool_name(self):
        self.assertEqual(extract_complete_intent('{"path":"a"}'), '')
        self.assertIsNone(extract_complete_intent('{"path":"a",}'))
        self.assertIsNone(extract_complete_intent('{"path":"a"}garbage'))


class IntentEventTest(unittest.IsolatedAsyncioTestCase):
    async def collect(self, pieces):
        # Exercise the actual streaming function with its network/logging edges
        # stubbed; keep this regression independent of Flask and deployment data.
        tree = ast.parse((ROOT / 'server/chat_flow_stream_loop.py').read_text())
        function = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef))
        module = ast.Module(body=[ast.ImportFrom(module='__future__',
            names=[ast.alias(name='annotations')], level=0), function], type_ignores=[])
        ast.fix_missing_locations(module)
        namespace = {
            'asyncio': asyncio, 'extract_intent_from_partial': extract_partial,
            'extract_complete_intent': extract_complete_intent,
            'get_stop_flag': lambda *args, **kwargs: None,
            'extract_usage_payload': lambda chunk: None,
            'debug_log': lambda *args: None, 'tr': lambda key, **kwargs: key
        }
        exec(compile(module, 'stream-regression', 'exec'), namespace)
        events = []

        class Client:
            async def chat(self, *args, **kwargs):
                for index, arguments in enumerate(pieces):
                    call = {'index': 0, 'function': {'arguments': arguments}}
                    if index == 0:
                        call.update(id='tool-1')
                        call['function']['name'] = 'write_file'
                    yield {'choices': [{'delta': {'tool_calls': [call]}}]}

        await namespace['run_streaming_attempts'](
            web_terminal=SimpleNamespace(api_client=Client()), messages=[], tools=[],
            sender=lambda name, data: events.append((name, data)), client_sid='client',
            username='test', conversation_id='conversation', current_iteration=1,
            max_api_retries=0, retry_delay_seconds=0, detected_tool_intent={},
            full_response='', tool_calls=[], current_thinking='', detected_tools={},
            last_usage_payload=None, in_thinking=False, thinking_started=False,
            thinking_ended=False, text_started=False, text_has_content=False,
            text_streaming=False, text_chunk_index=0, last_text_chunk_time=None,
            chunk_count=0, reasoning_chunks=0, content_chunks=0, tool_chunks=0,
            last_finish_reason=None, accumulated_response='')
        return events

    async def test_closing_quote_sends_completion_without_a_text_change(self):
        events = await self.collect(['{"intent":"完整', '"', ',"content":"still streaming', '"}'])
        preparing = next(data for name, data in events if name == 'tool_preparing')
        self.assertFalse(preparing['intent_complete'])
        completed = [data for name, data in events if name == 'tool_intent' and data['intent_complete']]
        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0]['intent'], preparing['intent'])

    async def test_first_chunk_can_already_contain_a_complete_intent(self):
        events = await self.collect(['{"intent":"完整", "content":"', 'remaining"}'])
        preparing = next(data for name, data in events if name == 'tool_preparing')
        self.assertTrue(preparing['intent_complete'])
        self.assertEqual(preparing['intent'], '完整')

    async def test_decoded_long_intent_is_sent_without_the_partial_parser_limit(self):
        intent = '核对' * 100
        encoded = json.dumps({'intent': intent, 'path': 'a'}, ensure_ascii=True)
        events = await self.collect([encoded[:50], encoded[50:]])
        completed = [data for name, data in events if name == 'tool_intent' and data['intent_complete']]
        self.assertEqual(completed[-1]['intent'], intent)


if __name__ == '__main__':
    unittest.main()
