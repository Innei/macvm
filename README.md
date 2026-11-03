# macvm

Run a Mac app's XCUITest suite inside a headless macOS VM (Tart), without touching the host's screen, pointer or keyboard. One base VM is shared by every project; each project gets its own warm runner cloned from it.

## Install

```sh
uv tool install --editable ~/git/innei-repo/macvm
```

Needs Apple silicon, Python 3.11+, [Tart](https://github.com/cirruslabs/tart) on `PATH`, and an APFS sparse bundle for the VM disks:

```sh
hdiutil create -size 200g -type SPARSEBUNDLE -fs APFS -volname MacVM /Volumes/WD/macvm.sparsebundle
```

## Global settings

`~/.config/macvm/config.toml` (or `$MACVM_CONFIG`). Every key is optional:

```toml
bundle = "/Volumes/WD/macvm.sparsebundle"   # sparse bundle holding VMs, IPSW, archives
volume = "/Volumes/MacVM"                     # where it mounts
base = "macos-27.2"
xcode = "/Applications/Xcode.app"             # copied into the base; host builds use it too
cpu = 6
memory = 16                                   # GB
display = "1680x1050"
idle_minutes = 30
vnc_port = 5905
```

## Add a project

Put `macvm.toml` at the project root:

```toml
name = "notes"                                # runner: notes-runner

[build]
command = ["xcodebuild", "-project", "Notes.xcodeproj", "-scheme", "Notes", "-configuration", "Debug",
           "-destination", "platform=macOS"]
xctestrun = "Notes_*.xctestrun"               # matched under BUILD_DIR, newest wins
products = ["Debug/Notes.app", "Debug/NotesUITests-Runner.app"]

[test]
always = ["NotesTests"]                       # run every time
suite = "NotesUITests"                        # run whole when no --case is given
[test.cases]
editor = "EditorTests"

[reset]                                       # before every run, inside the runner
processes = ["Notes"]
defaults = ["com.example.notes"]
paths = ["~/Library/Containers/com.example.notes"]

[grants]
targets = ["com.example.notes"]               # automation clients may send it Apple events
bypass = false                                # true: temporarily bypass TCC for every guest process
[grants.apps]
"com.example.notes" = []                      # e.g. ["camera", "notifications"]; [] starts undetermined
```

Optional: `build.prepare` with `build.prepare_when_missing` (a command to run when a path is missing), `[[extra]]` suites (`name`, `command` with `{out}` and `{cases}`, `files`, `node`, `help`; each becomes a `--<name>` flag), `[output] dir`.

## Commands

| Command | What it does |
| --- | --- |
| `macvm run [--case NAME] [--<extra>] [--hold] [--fresh] [--no-update]` | Build for testing on the host, run in the project's runner, export results |
| `macvm prepare [--recovery-vnc] [--repair-xcode] [--upgrade-tools] [--force]` | Build or update the shared base |
| `macvm status [--follow]` | Base freshness, runners, lock, current run, history |
| `macvm watch [--print-only]` | Open Screen Sharing on the project's runner (View → Observe) |
| `macvm up` | Start the runner and keep it up (idle stop still applies) |
| `macvm exec -- CMD…` | Run a command in the runner over SSH, Homebrew on `PATH` |
| `macvm screenshot [PATH]` | Full-screen PNG of the runner over Screen Sharing |
| `macvm reset` | Delete the project's runner; the next run reclones |

`up`, `exec` and `screenshot` take no lock; they refresh the runner's activity so it is not stopped for another project. Use them when XCUITest cannot reach something (system dialogs, menu bar extras, cross-app drags):

```sh
macvm exec -- cliclick c:400,300
macvm exec -- peekaboo see --app Notes --path /tmp/notes.png
```

## The base

`prepare` runs named steps and records each step's fingerprint in `runs/base.json`: `system`, `xcode` (host Xcode build), `runtime` (Xcode build + iOS SDK), `screen-sharing`, `services`, `tools` (Homebrew, cliclick, peekaboo), `tcc-bypass`, `grants`, `desktop` (Finder, Xcode, DeviceHub, Terminal in the Dock; solid wallpaper), `first-launch`. Only changed steps rerun, plus `runtime`, `tcc-bypass` and `first-launch` after a new Xcode. The work happens on `<base>-next`, which replaces the base only when every step and the settle login succeed; a failed update leaves the base alone and resumes from the failed step next time. `run` updates an out-of-date base first unless `--no-update`.

macOS runs at most two VMs at once. Starting a runner (or a prepare) stops the runner idle the longest whose lock is free, or waits.

## Permissions

SIP is off in the base, so root writes both TCC databases directly (macOS 27 keeps the per-user one under `/private/var/containers/Data/ProtectedSystem/…`). SSH, Terminal, osascript, the XCTest agent, cliclick and peekaboo get Accessibility, event posting and listening, Screen Recording, Full Disk Access, Developer Tools and Apple events to System Events and Finder. The app under test starts undetermined every run (`tccutil reset All`); only services listed in `[grants.apps]` are granted.

For unrestricted automation, set `[grants] bypass = true` in `macvm.toml`, or pass `--tcc-bypass` to `run`, `up`, `exec` or `screenshot`. `prepare` downloads a pinned, SHA-256-verified version of [unixzii/tcc-bypass](https://github.com/unixzii/tcc-bypass) and builds it inside the guest. It requires Apple silicon, SIP off and compatible private TCC methods (upstream verifies macOS 27.2).

The bypass covers every process in that VM, including the app under test. It is enabled after permission resets, renewed before each suite and idle manual operation, and expires after at least 60 minutes (longer for a suite's timeout, up to 24 hours). `--no-tcc-bypass` overrides project configuration and disables an existing bypass so permission prompts and denial paths can be tested. Notifications still use the separate grants configuration. A busy runner keeps its current TCC mode during manual operations and rejects explicit TCC overrides until the test run finishes.

```sh
macvm prepare
macvm run --tcc-bypass
macvm run --no-tcc-bypass                    # test the actual permission flow
macvm exec --tcc-bypass -- sudo /usr/local/bin/macvm-tcc-bypass status
macvm exec --tcc-bypass -- sudo /usr/local/bin/macvm-tcc-bypass logs --snapshot
```

Only guest `tccd` processes are modified. The bypass does not persist grants to the TCC databases; stopping it or reaching its expiry restores normal checks, while its inactive hooks remain until `tccd` exits. VM shutdown removes the in-memory hooks and helper.

## Turning SIP off on a fresh base

`macvm prepare --recovery-vnc` boots recovery and scripts `csrutil disable`. On a freshly installed base the scripted recovery can stall at the Language chooser: connect to the recovery VNC it prints, then Options → Language → Utilities › Terminal → `csrutil disable` (password `admin`) → `halt`, and rerun prepare.
