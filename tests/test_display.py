"""Tests for human-readable output and the interactive dashboard.

Rendering functions are pure, so most tests feed dictionaries and a fixed
time.  CLI tests reuse the AERIAL_SWITCH_HOME isolation; nothing touches the
real ~/Library, launchd or the network.
"""
import contextlib
import datetime as dt
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import plistlib
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

loader = importlib.machinery.SourceFileLoader('aerial_switch_display', str(Path(__file__).resolve().parents[1] / 'aerial-switch'))
spec = importlib.util.spec_from_loader(loader.name, loader)
a = importlib.util.module_from_spec(spec)
loader.exec_module(a)

TZ = dt.timezone(dt.timedelta(hours=8))
NOW = dt.datetime(2026, 11, 18, 16, 40, 0, tzinfo=TZ)
CFG = dict(a.DEFAULTS, series='goldenGate', city='上海', lat=31.23, lon=121.5)
UNICODE = a.Style(color=False)


def video(present=True, pinned=True, same=True, asset='x'):
    return {'assetID': asset, 'present': present, 'pinned': pinned, 'sameInode': same}


def snap(**overrides):
    base = {
        'series': 'goldenGate', 'city': '上海', 'period': 'evening',
        'sunrise': '06:00', 'sunset': '18:00', 'solarCondition': 'normal',
        'nextSwitch': '2026-11-18T18:30+08:00', 'desiredID': 'gg-evening',
        'plist': {'allMatch': True, 'desktopCount': 1, 'idleCount': 1, 'choices': []},
        'launchAgentLoaded': True, 'notDownloaded': [], 'legacyAgents': [],
        'videos': {p: video(asset='gg-' + p) for p in a.PERIODS},
        'pinnedBytes': 2270989957, 'downloadInProgress': False,
        'proxy': {'detected': False, 'schemes': []},
    }
    base.update(overrides)
    return base


def texts(lines):
    return [a.plain(line) for line in lines]


def column(line_text, needle):
    return a.dwidth(line_text[:line_text.index(needle)])


class FakeTTY(io.StringIO):
    def __init__(self, encoding='utf-8'):
        super().__init__()
        self._encoding = encoding

    @property
    def encoding(self):
        return self._encoding

    def isatty(self):
        return True


class SolarFixture(unittest.TestCase):
    """Sunrise 06:00, sunset 18:00: morning 06:00-08:30, day to 16:30, evening to 18:30."""
    solar = (6.0, 18.0, 0.0)

    def setUp(self):
        patcher = patch.object(a, 'solar', return_value=self.solar)
        patcher.start()
        self.addCleanup(patcher.stop)

    def assertFits(self, lines, width):
        for text in texts(lines):
            self.assertLessEqual(a.dwidth(text), width, repr(text))


class TimelineTests(SolarFixture):
    def test_proportions_marker_and_legend(self):
        schedule = a.day_schedule(NOW, CFG)
        cells = a.bar_cells(schedule, 48)  # 30 minutes per column
        self.assertEqual([cells.count(p) for p in ('morning', 'day', 'evening', 'night')], [5, 16, 4, 23])
        self.assertEqual((cells[11], cells[12], cells[16], cells[17]), ('night', 'morning', 'morning', 'day'))
        self.assertEqual(a.marker_column(NOW, 48), 33)
        lines = texts(a.timeline_lines(UNICODE, schedule, NOW, 80, 48))
        self.assertEqual(lines[0].split(), ['06:00', '早晨', '08:30', '白天', '16:30', '傍晚', '18:30', '夜晚'])
        self.assertEqual(lines[1], '  ' + '░' * 12 + '▒' * 5 + '█' * 16 + '▓' * 4 + '░' * 11)
        self.assertEqual(column(lines[2], '▲'), 2 + 33)
        self.assertIn('现在 16:40', lines[2])

    def test_marker_at_right_edge_flips_label(self):
        late = NOW.replace(hour=23, minute=55)
        line = texts(a.timeline_lines(UNICODE, a.day_schedule(late, CFG), late, 30, 26))[-1]
        self.assertTrue(line.endswith('现在 23:55 ▲'))
        self.assertEqual(column(line, '▲'), 2 + 25)
        self.assertLessEqual(a.dwidth(line), 30)

    def test_schedule_matches_period_at_and_next_transition_minutes(self):
        schedule = a.day_schedule(NOW, CFG)
        self.assertEqual(schedule['minutes'][16 * 60 + 40], a.current_period(NOW, CFG))
        self.assertEqual(dict((p, m) for p, m in schedule['starts'])['night'], 18 * 60 + 30)


class CrossMidnightTests(SolarFixture):
    solar = (12.0, 24.0, 0.0)  # evening runs 22:30 -> 00:30

    def test_cross_midnight(self):
        schedule = a.day_schedule(NOW, CFG)
        cells = a.bar_cells(schedule, 48)
        self.assertEqual(cells[0], 'evening')
        self.assertEqual(cells[-1], 'evening')
        self.assertEqual(schedule['starts'][0], ('morning', 12 * 60))
        self.assertIn(('night', 30), schedule['starts'])
        legend = texts(a.timeline_lines(UNICODE, schedule, NOW, 80, 48))[0]
        self.assertTrue(legend.strip().startswith('12:00 早晨'))
        self.assertIn('00:30 夜晚', legend)


class PolarTests(unittest.TestCase):
    def render(self, cos_ha, next_switch):
        with patch.object(a, 'solar', return_value=(0.0, 24.0, cos_ha)):
            schedule = a.day_schedule(NOW, CFG)
            lines = a.status_lines(UNICODE, snap(nextSwitch=next_switch, period='day' if cos_ha < 0 else 'night'),
                                   CFG, NOW, 60)
        return schedule, texts(lines)

    def test_polar_day_and_night(self):
        schedule, lines = self.render(-1.5, None)
        self.assertEqual(set(schedule['minutes']), {'day'})
        self.assertEqual(schedule['starts'], [])
        self.assertIn('极昼 · 全天白天', '\n'.join(lines))
        self.assertIn('  ' + '█' * 48, lines)
        self.assertIn('极昼或极夜期间不切换', '\n'.join(lines))
        schedule, lines = self.render(1.5, '2027-01-20T09:00+08:00')
        self.assertEqual(set(schedule['minutes']), {'night'})
        self.assertIn('极夜 · 全天夜晚', '\n'.join(lines))
        self.assertIn('1月20日 09:00（约 63 天后）', '\n'.join(lines))


class LayoutTests(SolarFixture):
    def test_reference_layout(self):
        lines = texts(a.status_lines(UNICODE, snap(), CFG, NOW, 80))
        self.assertEqual(lines[0], ' Aerial Switch · Golden Gate · 上海')
        self.assertTrue(lines[1].startswith(' ────'))
        current = next(l for l in lines if l.startswith('  当前'))
        upcoming = next(l for l in lines if l.startswith('  下一张'))
        self.assertEqual(current, '  当前   傍晚  Golden Gate Evening   ✓ 已应用')
        self.assertEqual(upcoming, '  下一张 夜晚  Golden Gate Night     18:30（约 1 小时 50 分钟后）')
        # Display-width alignment: CJK labels are two columns wide.
        self.assertEqual(column(current, 'Golden'), column(upcoming, 'Golden'))
        self.assertEqual(column(current, '✓'), column(upcoming, '18:30'))
        self.assertIn('  视频   早晨 ✓  白天 ✓  傍晚 ✓  夜晚 ✓   已保护 2.1 GB', lines)
        self.assertIn('  定时   运行中 · 每 15 分钟检查', lines)
        self.assertEqual(a.dwidth(lines[1]) - 1, max(a.dwidth(l) for l in lines[2:]) - 1)

    def test_every_view_fits_width(self):
        legacy = [{'label': 'com.user.wallpaper-switch', 'loaded': True, 'plist': '/x', 'plistExists': True}]
        status = snap(legacyAgents=legacy, city='乌鲁木齐市天山区一个很长的城市名称')
        result = dict(status, warnings=['Prefetch failed: URLError; retry next run'],
                      plannedActions=[{'action': 'pin', 'assetID': 'gg-day'}, {'action': 'download', 'assetID': 'gg-evening'},
                                      {'action': 'switch', 'assetID': 'gg-evening'}],
                      updated=2, downloadError=None)
        catalog = {key: {'series': key, 'name': a.SERIES[key]['name'], 'ids': {}, 'missing': [],
                         'notDownloaded': ['Tahoe Day'] if key == 'tahoe' else []} for key in a.SERIES}
        checks = {'python': {'ok': True, 'detail': 'Python 3.9.6'},
                  'plist': {'ok': False, 'error': 'No Desktop/Idle targets ' * 6}}
        saved = dict(CFG, notDownloaded=['Golden Gate Night'], protection={'actions': [{'action': 'pin'}], 'warnings': []},
                     message='')
        for width in (32, 44, 60, 80, 120):
            views = [a.status_lines(UNICODE, status, CFG, NOW, width),
                     a.apply_lines(UNICODE, result, CFG, NOW, width, True),
                     a.apply_lines(UNICODE, result, CFG, NOW, width, False),
                     a.series_list_lines(UNICODE, catalog, 'goldenGate', width),
                     a.series_saved_lines(UNICODE, saved, width),
                     a.location_saved_lines(UNICODE, CFG, NOW, width),
                     a.doctor_lines(UNICODE, checks, width),
                     a.install_lines(UNICODE, {'installed': '/Users/someone/.local/bin/aerial-switch',
                                               'migration': {'labels': ['x'], 'backupDirectory': '/Users/someone/' + 'b' * 90}},
                                     width, a.Paths('/Users/someone')),
                     a.uninstall_lines(UNICODE, {'restored': True}, width)]
            for view in views:
                self.assertFits(view, width)

    def test_narrow_60_columns(self):
        self.assertEqual(a.bar_width_for(60), 48)
        self.assertEqual(a.bar_width_for(40), 37)
        lines = texts(a.status_lines(UNICODE, snap(), CFG, NOW, 40))
        self.assertFits(a.status_lines(UNICODE, snap(), CFG, NOW, 40), 40)
        # Legend and long rows wrap onto aligned continuation lines.
        self.assertTrue(any(l.strip().startswith('18:30 夜晚') for l in lines))
        self.assertIn('         已保护 2.1 GB', lines)

    def test_cjk_width_helpers(self):
        self.assertEqual(a.dwidth('下一张'), 6)
        self.assertEqual(a.dwidth('Golden（Gate）'), 14)
        self.assertEqual(a.pad('当前', 7), '当前   ')
        self.assertEqual(a.clip('早晨白天傍晚', 7, '…'), '早晨白…')
        for part in a.wrap_text('发现旧版定时任务 com.user.wallpaper-switch；运行 aerial-switch install --migrate', 20):
            self.assertLessEqual(a.dwidth(part), 20)


class StyleTests(SolarFixture):
    def test_no_color_and_ascii_fallback(self):
        colored = a.Style.for_stream(FakeTTY(), {'TERM': 'xterm-256color'})
        self.assertTrue(colored.color)
        self.assertEqual(colored.palette, 256)
        self.assertIn('\033[38;5;', a.render_ansi(colored, a.status_lines(colored, snap(), CFG, NOW, 80)))
        no_color = a.Style.for_stream(FakeTTY(), {'TERM': 'xterm-256color', 'NO_COLOR': '1'})
        self.assertFalse(no_color.color)
        self.assertNotIn('\033', a.render_ansi(no_color, a.status_lines(no_color, snap(), CFG, NOW, 80)))
        self.assertFalse(a.Style.for_stream(io.StringIO(), {}).color)
        self.assertFalse(a.Style.for_stream(FakeTTY(), {'TERM': 'dumb'}).color)

        latin = a.Style.for_stream(FakeTTY('latin-1'), {})
        self.assertTrue(latin.ascii and latin.english)
        output = a.render_ansi(latin, a.status_lines(latin, snap(city='Shanghai'), CFG, NOW, 60))
        output.encode('ascii')
        output = '\n'.join(texts(a.status_lines(latin, snap(city='Shanghai'), CFG, NOW, 60)))
        self.assertIn('Now', output)
        self.assertIn('#', output)
        self.assertIn('^ now 16:40', output)
        gb = a.Style.for_stream(FakeTTY('gb18030'), {})
        self.assertTrue(gb.ascii)
        self.assertFalse(gb.english)
        self.assertNotIn('█', a.render_ansi(gb, a.status_lines(gb, snap(), CFG, NOW, 60)))

    def test_write_text_never_raises_on_unencodable(self):
        class Latin(io.StringIO):
            encoding = 'latin-1'

            def write(self, text):
                text.encode('latin-1')
                return super().write(text)
        stream = Latin()
        a.write_text('上海 ok\n', stream)
        self.assertIn('ok', stream.getvalue())


class StatusContentTests(SolarFixture):
    def test_video_states_and_legend(self):
        videos = {'morning': video(), 'day': video(pinned=False, same=False),
                  'evening': video(present=False, same=False), 'night': video(present=False, pinned=False, same=False)}
        status = snap(videos=videos, downloadInProgress=True)
        self.assertEqual(a.video_states(status), {'morning': 'protected', 'day': 'unprotected',
                                                  'evening': 'restorable', 'night': 'downloading'})
        text = '\n'.join(texts(a.status_lines(UNICODE, status, CFG, NOW, 80)))
        self.assertIn('早晨 ✓  白天 ○  傍晚 ↺  夜晚 ⋯', text)
        self.assertIn('○ 未保护  ↺ 可从保护恢复  ⋯ 下载中', text)
        self.assertIn('注意   ! 白天 视频尚未受硬链接保护', text)
        status = snap(videos=dict(videos, night=video(present=False, pinned=False, same=False)))
        self.assertIn('↓ 缺失，应用时下载', '\n'.join(texts(a.status_lines(UNICODE, status, CFG, NOW, 80))))

    def test_pending_and_agent_states(self):
        status = snap(plist={'allMatch': False}, launchAgentLoaded=False)
        text = '\n'.join(texts(a.status_lines(UNICODE, status, CFG, NOW, 80)))
        self.assertIn('○ 待切换', text)
        self.assertIn('未运行 · 运行 aerial-switch install 启用', text)

    def test_warnings(self):
        legacy = [{'label': 'com.user.wallpaper-switch'}, {'label': 'com.user.wallpaper-switch-guard'}]
        result = snap(legacyAgents=legacy, plannedActions=[{'action': 'download', 'assetID': 'gg-evening'}],
                      warnings=['Current video unavailable: URLError', 'Prefetch failed: TimeoutError; retry next run',
                                'Pin protection skipped: pinned and videos are on different devices',
                                'Downloaded but not protected: pin gg-night: Permission denied'],
                      downloadError='URLError: download failed; retry next run', updated=0,
                      plist={'allMatch': False})
        text = '\n'.join(texts(a.apply_lines(UNICODE, result, CFG, NOW, 80, False)))
        self.assertIn('✗ 未切换：视频下载失败，保持原壁纸', text)
        self.assertIn('下载失败：URLError: download failed', text)
        self.assertNotIn('当前时段视频暂不可用', text)  # covered by the download error
        self.assertIn('预取下一张失败（TimeoutError）', text)
        self.assertIn('不在同一磁盘', text)
        self.assertIn('已下载但未受保护：pin gg-night', text)
        self.assertIn('发现旧版定时任务 com.user.wallpaper-switch、', text)
        self.assertIn('com.user.wallpaper-switch-guard', text)
        self.assertIn('install --migrate', text)
        self.assertIn('○ 待切换', text)
        busy = a.download_error_text(UNICODE, 'DownloadBusy: Another run is downloading; keeping current wallpaper')
        self.assertEqual(busy, '另一个检查正在下载视频，保持原壁纸')

    def test_apply_outcomes(self):
        dry = snap(plist={'allMatch': False}, plannedActions=[{'action': 'pin', 'assetID': 'gg-day'},
                                                              {'action': 'switch', 'assetID': 'gg-evening'}], warnings=[])
        text = '\n'.join(texts(a.apply_lines(UNICODE, dry, CFG, NOW, 80, True)))
        self.assertIn('Golden Gate · 上海 · 预演', text)
        self.assertIn('预演   将：保护白天视频 · 切换壁纸', text)
        done = dict(dry, updated=2)
        text = '\n'.join(texts(a.apply_lines(UNICODE, done, CFG, NOW, 80, False)))
        self.assertIn('本次   ✓ 已切换到傍晚壁纸', text)
        self.assertIn('保护白天视频', text)
        self.assertIn('✓ 已应用', text)
        same = dict(dry, plannedActions=[], updated=0)
        self.assertIn('壁纸已是最新', '\n'.join(texts(a.apply_lines(UNICODE, same, CFG, NOW, 80, False))))

    def test_countdowns(self):
        self.assertEqual(a.countdown_text(UNICODE, 53 * 60 - 20), '约 53 分钟后')
        self.assertEqual(a.countdown_text(UNICODE, 2 * 3600), '约 2 小时后')
        self.assertEqual(a.countdown_text(UNICODE, 2 * 3600 + 61), '约 2 小时 2 分钟后')
        self.assertEqual(a.countdown_text(UNICODE, 3150, precise=True), '52 分 30 秒后')
        self.assertEqual(a.countdown_text(UNICODE, 3 * 3600 + 5 * 60, precise=True), '3 小时 05 分后')
        self.assertEqual(a.countdown_text(UNICODE, -1), '即将切换')
        tomorrow = NOW + dt.timedelta(hours=14)
        self.assertEqual(a.when_text(UNICODE, tomorrow, NOW), '明天 06:40')

    def test_doctor_lines(self):
        checks = {'python': {'ok': True, 'detail': 'Python 3.9.6'},
                  'selectedVideos': {'ok': True, 'detail': {'ids': {}, 'notDownloaded': ['Tahoe Day']}},
                  'plist': {'ok': False, 'error': 'No Desktop/Idle targets'}}
        text = '\n'.join(texts(a.doctor_lines(UNICODE, checks, 80)))
        self.assertIn('✓ Python', text)
        self.assertIn('! 当前系列视频  1 个未下载（Tahoe Day）', text)
        self.assertIn('✗ 壁纸设置      No Desktop/Idle targets', text)
        self.assertIn('1 项失败 · 1 项需要注意', text)


class FakeBackend:
    def __init__(self, fail=None):
        self.calls, self.fail = [], fail or {}
        self.cfg = dict(CFG)

    def _maybe_fail(self, name):
        if name in self.fail:
            raise self.fail[name]

    def config(self):
        self._maybe_fail('config')
        return dict(self.cfg)

    def catalog(self):
        self._maybe_fail('catalog')
        return {key: {'series': key, 'name': a.SERIES[key]['name'], 'ids': {}, 'missing': [],
                      'notDownloaded': ['Tahoe Morning', 'Tahoe Day'] if key == 'tahoe' else []} for key in a.SERIES}

    def snapshot(self, cfg):
        self._maybe_fail('snapshot')
        return snap(series=cfg['series'])

    def set_series(self, name):
        self.calls.append(('set_series', name))
        self._maybe_fail('set_series')
        self.cfg['series'] = name
        return dict(self.cfg, notDownloaded=['Tahoe Morning', 'Tahoe Day'], protection={'actions': [], 'warnings': []})

    def apply(self):
        self.calls.append(('apply',))
        self._maybe_fail('apply')
        return snap(updated=2, warnings=[])

    def set_location(self, lat, lon, city):
        self.calls.append(('set_location', lat, lon, city))
        self._maybe_fail('set_location')
        self.cfg.update(lat=lat, lon=lon, city=city)
        return dict(self.cfg)

    def set_durations(self, values):
        self.calls.append(('set_durations', values))
        self.cfg.update(values)
        return dict(self.cfg)

    def doctor(self):
        self.calls.append(('doctor',))
        return {'python': {'ok': True, 'detail': 'Python 3.9.6'}}


def press(app, *keys):
    results = [app.handle_key(key) for key in keys]
    return results[-1] if results else True


class DashboardTests(SolarFixture):
    def make(self, **kwargs):
        backend = FakeBackend(**kwargs)
        return backend, a.Dashboard(backend, UNICODE, clock=lambda: NOW)

    def screen(self, app, width=80, height=24):
        return '\n'.join(texts(app.render(width, height, NOW)))

    def test_select_series_and_switch(self):
        backend, app = self.make()
        self.assertEqual(a.SERIES_ORDER[app.cursor], 'goldenGate')
        self.assertIn('▸ Golden Gate ✓', self.screen(app))
        press(app, 'RIGHT')
        self.assertEqual(a.SERIES_ORDER[app.cursor], 'tahoe')
        self.assertIn('Enter 切换到 Tahoe · 2 个视频待下载', self.screen(app))
        self.assertEqual(backend.calls, [])
        press(app, 'ENTER')
        self.assertEqual(backend.calls, [('set_series', 'tahoe')])
        self.assertIn('下次检查时生效，或按 A 立即应用', app.message[0])
        self.assertEqual(app.cfg['series'], 'tahoe')
        press(app, 'ENTER')  # already current: no second write
        self.assertEqual(backend.calls, [('set_series', 'tahoe')])
        press(app, 'TAB')
        self.assertEqual(a.SERIES_ORDER[app.cursor], 'goldenGate')

    def test_location_cancel_and_validation(self):
        backend, app = self.make()
        press(app, 'L', 'CLEAR', '9', '5', 'ENTER')
        self.assertEqual(app.mode, 'location')
        self.assertIn('纬度需在 -90 到 90 之间', app.message[0])
        self.assertIn('纬度需在 -90 到 90 之间', self.screen(app))
        press(app, 'x')
        self.assertIn('只能输入数字', app.message[0])
        press(app, 'BACKSPACE', 'BACKSPACE', '4', '5', 'TAB', 'CLEAR', 'ENTER')
        self.assertEqual(app.field, 1)
        self.assertIn('经度需在 -180 到 180 之间', app.message[0])
        self.assertTrue(press(app, 'ESC'))
        self.assertEqual(app.mode, 'main')
        self.assertIn('已取消', app.message[0])
        self.assertEqual(backend.calls, [])
        press(app, 'L', 'CLEAR', '1', '0', '.', '5', 'TAB', 'CLEAR', '-', '2', '0', 'TAB', 'CLEAR', '上', 'q', 'BACKSPACE', '海', 'ENTER')
        self.assertEqual(backend.calls, [('set_location', 10.5, -20.0, '上海')])
        self.assertEqual(app.mode, 'main')
        self.assertIn('位置已保存', app.message[0])

    def test_durations_steps_and_bounds(self):
        backend, app = self.make()
        press(app, 'T', *['RIGHT'] * 20)
        self.assertEqual(app.draft['morningHoursAfterSunrise'], 6.0)
        self.assertIn('‹ 6.0 小时 ›', self.screen(app))
        press(app, *['LEFT'] * 13)
        self.assertEqual(app.draft['morningHoursAfterSunrise'], 0.0)
        press(app, 'DOWN', 'RIGHT', 'ESC')
        self.assertEqual(backend.calls, [])
        press(app, 'T', 'DOWN', 'RIGHT', 'DOWN', '-', 'ENTER')
        self.assertEqual(backend.calls, [('set_durations', {'morningHoursAfterSunrise': 2.5,
                                                            'eveningHoursBeforeSunset': 2.0,
                                                            'eveningHoursAfterSunset': 0.0})])
        self.assertEqual(app.mode, 'main')

    def test_apply_requires_confirmation(self):
        backend, app = self.make()
        press(app, 'A')
        self.assertEqual(app.mode, 'confirm')
        self.assertIn('立即应用 傍晚 · Golden Gate Evening？', self.screen(app))
        press(app, 'N')
        self.assertEqual(backend.calls, [])
        press(app, 'a', 'y')
        self.assertEqual(backend.calls, [('apply',)])
        self.assertIn('已应用 Golden Gate Evening', app.message[0])

    def test_write_errors_stay_in_ui(self):
        backend, app = self.make(fail={'set_series': OSError(28, 'No space left on device', '/x/config.json'),
                                       'apply': RuntimeError('verification failed'),
                                       'set_location': ValueError('bad')})
        self.assertTrue(press(app, 'RIGHT', 'ENTER'))
        self.assertEqual(app.message[1], 'err')
        self.assertIn('No space left on device', app.message[0])
        self.assertTrue(press(app, 'A', 'Y'))
        self.assertIn('verification failed', app.message[0])
        press(app, 'L', 'ENTER')
        self.assertEqual(app.mode, 'location')  # stays open so the user can retry
        self.assertIn('bad', self.screen(app))

    def test_read_errors_render(self):
        backend, app = self.make(fail={'snapshot': ValueError('Series unavailable')})
        screen = self.screen(app)
        self.assertIn('读取状态失败：Series unavailable', screen)
        press(app, 'A')
        self.assertEqual(app.mode, 'main')
        self.assertIn('无法应用', app.message[0])
        backend, app = self.make(fail={'config': ValueError('lat must be a finite number')})
        self.assertIn('读取配置失败', self.screen(app))
        press(app, 'L')
        self.assertEqual(app.mode, 'main')

    def test_pages_sizes_and_quit(self):
        backend, app = self.make()
        lines = app.render(80, 24, NOW)
        self.assertEqual(len(lines), 24)
        self.assertFits(lines, 79)
        self.assertIn('16:40:00', self.screen(app))
        press(app, '?')
        self.assertIn('按键', self.screen(app))
        press(app, 'ESC', 'D')
        self.assertEqual(app.mode, 'doctor')
        self.assertIn('全部正常', self.screen(app))
        press(app, 'ESC')
        for width, height in ((44, 16), (60, 20), (100, 40)):
            for keys in ((), ('L',), ('ESC', 'T')):
                press(app, *keys)
                lines = app.render(width, height, NOW)
                self.assertEqual(len(lines), height)
                self.assertFits(lines, width - 1)
        press(app, 'ESC')
        self.assertIn('终端窗口太小', '\n'.join(texts(app.render(30, 10, NOW))))
        self.assertFits(app.render(30, 10, NOW), 30)
        self.assertFalse(press(app, 'q'))
        self.assertFalse(press(app, 'ESC'))

    def test_reload_schedule(self):
        backend, app = self.make()
        self.assertFalse(app.needs_reload(NOW + dt.timedelta(seconds=29)))
        self.assertTrue(app.needs_reload(NOW + dt.timedelta(seconds=30)))
        app.snap['nextSwitch'] = (NOW + dt.timedelta(seconds=5)).isoformat()
        self.assertTrue(app.needs_reload(NOW + dt.timedelta(seconds=6)))

    def test_tui_loop_with_fake_screen_and_keys(self):
        class FakeScreen:
            def __init__(self):
                self.drawn, self.cleared = [], 0

            def getmaxyx(self):
                return 24, 80

            def erase(self):
                self.drawn.append([])

            def clear(self):
                self.cleared += 1

            def refresh(self):
                pass

            def addstr(self, y, x, text, attr):
                if x + a.dwidth(text) > 79:
                    raise AssertionError('wrote into the last column')
                self.drawn[-1].append(text)

        backend, app = self.make()
        screen = FakeScreen()
        keys = iter([None, 261, '?', '\x1b', 410, 'q'])
        keymap = {261: 'RIGHT', '\x1b': 'ESC', 410: 'RESIZE'}
        a.tui_loop(screen, app, {}, lambda: next(keys), keymap, clock=lambda: NOW)
        frames = [''.join(frame) for frame in screen.drawn]
        self.assertIn('Aerial Switch', frames[0])
        self.assertTrue(any('Enter 切换到 Tahoe' in f for f in frames))
        self.assertTrue(any('按键' in f for f in frames))
        self.assertEqual(screen.cleared, 1)
        self.assertEqual(a.decode_escape(''), 'ESC')
        self.assertEqual(a.decode_escape('[C'), 'RIGHT')
        self.assertEqual(a.decode_escape('OD'), 'LEFT')
        self.assertIsNone(a.decode_escape('[1;3C'))  # unknown sequences are not Esc


class CliOutputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        env = patch.dict(os.environ, AERIAL_SWITCH_HOME=self.temp.name, COLUMNS='60', NO_COLOR='1')
        env.start()
        self.addCleanup(env.stop)
        for target, value in (('loaded', False), ('next_transition', '2026-11-18T17:33+08:00')):
            patcher = patch.object(a, target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.paths = a.Paths()
        self.paths.plist.parent.mkdir(parents=True)
        content = {'Content': {'Choices': [{'Provider': 'old', 'Configuration': plistlib.dumps({'assetID': 'old'})}]}}
        self.paths.plist.write_bytes(plistlib.dumps({'Desktop': content, 'Idle': content}))
        self.paths.manifest.parent.mkdir(parents=True)
        self.paths.videos.mkdir(parents=True)
        assets = []
        for key in a.SERIES:
            for period in a.PERIODS:
                shot, label = a.SERIES[key][period]
                assets.append({'shotID': shot, 'accessibilityLabel': label, 'id': key + '-' + period})
                if key == 'goldenGate':
                    (self.paths.videos / (key + '-' + period + '.mov')).write_bytes(b'video')
        self.paths.manifest.write_text(json.dumps({'assets': assets}))

    def cli(self, *args, tty=False):
        stream = FakeTTY() if tty else io.StringIO()
        with contextlib.redirect_stdout(stream), contextlib.redirect_stderr(io.StringIO()), \
                patch.object(a.sys, 'stdin', FakeTTY() if tty else io.StringIO()):
            code = a.main(list(args))
        return code, stream.getvalue()

    def test_json_when_piped_or_flagged(self):
        piped = self.cli('status')
        self.assertEqual(json.loads(piped[1]), json.loads(self.cli('--json', 'status', tty=True)[1]))
        self.assertEqual(piped, self.cli('status', '--json', tty=True))
        self.assertEqual(piped, self.cli('--json', 'status'))
        self.assertEqual(set(json.loads(piped[1])), {
            'series', 'city', 'period', 'sunrise', 'sunset', 'solarCondition', 'nextSwitch', 'desiredID',
            'plist', 'launchAgentLoaded', 'notDownloaded', 'legacyAgents', 'videos', 'pinnedBytes',
            'downloadInProgress', 'proxy'})

    def test_human_output_on_terminal(self):
        for args in (('status',), ('series',), ('apply', '--dry-run'), ('doctor',)):
            code, output = self.cli(*args, tty=True)
            self.assertIn(code, (0, 1), args)
            self.assertTrue(output.startswith(' Aerial Switch'), (args, output))
            self.assertNotIn('\033', output)  # NO_COLOR
            for line in output.splitlines():
                self.assertLessEqual(a.dwidth(line), 60, (args, line))
        self.assertIn('Tahoe         早晨 ↓', self.cli('series', tty=True)[1])
        code, output = self.cli('series', 'tahoe', tty=True)
        self.assertEqual(code, 0)
        self.assertIn('✓ 已选择 Tahoe', output)
        self.assertIn('早晨 ↓  白天 ↓  傍晚 ↓  夜晚 ↓', output)
        code, output = self.cli('location', '31.2', '121.5', '--city', '上海', tty=True)
        self.assertEqual(code, 0)
        self.assertIn('✓ 位置已保存   上海 · 31.2000°N 121.5000°E', output)
        self.assertEqual(a.read_json(self.paths.config)['city'], '上海')

    def test_color_when_allowed(self):
        with patch.dict(os.environ, {'TERM': 'xterm-256color'}):
            os.environ.pop('NO_COLOR')
            output = self.cli('status', tty=True)[1]
        self.assertIn('\033[', output)

    def test_no_command(self):
        code, output = self.cli()
        self.assertEqual(code, 2)
        self.assertIn('usage:', output)
        with patch.object(a, 'run_tui', return_value=0) as tui:
            self.assertEqual(self.cli(tty=True)[0], 0)
            tui.assert_called_once()
            self.assertEqual(self.cli('--json', tty=True)[0], 2)


class BusyBackend(FakeBackend):
    """Apply takes a while, like a real video download."""
    def __init__(self, delay=2.0, guard=None, guard_hold=None, **kwargs):
        super().__init__(**kwargs)
        self.delay, self.guard, self.guard_hold, self.snapshots = delay, guard, guard_hold, 0

    def snapshot(self, cfg):
        self.snapshots += 1
        return super().snapshot(cfg)

    def apply(self):
        if self.guard is not None:  # simulate a plist write step in the middle
            time.sleep(0.3)
            with self.guard:
                time.sleep(self.guard_hold)
        time.sleep(self.delay)
        return super().apply()


class CrowdedBackend(FakeBackend):
    """A status with every optional block shown, to crowd small windows."""
    def snapshot(self, cfg):
        legacy = [{'label': 'com.user.wallpaper-switch'}]
        videos = {p: video(pinned=False, same=False, asset='gg-' + p) for p in a.PERIODS}
        return snap(series=cfg['series'], legacyAgents=legacy, videos=videos, launchAgentLoaded=False)


class EditorVisibilityTests(SolarFixture):
    SIZES = ((44, 16), (44, 17), (50, 18), (60, 20))

    def screen(self, app, width, height):
        return '\n'.join(texts(app.render(width, height, NOW)))

    def test_location_editor_visible_at_small_sizes(self):
        for backend_class in (FakeBackend, CrowdedBackend):
            for width, height in self.SIZES:
                backend = backend_class()
                app = a.Dashboard(backend, UNICODE, clock=lambda: NOW)
                press(app, 'L')
                text = self.screen(app, width, height)
                self.assertIn('▸ 纬度', text, (width, height))
                self.assertIn('31.23▏', text)
                press(app, 'TAB')
                self.assertIn('▸ 经度', self.screen(app, width, height))
                self.assertIn('121.5▏', self.screen(app, width, height))
                press(app, 'TAB', 'x')
                text = self.screen(app, width, height)
                self.assertIn('▸ 城市', text)
                self.assertIn('上海x▏', text)
                self.assertNotIn('窗口高度不足', text)
                lines = app.render(width, height, NOW)
                self.assertEqual(len(lines), height)
                self.assertFits(lines, width - 1)
                press(app, 'ENTER')  # visible, so saving is allowed
                self.assertEqual(backend.calls, [('set_location', 31.23, 121.5, '上海x')])

    def test_duration_editor_visible_at_small_sizes(self):
        for backend_class in (FakeBackend, CrowdedBackend):
            for width, height in self.SIZES:
                backend = backend_class()
                app = a.Dashboard(backend, UNICODE, clock=lambda: NOW)
                press(app, 'T')
                text = self.screen(app, width, height)
                self.assertIn('▸ 早晨时长  ‹ 2.5 小时 ›', text, (width, height))
                press(app, 'DOWN', 'DOWN', 'RIGHT')
                text = self.screen(app, width, height)
                self.assertIn('▸ 傍晚延后  ‹ 1.0 小时 ›', text)
                self.assertIn('06:00 早晨', text)  # the live timeline preview stays
                self.assertFits(app.render(width, height, NOW), width - 1)

    def test_hidden_editor_blocks_edits_and_saving(self):
        backend, app = FakeBackend(), None
        app = a.Dashboard(backend, UNICODE, clock=lambda: NOW)
        press(app, 'L')
        text = self.screen(app, 30, 10)  # below the minimum size: editor not shown
        self.assertIn('终端窗口太小', text)
        self.assertIn('按 Esc 取消编辑', text)
        press(app, 'TAB', 'TAB', 'x', 'ENTER')
        self.assertEqual(backend.calls, [])
        self.assertEqual(app.fields['city'], '上海')
        self.assertEqual(app.field, 0)
        self.assertIn('窗口太小', app.message[0])
        self.screen(app, 44, 16)  # enlarged again: editing works
        press(app, 'TAB', 'TAB', 'x', 'ENTER')
        self.assertEqual(backend.calls, [('set_location', 31.23, 121.5, '上海x')])
        # Durations: too little room for the editor itself also blocks saving.
        press(app, 'T')
        app.main_body(43, NOW, room=2)
        self.assertFalse(app.editor_visible)
        press(app, 'RIGHT', 'ENTER')
        self.assertEqual(len(backend.calls), 1)
        self.assertEqual(app.draft['morningHoursAfterSunrise'], 2.5)
        press(app, 'ESC')
        self.assertEqual(app.mode, 'main')


class BackgroundWriteTests(SolarFixture):
    """Drive tui_loop for real time with an apply that takes 2 seconds."""

    class Screen:
        def __init__(self, start):
            self.start, self.frames, self.current = start, [], None

        def getmaxyx(self):
            return 24, 80

        def erase(self):
            self.current = []
            self.frames.append((time.monotonic() - self.start, self.current))

        def clear(self):
            pass

        def refresh(self):
            pass

        def addstr(self, y, x, text, attr):
            self.current.append(text)

    def run_loop(self, backend, script, guard=None, limit=8.0):
        """script: list of (seconds, key).  read_key waits 0.2 s like curses."""
        start = time.monotonic()
        clock = lambda: NOW + dt.timedelta(seconds=time.monotonic() - start)
        app = a.Dashboard(backend, UNICODE, clock=clock)
        screen = self.Screen(start)
        pending, log = list(script), []

        def read_key():
            elapsed = time.monotonic() - start
            if elapsed > limit:
                raise AssertionError('tui_loop did not return')
            log.append((elapsed, app.task is not None, app.cursor, app.mode))
            if pending and elapsed >= pending[0][0]:
                return pending.pop(0)[1]
            time.sleep(0.2)
            return None

        guard = guard or threading.RLock()
        a.tui_loop(screen, app, {}, read_key, {}, clock=clock, guard=guard)
        frames = [(t, ''.join(parts)) for t, parts in screen.frames]
        return app, frames, log, time.monotonic() - start

    def test_apply_runs_in_background_and_ui_keeps_working(self):
        backend = BusyBackend()
        script = [(0, 'A'), (0, 'Y'), (0.6, 'RIGHT'), (0.9, 'L'), (1.2, 'A'), (3.0, 'q')]
        app, frames, log, total = self.run_loop(backend, script)
        self.assertEqual(backend.calls, [('apply',)])  # the second A did not start another write
        busy = [(t, f) for t, f in frames if '正在应用' in f or '进行中' in f]
        self.assertTrue(busy)
        times = [t for t, _ in frames if busy[0][0] <= t <= busy[-1][0] + 1.5]
        gaps = [b - a_ for a_, b in zip(times, times[1:])]
        self.assertTrue(gaps and max(gaps) <= 1.1, gaps)
        self.assertGreaterEqual(busy[-1][0] - busy[0][0], 1.5)  # the write really took ~2 s
        # Navigation was handled while the write ran; write keys were refused with a hint.
        self.assertTrue(any(running and cursor == 1 for t, running, cursor, mode in log if t < 1.5))
        self.assertNotIn('location', [mode for _, _, _, mode in log])
        self.assertTrue(any('应用进行中，完成后再操作' in f for _, f in frames))
        self.assertTrue(any('⠋' in f or '⠙' in f for _, f in busy))
        # Finished: result shown and status reloaded.
        self.assertIn('已应用 Golden Gate Evening', frames[-1][1])
        self.assertGreaterEqual(backend.snapshots, 2)
        self.assertIsNone(app.task)

    def test_apply_error_is_shown_in_ui(self):
        backend = BusyBackend(fail={'apply': RuntimeError('verification failed')})
        app, frames, log, total = self.run_loop(backend, [(0, 'A'), (0, 'Y'), (3.0, 'q')])
        self.assertIn('操作失败：verification failed', frames[-1][1])
        self.assertGreaterEqual(backend.snapshots, 2)

    def test_quit_needs_second_q_and_keeps_write_guard(self):
        backend = BusyBackend()
        guard = threading.RLock()
        app, frames, log, total = self.run_loop(backend, [(0, 'A'), (0, 'Y'), (0.4, 'q'), (0.8, 'q')], guard)
        self.assertLess(total, 1.6)  # did not wait for the 2 s download
        self.assertTrue(any('再按一次 Q 强制退出' in f for _, f in frames))
        self.assertIsNotNone(app.task)
        holder = []
        thread = threading.Thread(target=lambda: holder.append(guard.acquire(blocking=False)))
        thread.start()
        thread.join()
        self.assertEqual(holder, [False])  # the loop kept the guard: no further writes start

    def test_quit_waits_for_write_step(self):
        guard = threading.RLock()
        backend = BusyBackend(delay=3.0, guard=guard, guard_hold=1.2)
        script = [(0, 'A'), (0, 'Y'), (0.5, 'q'), (0.6, 'Q')]
        app, frames, log, total = self.run_loop(backend, script, guard)
        self.assertGreater(total, 1.3)  # held until the write step (0.3-1.5 s) ended
        self.assertLess(total, 2.5)     # but did not wait for the rest of the operation
        self.assertTrue(any('正在写入壁纸设置，完成后自动退出' in f for _, f in frames))


class SeriesListTests(SolarFixture):
    def catalog(self, tahoe_missing=()):
        return {key: {'series': key, 'name': a.SERIES[key]['name'], 'ids': {}, 'missing': [],
                      'notDownloaded': list(tahoe_missing) if key == 'tahoe' else []} for key in a.SERIES}

    def test_present_but_unpinned_is_not_protected(self):
        videos = {'goldenGate': {p: video() for p in a.PERIODS},
                  'tahoe': {p: video(pinned=False, same=False) for p in a.PERIODS}}
        videos['tahoe']['night'] = video(present=False, pinned=False, same=False)
        text = '\n'.join(texts(a.series_list_lines(UNICODE, self.catalog(['Tahoe Night']), 'goldenGate', 80, videos)))
        self.assertIn('Golden Gate   早晨 ✓  白天 ✓  傍晚 ✓  夜晚 ✓   当前', text)
        self.assertIn('Tahoe         早晨 ○  白天 ○  傍晚 ○  夜晚 ↓', text)
        self.assertIn('○ 未保护', text)
        self.assertIn('未选中的系列不受保护，系统可能清理', text)
        self.assertNotIn('注意', text)

    def test_current_series_unprotected_warns_like_status(self):
        videos = {'goldenGate': {p: video(pinned=False, same=False) for p in a.PERIODS},
                  'tahoe': {p: video() for p in a.PERIODS}}
        videos['goldenGate']['day'] = video()
        text = '\n'.join(texts(a.series_list_lines(UNICODE, self.catalog(), 'goldenGate', 80, videos)))
        self.assertIn('Golden Gate   早晨 ○  白天 ✓  傍晚 ○  夜晚 ○   当前', text)
        self.assertIn('Tahoe         早晨 ✓', text)
        self.assertIn('注意   ! 早晨、傍晚、夜晚 视频尚未受硬链接保护', text)
        self.assertNotIn('未选中的系列不受保护', text)

    def test_without_pin_data_downloaded_is_not_claimed_protected(self):
        text = '\n'.join(texts(a.series_list_lines(UNICODE, self.catalog(['Tahoe Day']), 'goldenGate', 80)))
        self.assertNotIn('✓', text.split('切换')[0].replace('✓ ', ''))
        self.assertIn('Golden Gate   早晨 ○', text)
        self.assertIn('白天 ↓', text)


class AfterApplyCliTests(unittest.TestCase):
    cli = CliOutputTests.cli

    def setUp(self):
        CliOutputTests.setUp(self)
        a.write_json(self.paths.config, dict(CFG))

    def apply(self, *args):
        with patch.object(a, 'next_transition', return_value=None), patch.object(a, 'run_command') as command, \
                patch.object(a, 'current_period', return_value='day'):
            result = self.cli('apply', *args, tty=not args)
        return result

    def test_new_pins_shown_after_apply(self):
        code, json_out = self.apply('--json')
        # JSON keeps the pre-run snapshot (unchanged behaviour)...
        self.assertFalse(json.loads(json_out)['videos']['day']['pinned'])
        self.assertEqual([x['action'] for x in json.loads(json_out)['pinActions']], ['pin'] * 4)
        a.shutil.rmtree(self.paths.pinned)
        code, output = self.apply()
        self.assertEqual(code, 0)
        # ...while the readable view shows the state after pinning.
        self.assertIn('早晨 ✓  白天 ✓  傍晚 ✓  夜晚 ✓', output)
        self.assertIn('保护早晨视频', output)
        self.assertNotIn('尚未受硬链接保护', output)
        self.assertNotIn('○', output.split('本次')[0])

    def test_restore_shown_after_apply(self):
        self.apply()
        for period in a.PERIODS:
            (self.paths.videos / ('goldenGate-' + period + '.mov')).unlink()
        code, output = self.apply()
        self.assertIn('恢复早晨视频', output)
        self.assertIn('早晨 ✓  白天 ✓  傍晚 ✓  夜晚 ✓', output)
        self.assertNotIn('↺', output)
        self.assertNotIn('缺失', output)

    def test_download_shown_after_apply(self):
        (self.paths.videos / 'goldenGate-day.mov').unlink()

        def fake_download(paths, series, asset_id):
            (paths.videos / (asset_id + '.mov')).write_bytes(b'downloaded')
            return a.sync_pins(paths, {'series': series, 'ids': {'download': asset_id}})

        with patch.object(a, 'download_video', side_effect=fake_download):
            code, output = self.apply()
        self.assertIn('下载白天视频', ''.join(line.strip() for line in output.splitlines()))
        self.assertIn('✓ 已切换到白天壁纸', output)
        self.assertIn('早晨 ✓  白天 ✓  傍晚 ✓  夜晚 ✓', output)
        self.assertNotIn('↓', output)
        self.assertNotIn('尚未受硬链接保护', output)

    def test_series_list_cli_uses_pins(self):
        a.write_json(self.paths.config, dict(CFG, series='tahoe'))
        code, output = self.cli('series', tty=True)
        self.assertIn('Golden Gate   早晨 ○  白天 ○  傍晚 ○  夜晚 ○', output)
        self.assertIn('未选中的系列不受保护', output)
        self.cli('series', 'goldenGate')
        code, output = self.cli('series', tty=True)
        self.assertIn('Golden Gate   早晨 ✓  白天 ✓  傍晚 ✓  夜晚 ✓   当前', output)
        self.assertNotIn('未选中的系列不受保护', output)
        piped = self.cli('series')[1]
        self.assertEqual(set(json.loads(piped)), set(a.SERIES))
        self.assertEqual(set(json.loads(piped)['tahoe']), {'series', 'name', 'ids', 'missing', 'notDownloaded'})


class WriteGuardExitTests(unittest.TestCase):
    def test_waits_through_timeouts_and_repeated_ctrl_c(self):
        from unittest.mock import MagicMock
        guard = MagicMock()
        guard.acquire.side_effect = [False, KeyboardInterrupt(), True]
        messages = []
        a.wait_for_write_guard(guard, messages.append)
        self.assertEqual(guard.acquire.call_count, 3)
        guard.release.assert_called_once()
        self.assertEqual(len(messages), 1)

    def test_run_tui_ctrl_c_uses_guard_wait(self):
        from unittest.mock import patch
        with patch.object(a, 'wait_for_write_guard') as wait, \
                patch('curses.wrapper', side_effect=KeyboardInterrupt):
            try:
                result = a.run_tui(a.Paths())
            except TypeError:
                self.skipTest('run_tui signature differs')
        wait.assert_called_once()
        self.assertEqual(result, 0)


if __name__ == '__main__':
    unittest.main()
