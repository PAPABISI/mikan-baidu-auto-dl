"""离线回归测试：不读取真实凭据，不向网盘发请求，不修改追番配置。"""
import concurrent.futures
import copy
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from unittest.mock import Mock, patch

import auto_dl
import agent_bridge
import baidu
import catalog
import qr_cloud_dl
import storage
import web_app
import filelock
from filelock import FileLock
from parse_mikan import episode_info, parse_mikan, select_for_series


def row(ep="01", group="绿茶字幕组", language="简体内嵌", number=1):
    title = f"[{group}] 测试番 [{ep}] [1080P] [{language}]"
    return (f"<tr class='resource'><td data-magnet='magnet:?xt=urn:btih:{number:040x}'></td>"
            f"<td><a href='/Home/Episode/x' class='other magnet-link-wrap'><span>{title}</span></a></td>"
            "<td>432.8 MiB</td><td>2026/10/29 10:00</td></tr>")


def series(**changes):
    result = {"mikan_id": 4059, "name": "测试番", "subtitle_groups": ["绿茶字幕组"],
              "language": "简体", "embedded_only": True, "save_path": "/Anime/测试番", "last_episode": "0"}
    result.update(changes)
    return result


class TemporaryProject(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix=".test-", dir=storage.BASE)
        self.addCleanup(self.temp.cleanup)
        for module, names in (
            (storage, ("LIST_FILE", "LIST_LOCK")),
            (auto_dl, ("RUN_LOG", "RUN_LOCK", "HISTORY_FILE")),
            (baidu, ("COOKIE_FILE",)),
            (qr_cloud_dl, ("COOKIE_FILE", "DIAGNOSTIC_FILE", "PENDING_FILE")),
            (web_app, ("QR_STATE_FILE", "QR_PNG")),
            (catalog, ("DATA_FILE", "DATA_LOCK")),
        ):
            for name in names:
                original = getattr(module, name)
                patcher = patch.object(module, name, os.path.join(self.temp.name, os.path.basename(original)))
                patcher.start()
                self.addCleanup(patcher.stop)
        storage.write_json(storage.LIST_FILE, {"series": [series()]})

    def client(self, statuses=None):
        client = Mock()
        client.add_task.side_effect = lambda magnet, path: {"errno": 0, "task_id": str(int(magnet.split(":")[-1], 16))}
        client.query_tasks.side_effect = lambda ids: {str(tid): {
            "status": str((statuses or {}).get(str(tid), 0)), "result": 0, "task_name": "测试.mp4"} for tid in ids}
        return client

    def run_pipeline(self, html, client):
        with patch.object(auto_dl, "fetch_mikan", return_value=html):
            return auto_dl.run(client=client)


class ParserTests(unittest.TestCase):
    def test_episode_formats_and_exclusions(self):
        for title, expected in (
            ("[组] 番名 [01v12] [1080P]", ("01", 12)),
            ("番名 第12.5话", ("12.5", 1)),
            ("番名 第02话v2", ("02", 2)),
            ("Show - 09v2 END [1080p]", ("09", 2)),
            ("Show S02E03", ("03", 1)),
            ("番名 [SP01]", ("?", 0)),
            ("番名 [OVA]", ("?", 0)),
            ("番名 [01-12]", ("?", 0)),
            ("番名 [1080P] [2026]", ("?", 0)),
        ):
            with self.subTest(title=title):
                self.assertEqual(episode_info(title), expected)

    def test_attribute_order_nested_title_and_dedup(self):
        items = parse_mikan(row() + row())
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["group"], "绿茶字幕组")
        self.assertEqual(items[0]["size"], "432.8 MiB")
        self.assertEqual(items[0]["ep"], "01")

    def test_preference_order_and_highest_revision(self):
        items = parse_mikan(row("1", "第二字幕组", number=1) +
                            row("01v2", number=2) + row("01", number=3) +
                            row("02", number=4) + row("12.5", number=5))
        selected = select_for_series(items, series(subtitle_groups=["绿茶字幕组", "第二字幕组"]))
        self.assertEqual([item["infohash"][-1] for item in selected], ["2", "4", "5"])

    def test_language_and_embedded_preferences(self):
        items = parse_mikan(row(language="繁体内嵌", number=1) +
                            row(language="简繁外挂", number=2) +
                            row(language="GB_CN 内嵌", number=3))
        self.assertEqual(select_for_series(items, series())[0]["infohash"][-1], "3")
        self.assertEqual(select_for_series(items, series(language="繁体"))[0]["infohash"][-1], "1")
        self.assertEqual(select_for_series(items, series(language="简繁", embedded_only=False))[0]["infohash"][-1], "2")
        self.assertFalse(select_for_series(items, series(language="简繁")))


class StorageTests(TemporaryProject):
    def test_transactions_do_not_lose_concurrent_updates(self):
        def add(index):
            storage.mutate_list(lambda cfg: cfg["series"].append(series(mikan_id=index + 1)))
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(add, range(16)))
        self.assertEqual(len(storage.load_list()["series"]), 17)

    def test_lock_timeout_closes_handle_and_releases(self):
        with FileLock(storage.LIST_LOCK):
            with self.assertRaises(TimeoutError):
                with FileLock(storage.LIST_LOCK, timeout=0):
                    pass
        with FileLock(storage.LIST_LOCK, timeout=0):
            pass

    def test_lock_waits_instead_of_writing_to_an_empty_locked_file(self):
        with open(storage.LIST_LOCK, "w+b") as owner:
            if os.name == "nt":
                filelock.msvcrt.locking(owner.fileno(), filelock.msvcrt.LK_NBLCK, 1)
            else:
                filelock.fcntl.flock(owner.fileno(), filelock.fcntl.LOCK_EX | filelock.fcntl.LOCK_NB)
            try:
                with self.assertRaises(TimeoutError):
                    with FileLock(storage.LIST_LOCK, timeout=0):
                        pass
                self.assertEqual(os.path.getsize(storage.LIST_LOCK), 0)
            finally:
                owner.seek(0)
                if os.name == "nt":
                    filelock.msvcrt.locking(owner.fileno(), filelock.msvcrt.LK_UNLCK, 1)
                else:
                    filelock.fcntl.flock(owner.fileno(), filelock.fcntl.LOCK_UN)
        with FileLock(storage.LIST_LOCK, timeout=0):
            pass

    def test_lock_blocks_another_process(self):
        code = "from filelock import FileLock; import sys; FileLock(sys.argv[1], timeout=0).__enter__()"
        with FileLock(storage.LIST_LOCK):
            result = subprocess.run([sys.executable, "-c", code, storage.LIST_LOCK], capture_output=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"TimeoutError", result.stderr)

    def test_failed_replace_keeps_original_json(self):
        with patch.object(storage.os, "replace", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                storage.write_json(storage.LIST_FILE, {"broken": True})
        self.assertEqual(storage.load_list()["series"][0]["mikan_id"], 4059)

    def test_validation_rejects_bad_types(self):
        for changes in ({"mikan_id": True}, {"subtitle_groups": "错误"}, {"embedded_only": "true"},
                        {"save_path": "../x"}, {"last_episode": "NaN"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                storage.validate_series(series(**changes))


class BaiduTests(TemporaryProject):
    def test_multifield_token_request(self):
        client = baidu.BaiduClient({"BDUSS": "fake-test-value"})
        with patch.object(baidu, "req", return_value=b'{"errno":0,"result":{"bdstoken":"token"}}') as request:
            self.assertTrue(client.login_info()["logged_in"])
        fields = urllib.parse.parse_qs(urllib.parse.urlsplit(request.call_args.args[1]).query)["fields"][0]
        self.assertEqual(json.loads(fields), ["bdstoken", "username", "dp"])

    def test_valid_session_is_not_rejected_by_token_endpoint(self):
        client = baidu.BaiduClient({"BDUSS": "fake-test-value"})
        responses = [b'{"errno":-6}', b'{"errno":0,"list":[]}',
                     b'<script>window.locals={"bdstoken":"0123456789abcdef0123456789abcdef"}</script>']
        with patch.object(baidu, "req", side_effect=responses):
            self.assertTrue(client.login_info()["logged_in"])
        self.assertEqual(client.token, "0123456789abcdef0123456789abcdef")

    def test_valid_session_without_token_has_specific_warning(self):
        client = baidu.BaiduClient({"BDUSS": "fake-test-value"})
        responses = [b'{"errno":-6}', b'{"errno":0,"list":[]}', b'<html>no token</html>']
        with patch.object(baidu, "req", side_effect=responses):
            info = client.login_info()
        self.assertTrue(info["logged_in"])
        self.assertEqual(info["status"], "TOKEN_UNAVAILABLE")
        with patch.object(client, "login_info", return_value=info), patch.object(baidu, "req") as request:
            with self.assertRaises(baidu.APIError):
                client.add_task("magnet:fake", "/test")
        request.assert_not_called()

    def test_token_page_probe_does_not_clear_verified_session(self):
        client = baidu.BaiduClient({"BDUSS": "fake-test-value"})
        original_opener = client.opener
        def response(opener, url, **kwargs):
            if "gettemplatevariable" in url:
                return b'{"errno":-6}'
            if "/api/list" in url:
                return b'{"errno":0,"list":[]}'
            self.assertIsNot(opener, original_opener)
            for handler in opener.handlers:
                if isinstance(handler, urllib.request.HTTPCookieProcessor):
                    handler.cookiejar.clear()
            return b'<html>not logged in</html>'
        with patch.object(baidu, "req", side_effect=response):
            self.assertTrue(client.login_info()["logged_in"])
        self.assertEqual(baidu.pan_cookies(client.jar)["BDUSS"], "fake-test-value")

    def test_login_not_accepted_by_both_endpoints(self):
        client = baidu.BaiduClient({"BDUSS": "fake-test-value"})
        with patch.object(baidu, "req", return_value=b'{"errno":-6}'):
            with self.assertRaisesRegex(baidu.LoginRequired, "暂未接受"):
                client.login_info()

    def test_network_failure_is_not_auth_failure(self):
        client = baidu.BaiduClient({"BDUSS": "fake-test-value"})
        with patch.object(baidu, "req", side_effect=urllib.error.URLError("offline")) as request:
            with self.assertRaises(urllib.error.URLError):
                client.login_info()
        self.assertEqual(request.call_count, 1)

    def test_export_excludes_passport_only_cookies(self):
        _, base_jar = baidu.make_opener({"BDUSS": "formal-pan-cookie"})
        base = next(iter(base_jar))
        jar = baidu.http.cookiejar.CookieJar()
        passport_cookie = copy.copy(base)
        passport_cookie.domain = "passport.baidu.com"
        passport_cookie.value = "passport-only-cookie"
        jar.set_cookie(passport_cookie)
        pan_cookie = copy.copy(base)
        pan_cookie.domain = "pan.baidu.com"
        jar.set_cookie(pan_cookie)
        self.assertEqual(baidu.pan_cookies(jar)["BDUSS"], "formal-pan-cookie")

    def test_read_retries_but_submission_does_not(self):
        opener = Mock()
        opener.open.side_effect = urllib.error.URLError("network")
        with patch.object(baidu.time, "sleep"):
            with self.assertRaises(urllib.error.URLError):
                baidu.req(opener, "https://example.invalid/")
        self.assertEqual(opener.open.call_count, 3)
        opener.reset_mock()
        with self.assertRaises(urllib.error.URLError):
            baidu.req(opener, "https://example.invalid/", data=b"x", retry=False)
        self.assertEqual(opener.open.call_count, 1)

    def test_magnet_type_and_single_submission_attempt(self):
        client = baidu.BaiduClient({"BDUSS": "fake-test-value"})
        client.token = "token"
        with patch.object(baidu, "req", return_value=b'{"task_id":"1"}') as request:
            client.add_task("magnet:?xt=urn:btih:" + "a" * 40, "/测试")
        params = urllib.parse.parse_qs(request.call_args.kwargs["data"].decode())
        self.assertEqual(params["type"], ["4"])
        self.assertIs(request.call_args.kwargs["retry"], False)

    def test_query_task_handles_both_response_shapes(self):
        client = baidu.BaiduClient({"BDUSS": "fake-test-value"})
        for data in ({"task_info": {"1": {"status": "0"}}},
                     {"task_info": [{"task_id": "1", "status": "0"}]}):
            with patch.object(client, "cloud", return_value=data):
                self.assertEqual(client.query_tasks(["1"])["1"]["status"], "0")

    def test_directory_pagination_and_missing_directory(self):
        client = baidu.BaiduClient({"BDUSS": "fake-test-value"})
        first = [{"fs_id": n, "server_filename": str(n)} for n in range(1, 101)]
        responses = [json.dumps({"errno": 0, "list": first}).encode(),
                     b'{"errno":0,"has_more":"0","list":[{"fs_id":101,"server_filename":"last"}]}']
        with patch.object(baidu, "req", side_effect=responses):
            self.assertEqual(len(client.list_files("/测试")), 101)
        with patch.object(baidu, "req", return_value=b'{"errno":-9}'):
            with self.assertRaises(baidu.APIError):
                client.list_files("/missing")

    def test_redaction_and_corrupt_login_cache(self):
        self.assertNotIn("secret", baidu.safe_error(Exception("error https://x/?bduss=secret")))
        with open(baidu.COOKIE_FILE, "w") as stream:
            stream.write("bad-json")
        with self.assertRaises(baidu.LoginRequired):
            baidu.BaiduClient()


class PipelineTests(TemporaryProject):
    def test_failure_cannot_advance_cursor_past_success(self):
        result = self.run_pipeline(row("01", number=1) + row("02", number=2), self.client({"1": 5}))
        self.assertEqual(result["status"], "GENERIC")
        self.assertEqual(storage.load_list()["series"][0]["last_episode"], "0")

    def test_pending_is_not_completed_and_not_submitted_twice(self):
        client = self.client({"1": 1})
        self.assertEqual(self.run_pipeline(row(), client)["status"], "PENDING")
        self.assertEqual(self.run_pipeline(row(), client)["status"], "PENDING")
        self.assertEqual(client.add_task.call_count, 1)
        self.assertEqual(storage.load_list()["series"][0]["last_episode"], "0")

    def test_pending_reconciles_and_advances_cursor(self):
        client = self.client({"1": 1})
        self.run_pipeline(row(), client)
        client.query_tasks.side_effect = lambda ids: {"1": {"result": 0, "status": "0"}}
        result = self.run_pipeline(row(), client)
        self.assertEqual(result["status"], "OK")
        self.assertEqual(storage.load_list()["series"][0]["last_episode"], "1")
        self.assertEqual(client.add_task.call_count, 1)

    def test_submission_response_loss_does_not_repeat(self):
        client = self.client()
        client.add_task.side_effect = urllib.error.URLError("lost response")
        self.assertEqual(self.run_pipeline(row(), client)["status"], "UNKNOWN")
        self.run_pipeline(row(), client)
        self.assertEqual(client.add_task.call_count, 1)

    def test_rejected_submission_has_bounded_retries(self):
        client = self.client()
        client.add_task.side_effect = None
        client.add_task.return_value = {"error_code": 99}
        for _ in range(4):
            self.assertEqual(self.run_pipeline(row(), client)["status"], "GENERIC")
        self.assertEqual(client.add_task.call_count, 3)

    def test_bak_and_missing_tasks_cannot_claim_success(self):
        record = {"state": "PENDING"}
        auto_dl.update_task(record, {"status": 0, "task_name": "x.bak"})
        self.assertEqual(record["state"], "FAILED")
        auto_dl.update_task(record, {"result": 1})
        self.assertEqual(record["state"], "UNKNOWN")

    def test_fetch_error_is_network_error_and_preserves_credentials(self):
        storage.write_json(baidu.COOKIE_FILE, {"BDUSS": "test"})
        with patch.object(auto_dl, "fetch_mikan", side_effect=urllib.error.URLError("proxy unavailable")):
            self.assertEqual(auto_dl.run(client=self.client())["status"], "NET_ERROR")
        self.assertTrue(os.path.exists(baidu.COOKIE_FILE))

    def test_no_updates_and_empty_list(self):
        storage.write_json(storage.LIST_FILE, {"series": [series(last_episode="03")]})
        self.assertEqual(self.run_pipeline(row(), self.client())["status"], "NO_UPDATES")
        storage.write_json(storage.LIST_FILE, {"series": []})
        self.assertEqual(auto_dl.run()["status"], "NO_UPDATES")

    def test_busy_run_does_not_overwrite_state(self):
        storage.write_json(auto_dl.RUN_LOG, {"status": "RUNNING", "msg": "other process"})
        with FileLock(auto_dl.RUN_LOCK):
            self.assertEqual(auto_dl.run()["status"], "BUSY")
        self.assertEqual(storage.read_json(auto_dl.RUN_LOG)["msg"], "other process")

    def test_configuration_edits_are_preserved(self):
        client = self.client()
        def submit(magnet, path):
            storage.mutate_list(lambda cfg: cfg["series"][0].update(notes="用户刚刚更新"))
            return {"errno": 0, "task_id": "1"}
        client.add_task.side_effect = submit
        self.run_pipeline(row(), client)
        self.assertEqual(storage.load_list()["series"][0]["notes"], "用户刚刚更新")

    def test_deleted_series_is_not_restored(self):
        client = self.client()
        client.add_task.side_effect = lambda *_: (storage.mutate_list(lambda cfg: cfg.update(series=[])) or {"errno": 0, "task_id": "1"})
        self.run_pipeline(row(), client)
        self.assertEqual(storage.load_list()["series"], [])

    def test_retry_clears_only_known_failures(self):
        history = {state: {"mikan_id": 4059, "state": state}
                   for state in ("FAILED", "REJECTED", "UNKNOWN", "PENDING", "COMPLETED")}
        storage.write_json(auto_dl.HISTORY_FILE, history)
        self.assertEqual(auto_dl.retry_failed(4059), 2)
        self.assertEqual(set(storage.read_json(auto_dl.HISTORY_FILE)), {"UNKNOWN", "PENDING", "COMPLETED"})

    def test_withdrawn_failed_episode_still_blocks_cursor(self):
        client = self.client({"1": 5})
        self.run_pipeline(row("01", number=1), client)
        result = self.run_pipeline(row("02", number=2), client)
        self.assertEqual(result["status"], "GENERIC")
        self.assertEqual(storage.load_list()["series"][0]["last_episode"], "0")
        self.assertEqual(len(result["submitted"]), 2)

    def test_unknown_resolution_requires_matching_state(self):
        client = self.client()
        client.add_task.side_effect = TimeoutError("lost response")
        self.run_pipeline(row(), client)
        key = next(iter(storage.read_json(auto_dl.HISTORY_FILE)))
        auto_dl.resolve_unknown(key, "completed")
        self.assertEqual(self.run_pipeline(row(), client)["status"], "OK")
        self.assertEqual(client.add_task.call_count, 1)
        with self.assertRaises(ValueError):
            auto_dl.resolve_unknown(key, "retry")

    def test_persistent_unknown_is_reported_if_resource_disappears(self):
        client = self.client()
        client.add_task.side_effect = TimeoutError("lost response")
        self.run_pipeline(row(), client)
        result = self.run_pipeline(row("00", number=9), client)
        self.assertEqual(result["status"], "UNKNOWN")

    def test_network_login_failure_preserves_cookie_cache(self):
        storage.write_json(baidu.COOKIE_FILE, {"BDUSS": "fake"})
        with patch.object(auto_dl, "BaiduClient") as cls:
            cls.return_value.login_info.side_effect = urllib.error.URLError("offline")
            self.assertEqual(auto_dl.run()["status"], "NET_ERROR")
        self.assertTrue(os.path.exists(baidu.COOKIE_FILE))


class QRTests(TemporaryProject):
    def test_login_redirect_rejects_external_urls(self):
        for url in ("https://baidu.com.evil.invalid/", "http://other.invalid/", "https://other.invalid/",
                    "https://attacker@pan.baidu.com/", "https://pan.baidu.com:8443/"):
            raw = json.dumps({"data": {"u": url}}).encode()
            with self.subTest(url=url), patch.object(qr_cloud_dl, "req") as request:
                with self.assertRaises(ValueError):
                    qr_cloud_dl.complete_login_redirect(Mock(), raw)
                request.assert_not_called()

    def test_login_redirect_normalizes_baidu_urls_to_https(self):
        for target in ("http://pan.baidu.com/callback", "/callback", "//pan.baidu.com/callback",
                       "https%253A%252F%252Fpan.baidu.com%252Fcallback"):
            with self.subTest(target=target), patch.object(qr_cloud_dl, "req") as request:
                qr_cloud_dl.complete_login_redirect(Mock(), json.dumps({"data": {"u": target}}).encode())
                self.assertEqual(request.call_args.args[1], "https://pan.baidu.com/callback")

    def test_pending_exchange_retains_domains_and_expires(self):
        _, jar = baidu.make_opener({"BDUSS": "formal-cookie"})
        raw = b'jsonp({"data":{"u":"https://pan.baidu.com/callback?ticket=secret"}})'
        qr_cloud_dl.save_pending(jar, raw)
        _, restored, _ = qr_cloud_dl.load_pending()
        self.assertEqual(baidu.pan_cookies(restored)["BDUSS"], "formal-cookie")
        with patch.object(qr_cloud_dl.time, "time", return_value=qr_cloud_dl.time.time() + 361):
            self.assertIsNone(qr_cloud_dl.load_pending())
        self.assertFalse(os.path.exists(qr_cloud_dl.PENDING_FILE))

    def test_login_redirect_supports_single_quoted_passport_response(self):
        raw = b"jsonp({'errInfo':{'no':'0'},'data':{'u':'https://pan.baidu.com/login/callback?ticket=test'}});"
        with patch.object(qr_cloud_dl, "req") as request:
            self.assertTrue(qr_cloud_dl.complete_login_redirect(Mock(), raw))
        self.assertEqual(request.call_args.args[1], "https://pan.baidu.com/login/callback?ticket=test")
        self.assertFalse(request.call_args.kwargs["retry"])

    def test_callback_parser_never_executes_javascript(self):
        with self.assertRaisesRegex(ValueError, "响应格式异常"):
            qr_cloud_dl.strip_callback(b"jsonp({'data': __import__('os')})")

    def test_cached_login_never_submits(self):
        client = Mock()
        client.login_info.return_value = {"username": "测试"}
        with patch.object(qr_cloud_dl, "BaiduClient", return_value=client):
            self.assertIs(qr_cloud_dl.ensure_login(), client)
        client.add_task.assert_not_called()

    def test_cached_login_network_error_does_not_start_qr(self):
        client = Mock()
        client.login_info.side_effect = urllib.error.URLError("offline")
        with patch.object(qr_cloud_dl, "BaiduClient", return_value=client), patch.object(qr_cloud_dl, "req") as request:
            with self.assertRaises(urllib.error.URLError):
                qr_cloud_dl.ensure_login()
        request.assert_not_called()

    def test_full_qr_flow_validates_before_saving(self):
        client = Mock()
        client.login_info.return_value = {"username": "测试"}
        events = []
        opener, jar = baidu.make_opener()
        requests = []
        def request(opener, url, **kwargs):
            requests.append(url)
            if "getqrcode" in url:
                return b'jsonp({"sign":"sign","imgurl":"//passport.baidu.com/qr.png"})'
            if "unicast" in url:
                return b'jsonp({"errno":0,"channel_v":{"status":0,"v":"temporary-ticket"}})'
            if "qrbdusslogin" in url:
                return b'jsonp({"errInfo":{"no":"0"},"data":{"u":"https://pan.baidu.com/login/callback?ticket=test"}})'
            if "/login/callback" in url:
                _, cookies = baidu.make_opener({"BDUSS": "formal-pan-cookie", "STOKEN": "formal-stoken"})
                for cookie in cookies:
                    jar.set_cookie(cookie)
                return b"OK"
            return b"PNG"
        with patch.object(qr_cloud_dl, "BaiduClient", return_value=client), \
             patch.object(qr_cloud_dl, "make_opener", return_value=(opener, jar)), \
             patch.object(qr_cloud_dl, "req", side_effect=request):
            qr_cloud_dl.ensure_login(qr_path=web_app.QR_PNG, on_state=lambda **state: events.append(state), force=True)
        self.assertEqual(storage.read_json(qr_cloud_dl.COOKIE_FILE)["BDUSS"], "formal-pan-cookie")
        self.assertTrue(any(event.get("qr_ready") for event in events))
        client.add_task.assert_not_called()
        exchange = next(url for url in requests if "qrbdusslogin" in url)
        params = urllib.parse.parse_qs(urllib.parse.urlsplit(exchange).query)
        self.assertEqual(params["loginVersion"], ["v4"])
        self.assertEqual(params["apiver"], ["v3"])
        self.assertEqual(params["alg"], ["v3"])
        self.assertEqual(params["bduss"], ["temporary-ticket"])
        self.assertEqual(params["u"], ["https%3A%2F%2Fpan.baidu.com%2Fdisk%2Fmain"])
        self.assertTrue(storage.read_json(qr_cloud_dl.DIAGNOSTIC_FILE)["redirect_followed"])
        diagnostic = json.dumps(storage.read_json(qr_cloud_dl.DIAGNOSTIC_FILE))
        self.assertNotIn("formal-pan-cookie", diagnostic)
        self.assertNotIn("temporary-ticket", diagnostic)

    def test_exchange_without_real_cookie_cannot_use_temporary_ticket(self):
        responses = [b"warmup", b'jsonp({"sign":"s","imgurl":"passport.baidu.com/q.png"})', b"PNG",
                     b'jsonp({"errno":0,"channel_v":{"status":0,"v":"temporary-ticket"}})',
                     b'jsonp({"errInfo":{"no":"0"}})']
        with patch.object(qr_cloud_dl, "req", side_effect=responses), \
             patch.object(qr_cloud_dl, "BaiduClient") as client:
            with self.assertRaisesRegex(ValueError, "未下发"):
                qr_cloud_dl.ensure_login(qr_path=web_app.QR_PNG, force=True, on_state=lambda **_: None)
        self.assertFalse(os.path.exists(qr_cloud_dl.COOKIE_FILE))
        client.assert_not_called()

    def test_scan_without_confirmation_does_not_exchange(self):
        responses = [b"warmup", b'jsonp({"sign":"s","imgurl":"passport.baidu.com/q.png"})', b"PNG",
                     b'jsonp({"errno":0,"channel_v":{"status":1,"v":"temporary-ticket"}})',
                     RuntimeError("stop after pending confirmation")]
        with patch.object(qr_cloud_dl, "req", side_effect=responses) as request, \
             patch.object(qr_cloud_dl.time, "sleep"):
            with self.assertRaises(RuntimeError):
                qr_cloud_dl.ensure_login(qr_path=web_app.QR_PNG, force=True, on_state=lambda **_: None)
        self.assertFalse(any("qrbdusslogin" in call.args[1] for call in request.call_args_list))

    def test_exchange_error_code_is_reported_without_response_secrets(self):
        raw = b"jsonp({'errInfo':{'no':'400031','msg':'secret-bduss'}});"
        self.assertEqual(qr_cloud_dl.exchange_error(raw), "400031")
        responses = [b"warmup", b'jsonp({"sign":"s","imgurl":"passport.baidu.com/q.png"})', b"PNG",
                     b'jsonp({"errno":0,"channel_v":{"status":0,"v":"temporary-ticket"}})', raw]
        with patch.object(qr_cloud_dl, "req", side_effect=responses):
            with self.assertRaises(ValueError) as caught:
                qr_cloud_dl.ensure_login(qr_path=web_app.QR_PNG, force=True, on_state=lambda **_: None)
        self.assertIn("400031", str(caught.exception))
        self.assertNotIn("secret-bduss", str(caught.exception))


class HTTPFixture(TemporaryProject):
    def setUp(self):
        super().setUp()
        self.server = web_app.ThreadingHTTPServer(("127.0.0.1", 0), web_app.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.root = f"http://127.0.0.1:{self.server.server_port}"

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(self, path, method="GET", body=None, headers=None):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.root + path, data=data, method=method,
                                         headers={"Content-Type": "application/json", **(headers or {})})
        try:
            response = urllib.request.urlopen(request)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            raw = response.read()
            return response.status, json.loads(raw) if "application/json" in response.headers.get("Content-Type", "") else raw

class HTTPTests(HTTPFixture):
    def test_crud_validation_and_duplicate_guard(self):
        self.assertEqual(self.request("/api/series")[0], 200)
        self.assertEqual(self.request("/api/series", "POST", series())[0], 400)
        self.assertEqual(self.request("/api/series", "POST", series(mikan_id=2))[0], 201)
        self.assertEqual(self.request("/api/series/1", "PUT", {"mikan_id": 4059, "save_path": "/x"})[0], 400)
        self.assertEqual(self.request("/api/series/1", "PUT", {"mikan_id": 2, "save_path": "/Anime/x"})[0], 200)
        self.assertEqual(self.request("/api/series/1", "DELETE", {"mikan_id": 2})[0], 200)
        self.assertEqual(self.request("/api/series/99", "DELETE")[0], 404)
        self.assertEqual(self.request("/api/series", "POST", {"mikan_id": "bad"})[0], 400)

    def test_static_traversal_and_external_origin_rejected(self):
        self.assertEqual(self.request("/")[0], 200)
        self.assertEqual(self.request("/static/app.js")[0], 200)
        self.assertEqual(self.request("/static/../README.md")[0], 404)
        self.assertEqual(self.request("/static/%2e%2e/README.md")[0], 404)
        self.assertEqual(self.request("/api/refresh", "POST", {}, {"Origin": "https://evil.invalid"})[0], 403)

    def test_login_status_checks_actual_session(self):
        with patch.object(web_app, "login_status", return_value={"logged_in": False, "status": "QRCODE_NEEDED"}):
            self.assertFalse(self.request("/api/cookies")[1]["logged_in"])

    def test_task_resolution_and_state_validation(self):
        record = {"mikan_id": 4059, "state": "UNKNOWN", "name": "测试番 第01话"}
        storage.write_json(auto_dl.HISTORY_FILE, {"key": record})
        self.assertEqual(len(self.request("/api/tasks?mikan_id=4059")[1]["tasks"]), 1)
        self.assertEqual(self.request("/api/tasks/resolve", "POST", {"key": "key", "action": "completed"})[0], 200)
        self.assertEqual(self.request("/api/tasks/resolve", "POST", {"key": "key", "action": "retry"})[0], 400)

    def test_request_body_must_be_an_object(self):
        self.assertEqual(self.request("/api/series", "POST", ["invalid"])[0], 400)

    def test_agent_http_lifecycle_updates_library_and_hides_claim_token(self):
        job = catalog.enqueue(series(), parse_mikan(row()))[0]
        claim = self.request("/api/agent/claim", "POST", {"agent": "HTTP-test"})[1]["job"]
        body = {"job_id": job["id"], "token": claim["claim_token"], "evidence": "网页实际受理该话任务"}
        for state in ("SUBMITTING", "SUBMITTED"):
            self.assertEqual(self.request("/api/agent/report", "POST", {**body, "state": state})[0], 200)
        data = self.request("/api/library")[1]
        self.assertEqual(data["stats"]["SUBMITTED"], 1)
        self.assertNotIn("claim_token", json.dumps(data))
        self.assertEqual(self.request("/api/agent/review", "POST", {"job_id": job["id"], "state": "COMPLETED", "evidence": "实际文件01.mp4已存在目标目录"})[0], 200)
        self.assertEqual(self.request("/api/library")[1]["stats"]["COMPLETED"], 1)

    def test_manual_record_http_rejects_missing_evidence(self):
        job = catalog.enqueue(series(), parse_mikan(row()))[0]
        body = {"job_id": job["id"], "state": "COMPLETED", "source": "user"}
        self.assertEqual(self.request("/api/agent/observe", "POST", {**body, "evidence": ""})[0], 400)
        self.assertEqual(self.request("/api/agent/observe", "POST", {**body, "evidence": "用户明确确认01.mp4完整入盘"})[0], 200)
        self.assertEqual(self.request("/api/library")[1]["jobs"][0]["observation_source"], "user")

    def test_export_retains_full_history(self):
        catalog.transact(lambda data: [catalog.event(data, "METADATA", f"事件 {i}") for i in range(205)])
        self.assertEqual(len(self.request("/api/library")[1]["events"]), 200)
        self.assertEqual(len(self.request("/api/library/export")[1]["events"]), 205)

    def test_batch_manual_record_http_is_atomic_and_updates_each_job(self):
        jobs = catalog.enqueue(series(), parse_mikan(row() + row("02", number=2)))
        ids = [job["id"] for job in jobs]
        body = {"job_ids": ids, "state": "COMPLETED", "source": "user", "evidence": "用户逐一核实01和02的mp4文件完整入盘"}
        self.assertEqual(self.request("/api/agent/observe-batch", "POST", {**body, "job_ids": []})[0], 400)
        self.assertEqual(self.request("/api/agent/observe-batch", "POST", {**body, "job_ids": ids + ["missing"]})[0], 400)
        self.assertEqual(self.request("/api/library")[1]["stats"]["READY"], 2)
        status, result = self.request("/api/agent/observe-batch", "POST", body)
        self.assertEqual(status, 200)
        self.assertEqual([job["id"] for job in result["jobs"]], ids)
        self.assertEqual(self.request("/api/library")[1]["stats"]["COMPLETED"], 2)
        self.assertEqual(self.request("/api/agent/observe-batch", "POST", body)[0], 400)

    def test_library_http_exposes_blocker_without_claiming_success(self):
        catalog.enqueue(series(), parse_mikan(row()))
        catalog.transact(lambda data: data.update(executor={"status": "BLOCKED"}))
        self.assertEqual(self.request("/api/agent/claim", "POST", {"agent": "test"})[0], 400)
        self.assertEqual(self.request("/api/library")[1]["stats"]["READY"], 1)

    def test_refresh_starts_in_background(self):
        event = threading.Event()
        def slow_run():
            event.wait(3)
        try:
            with patch.object(agent_bridge, "prepare", side_effect=slow_run):
                self.assertEqual(self.request("/api/refresh", "POST", {})[0], 202)
                self.assertEqual(self.request("/api/refresh", "POST", {})[0], 409)
                self.assertEqual(self.request("/api/state")[1]["status"], "RUNNING")
                event.set()
        finally:
            event.set()


if __name__ == "__main__":
    unittest.main()
