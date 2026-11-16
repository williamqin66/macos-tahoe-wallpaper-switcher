# macOS Tahoe Wallpaper Switcher

Automatically switches Apple's built-in macOS Tahoe dynamic wallpapers using approximate local sunrise and sunset times.

根据所在地的日出、日落时间，在 macOS 自带的“太浩湖”动态壁纸之间自动切换。脚本不包含或下载壁纸资源；请先在“系统设置 → 墙纸”中下载 Tahoe Morning、Tahoe Day、Tahoe Evening 和 Tahoe Night 四个选项。

## Requirements | 使用条件

- macOS Tahoe with the four built-in Tahoe wallpaper assets downloaded
- JavaScript for Automation via /usr/bin/osascript
- Apple's wallpaper manifest and wallpaper store available in the current user account
- Python 3 with the standard library

需要 macOS Tahoe，并先在系统设置里下载四种 Tahoe 壁纸；脚本依赖系统自带的 osascript、Python 3 和 Apple 壁纸清单。

## What it does | 工作方式

- Calculates approximate sunrise and sunset times from the configured latitude and longitude and the Mac's current time zone.
- Selects Morning, Day, Evening, or Night and reads each corresponding asset ID from Apple's wallpaper manifest.
- Updates the active wallpaper choices only when needed.
- Supports both the older Desktop/Idle plist layout and Tahoe's Linked choices under AllSpacesAndDisplays and SystemDefault.
- Backs up the current wallpaper plist before an update, writes atomically, verifies the result, and restores the backup if verification fails.
- Leaves the macOS Appearance setting unchanged.

脚本根据配置的经纬度和 Mac 当前时区估算日出、日落，选出 Morning、Day、Evening 或 Night。默认日程为：日出后 2.5 小时内使用 Morning；日落前 1.5 小时起切换到 Evening；日落后 0.5 小时切换到 Night。其余时段使用 Day。太阳时刻是近似计算值。

## Installation | 安装

1. In System Settings → Wallpaper, download all four Tahoe wallpaper choices.
2. From the project directory, install the scripts and create a config file:

       mkdir -p "$HOME/Library/Scripts" "$HOME/Library/Application Support/Tahoe Wallpaper Switcher"
       cp wallpaper-switch.js "$HOME/Library/Scripts/"
       cp wallpaper-helper.py "$HOME/Library/Application Support/Tahoe Wallpaper Switcher/"
       cp -n wallpaper-switch-config.example.json "$HOME/Library/Scripts/wallpaper-switch-config.json"
       chmod 755 "$HOME/Library/Scripts/wallpaper-switch.js"

   The config copy command keeps an existing config. Edit wallpaper-switch-config.json and set your own city, latitude, and longitude. The sample coordinates are only an example. 经度、纬度请填写你自己的位置；配置文件会留在本机，不应公开上传。

3. Install the optional LaunchAgent. It runs at login and every 15 minutes:

       mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
       sed "s|__HOME__|$HOME|g" launchd/com.example.tahoe-wallpaper-switcher.plist > "$HOME/Library/LaunchAgents/com.example.tahoe-wallpaper-switcher.plist"
       plutil -lint "$HOME/Library/LaunchAgents/com.example.tahoe-wallpaper-switcher.plist"
       launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.example.tahoe-wallpaper-switcher.plist"

4. To run once manually:

       /usr/bin/osascript -l JavaScript "$HOME/Library/Scripts/wallpaper-switch.js"

The LaunchAgent writes output and errors under ~/Library/Logs. If no wallpaper changes, first confirm the four assets have finished downloading and that the config coordinates are correct.

To stop and remove the optional LaunchAgent:

       launchctl bootout "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.example.tahoe-wallpaper-switcher.plist"
       rm "$HOME/Library/LaunchAgents/com.example.tahoe-wallpaper-switcher.plist"

## Configuration | 配置

See wallpaper-switch-config.example.json. Latitude and longitude use decimal degrees. The system time zone is used for the solar-time calculation.

## Safety and limitations | 安全与限制

The helper edits Apple's private wallpaper Index.plist and restarts WallpaperAgent after a change. Apple can change this undocumented data format in a macOS update. Keep a separate backup of important system settings and review changes before using this project. The helper stores its most recent pre-change plist backup at ~/Library/Application Support/Tahoe Wallpaper Switcher/Index.plist.last-good.

This project does not include Apple's wallpaper videos and is not affiliated with Apple Inc.

---

## English

This project switches the built-in Tahoe wallpapers according to approximate local solar time. It requires macOS Tahoe, the four built-in Tahoe wallpaper assets, /usr/bin/osascript, and Python 3. The scripts use only macOS and Python standard-library components.

### Install

1. Download Tahoe Morning, Tahoe Day, Tahoe Evening, and Tahoe Night in System Settings → Wallpaper.
2. In the project directory, run:

       mkdir -p "$HOME/Library/Scripts" "$HOME/Library/Application Support/Tahoe Wallpaper Switcher"
       cp wallpaper-switch.js "$HOME/Library/Scripts/"
       cp wallpaper-helper.py "$HOME/Library/Application Support/Tahoe Wallpaper Switcher/"
       cp -n wallpaper-switch-config.example.json "$HOME/Library/Scripts/wallpaper-switch-config.json"
       chmod 755 "$HOME/Library/Scripts/wallpaper-switch.js"

3. Edit ~/Library/Scripts/wallpaper-switch-config.json with your city and decimal-degree latitude and longitude. Keep this personal config out of public repositories.
4. To run automatically at login and every 15 minutes, install the optional LaunchAgent:

       mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
       sed "s|__HOME__|$HOME|g" launchd/com.example.tahoe-wallpaper-switcher.plist > "$HOME/Library/LaunchAgents/com.example.tahoe-wallpaper-switcher.plist"
       plutil -lint "$HOME/Library/LaunchAgents/com.example.tahoe-wallpaper-switcher.plist"
       launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.example.tahoe-wallpaper-switcher.plist"

5. To run a single update manually:

       /usr/bin/osascript -l JavaScript "$HOME/Library/Scripts/wallpaper-switch.js"

### How it works

The script calculates approximate sunrise and sunset times using the configured coordinates and the Mac's current time zone. Morning lasts from sunrise until 2.5 hours afterward. Evening starts 1.5 hours before sunset and ends 0.5 hours after sunset. Day and Night cover the remaining times. Transition values are configurable.

The helper supports both the legacy Desktop/Idle choices and Tahoe's Linked choices under AllSpacesAndDisplays and SystemDefault. It backs up the current wallpaper plist, writes atomically, verifies the new asset IDs, and restores the prior plist if verification fails. The macOS Appearance setting is left unchanged.

### Limitations

The helper edits Apple's private wallpaper Index.plist and restarts WallpaperAgent after a change. Apple may change this undocumented format in a future macOS release. The project does not include or download Apple's wallpaper videos and is not affiliated with Apple Inc.
