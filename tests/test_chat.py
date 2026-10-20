import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from wechat_ai_bridge.dispatch.processor import MessageProcessor
from wechat_ai_bridge.message import IncomingMessage, Outcome
from wechat_ai_bridge.models.client import ModelError
from wechat_ai_bridge.qa import service as chat_module
from wechat_ai_bridge.state.store import StateStore


class FakeModel:
    def __init__(self, response="今天适合出去走走。"):
        self.response = response
        self.calls = []

    def complete(self, messages):
        self.calls.append(messages)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def message(message_id="m1", *, group="group-a", sender="wxid-real-a", text="周末去哪玩？", mentioned=True):
    return IncomingMessage(message_id, group, sender, text, mentioned)


class FakeClock:
    def __init__(self):
        self.now = 0

    def __call__(self):
        return self.now


class ChatTests(unittest.TestCase):
    def setUp(self):
        self.model = FakeModel()
        self.chat = chat_module.ChatService(self.model)

    def respond(self, incoming):
        self.chat.observe(incoming)
        return self.chat.respond(incoming)

    def test_mention_calls_model_without_reference_material(self):
        self.assertEqual(self.respond(message()), "今天适合出去走走。")
        self.assertEqual(len(self.model.calls), 1)
        self.assertEqual(self.model.calls[0][0]["role"], "system")
        self.assertNotIn("来源", self.respond(message("m2")))

    def test_natural_participation_reply_or_silence(self):
        for response, expected in [(' {"reply": "一起去公园吧。"} ', "一起去公园吧。"),
                                   ('{"reply":null}', None), ('{"reply":"  "}', None)]:
            with self.subTest(response=response):
                self.model.response = response
                self.assertEqual(self.respond(message(mentioned=False)), expected)

    def test_natural_bad_format_stays_silent(self):
        for response in ["我想插话", "", '[]', 'null', '{"reply": 1}',
                         '{"reply": true}', '{"other":"hello"}', '```json\n{"reply":"hello"}\n```']:
            with self.subTest(response=response):
                self.model.response = response
                self.assertIsNone(self.respond(message(mentioned=False)))

    def test_explicit_mention_accepts_direct_text(self):
        self.model.response = "  直接回答就好。  "
        self.assertEqual(self.respond(message()), "直接回答就好。")

    def test_group_context_has_stable_anonymous_members(self):
        self.chat.observe(message(text="第一位的消息"))
        self.respond(message("m2", sender="wxid-real-b", text="第二位的消息"))
        self.respond(message("m3", text="第一位再说一句"))
        request = self.model.calls[-1]
        users = [item["content"] for item in request if item["role"] == "user"]
        self.assertEqual(len(users), 3)
        self.assertIn("第一位的消息", users[0])
        self.assertIn("第二位的消息", users[1])
        self.assertEqual(users[0].split(":", 1)[0], users[2].split(":", 1)[0])
        self.assertNotEqual(users[0].split(":", 1)[0], users[1].split(":", 1)[0])
        self.assertNotIn("wxid-real", json.dumps(request))

    def test_context_is_isolated_between_groups(self):
        self.respond(message(text="秘密群聊"))
        self.respond(message("m2", group="group-b", text="另一个群"))
        self.assertNotIn("秘密群聊", json.dumps(self.model.calls[-1], ensure_ascii=False))

    def test_context_expires_after_ten_minutes(self):
        with patch("wechat_ai_bridge.qa.service.monotonic", return_value=0):
            self.chat.observe(message(text="已过期的话题"))
        with patch("wechat_ai_bridge.qa.service.monotonic", return_value=601):
            self.respond(message("m2", text="新话题"))
        self.assertNotIn("已过期的话题", json.dumps(self.model.calls[-1], ensure_ascii=False))

    def test_context_is_bounded_to_twenty_messages(self):
        for index in range(25):
            self.chat.observe(message(str(index), text=f"消息{index}"))
        self.chat.respond(message("24", text="消息24"))
        users = [item for item in self.model.calls[-1] if item["role"] == "user"]
        self.assertEqual(len(users), 20)
        self.assertTrue(users[0]["content"].endswith("消息5"))

    def test_generated_answer_is_not_context_until_recorded(self):
        first = message()
        reply = self.respond(first)
        self.respond(message("m2"))
        self.assertFalse(any(item["role"] == "assistant" for item in self.model.calls[-1]))
        self.chat.record_reply(first, reply)
        self.respond(message("m3"))
        self.assertEqual([item["content"] for item in self.model.calls[-1] if item["role"] == "assistant"], [reply])


class ProcessorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "state.sqlite3"
        self.store = StateStore(self.path)
        self.addCleanup(self.store.close)
        self.store.allow_group("group-a")
        self.model = FakeModel('{"reply":null}')
        self.chat = chat_module.ChatService(self.model)
        self.clock = FakeClock()
        self.processor = MessageProcessor(self.store, self.chat, cooldown_seconds=0, clock=self.clock)

    def test_natural_silence_is_persistently_deduplicated(self):
        incoming = message(mentioned=False)
        self.assertIsNone(self.processor.process(incoming).reply)
        self.clock.now = 5
        self.assertEqual(self.processor.flush_pending()[0][1].reason, "model_silent")
        reopened = StateStore(self.path)
        self.addCleanup(reopened.close)
        processor = MessageProcessor(reopened, self.chat, cooldown_seconds=0)
        self.assertEqual(processor.process(incoming).outcome, Outcome.DUPLICATE)
        self.assertEqual(len(self.model.calls), 1)

    def test_mentions_mode_observes_bystanders_without_calling_model(self):
        processor = MessageProcessor(self.store, self.chat, cooldown_seconds=0, participation_mode="mentions")
        self.assertEqual(processor.process(message(mentioned=False, text="大家想吃火锅")).outcome, Outcome.IGNORED)
        self.assertEqual(self.model.calls, [])
        self.assertEqual(processor.flush_pending(), [])
        self.model.response = "那就火锅吧。"
        self.assertEqual(processor.process(message("m2", text="你觉得呢？")).outcome, Outcome.ANSWERED)
        self.assertIn("大家想吃火锅", json.dumps(self.model.calls[-1], ensure_ascii=False))

    def test_filters_do_not_call_model(self):
        cases = [replace(message(), is_group=False), replace(message(), is_from_bot=True),
                 message(text="  "), message(group="unlisted")]
        for incoming in cases:
            self.assertEqual(self.processor.process(incoming).outcome, Outcome.IGNORED)
        self.store.set_paused(True)
        self.assertEqual(self.processor.process(message()).outcome, Outcome.IGNORED)
        self.assertEqual(self.model.calls, [])

    def test_burst_restarts_five_second_deadline_and_keeps_full_context(self):
        processor = MessageProcessor(self.store, self.chat, cooldown_seconds=0,
                                     spontaneous_cooldown_seconds=0, clock=self.clock)
        first = message(mentioned=False, text="周末去哪玩")
        latest = message("m2", mentioned=False, text="我想去公园")
        self.assertEqual(processor.process(first).outcome, Outcome.DEFERRED)
        self.clock.now = 4
        self.assertEqual(processor.process(latest).outcome, Outcome.DEFERRED)
        self.clock.now = 5
        self.assertEqual(processor.flush_pending(), [])
        self.assertEqual(self.model.calls, [])
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 0)
        self.clock.now = 9
        self.model.response = '{"reply":"一起去吧"}'
        flushed = processor.flush_pending()
        self.assertEqual(flushed[0][0], latest)
        self.assertEqual(flushed[0][1].outcome, Outcome.ANSWERED)
        self.assertEqual(len(self.model.calls), 1)
        request = json.dumps(self.model.calls[0], ensure_ascii=False)
        self.assertIn(first.text, request)
        self.assertIn(latest.text, request)
        self.assertEqual(processor.flush_pending(), [])
        self.assertEqual(processor.process(first).outcome, Outcome.DUPLICATE)
        self.assertEqual(processor.process(latest).outcome, Outcome.DUPLICATE)

    def test_explicit_mention_cancels_pending_and_replies_immediately(self):
        self.processor.process(message(mentioned=False))
        self.model.response = "被叫到啦。"
        self.assertEqual(self.processor.process(message("m3")).outcome, Outcome.ANSWERED)
        self.clock.now = 20
        self.assertEqual(self.processor.flush_pending(), [])
        self.assertEqual(len(self.model.calls), 1)

    def test_configured_longer_delay_is_respected(self):
        processor = MessageProcessor(self.store, self.chat, clock=self.clock,
                                     spontaneous_cooldown_seconds=10)
        processor.process(message(mentioned=False))
        self.clock.now = 9
        self.assertEqual(processor.flush_pending(), [])
        self.clock.now = 10
        self.assertEqual(len(processor.flush_pending()), 1)

    def test_pause_and_revoked_group_discard_pending_without_resume_reply(self):
        for action in ("pause", "deny"):
            with self.subTest(action=action):
                self.store.set_paused(False)
                self.store.allow_group("group-a")
                self.processor.process(message(action, mentioned=False))
                if action == "pause":
                    self.store.set_paused(True)
                else:
                    self.store.deny_group("group-a")
                results = self.processor.flush_pending()
                self.assertEqual(results[0][1].outcome, Outcome.IGNORED)
                self.store.set_paused(False)
                self.store.allow_group("group-a")
                self.assertEqual(self.processor.flush_pending(), [])
        self.assertEqual(self.model.calls, [])

    def test_pending_deadlines_are_independent_between_groups(self):
        self.store.allow_group("group-b")
        self.processor.process(message("a1", mentioned=False))
        self.clock.now = 3
        self.processor.process(message("b1", group="group-b", mentioned=False))
        self.clock.now = 5
        self.assertEqual([incoming.group_id for incoming, _ in self.processor.flush_pending()], ["group-a"])
        self.clock.now = 8
        self.assertEqual([incoming.group_id for incoming, _ in self.processor.flush_pending()], ["group-b"])
        self.assertEqual(len(self.model.calls), 2)

    def test_hourly_quota_counts_silent_model_calls(self):
        processor = MessageProcessor(self.store, self.chat, cooldown_seconds=0, hourly_quota=1,
                                     clock=self.clock)
        processor.process(message(mentioned=False))
        self.clock.now = 5
        processor.flush_pending()
        self.assertEqual(processor.process(message("m2")).outcome, Outcome.RATE_LIMITED)
        self.assertEqual(len(self.model.calls), 1)

    def test_pending_is_not_restored_after_restart(self):
        incoming = message(mentioned=False)
        self.processor.process(incoming)
        reopened = StateStore(self.path)
        self.addCleanup(reopened.close)
        restarted = MessageProcessor(reopened, self.chat, clock=self.clock)
        self.clock.now = 5
        self.assertEqual(restarted.flush_pending(), [])
        self.assertEqual(restarted.process(incoming).outcome, Outcome.DUPLICATE)
        self.assertEqual(self.model.calls, [])

    def test_failed_pending_is_not_retried_and_has_safe_reason(self):
        self.model.response = ModelError("private diagnostic")
        self.processor.process(message(mentioned=False))
        self.clock.now = 5
        result = self.processor.flush_pending()[0][1]
        self.assertEqual(result.outcome, Outcome.ERROR)
        self.assertEqual(result.reason, self.model.response.code)
        self.assertNotIn("private diagnostic", result.reason)
        self.assertEqual(self.processor.flush_pending(), [])
        self.assertEqual(len(self.model.calls), 1)

    def test_configuration_failure_has_fixed_reason(self):
        self.model.response = OSError("private prompt path")
        result = self.processor.process(message())
        self.assertEqual(result.outcome, Outcome.ERROR)
        self.assertEqual(result.reason, "prompt_or_chat_configuration")

    def test_failure_has_no_reply_and_is_persistently_deduplicated(self):
        self.model.response = ModelError("do not send diagnostics")
        result = self.processor.process(message())
        self.assertEqual(result.outcome, Outcome.ERROR)
        self.assertIsNone(result.reply)
        reopened = StateStore(self.path)
        self.addCleanup(reopened.close)
        processor = MessageProcessor(reopened, self.chat, cooldown_seconds=0)
        self.assertEqual(processor.process(message()).outcome, Outcome.DUPLICATE)
        self.assertEqual(len(self.model.calls), 1)

    def test_answer_is_deduplicated_after_restart(self):
        self.model.response = "回答"
        self.processor.process(message())
        reopened = StateStore(self.path)
        self.addCleanup(reopened.close)
        self.assertEqual(MessageProcessor(reopened, self.chat).process(message()).outcome, Outcome.DUPLICATE)

    def test_claim_event_is_atomic_across_connections(self):
        reopened = StateStore(self.path)
        self.addCleanup(reopened.close)
        self.assertTrue(self.store.claim_event("m1", "group-a"))
        self.assertFalse(reopened.claim_event("m1", "group-a"))
        self.assertEqual(self.store.connection.execute("SELECT message_id, group_id FROM seen_events").fetchall(),
                         [("m1", "group-a")])


if __name__ == "__main__":
    unittest.main()
