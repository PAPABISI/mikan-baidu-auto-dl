import contextlib
import io
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import agent_cli
import catalog
import storage
import verify


def config():
    return {"mikan_id": 4059, "name": "测试番", "save_path": "/Anime/测试番", "language": "简体",
            "embedded_only": True, "subtitle_groups": ["字幕组"], "last_episode": "0"}


def resource(ep="01", hash_char="a", version=1):
    return {"ep": ep, "infohash": hash_char * 40, "title": f"[字幕组] 测试番 [{ep}] [简体内嵌]",
            "group": "字幕组", "lang": "简体", "embedded": True, "ver": version,
            "size": "400 MB", "date": "2026/10/30 10:00"}


def file_entry(ep="01", size=453786047):
    return {"filename": f"[Studio GreenTea] 测试番 [{ep}][WebRip][1080p].mp4", "size": size}


class VerifyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix=".test-", dir=storage.BASE)
        self.addCleanup(self.tmp.cleanup)
        for module, names in ((storage, ("LIST_FILE", "LIST_LOCK")), (catalog, ("DATA_FILE", "DATA_LOCK"))):
            for name in names:
                patcher = patch.object(module, name, os.path.join(self.tmp.name, os.path.basename(getattr(module, name))))
                patcher.start()
                self.addCleanup(patcher.stop)
        storage.write_json(storage.LIST_FILE, {"series": [config()]})

    def enqueue_all(self, episodes=("01", "02", "03")):
        return [job for ep in episodes for job in catalog.enqueue(config(), [resource(ep, chr(97 + int(float(ep))))])]

    def job_state(self, job_id):
        return next(job["state"] for job in catalog.snapshot()["jobs"] if job["id"] == job_id)

    def test_matching_file_completes_ready_job_with_real_evidence(self):
        job = self.enqueue_all(("01",))[0]
        plan = verify.verify_listing("/Anime/测试番", [file_entry("01")])
        self.assertTrue(plan["applied"])
        self.assertEqual(len(plan["recorded"]), 1)
        self.assertEqual(plan["recorded"][0]["state"], "COMPLETED")
        self.assertEqual(plan["recorded"][0]["observation_source"], "web")
        self.assertIn("[01]", plan["recorded"][0]["evidence"])
        self.assertIn("432.8 MB", plan["recorded"][0]["evidence"])
        self.assertEqual(self.job_state(job["id"]), "COMPLETED")
        self.assertEqual(catalog.snapshot()["stats"]["COMPLETED"], 1)

    def test_dry_run_reports_plan_without_writing(self):
        job = self.enqueue_all(("01",))[0]
        plan = verify.verify_listing("/Anime/测试番", [file_entry("01")], apply=False)
        self.assertFalse(plan["applied"])
        self.assertEqual(plan["recorded"], [])
        self.assertEqual(len(plan["matched"]), 1)
        self.assertEqual(plan["matched"][0]["state"], "READY")
        self.assertEqual(self.job_state(job["id"]), "READY")

    def test_submitted_and_unknown_jobs_can_be_verified(self):
        job = self.enqueue_all(("01",))[0]
        catalog.observe(job["id"], "SUBMITTED", "用户确认官网已受理该话", "user")
        verify.verify_listing("/Anime/测试番", [file_entry("01")])
        self.assertEqual(self.job_state(job["id"]), "COMPLETED")

    def test_ambiguous_versions_are_reported_not_recorded(self):
        jobs = self.enqueue_all(("01",))
        plan = verify.verify_listing("/Anime/测试番", [file_entry("01"), file_entry("01", size=100)])
        self.assertEqual(plan["recorded"], [])
        self.assertEqual(len(plan["ambiguous"]), 1)
        self.assertEqual(len(plan["ambiguous"][0]["filenames"]), 2)
        self.assertEqual(self.job_state(jobs[0]["id"]), "READY")

    def test_claimed_submitting_and_completed_jobs_are_never_overwritten(self):
        job = self.enqueue_all(("01",))[0]
        catalog.claim("some-agent", job["id"])
        plan = verify.verify_listing("/Anime/测试番", [file_entry("01")])
        self.assertEqual(plan["recorded"], [])
        # 正在执行的任务不参与匹配，对应文件只作为无任务文件报告。
        self.assertEqual(plan["matched"], [])
        self.assertEqual(plan["jobs_without_file"], [])
        self.assertEqual(len(plan["files_without_job"]), 1)
        self.assertEqual(self.job_state(job["id"]), "CLAIMED")

    def test_report_sides_for_both_directions(self):
        jobs = self.enqueue_all(("01", "02"))
        plan = verify.verify_listing("/Anime/测试番", [file_entry("02"), file_entry("99"),
                                                       {"filename": "[字幕组] 测试番 [01-12 合集].mkv"}])
        self.assertEqual([item["episode"] for item in plan["jobs_without_file"]], ["01"])
        self.assertEqual(len(plan["files_without_job"]), 2)
        self.assertEqual(self.job_state(jobs[1]["id"]), "COMPLETED")
        self.assertEqual(self.job_state(jobs[0]["id"]), "READY")

    def test_decimal_episode_matches(self):
        job = self.enqueue_all(("12.5",))[0]
        plan = verify.verify_listing("/Anime/测试番", [file_entry("12.5")])
        self.assertEqual(len(plan["recorded"]), 1)
        self.assertEqual(self.job_state(job["id"]), "COMPLETED")

    def test_only_same_save_path_jobs_are_considered(self):
        job = self.enqueue_all(("01",))[0]
        plan = verify.verify_listing("/Anime/其他番", [file_entry("01")])
        self.assertEqual(plan["matched"], [])
        self.assertEqual(self.job_state(job["id"]), "READY")

    def test_directory_entries_and_unknown_names_do_not_match(self):
        self.enqueue_all(("01",))
        plan = verify.verify_listing("/Anime/测试番",
                                     [{"filename": "测试番", "size": 0, "isdir": True},
                                      file_entry("01")])
        self.assertEqual(len(plan["recorded"]), 1)
        self.assertNotIn("测试番", plan["files_without_job"])

    def test_invalid_input_is_rejected(self):
        self.enqueue_all(("01",))
        for files in ([], "x", [{}], [{"filename": "../escape.mp4"}], [{"filename": "a/b.mp4"}],
                      [{"filename": "ok.mp4", "size": -1}], [{"filename": "ok.mp4", "size": "big"}]):
            with self.assertRaises(ValueError):
                verify.verify_listing("/Anime/测试番", files)
        for save_path in ("/", "/Anime/", "/Anime/../x"):
            with self.assertRaises(ValueError):
                verify.verify_listing(save_path, [file_entry("01")])
        with self.assertRaises(ValueError):
            verify.verify_listing("/Anime/测试番", [file_entry("01")], source="agent")

    def test_cli_verify_reads_json_input_and_dry_run_flag(self):
        self.enqueue_all(("01",))
        body = {"save_path": "/Anime/测试番", "files": [file_entry("01")]}
        path = os.path.join(self.tmp.name, "verify.json")
        with open(path, "w", encoding="utf-8") as stream:
            json.dump(body, stream, ensure_ascii=False)
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = agent_cli.main(["verify", "--input", path, "--dry-run"])
        self.assertEqual(code, 0)
        payload = json.loads(buffer.getvalue())
        self.assertEqual(len(payload["data"]["matched"]), 1)
        self.assertEqual(catalog.snapshot()["stats"]["READY"], 1)
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = agent_cli.main(["verify", "--input", path])
        self.assertEqual(code, 0)
        self.assertEqual(catalog.snapshot()["stats"]["COMPLETED"], 1)


if __name__ == "__main__":
    unittest.main()
