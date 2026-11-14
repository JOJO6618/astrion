"""Runtime notices are durable, retryable and never rewrite frozen system content."""
import copy
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import test_worktree
from modules.git_worktree.notices import consume


class NoticeTests(unittest.TestCase):
    def setUp(self):
        self.state = {'directory': '/workspace/tree', 'branch': 'task', 'revision': 2, 'active': True}
        self.document = {'metadata': {'git_worktree_notice': copy.deepcopy(self.state),
                                     'frozen_system_prompt': 'unchanged initial system'}, 'messages': []}
        self.persist_messages = True
        self.fail_ack = False
        self.manager = SimpleNamespace(read_conversation=lambda cid: copy.deepcopy(self.document),
                                       mutate_conversation=self.mutate)
        self.cm = SimpleNamespace(conversation_metadata=copy.deepcopy(self.document['metadata']),
                                  conversation_history=[], add_conversation=self.add,
                                  _get_conversation_manager_for_id=lambda cid: self.manager)
        self.owner = SimpleNamespace(context_manager=self.cm)
        self.sender = Mock()

    def mutate(self, cid, mutate):
        if self.fail_ack:
            raise OSError('fixture acknowledgement failed')
        candidate = copy.deepcopy(self.document)
        mutate(candidate)
        self.document = candidate
        return candidate, None

    def add(self, role, content, *, message_id, metadata, **kwargs):
        message = {'role': role, 'content': content, 'message_id': message_id, 'metadata': metadata}
        self.cm.conversation_history.append(message)
        if self.persist_messages:
            self.document['messages'].append(copy.deepcopy(message))
        return message

    def test_success_acknowledges_notice_and_preserves_frozen_system(self):
        consume(self.owner, sender=self.sender, conversation_id='conv_fixture')
        self.assertEqual(self.document['metadata']['frozen_system_prompt'], 'unchanged initial system')
        self.assertNotIn('git_worktree_notice', self.document['metadata'])
        self.assertNotIn('git_worktree_notice', self.cm.conversation_metadata)
        message = self.document['messages'][0]
        self.assertEqual(message['role'], 'user')
        self.assertEqual(message['metadata']['source'], 'execution_change')
        self.assertFalse(message['metadata']['starts_work'])
        self.sender.assert_called_once()
        consume(self.owner, sender=self.sender, conversation_id='conv_fixture')
        self.assertEqual(len(self.document['messages']), 1)

    def test_silent_save_failure_retains_pending_without_publishing(self):
        self.persist_messages = False
        with self.assertRaisesRegex(RuntimeError, '持久化'):
            consume(self.owner, sender=self.sender, conversation_id='conv_fixture')
        self.assertEqual(self.document['metadata']['git_worktree_notice'], self.state)
        self.assertEqual(self.cm.conversation_metadata['git_worktree_notice'], self.state)
        self.assertEqual(self.cm.conversation_history, [])
        self.sender.assert_not_called()
        self.persist_messages = True
        consume(self.owner, sender=self.sender, conversation_id='conv_fixture')
        self.assertEqual(len(self.document['messages']), 1)

    def test_acknowledgement_retry_does_not_duplicate_durable_notice(self):
        self.fail_ack = True
        with self.assertRaises(OSError):
            consume(self.owner, sender=self.sender, conversation_id='conv_fixture')
        self.assertEqual(len(self.document['messages']), 1)
        self.fail_ack = False
        consume(self.owner, sender=self.sender, conversation_id='conv_fixture')
        self.assertEqual(len(self.document['messages']), 1)
        self.sender.assert_called_once()


if __name__ == '__main__':
    unittest.main()
