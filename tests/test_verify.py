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


def collection(hash_char="c", start=1, end=12, group="合集组"):
    return {"title": f"[{group}] 测试番 [{start:02d}-{end:02d} 合集][1080p][简体内嵌]",
            "infohash": hash_char * 40, "group": group, "lang": "简体", "embedded": True,
            "soft": False, "sealed": False, "resolution": "1080p", "is_collection": True,
            "episode_start": start, "episode_end": end, "episode_label": f"{start:02d}–{end:02d} 合集",
            "size": "4.9GB", "date": "2026/10/30 10:00",
            "magnet": "magnet:?xt=urn:btih:" + hash_char * 40,
            "torrent": "https://mikanani.me/Download/20261030/x.torrent"}


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

    def add_collection(self, hash_char="c", **changes):
        return catalog.discover_collections(config(), [collection(hash_char, **changes)])[0]

    def jobs_by_episode(self):
        return {job["episode"]: job for job in catalog.snapshot()["jobs"]}

    def test_collection_files_create_episode_records_with_collection_evidence(self):
        record = self.add_collection()
        plan = verify.verify_listing("/Anime/测试番", [file_entry("01"), file_entry("02")])
        self.assertEqual([item["episode"] for item in plan["created_from_collection"]], ["01", "02"])
        job = self.jobs_by_episode()["01"]
        self.assertEqual(job["state"], "COMPLETED")
        self.assertEqual(job["origin"], "collection")
        self.assertEqual(job["collection_id"], record["id"])
        self.assertIn("[01]", job["evidence"])
        self.assertIn("合集", job["evidence"])
        self.assertEqual(plan["files_without_job"], [])
        # 合集范围内没有文件的其余话数如实报告，不虚构进度。
        self.assertEqual(plan["collection_missing_episodes"][0]["episodes"][:2], ["03", "04"])

    def test_collection_dry_run_creates_nothing(self):
        self.add_collection()
        plan = verify.verify_listing("/Anime/测试番", [file_entry("01")], apply=False)
        self.assertEqual(len(plan["created_from_collection"]), 1)
        self.assertEqual(catalog.snapshot()["jobs"], [])

    def test_multiple_covering_collections_need_explicit_choice(self):
        first = self.add_collection("c")
        self.add_collection("d", group="另一个组")
        plan = verify.verify_listing("/Anime/测试番", [file_entry("01")])
        self.assertEqual(plan["created_from_collection"], [])
        self.assertEqual(len(plan["collection_choices"]), 1)
        self.assertEqual(len(plan["collection_choices"][0]["candidates"]), 2)
        self.assertEqual(catalog.snapshot()["jobs"], [])
        chosen = verify.verify_listing("/Anime/测试番", [file_entry("01")], collection_id=first["infohash"])
        self.assertEqual(len(chosen["created_from_collection"]), 1)
        self.assertEqual(self.jobs_by_episode()["01"]["collection_id"], first["id"])

    def test_uncovered_episode_reuses_collection_already_recorded_in_same_directory(self):
        first = self.add_collection("c")
        self.add_collection("d", group="另一个组")
        # 第 01 话先按显式指定入盘，第 02 话随后出现：同目录只用一个合集，自动沿用。
        verify.verify_listing("/Anime/测试番", [file_entry("01")], collection_id=first["id"])
        plan = verify.verify_listing("/Anime/测试番", [file_entry("02")])
        self.assertEqual(plan["collection_choices"], [])
        self.assertEqual([(item["episode"], item["selected_by"]) for item in plan["created_from_collection"]],
                         [("02", "same_batch")])
        self.assertEqual(self.jobs_by_episode()["02"]["collection_id"], first["id"])

    def test_two_recorded_collections_still_need_explicit_choice(self):
        first = self.add_collection("c")
        second = self.add_collection("d", group="另一个组")
        verify.verify_listing("/Anime/测试番", [file_entry("01")], collection_id=first["id"])
        verify.verify_listing("/Anime/测试番", [file_entry("03")], collection_id=second["id"])
        # 同一目录已用过两个不同合集，无法判断 02 属于谁，必须显式指定。
        plan = verify.verify_listing("/Anime/测试番", [file_entry("02")])
        self.assertEqual(plan["created_from_collection"], [])
        self.assertEqual(len(plan["collection_choices"]), 1)
        self.assertEqual(sorted(plan["collection_choices"][0]["recorded_collection_ids"]),
                         sorted([first["id"], second["id"]]))

    def test_unknown_collection_id_is_rejected_without_writing(self):
        self.add_collection(start=1, end=3)
        with self.assertRaises(ValueError):
            verify.verify_listing("/Anime/测试番", [file_entry("01")], collection_id="nope")
        self.assertEqual(catalog.snapshot()["jobs"], [])

    def test_collection_does_not_touch_running_or_completed_jobs(self):
        job = self.enqueue_all(("01",))[0]
        catalog.claim("some-agent", job["id"])
        self.add_collection()
        plan = verify.verify_listing("/Anime/测试番", [file_entry("01")])
        self.assertEqual(plan["created_from_collection"], [])
        self.assertEqual([item["episode"] for item in plan["skipped_collection_episodes"]], ["01"])
        self.assertEqual(self.job_state(job["id"]), "CLAIMED")

    def test_listing_at_page_limit_is_flagged_as_possibly_truncated(self):
        self.add_collection()
        plan = verify.verify_listing("/Anime/测试番", [file_entry(f"{ep:02d}") for ep in range(1, 11)], apply=False)
        self.assertEqual(plan["listing"]["files"], 10)
        self.assertTrue(plan["listing"]["suspect_truncated"])
        self.assertTrue(plan["warnings"])
        self.assertFalse(plan["collection_missing_episodes"][0]["certain"])

    def test_declared_complete_listing_has_no_warning(self):
        self.add_collection()
        plan = verify.verify_listing("/Anime/测试番", [file_entry(f"{ep:02d}") for ep in range(1, 13)],
                                     apply=False, complete=True)
        self.assertFalse(plan["listing"]["suspect_truncated"])
        self.assertEqual(plan["warnings"], [])
        self.assertEqual(plan["collection_missing_episodes"][0]["episodes"], [])
        self.assertTrue(plan["collection_missing_episodes"][0]["certain"])

    def test_overlapping_pages_are_deduped_not_ambiguous(self):
        self.enqueue_all(("01",))
        plan = verify.verify_listing("/Anime/测试番", [file_entry("01"), file_entry("01")])
        self.assertEqual(plan["ambiguous"], [])
        self.assertEqual(len(plan["recorded"]), 1)

    def test_cli_verify_merges_paginated_inputs(self):
        self.add_collection()
        first = os.path.join(self.tmp.name, "page1.json")
        second = os.path.join(self.tmp.name, "page2.json")
        with open(first, "w", encoding="utf-8") as stream:
            json.dump({"save_path": "/Anime/测试番",
                       "files": [file_entry(f"{ep:02d}") for ep in range(1, 11)]}, stream, ensure_ascii=False)
        with open(second, "w", encoding="utf-8") as stream:
            json.dump({"files": [file_entry("11"), file_entry("12"), file_entry("01")]}, stream, ensure_ascii=False)
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = agent_cli.main(["verify", "--input", first, "--input", second, "--complete"])
        self.assertEqual(code, 0)
        data = json.loads(buffer.getvalue())["data"]
        self.assertEqual(len(data["created_from_collection"]), 12)
        self.assertFalse(data["listing"]["suspect_truncated"])
        # 第二页与第一页重叠了一项，去重后仍然只按真实文件计数。
        self.assertEqual(data["listing"]["files"], 12)
        self.assertTrue(any("重复" in message for message in data["warnings"]))

    def test_observe_collection_records_user_confirmation(self):
        record = self.add_collection(start=1, end=3)
        result = catalog.observe_collection(record["id"], [1, "02"], "用户确认网盘可见 01、02 两个文件", "user")
        self.assertEqual([job["episode"] for job in result["recorded"]], ["01", "02"])
        self.assertTrue(all(job["observation_source"] == "user" for job in result["recorded"]))
        self.assertIn("用户确认", catalog.snapshot()["events"][0]["message"])
        again = catalog.observe_collection(record["id"], [1], "再次确认第 1 话仍在网盘", "user")
        self.assertEqual(again["recorded"], [])
        self.assertEqual(len(again["skipped"]), 1)

    def test_observe_collection_rejects_episodes_outside_range(self):
        record = self.add_collection(start=1, end=3)
        with self.assertRaises(ValueError):
            catalog.observe_collection(record["id"], [4], "用户确认网盘里能看到第 4 话", "user")
        with self.assertRaises(ValueError):
            catalog.observe_collection("c-missing", [1], "用户确认合集已入盘", "user")
        self.assertEqual(catalog.snapshot()["jobs"], [])

    def test_collection_observe_cli(self):
        record = self.add_collection(start=1, end=3)
        path = os.path.join(self.tmp.name, "collection.json")
        with open(path, "w", encoding="utf-8") as stream:
            json.dump({"collection_id": record["id"], "episodes": ["01", "02"],
                       "evidence": "用户确认网盘可见两话"}, stream, ensure_ascii=False)
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = agent_cli.main(["collection-observe", "--input", path])
        self.assertEqual(code, 0)
        self.assertEqual(len(json.loads(buffer.getvalue())["data"]["recorded"]), 2)


if __name__ == "__main__":
    unittest.main()
