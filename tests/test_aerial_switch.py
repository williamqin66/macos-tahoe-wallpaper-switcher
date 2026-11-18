import fcntl
import http.server
import threading
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
import subprocess
import unittest
from unittest.mock import patch

loader = importlib.machinery.SourceFileLoader('aerial_switch', str(Path(__file__).resolve().parents[1] / 'aerial-switch'))
spec = importlib.util.spec_from_loader(loader.name, loader)
a = importlib.util.module_from_spec(spec)
loader.exec_module(a)


def fixture(linked=False):
    def content():
        return {'Content': {'Choices': [{'Provider': 'old', 'Configuration': plistlib.dumps({'assetID': 'old'}), 'other': 42}]}}
    if linked:
        return {'AllSpacesAndDisplays': {'Linked': content()}, 'SystemDefault': {'Linked': content()}, 'untouched': ['keep']}
    return {'Desktop': content(), 'Idle': content(), 'untouched': ['keep']}


class SolarTests(unittest.TestCase):
    def test_boundaries(self):
        for h, expected in [(5.99, 'night'), (6, 'morning'), (8.5, 'day'), (16.5, 'evening'), (18.5, 'night')]:
            self.assertEqual(a.period_at(h, 6, 18, 0, a.DEFAULTS), expected)

    def test_midnight(self):
        self.assertTrue(a.in_window(.25, 23, 25))
        self.assertTrue(a.in_window(23, -2, 1))
        self.assertFalse(a.in_window(2, 23, 25))
        self.assertFalse(a.in_window(1, 3, 2))
        self.assertEqual(a.period_at(.25, 12, 24, 0, a.DEFAULTS), 'evening')

    def test_polar(self):
        for h in range(24):
            self.assertEqual(a.period_at(h, 0, 24, -1, a.DEFAULTS), 'day')
            self.assertEqual(a.period_at(h, 12, 12, 1, a.DEFAULTS), 'night')
        now = dt.datetime(2026, 6, 21, tzinfo=dt.timezone.utc)
        self.assertLess(a.solar(now, 89, 0)[2], -1)
        self.assertGreater(a.solar(now.replace(month=12), 89, 0)[2], 1)

    def test_short_winter_day(self):
        self.assertEqual(a.period_at(11, 10, 12, 0, a.DEFAULTS), 'morning')
        self.assertEqual(a.period_at(12.5, 10, 12, 0, a.DEFAULTS), 'night')
        self.assertEqual(a.period_at(12.5, 10, 12.5, 0, a.DEFAULTS), 'evening')

    def test_next_transition(self):
        now = dt.datetime(2026, 11, 18, 10, 0).astimezone()
        cfg = dict(a.DEFAULTS, lat=31.2304, lon=121.4737)
        transition = dt.datetime.fromisoformat(a.next_transition(now, cfg))
        self.assertGreater(transition, now)
        self.assertNotEqual(a.current_period(transition, cfg), a.current_period(now, cfg))
        self.assertEqual(a.current_period(transition - dt.timedelta(minutes=1), cfg), a.current_period(now, cfg))

    def test_clock(self):
        for value in (6.999, 23.999, -.001, -24.001, 48.999):
            text = a.clock(value)
            self.assertNotIn(':60', text)
            self.assertTrue(0 <= int(text[:2]) < 24)
        self.assertEqual(a.clock(6.999), '07:00')
        self.assertEqual(a.clock(23.999), '00:00')


class IsolatedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(os.environ, AERIAL_SWITCH_HOME=self.temp.name)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.paths = a.Paths()
        self.paths.plist.parent.mkdir(parents=True)
        self.paths.plist.write_bytes(plistlib.dumps(fixture()))
        self.paths.manifest.parent.mkdir(parents=True)
        self.paths.videos.mkdir(parents=True)
        assets = []
        for key in a.SERIES:
            for period in a.PERIODS:
                shot, label = a.SERIES[key][period]
                asset_id = key + '-' + period
                assets.append({'shotID': shot, 'accessibilityLabel': label, 'id': asset_id})
                (self.paths.videos / (asset_id + '.mov')).write_bytes(b'video')
        self.paths.manifest.write_text(json.dumps({'assets': assets}))

    def cli(self, *args):
        with contextlib.redirect_stdout(io.StringIO()) as output, contextlib.redirect_stderr(io.StringIO()):
            code = a.main(list(args))
        return code, output.getvalue()

    def test_series_lookup_and_fallback(self):
        for key in a.SERIES:
            info = a.series_payload(str(self.paths.manifest), key, str(self.paths.videos))
            self.assertEqual(len(info['ids']), 4)
            self.assertEqual(info['notDownloaded'], [])
        manifest = a.read_json(self.paths.manifest)
        manifest['assets'][0].pop('shotID')
        self.paths.manifest.write_text(json.dumps(manifest))
        self.assertEqual(a.selected_series(self.paths, a.DEFAULTS)['ids']['morning'], 'tahoe-morning')

    def test_series_download_force_and_missing(self):
        (self.paths.videos / 'goldenGate-night.mov').unlink()
        code, output = self.cli('series', 'goldenGate')
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output)['notDownloaded'], ['Golden Gate Night'])
        self.assertTrue(self.paths.config.exists())
        self.assertEqual(self.cli('series', 'goldenGate', '--force')[0], 0)
        self.paths.manifest.write_text('{"assets": []}')
        self.assertEqual(self.cli('series', 'tahoe', '--force')[0], 1)

    def test_location_validation(self):
        for lat, lon in [('91', '0'), ('0', '-181'), ('nan', '0')]:
            self.assertEqual(self.cli('location', lat, lon)[0], 1)
        self.assertFalse(self.paths.config.exists())
        self.assertEqual(self.cli('location', '31', '121', '--city', 'Shanghai')[0], 0)
        self.assertEqual(a.load_config(self.paths)['city'], 'Shanghai')
        for raw in [{'lat': True}, {'series': 'unknown'}, {'eveningHoursAfterSunset': 7}]:
            with self.assertRaises(ValueError):
                a.validate_config(raw)

    def test_migration_read_only_then_copy(self):
        self.paths.legacy.parent.mkdir(parents=True)
        self.paths.legacy.write_text('{"city":"Legacy","lat":32}')
        a.load_config(self.paths)
        self.assertFalse(self.paths.config.exists())
        a.load_config(self.paths, migrate=True)
        self.assertEqual(self.paths.config.read_bytes(), self.paths.legacy.read_bytes())

    def test_apply_both_layouts_and_noop(self):
        for linked in (False, True):
            original = fixture(linked)
            before = plistlib.dumps(original)
            self.paths.plist.write_bytes(before)
            result = a.apply_asset(str(self.paths.plist), 'new', str(self.paths.backup))
            self.assertEqual(result['updated'], 2)
            self.assertEqual(self.paths.backup.read_bytes(), before)
            self.assertTrue(a.status_payload(str(self.paths.plist), 'new')['allMatch'])
            after = a.load_plist(self.paths.plist)
            self.assertEqual(after['untouched'], ['keep'])
            for target in a.collect_target_choices(after):
                self.assertEqual(target['choice']['other'], 42)
            modified = self.paths.plist.stat().st_mtime_ns
            self.assertEqual(a.apply_asset(str(self.paths.plist), 'new', str(self.paths.backup))['updated'], 0)
            self.assertEqual(self.paths.plist.stat().st_mtime_ns, modified)
            self.assertEqual(self.paths.backup.read_bytes(), before)

    def test_verification_rollback(self):
        for linked in (False, True):
            before = plistlib.dumps(fixture(linked))
            self.paths.plist.write_bytes(before)
            with patch.object(a, 'status_payload', side_effect=[{'allMatch': False}, {'allMatch': False}]):
                with self.assertRaisesRegex(RuntimeError, 'restored'):
                    a.apply_asset(str(self.paths.plist), 'new', str(self.paths.backup))
            self.assertEqual(self.paths.plist.read_bytes(), before)

    def test_write_failure(self):
        before = self.paths.plist.read_bytes()
        with patch.object(a, 'atomic_write_plist', side_effect=OSError('write failure')):
            with self.assertRaises(OSError):
                a.apply_asset(str(self.paths.plist), 'new', str(self.paths.backup))
        self.assertEqual(self.paths.plist.read_bytes(), before)

    def test_missing_targets_and_wrong_provider(self):
        self.paths.plist.write_bytes(plistlib.dumps({'Desktop': {'Content': {'Choices': []}}}))
        with self.assertRaises(RuntimeError):
            a.apply_asset(str(self.paths.plist), 'new', str(self.paths.backup))
        self.assertFalse(self.paths.backup.exists())
        self.paths.plist.write_bytes(plistlib.dumps(fixture()))
        self.assertFalse(a.status_payload(str(self.paths.plist), 'old')['allMatch'])

    def test_readonly_commands(self):
        with patch.object(a, 'loaded', return_value=False), patch.object(a, 'run_command', side_effect=AssertionError('unexpected command')):
            for args in [('status',), ('series',), ('apply', '--dry-run')]:
                self.assertEqual(self.cli(*args)[0], 0)
        self.assertFalse(self.paths.support.exists())
        self.assertEqual(a.configuration_asset_id({'Configuration': b'bad'}), None)

    def test_apply_self_heal_and_state_noop(self):
        with patch.object(a, 'loaded', return_value=False), patch.object(a, 'run_command') as command:
            self.assertEqual(self.cli('apply')[0], 0)
            state_mtime = self.paths.state.stat().st_mtime_ns
            plist_mtime = self.paths.plist.stat().st_mtime_ns
            self.assertEqual(self.cli('apply')[0], 0)
            self.assertEqual(self.paths.state.stat().st_mtime_ns, state_mtime)
            self.assertEqual(self.paths.plist.stat().st_mtime_ns, plist_mtime)
            self.assertEqual(command.call_count, 1)
            self.paths.plist.write_bytes(plistlib.dumps(fixture(True)))
            self.assertEqual(self.cli('apply')[0], 0)
            self.assertEqual(command.call_count, 2)
            state = a.read_json(self.paths.state)
            self.assertTrue(a.status_payload(str(self.paths.plist), state['desiredID'])['allMatch'])

    def test_restore_mocked(self):
        before = self.paths.plist.read_bytes()
        a.apply_asset(str(self.paths.plist), 'new', str(self.paths.backup))
        with patch.object(a, 'loaded', return_value=False), patch.object(a, 'run_command'):
            a.uninstall(self.paths, True)
        self.assertEqual(self.paths.plist.read_bytes(), before)

    def test_missing_videos_preview(self):
        (self.paths.videos / 'tahoe-night.mov').unlink()
        with patch.object(a, 'loaded', return_value=False):
            code, text = self.cli('apply', '--dry-run')
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(text)['ready'], json.loads(text)['desiredID'] != 'tahoe-night')
        self.assertEqual(json.loads(text)['wouldUpdate'], json.loads(text)['ready'])
        self.assertFalse(self.paths.support.exists())

    def test_launch_agent_lifecycle_mocked(self):
        # No real launchctl, killall or user Library access.
        with patch.object(a, 'run_command', side_effect=self.launchctl_mock({a.OLD_LABELS[-1]})) as command:
            with self.assertRaisesRegex(ValueError, '--migrate'):
                a.install(self.paths, str(Path(self.temp.name) / 'prefix'), False)
            result = a.install(self.paths, str(Path(self.temp.name) / 'prefix'), True)
            agent = a.load_plist(self.paths.agent)
            self.assertEqual(agent['StartInterval'], 900)
            self.assertTrue(agent['RunAtLoad'])
            self.assertEqual(agent['ProgramArguments'], [result['installed'], 'apply'])
            self.assertIn(a.OLD_LABELS[-1], str([c for c in command.call_args_list if c.args[0][1] == 'bootout'][0]))
            self.assertEqual(command.call_args_list[-1].args[0][1], 'bootstrap')
        with patch.object(a, 'loaded', return_value=False):
            with self.assertRaisesRegex(ValueError, 'Backup does not exist'):
                a.uninstall(self.paths, True)
            self.assertTrue(self.paths.agent.exists())
            a.uninstall(self.paths, False)
            self.assertFalse(self.paths.agent.exists())

    @contextlib.contextmanager
    def cdn(self, short=False, ignore_range=False):
        body = b'local movie bytes' * 100
        requests = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(handler):
                value = handler.headers.get('Range')
                requests.append(value)
                offset = int(value[6:-1]) if value and not ignore_range else 0
                if_range = handler.headers.get('If-Range')
                if if_range is not None and if_range != '"test-etag"':
                    offset = 0  # RFC 9110: changed representation -> full 200
                handler.send_response(206 if offset else 200)
                handler.send_header('ETag', '"test-etag"')
                handler.send_header('Content-Length', str(len(body) - offset + (10 if short else 0)))
                if offset:
                    handler.send_header('Content-Range', f'bytes {offset}-{len(body)-1}/{len(body)}')
                handler.end_headers()
                handler.wfile.write(body[offset:])
                handler.close_connection = True

            def log_message(self, *args):
                pass

        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            url = f'http://127.0.0.1:{server.server_port}/video.mov'
            manifest = a.read_json(self.paths.manifest)
            for asset in manifest['assets']:
                asset['url-4K-SDR-240FPS'] = url
            self.paths.manifest.write_text(json.dumps(manifest))
            # Explicitly bypass all external proxies for the isolated local CDN.
            with patch.object(a.urllib.request, '_opener', a.urllib.request.build_opener(a.urllib.request.ProxyHandler({}))):
                yield body, requests, url
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())

    def test_pin_restore_replace_and_unique_size(self):
        info = a.selected_series(self.paths, a.DEFAULTS)
        a.sync_pins(self.paths, info)
        video = self.paths.videos / 'tahoe-day.mov'
        pin = a.pin_path(self.paths, 'tahoe', 'tahoe-day')
        inode = video.stat().st_ino
        video.unlink()
        a.sync_pins(self.paths, info)
        self.assertEqual(video.stat().st_ino, inode)
        self.assertTrue(os.path.samefile(video, pin))
        video.unlink()
        video.write_bytes(b'new version')
        a.sync_pins(self.paths, info)
        self.assertTrue(os.path.samefile(video, pin))
        self.assertEqual(pin.read_bytes(), b'new version')
        os.link(pin, pin.parent / 'duplicate.mov')
        self.assertEqual(a.cache_status(self.paths, info)['pinnedBytes'], 3*5+11)

    def test_series_removes_old_pins_without_download_or_switch(self):
        a.sync_pins(self.paths, a.selected_series(self.paths, a.DEFAULTS))
        before = self.paths.plist.read_bytes()
        with patch.object(a.urllib.request, 'urlopen', side_effect=AssertionError('network')):
            self.assertEqual(self.cli('series', 'goldenGate')[0], 0)
        self.assertFalse((self.paths.pinned / 'tahoe').exists())
        self.assertEqual(len(list((self.paths.pinned / 'goldenGate').iterdir())), 4)
        self.assertEqual(self.paths.plist.read_bytes(), before)

    def test_cross_device_skips_pins(self):
        with patch.object(a, 'device', side_effect=[1, 2]):
            result = a.sync_pins(self.paths, a.selected_series(self.paths, a.DEFAULTS))
        self.assertIn('different devices', str(result['warnings']))
        self.assertFalse(self.paths.pinned.exists())

    def test_download_success_and_xattrs_before_switch(self):
        (self.paths.videos / 'tahoe-day.mov').unlink()
        with self.cdn() as (body, requests, url), patch.object(a, 'current_period', return_value='day'), patch.object(a, 'next_transition', return_value=None), patch.object(a, 'run_command', side_effect=self.launchctl_mock()) as command:
            code, text = self.cli('apply')
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(text)['updated'], 2)
            video = self.paths.videos / 'tahoe-day.mov'
            self.assertEqual(video.read_bytes(), body)
            self.assertEqual(video.stat().st_mode & 0o777, 0o600)
            self.assertTrue(os.path.samefile(video, a.pin_path(self.paths, 'tahoe', 'tahoe-day')))
            attrs = [c.args[0] for c in command.call_args_list if c.args[0][0] == '/usr/bin/xattr']
            part = str(self.paths.videos / '.tahoe-day.mov.part')
            self.assertEqual(attrs, [['/usr/bin/xattr', '-w', 'SourceURL', url, part], ['/usr/bin/xattr', '-w', 'LastETag', '"test-etag"', part]])
            self.assertEqual(requests, [None])

    def test_length_mismatch_keeps_wallpaper_deletes_part(self):
        before = self.paths.plist.read_bytes()
        (self.paths.videos / 'tahoe-day.mov').unlink()
        with self.cdn(short=True), patch.object(a, 'current_period', return_value='day'), patch.object(a, 'next_transition', return_value=None), patch.object(a, 'run_command', side_effect=self.launchctl_mock()):
            code, text = self.cli('apply')
        self.assertEqual(code, 0)
        self.assertIn('length mismatch', json.loads(text)['downloadError'])
        self.assertEqual(self.paths.plist.read_bytes(), before)
        self.assertFalse((self.paths.videos / '.tahoe-day.mov.part').exists())
        self.assertFalse(self.paths.state.exists())

    def test_range_resume_and_server_ignoring_range(self):
        for ignore in (False, True):
            video = self.paths.videos / 'tahoe-day.mov'
            video.unlink()
            with self.cdn(ignore_range=ignore) as (body, requests, url), patch.object(a, 'run_command'):
                (self.paths.videos / '.tahoe-day.mov.part').write_bytes(body[:7])
                a.write_json(self.paths.videos / '.tahoe-day.mov.part.json', {'url': url, 'etag': '"test-etag"'})
                a.download_video(self.paths, 'tahoe', 'tahoe-day')
                self.assertEqual(requests, ['bytes=7-'])
                self.assertEqual(video.read_bytes(), body)

    def test_download_lock_contention_no_switch(self):
        before = self.paths.plist.read_bytes()
        (self.paths.videos / 'tahoe-day.mov').unlink()
        self.paths.support.mkdir(parents=True)
        with open(self.paths.download_lock, 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertTrue(a.download_busy(self.paths))
            with patch.object(a, 'current_period', return_value='day'), patch.object(a, 'next_transition', return_value=None), patch.object(a, 'run_command', side_effect=self.launchctl_mock()), patch.object(a.urllib.request, 'urlopen', side_effect=AssertionError('network')):
                code, text = self.cli('apply')
            self.assertEqual(code, 0)
            self.assertIn('DownloadBusy', json.loads(text)['downloadError'])
        self.assertEqual(before, self.paths.plist.read_bytes())
        self.assertFalse(a.download_busy(self.paths))

    def test_network_failure_does_not_switch(self):
        before = self.paths.plist.read_bytes()
        (self.paths.videos / 'tahoe-day.mov').unlink()
        with self.cdn(), patch.object(a.urllib.request, 'urlopen', side_effect=TimeoutError('timeout')), patch.object(a, 'current_period', return_value='day'), patch.object(a, 'next_transition', return_value=None), patch.object(a, 'run_command', side_effect=self.launchctl_mock()):
            code, text = self.cli('apply')
        self.assertEqual(code, 0)
        self.assertIn('TimeoutError', json.loads(text)['downloadError'])
        self.assertEqual(before, self.paths.plist.read_bytes())

    def test_prefetch_window_and_failure(self):
        for minutes, fail in [(44, False), (46, False), (44, True)]:
            video = self.paths.videos / 'tahoe-evening.mov'
            video.unlink(missing_ok=True)
            pin = a.pin_path(self.paths, 'tahoe', 'tahoe-evening')
            pin.unlink(missing_ok=True)
            when = dt.datetime.now().astimezone() + dt.timedelta(minutes=minutes)
            with self.cdn() as (body, requests, url), patch.object(a, 'current_period', side_effect=lambda now, cfg: 'evening' if now == when else 'day'), patch.object(a, 'next_transition', return_value=when.isoformat()), patch.object(a, 'run_command', side_effect=self.launchctl_mock()):
                if fail:
                    with patch.object(a.urllib.request, 'urlopen', side_effect=TimeoutError()):
                        code, text = self.cli('apply')
                    self.assertIn('Prefetch failed', str(json.loads(text)['warnings']))
                else:
                    code, text = self.cli('apply')
                    self.assertEqual(len(requests), int(minutes < 45))
                    self.assertEqual(video.exists(), minutes < 45)
                self.assertEqual(code, 0)

    def test_dry_run_no_writes_or_network_with_missing_videos(self):
        for video in self.paths.videos.iterdir():
            video.unlink()
        before = {str(p): (p.stat().st_mtime_ns, p.read_bytes()) for p in Path(self.temp.name).rglob('*') if p.is_file()}
        with patch.object(a, 'loaded', return_value=False), patch.object(a.urllib.request, 'urlopen', side_effect=AssertionError('network')):
            code, text = self.cli('apply', '--dry-run')
        self.assertEqual(code, 0)
        self.assertIn('download', str(json.loads(text)['plannedActions']))
        after = {str(p): (p.stat().st_mtime_ns, p.read_bytes()) for p in Path(self.temp.name).rglob('*') if p.is_file()}
        self.assertEqual(before, after)
        self.assertFalse(self.paths.support.exists())

    def test_uninstall_removes_pins(self):
        a.sync_pins(self.paths, a.selected_series(self.paths, a.DEFAULTS))
        with patch.object(a, 'loaded', return_value=False):
            result = a.uninstall(self.paths, False)
        self.assertIn('removedPins', result)
        self.assertFalse(self.paths.pinned.exists())
        self.assertEqual(len(list(self.paths.videos.iterdir())), 8)

    def test_download_url_fallback_and_proxy_redaction(self):
        manifest = a.read_json(self.paths.manifest)
        manifest['assets'][0].update({'url-z': 'http://localhost/z.mov', 'url-a': 'http://localhost/a.mov'})
        self.paths.manifest.write_text(json.dumps(manifest))
        self.assertEqual(a.download_url(self.paths, 'tahoe-morning'), 'http://localhost/a.mov')
        manifest['assets'][0]['url-4K-SDR-240FPS'] = 'http://localhost/preferred.mov'
        self.paths.manifest.write_text(json.dumps(manifest))
        self.assertEqual(a.download_url(self.paths, 'tahoe-morning'), 'http://localhost/preferred.mov')
        with patch.object(a.urllib.request, 'getproxies', return_value={'https': 'http://user:secret@proxy', 'no': '*'}):
            result = a.cache_status(self.paths, a.selected_series(self.paths, a.DEFAULTS))
        self.assertEqual(result['proxy'], {'detected': True, 'schemes': ['https']})
        self.assertNotIn('secret', str(result))

    def test_prefetch_restores_pin_without_network(self):
        a.sync_pins(self.paths, a.selected_series(self.paths, a.DEFAULTS))
        video = self.paths.videos / 'tahoe-evening.mov'
        inode = video.stat().st_ino
        video.unlink()
        when = dt.datetime.now().astimezone() + dt.timedelta(minutes=20)
        with patch.object(a, 'current_period', side_effect=lambda now, cfg: 'evening' if now == when else 'day'), patch.object(a, 'next_transition', return_value=when.isoformat()), patch.object(a, 'run_command', side_effect=self.launchctl_mock()), patch.object(a.urllib.request, 'urlopen', side_effect=AssertionError('network')):
            code, text = self.cli('apply')
        self.assertEqual(code, 0)
        self.assertEqual(video.stat().st_ino, inode)
        self.assertIn('restore', str(json.loads(text)['pinActions']))

    def test_xattr_failure_keeps_current_wallpaper(self):
        before = self.paths.plist.read_bytes()
        video = self.paths.videos / 'tahoe-day.mov'
        video.unlink()
        runner = self.launchctl_mock()

        def command(args, check=True):
            if args[0] == '/usr/bin/xattr':
                raise subprocess.CalledProcessError(1, args)
            return runner(args, check)

        with self.cdn(), patch.object(a, 'current_period', return_value='day'), patch.object(a, 'next_transition', return_value=None), patch.object(a, 'run_command', side_effect=command):
            code, text = self.cli('apply')
        self.assertEqual(code, 0)
        self.assertIn('CalledProcessError', json.loads(text)['downloadError'])
        self.assertFalse(video.exists())
        self.assertEqual(before, self.paths.plist.read_bytes())

    def test_http_416_discards_stale_part(self):
        (self.paths.videos / 'tahoe-day.mov').unlink()
        part = self.paths.videos / '.tahoe-day.mov.part'
        part.write_bytes(b'stale')
        with self.cdn(), patch.object(a.urllib.request, 'urlopen', side_effect=a.urllib.error.HTTPError('http://localhost/video.mov', 416, 'range', {}, None)):
            with self.assertRaises(a.urllib.error.HTTPError):
                a.download_video(self.paths, 'tahoe', 'tahoe-day')
        self.assertFalse(part.exists())

    def launchctl_mock(self, active=(), fail_label=None, stuck=False):
        active = set(active)

        def command(args, check=True):
            result = subprocess.CompletedProcess(args, 0, stdout="", stderr="")
            if args[0] == "/bin/launchctl":
                label = args[-1].split("/")[-1]
                if args[1] == "print":
                    if label not in active:
                        result.returncode = 113
                        result.stderr = "Could not find service"
                elif args[1] == "bootout":
                    if label == fail_label:
                        result.returncode = 5
                        result.stderr = "Boot-out failed: 5: Input/output error"
                    elif label not in active:
                        result.returncode = 3
                        result.stderr = "Boot-out failed: 3: No such process"
                    elif not stuck:
                        active.remove(label)
                elif args[1] == "bootstrap":
                    active.add(a.LABEL)
            if check and result.returncode:
                raise subprocess.CalledProcessError(result.returncode, args, result.stdout, result.stderr)
            return result
        return command

    def make_legacy_plists(self):
        self.paths.agent.parent.mkdir(parents=True, exist_ok=True)
        result = []
        for label in a.OLD_LABELS:
            path = self.paths.agent.parent / (label + '.plist')
            path.write_bytes(plistlib.dumps({'Label': label}))
            result.append(path)
        return result

    def test_missing_videos_apply_and_strict(self):
        for video in self.paths.videos.iterdir():
            video.unlink()
        before = self.paths.plist.read_bytes()
        with patch.object(a, 'run_command', side_effect=self.launchctl_mock()):
            for args in [('apply', '--strict'), ('apply',)]:
                code, text = self.cli(*args)
                self.assertEqual(code, 0)
                self.assertIn('No download URL', json.loads(text)['downloadError'])
                self.assertEqual(json.loads(text)['updated'], 0)
                self.assertEqual(self.paths.plist.read_bytes(), before)
            code, text = self.cli('apply', '--dry-run', '--strict')
            self.assertFalse(json.loads(text)['ready'])

    def test_doctor_missing_videos_is_warning_but_missing_id_is_error(self):
        (self.paths.videos / 'tahoe-night.mov').unlink()
        with patch.object(a, 'run_command', side_effect=self.launchctl_mock()):
            code, text = self.cli('doctor')
            self.assertEqual(code, 0)
            self.assertIn('notDownloaded', json.loads(text)['selectedVideos']['warning'])
            self.paths.manifest.write_text('{"assets": []}')
            self.assertEqual(self.cli('doctor')[0], 1)

    def test_migrate_guard_first_and_move_immutable_plists(self):
        plists = self.make_legacy_plists()
        before = {path: path.read_bytes() for path in plists}
        # Model uchg using a set: moving is only allowed after mocked chflags.
        immutable = set(plists[:2])
        original_rename = Path.rename
        runner = self.launchctl_mock(a.OLD_LABELS[:2])

        def command(args, check=True):
            if args[:2] == ['/usr/bin/chflags', 'nouchg']:
                immutable.discard(Path(args[2]))
            return runner(args, check)

        def rename(source, target):
            self.assertNotIn(source, immutable)
            return original_rename(source, target)

        legacy_files = [self.paths.legacy, self.paths.legacy.parent / '.tahoe-wallpaper-switcher-recovery/guard.sh',
                        self.paths.legacy.parent / 'wallpaper-switch.js']
        for path in legacy_files:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('{}' if path == self.paths.legacy else 'keep')
        with patch.object(a, 'run_command', side_effect=command) as mocked, patch.object(Path, 'rename', rename):
            result = a.install(self.paths, str(Path(self.temp.name) / 'prefix'), True)
        calls = [call.args[0] for call in mocked.call_args_list]
        stops = [call[-1].split('/')[-1] for call in calls if call[1] == 'bootout']
        self.assertEqual(stops, list(a.OLD_LABELS[:2]))
        self.assertEqual(calls[-1][1], 'bootstrap')
        self.assertTrue(all(call[1] == 'print' for call in calls[-4:-1]))
        migration = result['migration']
        self.assertEqual(migration['labels'], list(a.OLD_LABELS))
        backup = Path(migration['backupDirectory'])
        self.assertEqual(backup.parent, self.paths.support / 'legacy-backup')
        for source in plists:
            self.assertFalse(source.exists())
            self.assertEqual((backup / source.name).read_bytes(), before[source])
        for path in legacy_files:
            self.assertEqual(path.read_text(), '{}' if path == self.paths.legacy else 'keep')

    def test_legacy_files_without_loaded_jobs_require_migrate_and_warn(self):
        plists = self.make_legacy_plists()
        with patch.object(a, 'run_command', side_effect=self.launchctl_mock()) as command:
            with self.assertRaisesRegex(ValueError, '--migrate'):
                a.install(self.paths, None, False)
            self.assertFalse(any(c.args[0][1] == 'bootout' for c in command.call_args_list))
            for name in ('status', 'doctor'):
                code, text = self.cli(name)
                self.assertEqual(code, 0)
                result = json.loads(text)
                info = result['launchAgent']['detail'] if name == 'doctor' else result
                self.assertEqual(len(info['legacyAgents']), 3)
                self.assertIn('install --migrate', info['warning'])
        self.assertTrue(all(path.exists() for path in plists))

    def test_migration_failure_aborts_before_bootstrap_or_move(self):
        for fail_label in a.OLD_LABELS:
            plists = self.make_legacy_plists()
            with patch.object(a, 'run_command', side_effect=self.launchctl_mock(a.OLD_LABELS, fail_label)) as command:
                with self.assertRaisesRegex(RuntimeError, 'Cannot stop'):
                    a.install(self.paths, None, True)
            self.assertFalse(any(c.args[0][1] == 'bootstrap' for c in command.call_args_list))
            self.assertTrue(all(path.exists() for path in plists))
            self.assertFalse(self.paths.config.exists())

    def test_migration_still_loaded_aborts(self):
        with patch.object(a, 'run_command', side_effect=self.launchctl_mock(a.OLD_LABELS, stuck=True)) as command:
            with self.assertRaisesRegex(RuntimeError, 'still loaded'):
                a.install(self.paths, None, True)
        self.assertFalse(any(c.args[0][1] == 'bootstrap' for c in command.call_args_list))

    def test_chflags_failure_aborts_without_bootstrap(self):
        plists = self.make_legacy_plists()
        runner = self.launchctl_mock(a.OLD_LABELS)

        def command(args, check=True):
            if args[0] == '/usr/bin/chflags':
                raise subprocess.CalledProcessError(1, args, stderr='Operation not permitted')
            return runner(args, check)

        with patch.object(a, 'run_command', side_effect=command) as mocked:
            with self.assertRaises(subprocess.CalledProcessError):
                a.install(self.paths, None, True)
        self.assertTrue(all(path.exists() for path in plists))
        self.assertFalse(any(c.args[0][1] == 'bootstrap' for c in mocked.call_args_list))

    def test_bootout_disappeared_job_is_ignored(self):
        runner = self.launchctl_mock(a.OLD_LABELS)

        def command(args, check=True):
            result = runner(args, check)
            if args[1] == 'bootout':
                result.returncode = 3
                result.stderr = 'No such process'
            return result

        with patch.object(a, 'run_command', side_effect=command):
            self.assertTrue(a.install(self.paths, None, True)['installed'])

    def test_loaded_inspection_failure_is_not_absence(self):
        with patch.object(a, 'run_command', return_value=subprocess.CompletedProcess([], 1, '', 'Permission denied')):
            with self.assertRaisesRegex(RuntimeError, 'Cannot inspect'):
                a.install(self.paths, None, True)

    def test_restore_uses_backup_read_under_lock(self):
        # A concurrent apply refreshes last-good after the early check but
        # before the locked restore: the restore must use the refreshed copy
        # consistently and verify it.
        a.apply_asset(str(self.paths.plist), 'first', str(self.paths.backup))
        a.apply_asset(str(self.paths.plist), 'second', str(self.paths.backup))
        refreshed = plistlib.dumps(fixture(linked=True))

        def concurrent_apply(label):
            self.paths.backup.write_bytes(refreshed)
            return False

        with patch.object(a, 'loaded', side_effect=concurrent_apply), patch.object(a, 'run_command'):
            a.uninstall(self.paths, True)
        self.assertEqual(self.paths.plist.read_bytes(), refreshed)

    def test_restore_verification_failure_puts_previous_back(self):
        a.apply_asset(str(self.paths.plist), 'new', str(self.paths.backup))
        before = self.paths.plist.read_bytes()
        real_write = a.atomic_write_bytes
        calls = []

        def corrupt_first_write(path, raw):
            calls.append(raw)
            real_write(path, b'corrupt' if len(calls) == 1 else raw)

        with patch.object(a, 'atomic_write_bytes', side_effect=corrupt_first_write):
            with self.assertRaisesRegex(RuntimeError, 'previous settings were put back'):
                a.restore_backup(self.paths.plist, self.paths.backup)
        self.assertEqual(self.paths.plist.read_bytes(), before)

    def test_install_relative_prefix_is_absolute(self):
        cwd = os.getcwd()
        os.chdir(self.temp.name)
        self.addCleanup(os.chdir, cwd)
        with patch.object(a, 'loaded', return_value=False), patch.object(a, 'run_command'):
            result = a.install(self.paths, 'relative-prefix', False)
        program = a.load_plist(self.paths.agent)['ProgramArguments'][0]
        self.assertTrue(Path(program).is_absolute())
        self.assertEqual(program, result['installed'])
        self.assertTrue(Path(program).is_file())

    def test_video_purged_during_check(self):
        real_stat = os.stat
        target = str(self.paths.videos / 'tahoe-morning.mov')

        def purge_then_stat(path, *args, **kwargs):
            if str(path) == target:
                with contextlib.suppress(FileNotFoundError):
                    os.unlink(target)
            return real_stat(path, *args, **kwargs)

        with patch.object(a.os, 'stat', side_effect=purge_then_stat):
            info = a.series_payload(str(self.paths.manifest), 'tahoe', str(self.paths.videos))
        self.assertIn('Tahoe Morning', info['notDownloaded'])

    def test_resume_rejects_part_from_other_version_or_url(self):
        video = self.paths.videos / 'tahoe-day.mov'
        part = self.paths.videos / '.tahoe-day.mov.part'
        meta = self.paths.videos / '.tahoe-day.mov.part.json'
        cases = [
            ({'url': None, 'etag': '"test-etag"'}, [None]),          # URL changed -> restart
            (None, [None]),                                          # no metadata -> restart
            ({'url': None, 'etag': '"old-etag"'}, ['bytes=7-']),     # If-Range mismatch -> 200 full body
        ]
        for stored, expected in cases:
            video.unlink(missing_ok=True)
            with self.cdn() as (body, requests, url), patch.object(a, 'run_command'):
                part.write_bytes(b'OLDVERS')
                if stored is not None:
                    a.write_json(meta, {'url': stored['url'] or (url if stored['etag'] == '"old-etag"' else url + '?old'), 'etag': stored['etag']})
                a.download_video(self.paths, 'tahoe', 'tahoe-day')
                self.assertEqual(requests, expected)
                self.assertEqual(video.read_bytes(), body)
                self.assertFalse(part.exists())
                self.assertFalse(meta.exists())

    def test_pin_failure_after_download_is_reported(self):
        (self.paths.videos / 'tahoe-day.mov').unlink()
        real_link = os.link
        target = str(self.paths.videos / 'tahoe-day.mov')

        def deny(src, dst, *args, **kwargs):
            if str(src) == target:
                raise PermissionError('denied')
            return real_link(src, dst, *args, **kwargs)

        with self.cdn(), patch.object(a, 'current_period', return_value='day'), patch.object(a, 'next_transition', return_value=None), \
                patch.object(a, 'run_command', side_effect=self.launchctl_mock()), patch.object(a.os, 'link', side_effect=deny):
            code, text = self.cli('apply')
        result = json.loads(text)
        self.assertEqual(code, 0)
        self.assertEqual(result['updated'], 2)
        self.assertTrue(any('not protected' in w for w in result['warnings']))

    def test_cache_status_tolerates_purge_after_pin(self):
        info = a.selected_series(self.paths, a.DEFAULTS)
        a.sync_pins(self.paths, info)
        video = self.paths.videos / 'tahoe-day.mov'
        real_stat = a.video_stat

        def purge_video_first(path):
            if Path(path) == video:
                video.unlink(missing_ok=True)
            return real_stat(path)

        with patch.object(a, 'video_stat', side_effect=purge_video_first):
            status = a.cache_status(self.paths, info)
        self.assertEqual(status['videos']['day'], {'assetID': 'tahoe-day', 'present': False, 'pinned': True, 'sameInode': False})
        a.sync_pins(self.paths, info)
        self.assertTrue(os.path.samefile(video, a.pin_path(self.paths, 'tahoe', 'tahoe-day')))


if __name__ == '__main__':
    unittest.main()
