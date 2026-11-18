# Changelog

## Unreleased

- Added human-readable terminal output for every command and a curses dashboard when run without a command; JSON output is unchanged for pipes, launchd and `--json`.
- Dashboard: writes (apply, series, location, periods) run in the background with a spinner, so the clock, keys and resizing keep working during downloads; quitting mid-write needs a second Q and never exits inside a plist/JSON/pin write step. Editors stay visible in small windows, and editing is refused while the editor is hidden.
- Readable `apply` output re-reads videos, pins and the plist after the run; readable `series` uses the same ✓/○/↺/↓ protection symbols as `status`.
- Replaced JXA and the Python helper with the executable standard-library `aerial-switch` CLI.
- Added status, apply/dry-run, series, location, install/migrate, uninstall/restore and doctor commands.
- Moved configuration, state and last-good backup to Application Support/aerial-switch; legacy config is copied on the first write operation.
- Retained safe plist writes and both layouts; matching plist choices now skip writes even when state is stale.
- Added isolated unittest coverage for solar periods, manifest lookup, configuration, rollback and mocked LaunchAgent management.

- Added a `series` config option with Golden Gate support alongside Tahoe (default).
- Asset IDs are looked up by Apple's manifest shotID, and the script refuses to switch to a series whose videos are not downloaded.
- Fixed log times that could print as `6:60`, and periods that wrap past midnight at high latitudes; polar day and night now map to Day and Night.
- Manifest lookup moved into the helper with proper shell quoting and a fixed `/usr/bin/python3` path.

## 2026-11-16

- Added support for macOS Tahoe's Linked wallpaper choices under AllSpacesAndDisplays and SystemDefault.
- Kept support for the earlier Desktop and Idle choices.
- Kept the helper's file lock, last-good backup, atomic plist write, and post-write verification so format changes fail visibly and a failed verification can restore the previous plist.
