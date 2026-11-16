# Changelog

## 2026-11-16

- Added support for macOS Tahoe's Linked wallpaper choices under AllSpacesAndDisplays and SystemDefault.
- Kept support for the earlier Desktop and Idle choices.
- Kept the helper's file lock, last-good backup, atomic plist write, and post-write verification so format changes fail visibly and a failed verification can restore the previous plist.
