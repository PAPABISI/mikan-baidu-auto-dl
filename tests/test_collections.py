"""合集识别与候选展示；临时库、虚构磁力和模拟远端，不操作真实网盘。"""
import contextlib
import html
import io
import unittest
from unittest.mock import patch

import agent_bridge
import catalog
import storage
import web_app
from parse_mikan import collection_info, parse_mikan, select_for_series, subtitle_kind
from test_project import HTTPFixture, TemporaryProject, row, series

TITLE = '[LoliHouse] 冰之城墙 / Koori no Jouheki [01-14 合集][WebRip 1080p HEVC-10bit AAC][简繁内封字幕][Fin]'


def collection_html(title=TITLE):
    return ("<tr><td data-magnet='magnet:?xt=urn:btih:" + 'c' * 40 + "'></td>"
            "<td><a class='magnet-link-wrap' href='/Home/Episode/sample'>" + html.escape(title) +
            "</a></td><td>6.4GB</td><td>2026/09/09 22:24</td></tr>")


class CollectionParsingTests(unittest.TestCase):
    def test_lolihouse_case_preserves_range_and_actual_subtitle_type(self):
        item = parse_mikan(collection_html())[0]
        self.assertTrue(item['is_collection'])
        self.assertEqual((item['episode_start'], item['episode_end']), (1, 14))
        self.assertEqual(item['ep'], '?')
        self.assertEqual(item['group'], 'LoliHouse')
        self.assertEqual(item['lang'], '简繁')
        self.assertEqual(subtitle_kind(item), '内封')
        self.assertEqual(item['resolution'], '1080p')
        self.assertTrue(item['magnet'].startswith('magnet:?xt=urn:btih:'))
        self.assertFalse(select_for_series([item], series(language='不限', subtitle_type='不限')))

    def test_range_and_keyword_variants(self):
        for marker in ('[01~12]', '[01～12]', '【01—12】', '第1至12话', '[S01E01-E12]'):
            with self.subTest(marker=marker):
                info = collection_info('测试番 ' + marker)
                self.assertEqual((info['episode_start'], info['episode_end']), (1, 12))
        for marker in ('[Batch]', '[Complete Series]', '全集', '合輯', '全12集'):
            with self.subTest(marker=marker):
                info = collection_info('测试番 ' + marker)
                self.assertTrue(info['is_collection'])
                self.assertIsNone(info['episode_start'])

    def test_resolution_year_and_single_episode_are_not_collections(self):
        for marker in ('[01]', '[14 END]', '[1080p]', '[1920x1080]', '[10-bit]', '[2026-04]'):
            with self.subTest(marker=marker):
                self.assertFalse(collection_info('测试番 ' + marker)['is_collection'])


class CollectionCatalogTests(TemporaryProject):
    def test_prepare_keeps_collection_candidate_outside_episode_queue(self):
        with patch.object(web_app.auto_dl, 'fetch_mikan', return_value=collection_html()), contextlib.redirect_stdout(io.StringIO()):
            result = agent_bridge.prepare()
        data = catalog.snapshot()
        self.assertEqual(result['status'], 'READY')
        self.assertEqual(len(result['collections']), 1)
        self.assertFalse(result['added'])
        self.assertFalse(data['jobs'])
        self.assertEqual(data['stats']['COMPLETED'], 0)
        self.assertEqual(len(data['series'][0]['collections']), 1)
        self.assertIsNone(catalog.claim('test'))

    def test_candidates_are_deduplicated_without_changing_progress_or_blocker(self):
        items = parse_mikan(collection_html())
        catalog.transact(lambda data: data.update(executor={'status':'BLOCKED'}))
        self.assertEqual(len(catalog.discover_collections(series(), items)), 1)
        self.assertFalse(catalog.discover_collections(series(), items))
        data = catalog.snapshot()
        self.assertEqual(len(data['collections']), 1)
        self.assertEqual(sum(e['kind'] == 'COLLECTION' for e in data['events']), 1)
        self.assertEqual(data['executor']['status'], 'BLOCKED')
        self.assertEqual(storage.load_list()['series'][0]['last_episode'], '0')

    def test_overlap_and_preference_mismatch_are_visible_without_rewriting_jobs(self):
        job = catalog.enqueue(series(), parse_mikan(row()))[0]
        catalog.observe(job['id'], 'SUBMITTED', '临时测试：用户确认第01话已受理', 'user')
        before = catalog.load()['jobs']
        catalog.discover_collections(series(), parse_mikan(collection_html()))
        candidate = catalog.snapshot()['series'][0]['collections'][0]
        self.assertEqual(candidate['overlap_episodes'], ['01'])
        self.assertFalse(candidate['matches_preferences'])
        self.assertEqual(catalog.load()['jobs'], before)
        storage.mutate_list(lambda data: data['series'][0].update(subtitle_groups=['LoliHouse'], language='简繁', subtitle_type='内封', embedded_only=False))
        self.assertTrue(catalog.snapshot()['series'][0]['collections'][0]['matches_preferences'])

    def test_unknown_range_does_not_claim_to_cover_individual_episodes(self):
        catalog.enqueue(series(), parse_mikan(row()))
        catalog.discover_collections(series(), parse_mikan(collection_html('[LoliHouse] 测试番 [Batch] [简繁内封]')))
        candidate = catalog.snapshot()['series'][0]['collections'][0]
        self.assertIsNone(candidate['episode_end'])
        self.assertEqual(candidate['overlap_episodes'], [])

    def test_collection_cannot_be_enqueued_even_with_a_numeric_ep(self):
        item = parse_mikan(collection_html())[0]
        item['ep'] = '01'
        self.assertFalse(catalog.enqueue(series(), [item]))
        self.assertFalse(catalog.snapshot()['jobs'])


class CollectionHTTPTests(HTTPFixture):
    def test_group_with_only_collection_is_available_with_magnet(self):
        with patch.object(web_app.auto_dl, 'fetch_mikan', return_value=collection_html()):
            status, data = self.request('/api/mikan/bangumi/4059')
        self.assertEqual(status, 200)
        variant = data['groups'][0]
        self.assertEqual(variant['group'], 'LoliHouse')
        self.assertEqual(variant['count'], 0)
        self.assertEqual(variant['episodes'], [])
        self.assertEqual(variant['collection_count'], 1)
        self.assertEqual(variant['collections'][0]['episode_end'], 14)
        self.assertTrue(variant['collections'][0]['magnet'].startswith('magnet:'))

    def test_mixed_resources_do_not_inflate_episode_count_and_library_exports_candidates(self):
        with patch.object(web_app.auto_dl, 'fetch_mikan', return_value=row(group='LoliHouse', language='简繁内封') + collection_html()):
            variant = self.request('/api/mikan/bangumi/4059')[1]['groups'][0]
        self.assertEqual(variant['count'], 1)
        self.assertEqual(variant['collection_count'], 1)
        catalog.discover_collections(series(), parse_mikan(collection_html()))
        exported = self.request('/api/library/export')[1]
        self.assertEqual(len(exported['collections']), 1)
        self.assertEqual(len(exported['series'][0]['collections']), 1)
        self.assertFalse(exported['jobs'])


if __name__ == '__main__':
    unittest.main()
