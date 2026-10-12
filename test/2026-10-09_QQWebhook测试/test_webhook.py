import json
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[2] / "_experiments" / "qqbot_webhook_test"
sys.path.insert(0, str(SOURCE))
from app import Service
from qq_api import QQClient
from security import challenge_response, private_key

SECRET = "fixture-only-not-a-real-bot-secret"
APP_ID = "test-app"


class WebhookTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.service = Service({"app_id": APP_ID, "app_secret": SECRET}, Path(self.directory.name))

    def tearDown(self):
        self.directory.cleanup()

    def event(self, message_id="message-1", group_id="group-1", content="hello"):
        return {"op": 0, "id": "event-" + message_id, "t": "GROUP_AT_MESSAGE_CREATE",
                "d": {"id": message_id, "group_openid": group_id,
                      "author": {"member_openid": "member-1"}, "content": content}}

    def signed(self, event, timestamp=None):
        raw = json.dumps(event, ensure_ascii=False).encode()
        timestamp = timestamp or str(int(time.time()))
        signature = private_key(SECRET).sign(timestamp.encode() + raw).hex()
        return raw, {"X-Bot-Appid": APP_ID, "X-Signature-Timestamp": timestamp,
                     "X-Signature-Ed25519": signature}

    def test_challenge_matches_ed25519(self):
        body = {"op": 13, "d": {"event_ts": "1234", "plain_token": "challenge"}}
        status, result = self.service.process(json.dumps(body).encode(), {"X-Bot-Appid": APP_ID})
        self.assertEqual(status, 200)
        private_key(SECRET).public_key().verify(bytes.fromhex(result["signature"]), b"1234challenge")
        self.assertEqual(result["plain_token"], "challenge")

    def test_official_documentation_public_key_fixture(self):
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
        fixture_secret = "naOC0ocQE3shWLAfffVLB1rhYPG7"
        public = private_key(fixture_secret).public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        # The published public-key bytes have an unambiguous input. The
        # document's separate signature sample does not reproduce against
        # its displayed body, so do not use that body as an exact-byte vector.
        self.assertEqual(public, bytes([
            215, 195, 98, 254, 120, 174, 248, 31, 242, 50, 135, 180, 147, 98,
            139, 93, 176, 42, 60, 79, 227, 11, 33, 94, 77, 25, 96, 155, 93,
            118, 103, 58,
        ]))

    def test_bad_signature_cannot_bind_group(self):
        raw, headers = self.signed(self.event())
        headers["X-Signature-Ed25519"] = "00" * 64
        self.assertEqual(self.service.process(raw, headers)[0], 401)
        self.assertFalse(self.service.store.stats()["group_bound"])

    def test_original_body_is_used(self):
        raw, headers = self.signed(self.event())
        self.assertEqual(self.service.process(raw.replace(b"hello", b"changed"), headers)[0], 401)

    def test_stale_request_rejected(self):
        raw, headers = self.signed(self.event(), str(int(time.time()) - 1000))
        self.assertEqual(self.service.process(raw, headers)[0], 401)

    def test_duplicate_only_queues_once_and_survives_restart(self):
        raw, headers = self.signed(self.event())
        self.assertEqual(self.service.process(raw, headers), (200, {"op": 12}))
        self.assertEqual(self.service.process(raw, headers)[0], 200)
        restored = Service({"app_id": APP_ID, "app_secret": SECRET}, Path(self.directory.name))
        self.assertEqual(restored.process(raw, headers)[0], 200)
        self.assertEqual(restored.store.stats()["messages"], {"queued": 1})

    def test_other_group_is_ignored(self):
        self.service.process(*self.signed(self.event()))
        self.service.process(*self.signed(self.event("message-2", "group-2")))
        self.assertEqual(self.service.store.stats()["messages"], {"queued": 1})

    def test_parallel_duplicate_delivery(self):
        event = self.event()
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.service.store.accept(event), range(16)))
        self.assertEqual(results.count("accepted"), 1)
        self.assertEqual(results.count("duplicate"), 15)

    def test_unfinished_send_not_retried_after_restart(self):
        self.service.store.accept(self.event())
        self.assertIsNotNone(self.service.store.claim())
        restored = Service({"app_id": APP_ID, "app_secret": SECRET}, Path(self.directory.name))
        self.assertEqual(restored.store.stats()["messages"], {"uncertain": 1})
        self.assertIsNone(restored.store.claim())

    def test_bad_challenge_and_malformed_payload(self):
        self.assertEqual(self.service.process(b"null", {})[0], 400)
        self.assertEqual(self.service.process(b"not-json", {})[0], 400)
        self.assertEqual(self.service.process(b'{"op":13,"d":{}}', {"X-Bot-Appid": APP_ID})[0], 400)
        self.assertEqual(self.service.process(b'{"op":13,"d":{}}', {})[0], 403)

    def test_proactive_reply_has_no_passive_message_id(self):
        class Response:
            ok = True
            status_code = 200
            def json(self):
                return {"id": "qq-reply-id"}
        class Session:
            def post(self, url, **kwargs):
                self.payload = kwargs["json"]
                return Response()
        client = QQClient(APP_ID, SECRET)
        client.token, client.token_expires = "synthetic-token", time.monotonic() + 100
        client.session = Session()
        self.assertEqual(client.reply("group-1"), "qq-reply-id")
        self.assertNotIn("msg_id", client.session.payload)
        self.assertNotIn("event_id", client.session.payload)
        self.assertEqual(client.session.payload["msg_type"], 0)

    def test_passive_reply_uses_received_id_and_sequence(self):
        class Response:
            ok = True
            status_code = 200
            def json(self):
                return {"id": "passive-reply-id"}
        class Session:
            def post(self, url, **kwargs):
                self.payload = kwargs["json"]
                return Response()
        client = QQClient(APP_ID, SECRET)
        client.token, client.token_expires = "synthetic-token", time.monotonic() + 100
        client.session = Session()
        self.assertEqual(client.reply("group-1", message_id="received-message"), "passive-reply-id")
        self.assertEqual(client.session.payload["msg_id"], "received-message")
        self.assertEqual(client.session.payload["msg_seq"], 1)
        self.assertIn("被动回复", client.session.payload["content"])
        self.assertNotIn("event_id", client.session.payload)

    def test_passive_worker_passes_persisted_message_id(self):
        service = Service({"app_id": APP_ID, "app_secret": SECRET, "reply_mode": "passive"},
                          Path(self.directory.name))
        calls = []
        class Client:
            def reply(self, group_id, *, message_id=None):
                calls.append((group_id, message_id))
                service.stop.set()
                return "worker-reply-id"
        service.client = Client()
        service.store.accept(self.event("original-qq-message"))
        service.worker()
        self.assertEqual(calls, [("group-1", "original-qq-message")])
        self.assertEqual(service.store.stats()["messages"], {"sent": 1})

    def test_passive_requires_message_id(self):
        client = QQClient(APP_ID, SECRET)
        with self.assertRaises(ValueError):
            client.reply("group-1", message_id="")

    def test_invalid_mode_is_not_silently_changed(self):
        with self.assertRaises(ValueError):
            Service({"app_id": APP_ID, "app_secret": SECRET, "reply_mode": "typo"},
                    Path(self.directory.name))


if __name__ == "__main__":
    unittest.main()
