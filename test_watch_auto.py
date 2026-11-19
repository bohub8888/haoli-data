#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""watch_auto 解析器单测：验证结构化日期提取与强闸门。"""
import datetime as dt
import unittest

import watch_auto as w


class TestExtract(unittest.TestCase):
    def test_single_event(self):
        html = '''<script type="application/ld+json">{"@type":"Event",
            "startDate":"2027-11-09","endDate":"2027-11-12"}</script>'''
        self.assertEqual(w.extract_event_dates(html),
                         (dt.date(2027, 11, 9), dt.date(2027, 11, 12)))

    def test_iso_datetime(self):
        html = '''<script type="application/ld+json">{"@type":"Event",
            "startDate":"2026-11-16T09:00:00+02:00",
            "endDate":"2026-11-19T18:00:00+02:00"}</script>'''
        self.assertEqual(w.extract_event_dates(html),
                         (dt.date(2026, 11, 16), dt.date(2026, 11, 19)))

    def test_multi_year_takes_latest(self):
        # Interpack 真实情况：2026 与 2021 两组，须取最新年份
        html = '''<script type="application/ld+json">{"@type":"Event",
            "startDate":"2026-05-07","endDate":"2026-05-13"}</script>
            <script type="application/ld+json">{"@type":"Event",
            "startDate":"2021-02-25","endDate":"2021-03-03"}</script>'''
        self.assertEqual(w.extract_event_dates(html),
                         (dt.date(2026, 5, 7), dt.date(2026, 5, 13)))

    def test_ambiguous_same_year_abandon(self):
        html = '''<script type="application/ld+json">{"@type":"Event",
            "startDate":"2027-11-09","endDate":"2027-11-12"}</script>
            <script type="application/ld+json">{"@type":"Event",
            "startDate":"2027-12-01","endDate":"2027-12-05"}</script>'''
        self.assertEqual(w.extract_event_dates(html), (None, None))

    def test_no_event(self):
        self.assertEqual(w.extract_event_dates('<script type="application/ld+json">{"@type":"WebSite"}</script>'),
                         (None, None))
        self.assertEqual(w.extract_event_dates('<html>nothing</html>'), (None, None))

    def test_non_event_date_ignored(self):
        # 只有 SearchAction 里的 date，不应被当作会期
        html = '<script type="application/ld+json">{"@type":"WebPage","datePublished":"2020-01-01"}</script>'
        self.assertEqual(w.extract_event_dates(html), (None, None))


class TestGate(unittest.TestCase):
    def test_pass(self):
        self.assertTrue(w.gate(dt.date(2027, 11, 9), dt.date(2027, 11, 12), "2027-11-09")[0])

    def test_out_of_window(self):
        # 官网挂 2026 旧届次，watch 期望 2027 → 拒绝
        ok, why = w.gate(dt.date(2026, 11, 9), dt.date(2026, 11, 12), "2027-11-09")
        self.assertFalse(ok)
        self.assertIn("超过", why)

    def test_end_before_start(self):
        # Gulfood 真实情况：startDate 比 endDate 晚 → 拒绝
        ok, why = w.gate(dt.date(2026, 3, 19), dt.date(2026, 3, 15), "2026-03-15")
        self.assertFalse(ok)

    def test_too_long(self):
        ok, _ = w.gate(dt.date(2027, 1, 1), dt.date(2027, 3, 1), "2027-01-15")
        self.assertFalse(ok)

    def test_no_start(self):
        self.assertFalse(w.gate(None, dt.date(2027, 11, 12), "2027-11-09")[0])


class TestEntryGen(unittest.TestCase):
    def test_entry_passes_validate_shape(self):
        cfg = {"url": "https://websummit.com/", "tag": "会议",
               "scope": "world", "place": "葡萄牙里斯本"}
        line = w.make_entry_line("Web Summit", dt.date(2027, 11, 9), dt.date(2027, 11, 12), cfg)
        self.assertIn('C("2027-11-09", "2027-11-12", "Web Summit"', line)
        self.assertIn('"https://websummit.com/"', line)
        self.assertIn('"A"', line)
        self.assertNotIn("http", line.split('"A",')[1].split('"')[1] if '"A",' in line else "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
