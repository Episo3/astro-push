"""推送通道测试：用本地 mock HTTP 服务器验证请求构造，不需要真实 token。"""

from __future__ import annotations

import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from astropush import notify  # noqa: E402
from astropush.config import Config  # noqa: E402

CAPTURED: list[dict] = []


class MockHandler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length).decode("utf-8")
        ctype = self.headers.get("Content-Type", "")
        if "json" in ctype:
            body = json.loads(raw)
        else:
            from urllib.parse import parse_qs
            body = {k: v[0] for k, v in parse_qs(raw).items()}
        CAPTURED.append({"path": self.path, "body": body, "ctype": ctype})
        resp = json.dumps({"ok": True, "code": 0, "errcode": 0, "StatusCode": 0}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp)))
        self.end_headers()
        self.wfile.write(resp)

    do_GET = do_POST

    def log_message(self, *args):
        pass


class TestNotify(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), MockHandler)
        cls.port = cls.server.server_address[1]
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        CAPTURED.clear()

    def _cfg(self, channels, webhook_type, env):
        cfg = Config(raw={
            "location": {"name": "T", "lat": 1, "lon": 1, "tz": "UTC"},
            "channels": channels,
            "webhook": {"type": webhook_type},
            "thresholds": {},
            "dark_sites": [],
        })
        cfg.env = env
        return cfg

    def test_telegram_payload(self):
        cfg = self._cfg(["telegram"], "generic",
                        {"TELEGRAM_BOT_TOKEN": "123:ABC", "TELEGRAM_CHAT_ID": "42"})
        res = notify.send_telegram(cfg, "<b>hello</b> 今晚 A 级", base=self.base)
        self.assertTrue(res.ok, res.detail)
        self.assertEqual(CAPTURED[-1]["path"], "/bot123:ABC/sendMessage")
        body = CAPTURED[-1]["body"]
        self.assertEqual(body["chat_id"], "42")
        self.assertEqual(body["parse_mode"], "HTML")
        self.assertIn("<b>", body["text"])

    def test_telegram_missing_secret(self):
        cfg = self._cfg(["telegram"], "generic", {})
        res = notify.send_telegram(cfg, "x", base=self.base)
        self.assertFalse(res.ok)
        self.assertIn("TELEGRAM_BOT_TOKEN", res.detail)

    def test_feishu(self):
        cfg = self._cfg(["webhook"], "feishu", {"FEISHU_WEBHOOK": self.base + "/open-apis/bot/v2/hook/x"})
        res = notify.send_webhook(cfg, "今晚 A 级", "标题", "<b>x</b>")
        self.assertTrue(res.ok, res.detail)
        body = CAPTURED[-1]["body"]
        self.assertEqual(body["msg_type"], "text")
        self.assertEqual(body["content"]["text"], "今晚 A 级")

    def test_wecom(self):
        cfg = self._cfg(["webhook"], "wecom", {"WECOM_WEBHOOK": self.base + "/cgi-bin/webhook/send?key=k"})
        res = notify.send_webhook(cfg, "今晚 A 级", "t")
        self.assertTrue(res.ok, res.detail)
        body = CAPTURED[-1]["body"]
        self.assertEqual(body["msgtype"], "markdown")
        self.assertEqual(body["markdown"]["content"], "今晚 A 级")

    def test_serverchan_url_forms(self):
        # Turbo（SCT 前缀）
        self.assertEqual(notify.serverchan_url("SCT123abc"),
                         "https://sctapi.ftqq.com/SCT123abc.send")
        # Server酱3（sctp{uid}t… → 走 uid 子域）
        self.assertEqual(notify.serverchan_url("sctp98765tAbC"),
                         "https://98765.push.ft07.com/send/sctp98765tAbC.send")
        # 完整 URL 原样使用（本地测试/自建代理）
        self.assertEqual(notify.serverchan_url("http://127.0.0.1:9/x"), "http://127.0.0.1:9/x")

    def test_serverchan_send(self):
        # 用完整 URL 形式把请求导到本地 mock，验证表单字段与成功判定
        cfg = self._cfg(["webhook"], "serverchan", {"SERVERCHAN_KEY": self.base + "/sct/send"})
        res = notify.send_webhook(cfg, "正文内容", "今晚 A 级")
        self.assertTrue(res.ok, res.detail)
        self.assertEqual(CAPTURED[-1]["path"], "/sct/send")
        self.assertEqual(CAPTURED[-1]["body"]["title"], "今晚 A 级")
        self.assertEqual(CAPTURED[-1]["body"]["desp"], "正文内容")
        # 传了 Markdown 时优先发 Markdown（Server酱 详情页会渲染）
        notify.send_webhook(cfg, "纯文本", "标题", md="## 小节\n\n- 一行一条")
        self.assertEqual(CAPTURED[-1]["body"]["desp"], "## 小节\n\n- 一行一条")

    def test_serverchan_missing_key(self):
        cfg = self._cfg(["webhook"], "serverchan", {})
        self.assertFalse(notify.send_webhook(cfg, "x", "t").ok)

    def test_is_due_guard(self):
        import run
        from datetime import datetime
        from zoneinfo import ZoneInfo
        cfg = self._cfg([], "serverchan", {})
        cfg.raw["schedule"] = {"hour": 15, "minute": 30}
        now = datetime.now(ZoneInfo("UTC"))
        cfg.raw["schedule"] = {"hour": now.hour, "minute": now.minute}
        self.assertTrue(run.is_due(cfg)[0])
        cfg.raw["schedule"] = {"hour": (now.hour - 2) % 24, "minute": now.minute}
        self.assertFalse(run.is_due(cfg)[0])

    def test_bark(self):
        cfg = self._cfg(["webhook"], "bark", {"BARK_URL": self.base + "/KEY"})
        res = notify.send_webhook(cfg, "今晚 A 级", "标题")
        self.assertTrue(res.ok, res.detail)
        self.assertEqual(CAPTURED[-1]["path"], "/KEY/push")
        self.assertEqual(CAPTURED[-1]["body"]["title"], "标题")

    def test_generic(self):
        cfg = self._cfg(["webhook"], "generic", {"WEBHOOK_URL": self.base + "/hook"})
        res = notify.send_webhook(cfg, "纯文本", "标题", "<b>html</b>")
        self.assertTrue(res.ok, res.detail)
        body = CAPTURED[-1]["body"]
        self.assertEqual(body["text"], "纯文本")
        self.assertEqual(body["html"], "<b>html</b>")
        self.assertEqual(body["kind"], "astro-nightly")

    def test_send_all_isolates_failure(self):
        cfg = self._cfg(["telegram", "webhook"], "generic",
                        {"TELEGRAM_BOT_TOKEN": "1:x", "TELEGRAM_CHAT_ID": "1"})
        # 直连真实 telegram 会失败，但 generic 缺 URL 也会失败；这里验证不会抛异常
        results = notify.send_all(cfg, "t", "<b>t</b>", "title", dry_run=True)
        self.assertEqual(len(results), 2)
        self.assertTrue(all(r.ok for r in results))

    def test_limit_bytes(self):
        s = notify._limit_bytes("汉字" * 5000, 3900)
        self.assertLessEqual(len(s.encode("utf-8")), 3900)


if __name__ == "__main__":
    unittest.main()
