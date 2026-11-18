<p align="center">
  <img src="assets/icon.svg" width="160" alt="aerial-switch icon">
</p>

<h1 align="center">aerial-switch</h1>

按所在地的近似日出、日落时间，自动切换 macOS Tahoe 自带的 Tahoe 或 Golden Gate 动态壁纸。单文件 Python CLI，仅使用标准库，需要 `/usr/bin/python3`。不改变系统浅色／深色外观。

## 安装与使用

在“系统设置 → 墙纸”选择过所需系列后，在项目目录运行：

```sh
./aerial-switch location 31.23 121.47 --city 上海
./aerial-switch series tahoe
./aerial-switch doctor
./aerial-switch apply --dry-run
./aerial-switch install
```

安装会复制程序到 `~/.local/bin/aerial-switch`，生成 `com.local.aerial-switch` LaunchAgent，并立即加载。它会在登录时和每 900 秒执行 `apply`；RunAtLoad 会立即尝试切换壁纸。日志：`~/Library/Logs/aerial-switch.log`。可用 `install --prefix /your/prefix` 将程序安装到 `/your/prefix/bin/aerial-switch`。以下命令可用安装后的完整路径运行，或把 `~/.local/bin` 加入 PATH。

```sh
./aerial-switch status                 # 当前时段、日出日落、下次切换、plist 匹配与任务状态
./aerial-switch series                 # 两个系列的 ID、缺失及未下载视频
./aerial-switch series goldenGate      # 保存系列并同步保护，不下载、不切换
./aerial-switch apply --strict         # 兼容选项；始终在当前视频就绪后才切换
./aerial-switch location 31.23 121.47 --city 上海
./aerial-switch apply --dry-run        # 只读预览
./aerial-switch apply                  # 按当前时段切换，已有匹配结果则不写 plist
./aerial-switch uninstall              # 停止并删除新 LaunchAgent，同时删除 pinned
./aerial-switch uninstall --restore    # 同时恢复最近一次切换前的 plist
```

### 可读输出与交互界面

在终端里直接运行时，各子命令输出整理好的中文仪表盘：标题行、按 24 小时比例绘制的时段条（▲ 标出当前时间）、当前与下一张壁纸、四个视频的状态（✓ 已保护、○ 未保护、↺ 可从保护恢复、⋯ 下载中、↓ 缺失，应用时下载）与定时任务状态；有旧版任务残留、下载失败或未受保护的视频时，在底部“注意”中列出。对齐按显示宽度计算，窄终端会自动换行并缩短时段条。

输出到管道、文件或 launchd 日志时，以及加 `--json`（如 `aerial-switch status --json`）时，仍输出与以前完全相同的 JSON。设置 `NO_COLOR` 关闭颜色；终端编码不是 UTF-8 时改用 ASCII 字符（无法显示中文时改用英文）。

不带参数运行 `aerial-switch`（且处在终端中）会打开交互界面，否则打印帮助：

| 按键 | 作用 |
|---|---|
| ← → / Tab | 在 Golden Gate 与 Tahoe 之间选择 |
| Enter | 切换到所选系列（下次检查时生效，或按 A 立即应用） |
| A | 立即应用当前时段壁纸（先确认，Y/N） |
| L | 修改纬度、经度、城市（Tab 换项，Enter 保存，Esc 取消） |
| T | 调整早晨时长、傍晚提前与延后（0–6 小时，←→ 每次 0.5，Enter 保存） |
| D | 自检结果；R 重新自检 |
| R | 立即重新读取状态 |
| ? | 帮助 |
| Q / Esc | 退出 |

界面每秒刷新时间与倒计时，每 30 秒重新读取状态，支持调整窗口大小；窗口小于 44×16 时显示提示。编辑位置或时段时，窗口较小会先收起其它信息以保证编辑框可见；编辑框无法显示时不接受输入和保存（Esc 取消）。应用、切换系列、保存等操作在后台进行并显示进度，期间可以浏览，但不能再发起修改；此时按 Q 需再按一次才强制退出：未完成的下载保留 `.part` 下次续传，正在写壁纸设置、配置或 pin 的那一小步会先完成再退出。卸载等操作不在界面中提供。

不带名称的 `series` 用与 `status` 相同的符号显示两个系列的视频：✓ 已保护、○ 已下载但未保护、↺ 可从保护恢复、↓ 未下载。未选中的系列不受保护属正常情况，系统可能清理。

`status`、不带名称的 `series`、`doctor`、`apply --dry-run` 均不创建配置或状态。`doctor` 检查 Python、配置、manifest、plist 目标、视频和 LaunchAgent；配置、manifest ID 或 plist 等检查失败返回非零退出码；缺视频仅为 warning。未加载 LaunchAgent 会如实显示，不视为安装前的故障。下次切换时间按分钟计算，自动执行可能比该时间晚最多约 15 分钟。极昼极夜时显示相应状态，日出日落为 null。

macOS 将 aerials/videos 登记为 CacheDelete 可清除缓存（com.apple.wallpaper.aerials.delete），可能清除非当前使用的 .mov，且不一定及时补下载。程序为当前系列在 `~/Library/Application Support/aerial-switch/pinned/<series>/` 建立硬链接：同一 inode 不增加视频副本空间，但保留的视频仍占磁盘空间。仅在 pinned 与 videos 同设备时启用；跨设备跳过并警告，不复制。系统视频被删除后从 pin 恢复；系统换成新 inode 时原子更新 pin。切换系列会删除旧系列保护，允许系统回收。`series NAME` 保存后立即同步保护，不下载、不切换。

`apply` 的执行顺序：

1. 同步当前系列的四个 pin、恢复已清除的系统路径，并撤销其它系列保护。
2. 检查当前时段视频。缺失时先获取独立的非阻塞 `download.lock`；已被占用则保留当前壁纸并以 0 退出。
3. 从 manifest 优先选择 `url-4K-SDR-240FPS`，否则按字段排序选首个 `url-` 且以 `.mov` 结尾的地址。用 urllib 默认代理处理（包括 macOS 系统代理），连接／读取超时 60 秒，下载至 videos 内 `.<assetID>.mov.part`，使用 Range 续传；服务端忽略 Range 时从头覆盖。验证 Content-Length（续传同时验证 Content-Range），长度不符删除 part。其它中断可保留 part 供下轮续传；416 会清除失效 part，下一轮重下。
4. 完整文件写入 SourceURL、LastETag 扩展属性，设置 0600，原子替换正式视频并建立 pin，之后才更新壁纸 plist。下载或属性写入失败不切换，返回 0 并在 JSON 的 downloadError、warnings 中报告，等待下轮重试。
5. 处理完当前时段后，若 nextSwitch 在 45 分钟内，预取下一时段缺失的视频（优先从 pin 恢复）。预取失败只警告，不撤销当前切换。

`apply --strict` 保留为兼容无操作；默认始终要求当前视频就绪。`apply --dry-run` 不写文件、不请求网络，输出 pinActions、plannedActions，包括 pin／restore／replacePin／download／switch／prefetch；ready 表示当前视频已存在或计划可从 pin 恢复，下载仍是切换前提。`series --force` 也是兼容无操作。

`status` 及 `doctor` 的 cache 检查显示每个时段的 present、pinned、sameInode，全部 pin 按唯一 inode 累计的 pinnedBytes，downloadInProgress 和代理有无及 scheme（不显示地址或凭据）。普通卸载默认删除整个 pinned 目录以撤销保护，系统 videos 仍由系统管理。

硬链接已能在普通文件 unlink 后保留视频；**CacheDelete 若使用 APFS 层 purge 是否仍保留数据，尚未在真机验证**。运行后可比较 `status` 中四个时段的 present／pinned／sameInode；系统清理后关注下一轮日志的 restore 动作、downloadError 和 warnings。日志在 `~/Library/Logs/aerial-switch.log`，可同时用系统“控制台”筛选 `com.apple.wallpaper.aerials.delete`、CacheDelete、WallpaperAerialsExtension，按时间比对清理与恢复。若 pin 也消失或读出失败，应保留日志与 status 证据；硬链接不能被视为 APFS purge 的保证。

## 从旧版本迁移

检测到以下任一旧 label 已加载或对应 plist 仍在 LaunchAgents 时，普通安装会列出旧项并停止：com.user.wallpaper-switch-guard、com.user.wallpaper-switch、com.example.tahoe-wallpaper-switcher。status 和 doctor 也会给出迁移警告。检查位置和系列后执行：

```sh
./aerial-switch install --migrate
~/.local/bin/aerial-switch status
```

`--migrate` 先 bootout guard，再停止主任务与 com.example 任务；仅明确表示未加载的失败可以忽略，其它停止或检查失败会中止安装。随后用 /usr/bin/chflags nouchg 解除旧 plist 的不可变标志，将它们移动到 ~/Library/Application Support/aerial-switch/legacy-backup/<时间戳>/。恢复目录、旧脚本、旧配置和旧状态保持原样。再次确认全部旧 label 未加载后，才 bootstrap 新任务。返回 JSON 的 migration 列出旧 label、移动文件和备份目录。迁移中途失败时，已停止的任务不会自动恢复；检查错误与备份目录后再重试。自定义旧 label 仍需手动停用。

uninstall 不恢复旧版。如需回退，先卸载新 LaunchAgent，再将该次 legacy-backup 中的 plist 移回 ~/Library/LaunchAgents/，用 launchctl bootstrap gui/$(id -u) <plist完整路径> 加载旧主任务，最后加载 guard（如有）。旧配置和恢复副本仍可使用；无需恢复 uchg 即可运行。

配置、状态和备份位于 `~/Library/Application Support/aerial-switch/`，文件分别为 `config.json`、`state.json`、`Index.plist.last-good`。新配置不存在时读取 `~/Library/Scripts/wallpaper-switch-config.json`；首次执行写操作时迁移，旧文件不删除。无配置时使用 Apple Park 默认坐标，请先设置自己的位置。配置字段见 [示例](wallpaper-switch-config.example.json)，纬度范围 -90～90，经度 -180～180，时段参数均为 0～6 小时。

| 系列 | morning | day | evening | night |
|---|---|---|---|---|
| tahoe | Tahoe Morning | Tahoe Day | Tahoe Evening | Tahoe Night |
| goldenGate | Golden Gate Sunset | Golden Gate Day | Golden Gate Evening | Golden Gate Night |

默认 morning 从日出持续 2.5 小时；evening 从日落前 1.5 小时开始，但不会早于 morning 结束，持续到日落后 0.5 小时。其余分别为 day 或 night。按 Mac 的本地时区估算，支持跨午夜、极昼和极夜。

## 安全与验证

程序修改 Apple 私有的 `com.apple.wallpaper/Store/Index.plist`，兼容旧 Desktop/Idle 和 Tahoe Linked 布局。写入时持有文件锁、备份切换前文件、原子替换、回读验证；验证失败恢复 last-good。即使 state 显示已应用，仍检查实际 plist，可修复手动更换壁纸后的偏差。只有 plist 确实修改后才重启 WallpaperAgent。

Apple 可在系统更新中改变私有格式。请保留重要设置的独立备份；last-good 是最近一次切换前的设置，并非永久保存的首次安装快照。`uninstall --restore` 先检查备份存在且可解析，再恢复；普通卸载保留程序、配置、状态和备份。无数据库迁移。项目不包含 Apple 视频，运行时按需从 Apple manifest 地址下载，与 Apple Inc. 无关联。

```sh
python3 -m unittest discover tests
```

测试只用临时目录、模拟进程和本地线程 HTTP 服务，不访问外网。`AERIAL_SWITCH_HOME` 可替换整个用户主目录用于隔离测试，不要在正常安装时设置。

## English

`aerial-switch` is a single-file Python standard-library CLI for macOS Tahoe. It selects Tahoe or Golden Gate aerial wallpapers using approximate solar time in the Mac's local time zone. It requires `/usr/bin/python3` and manifest IDs for all four periods of the selected series.

Select the desired series in System Settings → Wallpaper, then run:

```sh
./aerial-switch location 37.7749 -122.4194 --city 'San Francisco'
./aerial-switch series tahoe
./aerial-switch doctor
./aerial-switch apply --dry-run
./aerial-switch install
```

Install copies the executable to `~/.local/bin/aerial-switch` and loads `com.local.aerial-switch`, running immediately, at login and every 900 seconds. `--prefix PATH` uses `PATH/bin/aerial-switch`. Output and errors go to `~/Library/Logs/aerial-switch.log`.

On a terminal every command prints a readable dashboard (24-hour period bar with a now marker, current and next wallpaper, per-video state, schedule and warnings); pipes, files, launchd logs and `--json` keep the exact previous JSON. `NO_COLOR` disables colour and non-UTF-8 terminals fall back to ASCII (English when Chinese cannot be encoded). Running `aerial-switch` with no command on a terminal opens a curses dashboard: arrows/Tab choose the series, Enter selects it, A applies (after confirmation), L edits location, T adjusts period lengths in 0.5-hour steps, D runs doctor, R reloads, ? shows help, Q/Esc quits.

Use `status` for solar times, the next transition, actual plist matching and agent state. `series` lists both sets and unavailable videos; `series goldenGate` validates manifest IDs and saves the selection even when videos are absent. `--force` remains a compatibility no-op. `location LAT LON --city NAME` saves coordinates. `apply --dry-run` previews; `apply` switches only when needed and heals mismatched wallpaper choices. Read-only commands do not create files. The next transition is calculated to the minute; scheduled application can lag by approximately 15 minutes.

macOS can purge unused aerial videos without promptly downloading them again. Each apply synchronizes hard links for the selected series under Application Support/aerial-switch/pinned, restoring missing system paths and atomically updating pins when system files change. Pins on another device are skipped with a warning; no copies are made. Selecting a series removes other series' pins and protects existing videos without downloading or switching.

A missing current video must finish downloading before switching. The CLI uses a nonblocking download.lock, urllib's default macOS proxy detection, a 60-second connection/read timeout, a .mov.part file with Range resume, Content-Length/Content-Range validation, SourceURL/LastETag xattrs, mode 0600, and atomic replacement. Download failure or lock contention keeps the current wallpaper and returns 0 with a JSON warning for the next run. Length mismatches delete the part; HTTP 416 discards it for the next retry. After the current period, a missing next video is prefetched when the transition is within 45 minutes; prefetch failure only warns. --strict is a compatibility no-op. Dry-run never writes or requests the network and lists planned pin, restore, download, switch and prefetch actions.

status and doctor's cache check report present/pinned/sameInode per period, pinnedBytes counted by unique inode, downloadInProgress, and proxy presence/schemes without endpoints or credentials. Hard links survive ordinary unlink; retention under APFS-level CacheDelete purge is unverified. Compare status with restore actions and download warnings in ~/Library/Logs/aerial-switch.log and Console's CacheDelete/WallpaperAerialsExtension events after cleanup.

Install detects loaded jobs OR existing plists for com.user.wallpaper-switch-guard, com.user.wallpaper-switch and com.example.tahoe-wallpaper-switcher. Without --migrate it lists them and stops. status and doctor warn about them. install --migrate stops the guard first, then both main legacy labels. Only an explicit service-not-found response is ignored; other bootout or inspection failures abort. It clears each old plist's immutable flag using /usr/bin/chflags nouchg and moves it into ~/Library/Application Support/aerial-switch/legacy-backup/<timestamp>/. Recovery directories, scripts, config and state are untouched. It checks again that every legacy label is unloaded before bootstrapping the new job. The migration JSON lists labels, moved files and the backup directory. A partial failure does not automatically restart stopped jobs; inspect the error and backups before retrying. Custom labels require manual handling.

Uninstall does not restore the legacy installation. To return to it, uninstall the new agent, move backed-up plists into ~/Library/LaunchAgents/, and use launchctl bootstrap gui/$(id -u) <full-plist-path> for the old main job, then the guard if present. The old config and recovery files remain available; restoring uchg is optional.

Config, state and last-good backup live in `~/Library/Application Support/aerial-switch/`. If the new config is absent, the old `~/Library/Scripts/wallpaper-switch-config.json` is read and copied on the first write operation; read-only commands never migrate files. Defaults use Apple Park coordinates. See the example config for fields. Morning defaults to 2.5 hours after sunrise; evening starts 1.5 hours before sunset, never before morning ends, and ends 0.5 hours after sunset. Polar day maps to day; polar night maps to night.

The CLI edits Apple's undocumented Index.plist, supporting Desktop/Idle and Tahoe Linked layouts. It keeps a file lock, pre-change last-good backup, atomic replacement, read-back verification and rollback on verification failure. It restarts WallpaperAgent after a change and leaves macOS Appearance unchanged. Apple may change the format. Keep independent backups: last-good is the most recent pre-change snapshot, not an installation snapshot.

`uninstall` removes the new LaunchAgent and the entire pinned directory, retaining the executable, configuration, state and backup. `uninstall --restore` additionally checks and restores last-good. Tests use temporary directories and mocked processes (`python3 -m unittest discover tests`). `AERIAL_SWITCH_HOME` overrides the entire home for isolated tests. Apple videos are not included; missing videos are downloaded at runtime from manifest URLs; this project is not affiliated with Apple Inc.
