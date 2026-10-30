import contextlib
import io
import json
import os
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import agent_bridge
import agent_cli
import auto_dl
import catalog
import metadata
import storage


def config():
    return {"mikan_id": 4059, "name": "测试番", "save_path": "/Anime/测试番", "language": "简体",
            "embedded_only": True, "subtitle_groups": ["字幕组"], "last_episode": "03"}


def resource(ep="01", hash_char="a", version=1):
    return {"ep": ep, "infohash": hash_char * 40, "title": f"[字幕组] 测试番 [{ep}] [简体内嵌]",
            "group": "字幕组", "lang": "简体", "embedded": True, "ver": version,
            "size": "400 MB", "date": "2026/10/23 10:00"}


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix=".test-", dir=storage.BASE)
        self.addCleanup(self.tmp.cleanup)
        for module, names in ((storage, ("LIST_FILE", "LIST_LOCK")), (catalog, ("DATA_FILE", "DATA_LOCK")),
                              (auto_dl, ("RUN_LOG",)), (agent_bridge, ("PREPARE_LOCK",))):
            for name in names:
                patcher = patch.object(module, name, os.path.join(self.tmp.name, os.path.basename(getattr(module, name))))
                patcher.start()
                self.addCleanup(patcher.stop)
        storage.write_json(storage.LIST_FILE, {"series": [config()]})

    def ready(self):
        return catalog.enqueue(config(), [resource()])[0]

    def test_path_is_always_in_anime(self):
        self.assertEqual(catalog.target_path('名字:一/二'), '/Anime/名字_一_二')
        for value in ('/测试', '/Anime/', '/Anime/../x', '/Anime//x', '/Anime//', '/Anime/x//', '/Anime/x:y'):
            with self.assertRaises(ValueError):
                catalog.check_target(value)

    def test_enqueue_is_deduplicated_and_ignores_legacy_cursor(self):
        self.assertEqual(len(catalog.enqueue(config(), [resource()])), 1)
        self.assertFalse(catalog.enqueue(config(), [resource()]))
        self.assertEqual(catalog.snapshot()["stats"]["READY"], 1)
        self.assertEqual(catalog.snapshot()["stats"]["COMPLETED"], 0)

    def test_only_one_agent_can_claim_a_job(self):
        self.ready()
        with ThreadPoolExecutor(max_workers=6) as pool:
            claims = list(pool.map(catalog.claim, ["agent"] * 6))
        self.assertEqual(sum(c is not None for c in claims), 1)

    def test_each_report_immediately_changes_web_data(self):
        self.ready()
        job = catalog.claim("test-agent")
        for state in ("SUBMITTING", "SUBMITTED", "DOWNLOADING", "COMPLETED"):
            catalog.report(job["id"], job["claim_token"], state, "官网观察到状态：" + state)
            self.assertEqual(catalog.snapshot()["jobs"][0]["state"], state)
        self.assertEqual(catalog.snapshot()["stats"]["COMPLETED"], 1)
        self.assertNotIn("claim_token", json.dumps(catalog.snapshot()))

    def test_submitted_cannot_be_claimed_or_replaced(self):
        self.ready()
        job = catalog.claim("test-agent")
        catalog.report(job["id"], job["claim_token"], "SUBMITTING", "已确认目标路径，准备提交")
        catalog.report(job["id"], job["claim_token"], "SUBMITTED", "官网明确提示已添加任务")
        self.assertIsNone(catalog.claim("another-agent"))
        self.assertFalse(catalog.enqueue(config(), [resource("1", "b", 2)]))

    def test_expired_submission_becomes_unknown_and_cannot_repeat(self):
        self.ready()
        job = catalog.claim("test-agent")
        catalog.report(job["id"], job["claim_token"], "SUBMITTING", "准备点击最终提交按钮")
        with patch.object(catalog.time, "time", return_value=time.time() + 901):
            self.assertIsNone(catalog.claim("new-agent"))
        self.assertEqual(catalog.snapshot()["jobs"][0]["state"], "UNKNOWN")
        with self.assertRaises(ValueError):
            catalog.report(job["id"], job["claim_token"], "SUBMITTED", "旧 Agent 迟到的结果")

    def test_expired_unstarted_claim_can_be_reclaimed(self):
        self.ready()
        job = catalog.claim("test-agent")
        with patch.object(catalog.time, "time", return_value=time.time() + 901):
            next_job = catalog.claim("new-agent")
        self.assertNotEqual(job["claim_token"], next_job["claim_token"])

    def test_ready_revision_can_be_superseded(self):
        self.ready()
        catalog.enqueue(config(), [resource("1", "b", 2)])
        self.assertEqual(catalog.snapshot()["stats"]["SUPERSEDED"], 1)
        self.assertEqual(catalog.snapshot()["stats"]["READY"], 1)

    def test_report_requires_token_legal_transition_and_evidence(self):
        self.ready()
        job = catalog.claim("test-agent")
        for token, state, evidence in (("wrong", "SUBMITTING", "已准备提交"),
                                       (job["claim_token"], "COMPLETED", "并未执行实际操作"),
                                       (job["claim_token"], "SUBMITTING", "")):
            with self.assertRaises(ValueError):
                catalog.report(job["id"], token, state, evidence)

    def test_repeated_identical_report_does_not_duplicate_history(self):
        self.ready()
        job = catalog.claim("test-agent")
        for _ in range(2):
            catalog.report(job["id"], job["claim_token"], "SUBMITTING", "准备点击最终提交按钮")
        events = [e for e in catalog.snapshot()["events"] if e["kind"] == "SUBMITTING"]
        self.assertEqual(len(events), 1)

    def test_credentials_are_redacted_from_evidence(self):
        self.ready()
        job = catalog.claim("test-agent")
        catalog.report(job["id"], job["claim_token"], "SUBMITTING", "准备提交 BDUSS=not-a-real-cookie")
        self.assertNotIn("not-a-real-cookie", json.dumps(catalog.snapshot()))

    def test_browser_block_preserves_queue_and_prevents_claim(self):
        self.ready()
        catalog.transact(lambda data: data.update(executor={"status": "BLOCKED", "message": "工具策略拦截"}))
        with self.assertRaisesRegex(ValueError, "执行受阻"):
            catalog.claim("agent")
        self.assertEqual(catalog.snapshot()["stats"]["READY"], 1)

    def test_manual_record_is_attributed_and_prevents_resubmission(self):
        job = self.ready()
        catalog.observe(job["id"], "SUBMITTED", "用户确认官网已受理第01话到 /Anime/测试番", "user")
        self.assertEqual(catalog.snapshot()["stats"]["COMPLETED"], 0)
        catalog.observe(job["id"], "COMPLETED", "/Anime/测试番/01.mp4 已完整入盘", "user")
        self.assertEqual(catalog.snapshot()["jobs"][0]["observation_source"], "user")
        self.assertEqual(catalog.snapshot()["stats"]["COMPLETED"], 1)
        self.assertIsNone(catalog.claim("agent"))
        self.assertFalse(catalog.enqueue(config(), [resource("01", "b")]))

    def test_observation_cannot_override_active_agent_or_without_evidence(self):
        job = self.ready()
        catalog.claim("agent")
        with self.assertRaises(ValueError):
            catalog.observe(job["id"], "COMPLETED", "实际文件已存在目录中", "web")
        with self.assertRaises(ValueError):
            catalog.observe(job["id"], "COMPLETED", "", "user")

    def test_batch_observation_records_each_selected_episode_only(self):
        jobs = catalog.enqueue(config(), [resource(), resource("02", "b"), resource("03", "c")])
        ids = [jobs[0]["id"], jobs[2]["id"]]
        result = catalog.observe_batch(ids, "COMPLETED", "已逐一核实01和03的mp4文件完整入盘", "user")
        data = catalog.snapshot()
        self.assertEqual(data["stats"]["COMPLETED"], 2)
        self.assertEqual(data["stats"]["READY"], 1)
        self.assertEqual([job["id"] for job in result], ids)
        self.assertNotIn("claim_token", json.dumps(result))
        for job in result:
            self.assertEqual(job["observation_source"], "user")
            self.assertTrue(job["completed_at"])
            self.assertIn("第 " + job["episode"] + " 话", job["evidence"])
            self.assertIn(job["save_path"], job["evidence"])
        events = [event for event in data["events"] if event["kind"] == "COMPLETED"]
        self.assertEqual({event["job_id"] for event in events}, set(ids))
        self.assertTrue(all(event["source"] == "user" for event in events))

    def test_batch_observation_conflict_preserves_entire_library(self):
        jobs = catalog.enqueue(config(), [resource(), resource("02", "b")])
        ids = [job["id"] for job in jobs]
        for state in ("CLAIMED", "SUBMITTING", "SUPERSEDED", "COMPLETED"):
            with self.subTest(state=state):
                catalog.transact(lambda data: data["jobs"][ids[1]].update(state=state))
                before = catalog.load()
                with self.assertRaises(ValueError):
                    catalog.observe_batch(ids, "COMPLETED", "用户核实所选文件已入盘", "user")
                self.assertEqual(catalog.load(), before)

    def test_batch_observation_rejects_invalid_ids_and_evidence(self):
        job = self.ready()
        before = catalog.load()
        for ids in ([], job["id"], [job["id"], job["id"]], [job["id"], None], [job["id"], "missing"]):
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                catalog.observe_batch(ids, "COMPLETED", "已核实文件入盘", "user")
        for state, evidence, source in (("READY", "实际文件已存在", "user"),
                                        ("COMPLETED", "", "user"), ("COMPLETED", "实际文件已存在", "agent")):
            with self.subTest(state=state, source=source), self.assertRaises(ValueError):
                catalog.observe_batch([job["id"]], state, evidence, source)
        self.assertEqual(catalog.load(), before)

    def test_batch_submission_is_not_completion_and_preserves_blocker(self):
        jobs = catalog.enqueue(config(), [resource(), resource("02", "b")])
        catalog.transact(lambda data: data.update(executor={"status": "BLOCKED"}))
        catalog.observe_batch([job["id"] for job in jobs], "SUBMITTED",
                              "官网已受理这两话 BDUSS=not-a-real-cookie", "user")
        data = catalog.snapshot()
        self.assertEqual(data["stats"]["SUBMITTED"], 2)
        self.assertEqual(data["stats"]["COMPLETED"], 0)
        self.assertEqual(data["executor"]["status"], "BLOCKED")
        self.assertNotIn("not-a-real-cookie", json.dumps(data))

    def test_batch_observation_cannot_mix_series_or_directories(self):
        job = self.ready()
        for other in ({**config(), "mikan_id": 4060}, {**config(), "save_path": "/Anime/其他目录"}):
            other_job = catalog.enqueue(other, [resource("02", "b")])[0]
            before = catalog.load()
            with self.assertRaisesRegex(ValueError, "同一番剧和保存目录"):
                catalog.observe_batch([job["id"], other_job["id"]], "COMPLETED", "用户核实了目录文件", "user")
            self.assertEqual(catalog.load(), before)

    def test_review_redacts_credentials(self):
        self.ready()
        job = catalog.claim("agent")
        catalog.report(job["id"], job["claim_token"], "SUBMITTING", "准备提交第01话")
        catalog.report(job["id"], job["claim_token"], "SUBMITTED", "任务已受理等待核验")
        catalog.review(job["id"], "COMPLETED", "文件已存在 BDUSS=not-a-real-cookie")
        self.assertNotIn("not-a-real-cookie", json.dumps(catalog.snapshot()))

    def test_prepare_failure_is_not_no_updates(self):
        with patch.object(auto_dl, "fetch_mikan", side_effect=OSError("proxy down")), contextlib.redirect_stdout(io.StringIO()):
            state = agent_bridge.prepare()
        self.assertEqual(state["status"], "GENERIC")
        self.assertEqual(catalog.snapshot()["series"][0]["last_scan_error"], "proxy down")

    def test_prepare_queues_all_episodes_in_new_directory(self):
        with patch.object(auto_dl, "fetch_mikan", return_value="HTML"), \
             patch.object(agent_bridge, "parse_mikan", return_value=[resource()]), \
             patch.object(agent_bridge, "select_for_series", side_effect=lambda items, cfg: items), \
             contextlib.redirect_stdout(io.StringIO()):
            state = agent_bridge.prepare()
        self.assertEqual(state["status"], "READY")
        self.assertEqual(len(catalog.snapshot()["jobs"]), 1)

    def test_prepare_replaces_legacy_official_override_with_bangumi(self):
        catalog.save_metadata(4059, {"bangumi_id": 1, "broadcast_weekday": 5,
                                   "broadcast_source": "https://example.com/onair", "broadcast_time": "20:30",
                                   "broadcast_timezone": "Asia/Shanghai", "metadata_updated_at": catalog.now()})
        fresh = metadata.normalize_subject({"id": 1, "infobox": [{"key": "放送星期", "value": "星期四"}]})
        with patch.object(auto_dl, "fetch_mikan", return_value="HTML"), \
             patch.object(agent_bridge, "parse_mikan", return_value=[resource()]), \
             patch.object(metadata, "get_subject", return_value=fresh) as request, \
             contextlib.redirect_stdout(io.StringIO()):
            agent_bridge.prepare()
        request.assert_called_once_with(1)
        data = catalog.load()["metadata"]["4059"]
        self.assertEqual(data["broadcast_weekday"], 4)
        self.assertEqual(data["broadcast_source"], "https://bgm.tv/subject/1")
        self.assertIsNone(data["broadcast_time"])

    def test_failed_bangumi_refresh_preserves_metadata_and_reports_error(self):
        values = {"bangumi_id": 1, "broadcast_weekday": 5, "broadcast_source": "https://example.com/onair"}
        catalog.save_metadata(4059, values)
        with patch.object(auto_dl, "fetch_mikan", return_value="HTML"), \
             patch.object(agent_bridge, "parse_mikan", return_value=[resource()]), \
             patch.object(metadata, "get_subject", side_effect=OSError("offline")), \
             contextlib.redirect_stdout(io.StringIO()):
            result = agent_bridge.prepare()
        self.assertEqual(result["status"], "GENERIC")
        self.assertIn("Bangumi", result["errors"][0]["msg"])
        data = catalog.load()["metadata"]["4059"]
        for key, value in values.items():
            self.assertEqual(data[key], value)

    def test_cli_is_machine_readable_json(self):
        self.ready()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = agent_cli.main(["queue"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["data"][0]["state"], "READY")


class MetadataTests(unittest.TestCase):
    def test_missing_summary_does_not_break_search(self):
        self.assertEqual(metadata.normalize_subject({"id": 1, "summary": None})["summary"], "")

    def test_main_episodes_exclude_special_entries(self):
        item = {"id": 1, "eps": 12, "total_episodes": 14,
                "infobox": [{"key": "话数", "value": "12"}, {"key": "放送星期", "value": "星期四"}]}
        data = metadata.normalize_subject(item)
        self.assertEqual(data["total_episodes"], 12)
        self.assertEqual(data["broadcast_weekday"], 4)

    def test_release_pattern_is_explicitly_inferred(self):
        data = metadata.release_pattern([resource(), resource("02", "b")])
        self.assertEqual(data["release_weekdays"], [5])
        self.assertIn("推测", data["release_note"])

    def test_bangumi_weekday_does_not_fall_back_to_premiere_date(self):
        data = metadata.normalize_subject({"id": 1, "date": "2026-10-01", "infobox": []})
        self.assertIsNone(data["broadcast_weekday"])
        self.assertIsNone(data["broadcast_time"])
        self.assertIsNone(data["broadcast_timezone"])
        self.assertEqual(data["broadcast_source"], "https://bgm.tv/subject/1")



if __name__ == "__main__":
    unittest.main()
