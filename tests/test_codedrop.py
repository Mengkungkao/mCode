"""The sign-in code page (app/web/codedrop.py): a phone pastes Claude's login
code over the local network while the device's sign-in waits for it."""

from __future__ import annotations

import logging
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request

from app.web import codedrop
from app.web.codedrop import CodeDrop


def fetch(url, code=None, raw=None):
    data = None
    if code is not None:
        data = urllib.parse.urlencode({"code": code}).encode()
    if raw is not None:
        data = raw
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=5) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


class CodeDropTests(unittest.TestCase):
    def start(self, submit):
        drop = CodeDrop(submit, host="127.0.0.1")
        self.assertTrue(drop.start())
        self.addCleanup(drop.stop)
        return drop

    def test_one_code_reaches_the_sign_in_once(self):
        got = []
        drop = self.start(lambda code: got.append(code))
        url = drop.url()
        self.assertTrue(url.startswith(f"http://127.0.0.1:{drop.port}/c/"))
        status, page = fetch(url)
        self.assertEqual(status, 200)
        self.assertIn("Send to mCode", page)
        records = []
        handler = logging.Handler(logging.DEBUG)
        handler.emit = records.append
        logger = logging.getLogger("mcode")
        logger.addHandler(handler)
        old_level = logger.level
        logger.setLevel(logging.DEBUG)
        try:
            status, page = fetch(url, code="  abc#def  ")
        finally:
            logger.removeHandler(handler)
            logger.setLevel(old_level)
        self.assertFalse([r for r in records if "abc#def" in r.getMessage()])
        self.assertEqual(status, 200)
        self.assertIn("Sent.", page)
        self.assertEqual(got, ["abc#def"])
        status, page = fetch(url, code="second")
        self.assertEqual(status, 400)
        self.assertIn("already sent", page)
        self.assertEqual(got, ["abc#def"])

    def test_wrong_token_and_other_paths_are_not_found(self):
        drop = self.start(lambda code: None)
        base = f"http://127.0.0.1:{drop.port}"
        self.assertEqual(fetch(base + "/")[0], 404)
        self.assertEqual(fetch(base + "/c/" + "x" * 22)[0], 404)
        self.assertEqual(fetch(base + "/c/" + "x" * 22, code="abc")[0], 404)
        self.assertEqual(fetch(base + "/c/%FF%FE")[0], 404)

    def test_refused_code_can_be_sent_again(self):
        answers = ["The sign-in is not waiting for a code.", None]
        drop = self.start(lambda code: answers.pop(0))
        status, page = fetch(drop.url(), code="abc")
        self.assertEqual(status, 400)
        self.assertIn("not waiting", page)
        self.assertEqual(fetch(drop.url(), code="abc")[0], 200)

    def test_empty_long_and_multiline_codes_are_refused(self):
        got = []
        drop = self.start(lambda code: got.append(code))
        self.assertEqual(fetch(drop.url(), code="  ")[0], 400)
        self.assertEqual(fetch(drop.url(), code="a\nb")[0], 400)
        self.assertEqual(fetch(drop.url(), raw=b"code=" + b"a" * 5000)[0], 413)
        self.assertEqual(got, [])

    def test_closes_after_too_many_wrong_requests(self):
        drop = self.start(lambda code: None)
        for _ in range(codedrop.MAX_WRONG):
            fetch(f"http://127.0.0.1:{drop.port}/c/wrong")
        self.assertFalse(drop.running)
        self.assertEqual(drop.urls(), [])

    def test_page_escapes_the_error_text(self):
        drop = self.start(lambda code: "<b>bad</b>")
        _status, page = fetch(drop.url(), code="x")
        self.assertIn("&lt;b&gt;bad&lt;/b&gt;", page)


if __name__ == "__main__":
    unittest.main()
