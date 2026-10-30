"""字幕版本筛选与季度新番；使用临时文件和模拟远端，不修改实际追番库。"""
import os
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

import catalog
import metadata
import season
import storage
import web_app
from parse_mikan import parse_mikan, resolution_of, select_for_series
from test_project import HTTPFixture, TemporaryProject, row, series


def subject(sid=1, score=7.5, votes=123):
    return {"id": sid, "type": 2, "name": "原名", "name_cn": "新番", "date": "2026-10-01",
            "rating": {"score": score, "total": votes, "rank": 1234}, "platform": "TV",
            "images": {"large": "https://lain.bgm.tv/pic/cover/l/a.jpg", "common": "https://lain.bgm.tv/r/400/pic/cover/l/a.jpg"}}


class SubtitleTests(TemporaryProject):
    def test_resolution_and_traditional_subtitle_tags(self):
        for title, expected in (("[1080P]", "1080p"), ("[4K]", "2160p"), ("1920x1080", "1080p"),
                                ("1280×720", "720p"), ("[2160p]", "2160p"), ("未注明", "未知")):
            self.assertEqual(resolution_of(title), expected)
        item = parse_mikan(row(language="簡繁外掛"))[0]
        self.assertEqual(item["lang"], "简繁")
        self.assertTrue(item["soft"])
        self.assertEqual(item["resolution"], "1080p")

    def test_exact_group_type_and_resolution_affect_preparation(self):
        html = row(number=1) + row(group="其他字幕组", number=2) + row(language="简体外挂", number=3)
        html += row(ep="02", number=4).replace("1080P", "720p")
        items = parse_mikan(html)
        cfg = series(subtitle_type="外挂", resolution="1080p", strict_groups=True)
        self.assertEqual([i["infohash"] for i in select_for_series(items, cfg)], [f"{3:040x}"])
        cfg.update(subtitle_type="内嵌", resolution="720p")
        self.assertEqual([i["ep"] for i in select_for_series(items, cfg)], ["02"])
        cfg["subtitle_groups"] = ["不存在的组"]
        self.assertFalse(select_for_series(items, cfg))
        cfg["strict_groups"] = False
        self.assertEqual(len(select_for_series(items, cfg)), 1)

    def test_sealed_subtitles_are_selectable_without_treating_them_as_hard_subs(self):
        items = parse_mikan(row(language="简繁日内封"))
        self.assertTrue(items[0]["sealed"])
        self.assertFalse(select_for_series(items, series()))
        chosen = select_for_series(items, series(language="简繁", subtitle_type="内封", resolution="1080p"))
        self.assertEqual(len(chosen), 1)

    def test_variant_options_keep_resolutions_separate_without_counting_revisions_twice(self):
        html = row(number=1) + row(ep="01v2", number=2) + row(number=3).replace("1080P", "720p")
        with patch.object(web_app.auto_dl, "fetch_mikan", return_value=html):
            variants = web_app.mikan_bangumi(4059)
        self.assertEqual({v["resolution"] for v in variants}, {"1080p", "720p"})
        self.assertTrue(all(v["count"] == 1 for v in variants))
        self.assertTrue(all(v["sample"] for v in variants))

    def test_invalid_new_preferences_are_rejected(self):
        for changes in ({"resolution": "999p"}, {"subtitle_type": "随便"}, {"strict_groups": "true"}):
            with self.assertRaises(ValueError):
                storage.validate_series(series(**changes))

    def test_cli_watch_fields_are_preserved_by_catalog(self):
        result = catalog.watch_series(4059, "测试番", ["绿茶字幕组"], subtitle_type="外挂", resolution="720p", strict_groups=True)
        self.assertFalse(result["embedded_only"])
        self.assertEqual(storage.load_list()["series"][0]["resolution"], "720p")

    def test_preference_change_retires_unmatched_ready_jobs_and_can_restore(self):
        items = parse_mikan(row())
        catalog.enqueue(series(), items)
        catalog.enqueue(series(resolution="720p"), [])
        self.assertEqual(catalog.snapshot()["stats"]["READY"], 0)
        self.assertEqual(catalog.snapshot()["stats"]["SUPERSEDED"], 1)
        catalog.enqueue(series(resolution="1080p"), items)
        self.assertEqual(catalog.snapshot()["stats"]["READY"], 1)
        self.assertEqual(len(catalog.snapshot()["jobs"]), 1)

    def test_preference_change_does_not_rewrite_submitted_jobs(self):
        catalog.enqueue(series(), parse_mikan(row()))
        job = catalog.claim("test")
        catalog.report(job["id"], job["claim_token"], "SUBMITTING", "已在官网准备点击确定")
        catalog.report(job["id"], job["claim_token"], "SUBMITTED", "官网明确受理该话任务")
        catalog.enqueue(series(resolution="720p"), [])
        self.assertEqual(catalog.snapshot()["stats"]["SUBMITTED"], 1)


class DiscoveryHTTPTests(HTTPFixture):
    def test_preference_crud_round_trip_and_legacy_update(self):
        body = {"mikan_id": 4059, "subtitle_groups": ["其他组"], "language": "简繁",
                "subtitle_type": "外挂", "resolution": "720p", "strict_groups": True}
        self.assertEqual(self.request("/api/series/0", "PUT", body)[0], 200)
        item = self.request("/api/series")[1]["series"][0]
        self.assertEqual(item["resolution"], "720p")
        self.assertEqual(item["subtitle_type"], "外挂")
        self.assertFalse(item["embedded_only"])
        self.assertTrue(item["strict_groups"])
        self.assertEqual(self.request("/api/series/0", "PUT", {"mikan_id": 4059, "embedded_only": True})[0], 200)
        self.assertEqual(self.request("/api/series")[1]["series"][0]["subtitle_type"], "内嵌")
        self.assertEqual(self.request("/api/series/0", "PUT", {"resolution": "bad"})[0], 400)
        self.assertEqual(self.request("/api/series")[1]["series"][0]["resolution"], "720p")

    def test_new_series_saves_subtitle_child_choices(self):
        body = series(mikan_id=999, subtitle_type="外挂", resolution="2160p", strict_groups=True)
        self.assertEqual(self.request("/api/series", "POST", body)[0], 201)
        saved = self.request("/api/series")[1]["series"][-1]
        self.assertEqual(saved["subtitle_type"], "外挂")
        self.assertFalse(saved["embedded_only"])
        self.assertEqual(saved["resolution"], "2160p")

    def test_season_route_parameters_and_validation(self):
        with patch.object(season, "browse", return_value={"items": [], "total": 0}) as browse:
            self.assertEqual(self.request("/api/bangumi/season?year=2026&quarter=4&page=2&sort=heat&q=%E6%B5%8B%E8%AF%95&refresh=1")[0], 200)
            browse.assert_called_once_with(year=2026, quarter=4, page=2, sort="heat", query="测试", refresh=True)
        self.assertEqual(self.request("/api/bangumi/season?quarter=7")[0], 400)

    def test_bangumi_metadata_accepts_rating_votes_and_platform(self):
        with patch.object(metadata, "get_subject", return_value=metadata.normalize_subject(subject())):
            self.assertEqual(self.request("/api/metadata/4059", "POST", {"bangumi_id": 1})[0], 200)
        info = self.request("/api/library")[1]["series"][0]
        self.assertEqual(info["score_votes"], 123)
        self.assertEqual(info["platform"], "TV")


class SeasonTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix=".test-", dir=storage.BASE)
        self.addCleanup(self.tmp.cleanup)
        for field in ("CACHE_FILE", "CACHE_LOCK"):
            p = patch.object(season, field, os.path.join(self.tmp.name, field))
            p.start(); self.addCleanup(p.stop)
        self.result = {"total": 43, "limit": 20, "offset": 0, "data": [subject()]}

    def test_quarter_boundaries_and_invalid_values(self):
        self.assertEqual(season.bounds(2026, 4), ("2026-10-01", "2027-01-01"))
        self.assertEqual(season.bounds(2026, 1), ("2026-01-01", "2026-04-01"))
        for year, quarter in ((2026, 0), (2026, 5), (True, 1), (1800, 1)):
            with self.assertRaises(ValueError): season.bounds(year, quarter)

    def test_public_quarter_paging_does_not_skip_api_twenty_item_limit(self):
        with patch.object(metadata, "bangumi_request", return_value=self.result) as request:
            result = season.browse(2026, 4, page=3, sort="score", query="测试")
        self.assertEqual(request.call_args.args[0], "/v0/search/subjects?limit=20&offset=40")
        self.assertEqual(request.call_args.args[1]["filter"]["air_date"], [">=2026-10-01", "<2027-01-01"])
        self.assertEqual(request.call_args.args[1]["filter"]["type"], [2])
        self.assertEqual(result["total"], 43)
        self.assertEqual(result["items"][0]["score_votes"], 123)
        self.assertEqual(result["page_size"], 20)
        self.assertIn("/r/400/", result["items"][0]["cover_url"])

    def test_cache_hit_and_page_sort_quarter_query_isolation(self):
        with patch.object(metadata, "bangumi_request", return_value=self.result) as request:
            first = season.browse(2026, 4)
            self.assertFalse(first["cached"])
            self.assertTrue(season.browse(2026, 4)["cached"])
            season.browse(2026, 4, page=2)
            season.browse(2026, 4, sort="heat")
            season.browse(2026, 3)
            season.browse(2026, 4, query="片名")
            self.assertEqual(request.call_count, 5)

    def test_stale_data_is_explicit_on_remote_failure(self):
        with patch.object(metadata, "bangumi_request", return_value=self.result): season.browse(2026, 4)
        with patch.object(metadata, "bangumi_request", side_effect=urllib.error.URLError("offline")):
            result = season.browse(2026, 4, refresh=True)
        self.assertTrue(result["stale"])
        self.assertIn("缓存", result["warning"])

    def test_no_cache_failure_is_not_empty_season(self):
        with patch.object(metadata, "bangumi_request", side_effect=urllib.error.URLError("offline")):
            with self.assertRaises(urllib.error.URLError): season.browse(2026, 4)

    def test_cache_expiry_and_force_refresh(self):
        with patch.object(metadata, "bangumi_request", return_value=self.result) as request:
            season.browse(2026, 4)
            season.browse(2026, 4, refresh=True)
            with patch.object(season.time, "time", return_value=season.time.time() + season.CACHE_SECONDS + 1):
                season.browse(2026, 4)
        self.assertEqual(request.call_count, 3)


if __name__ == "__main__":
    unittest.main()
