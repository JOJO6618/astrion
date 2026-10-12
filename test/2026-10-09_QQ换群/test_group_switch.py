"""Offline one-shot group switching; synthetic IDs, no network or credentials."""
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '_experiments/qq_bridge'))
from app import Service
from arm_group_switch import arm
from group_switch import GroupSwitch
from relay import Relay
from security import private_key
from store import Store


class SwitchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.path = self.root / 'config.json'
        self.config = {
            'group_id': 'old', 'conversation_id': 'same-conversation',
            'group_switch': {'expires_at': time.time() + 3600},
        }
        self.store = Store(self.root / 'bridge.sqlite3', 'old')
        self.switch = GroupSwitch(self.config, self.path, self.store)
        self.switch.save()
        self.gateway = Mock()
        self.gateway.tasks.return_value = []
        self.relay = Relay(self.gateway, Mock(), self.store, self.root)
        self.relay.switch_hook = lambda: self.switch.apply(self.relay)

    def tearDown(self):
        self.tmp.cleanup()

    def message(self, group='new', message_id='m1', content='hello'):
        return {'group_openid': group, 'id': message_id, 'content': content,
                'author': {'member_openid': 'synthetic-member'}}

    def capture(self):
        self.assertEqual(self.switch.incoming(self.message()), 'switch_pending')

    def test_arm_preserves_conversation_and_configuration(self):
        self.config.pop('group_switch')
        self.config['unrelated'] = {'value': 'keep'}
        self.switch.save()
        result = arm(self.path)
        saved = json.loads(self.path.read_text())
        self.assertEqual(result['conversation_id'], 'same-conversation')
        self.assertEqual(saved['group_id'], 'old')
        self.assertEqual(saved['unrelated'], {'value': 'keep'})
        self.assertGreater(saved['group_switch']['expires_at'], time.time() + 3500)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_arm_refuses_to_overwrite_captured_candidate(self):
        self.capture()
        before = self.path.read_bytes()
        with self.assertRaises(ValueError):
            arm(self.path)
        self.assertEqual(self.path.read_bytes(), before)

    def test_other_groups_ignored_without_arming(self):
        self.config.pop('group_switch')
        self.assertEqual(self.switch.incoming(self.message()), 'other_group')
        self.assertFalse(self.switch.apply(self.relay))

    def test_expired_window_ignores_other_groups(self):
        self.config['group_switch']['expires_at'] = time.time() - 1
        self.assertEqual(self.switch.incoming(self.message()), 'other_group')
        self.assertEqual(self.switch.status(), 'bound')

    def test_old_group_works_while_waiting(self):
        self.assertEqual(self.switch.incoming(self.message('old')), 'queued')
        self.assertEqual(self.switch.status(), 'awaiting_new_group')

    def test_single_candidate_duplicate_and_queue_bound(self):
        self.capture()
        self.assertEqual(self.switch.status(), 'binding')
        self.assertEqual(self.switch.incoming(self.message()), 'duplicate')
        self.assertEqual(self.switch.incoming(self.message('third', 'other')), 'other_group')
        for index in range(2, 6):
            self.assertEqual(self.switch.incoming(self.message(message_id=f'm{index}')),
                             'switch_pending')
        self.assertEqual(self.switch.incoming(self.message(message_id='m6')), 'switch_queue_full')
        self.assertEqual(len(self.config['group_switch']['messages']), 5)

    def test_invalid_message_cannot_claim_candidate(self):
        for change in ({'id': ''}, {'content': []}, {'author': 'invalid'}, {'group_openid': 42}):
            message = self.message()
            message.update(change)
            self.assertEqual(self.switch.incoming(message), 'invalid')
        self.assertEqual(self.switch.incoming(self.message(content='  ')), 'empty')
        self.assertNotIn('candidate_group_id', self.config['group_switch'])

    def test_waits_for_old_input(self):
        self.capture()
        self.store.accept(self.message('old', 'old-input'))
        self.assertFalse(self.switch.apply(self.relay))
        row = self.store.next_input()
        self.store.input_state(row['id'], 'forwarding')
        self.assertFalse(self.switch.apply(self.relay))
        self.store.input_state(row['id'], 'accepted')
        self.assertTrue(self.switch.apply(self.relay))

    def test_waits_for_old_output_including_inflight_send(self):
        self.capture()
        self.store.add_output('old-output', 'text', 'old reply')
        self.assertFalse(self.switch.apply(self.relay))
        row = self.store.next_output()
        self.assertFalse(self.switch.apply(self.relay))
        self.store.output_state(row['id'], 'sent')
        self.assertTrue(self.switch.apply(self.relay))

    def test_running_task_blocks_scheduler_switch(self):
        self.capture()
        self.gateway.tasks.return_value = [
            {'task_id': 'active', 'status': 'running', 'created_at': time.time()}]
        self.gateway.task.return_value = {'status': 'running', 'events': [], 'next_offset': 0}
        self.relay.tick()
        self.assertEqual(self.store.group_id, 'old')
        self.assertEqual(self.switch.status(), 'binding')
        self.gateway.start.assert_not_called()

    def test_pending_guidance_runs_before_switch(self):
        self.capture()
        self.relay.pending = [{'id': 'queued-id', 'text': 'old guidance'}]
        self.gateway.start.return_value = 'continued'
        self.relay.tick()
        self.assertEqual(self.store.group_id, 'old')
        self.gateway.start.assert_called_once_with('old guidance', queued_id='queued-id')

    def test_switch_keeps_conversation_and_accepts_first_new_message(self):
        self.capture()
        self.relay.tick()
        self.gateway.start.assert_not_called()
        saved = json.loads(self.path.read_text())
        self.assertEqual(saved['group_id'], 'new')
        self.assertEqual(saved['conversation_id'], 'same-conversation')
        self.assertNotIn('group_switch', saved)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.store.next_input()['qq_id'], 'm1')
        self.assertEqual(self.switch.incoming(self.message('old', 'old-later')), 'other_group')
        self.assertEqual(self.switch.incoming(self.message('third', 'third-later')), 'other_group')
        self.assertEqual(self.switch.status(), 'bound')
        self.gateway.start.return_value = 'new-task'
        self.relay.tick()
        self.gateway.start.assert_called_once()

    def test_capture_save_failure_does_not_claim_candidate(self):
        with patch.object(self.switch, 'save', side_effect=OSError('synthetic failure')):
            with self.assertRaises(OSError):
                self.switch.incoming(self.message())
        self.assertNotIn('candidate_group_id', self.config['group_switch'])
        self.assertEqual(self.store.group_id, 'old')

    def test_switch_save_failure_keeps_old_binding(self):
        self.capture()
        with patch.object(self.switch, 'save', side_effect=OSError('synthetic failure')):
            with self.assertRaises(OSError):
                self.switch.apply(self.relay)
        self.assertEqual(self.config['group_id'], 'old')
        self.assertEqual(self.store.group_id, 'old')
        self.assertEqual(self.switch.status(), 'binding')
        self.assertIsNone(self.store.next_input())

    def test_only_signed_at_event_can_capture_candidate(self):
        config = dict(self.config, app_secret='synthetic-secret', app_id='123',
                      data_dir=str(self.root / 'service'), astrion_url='http://127.0.0.1:8095',
                      token_file=str(self.root / 'unused'), workspace_id='workspace',
                      workspace_path=str(self.root))
        service = Service(config, self.path)
        event = {'op': 0, 't': 'GROUP_AT_MESSAGE_CREATE', 'd': self.message()}
        raw = json.dumps(event).encode()
        timestamp = str(int(time.time()))
        headers = {'X-Bot-Appid': '123', 'X-Signature-Timestamp': timestamp,
                   'X-Signature-Ed25519': private_key(config['app_secret']).sign(
                       timestamp.encode() + raw).hex()}
        status, _ = service.process(raw.replace(b'hello', b'changed'), headers)
        self.assertEqual(status, 401)
        self.assertEqual(service.group_switch.status(), 'awaiting_new_group')
        ordinary = dict(event, t='GROUP_MESSAGE_CREATE')
        ordinary_raw = json.dumps(ordinary).encode()
        ordinary_headers = dict(headers, **{'X-Signature-Ed25519': private_key(
            config['app_secret']).sign(timestamp.encode() + ordinary_raw).hex()})
        self.assertEqual(service.process(ordinary_raw, ordinary_headers)[0], 200)
        self.assertEqual(service.group_switch.status(), 'awaiting_new_group')
        self.assertEqual(service.process(raw, headers)[0], 200)
        self.assertEqual(service.health()['group_binding'], 'binding')
        self.assertEqual(service.store.counts()['inbox'], {})


if __name__ == '__main__':
    unittest.main()
