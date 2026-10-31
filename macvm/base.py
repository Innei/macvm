from dataclasses import dataclass
import hashlib
import json
import os
import re
import subprocess
import time

from .guest import (BASE, BASE_LOCK, SLOTS_LOCK, GUEST_PASSWORD, GUEST_USER, GUEST_XCODE, HISTORY, IPSW, IPSW_URL, KEY, SHARED, SHARED_XCODE,
                      STATUS, TART_HOME, VOLUME, XCODE, Guest, exclusive, mount, progress, tart, vms)
from .fleet import free_slot
from . import grants, steps
from .rfb import RFB
from . import status as vm_status

ARCHIVES = VOLUME / 'xcode'
RUNTIMES = VOLUME / 'runtimes'
INTEGRITY = [
    'Contents/Developer/usr/bin/xcodebuild',
    'Contents/Developer/Platforms/MacOSX.platform/Developer/Library/Xcode/Agents/xctest',
    'Contents/Developer/Platforms/MacOSX.platform/Developer/Library/Frameworks/XCTest.framework',
    'Contents/Developer/Platforms/MacOSX.platform/Developer/Library/Frameworks/XCUIAutomation.framework',
    'Contents/Developer/Platforms/MacOSX.platform/Developer/Library/PrivateFrameworks/XCTestCore.framework',
    'Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/bin/swift-frontend',
    'Contents/SharedFrameworks/DVTFoundation.framework',
    'Contents/Frameworks/IDEFoundation.framework',
]
SERVICES = [
    ('system', 'com.apple.softwareupdated', 'Software Update checks and downloads'),
    ('system', 'com.apple.mobile.softwareupdated', 'Software Update checks and downloads'),
    ('gui', 'com.apple.SoftwareUpdateNotificationManager', 'Software Update banners'),
    ('system', 'com.apple.backupd', 'Time Machine'),
    ('system', 'com.apple.backupd-helper', 'Time Machine scheduling'),
    ('gui', 'com.apple.corespotlightd', 'Spotlight app indexing; mdutil already stops volume indexing'),
    ('gui', 'com.apple.spotlightknowledged', 'Spotlight knowledge indexing'),
    ('gui', 'com.apple.spotlightknowledged.importer', 'Spotlight knowledge indexing'),
    ('gui', 'com.apple.spotlightknowledged.updater', 'Spotlight knowledge indexing'),
    ('gui', 'com.apple.assistantd', 'Siri'),
    ('gui', 'com.apple.assistant_service', 'Siri'),
    ('gui', 'com.apple.assistant_cdmd', 'Siri'),
    ('gui', 'com.apple.siriactionsd', 'Siri actions'),
    ('gui', 'com.apple.siriinferenced', 'Siri inference'),
    ('gui', 'com.apple.siriknowledged', 'Siri knowledge'),
    ('gui', 'com.apple.sirittsd', 'Siri speech'),
    ('gui', 'com.apple.SiriTTSTrainingAgent', 'Siri speech training'),
    ('gui', 'com.apple.intelligenceplatformd', 'Apple Intelligence'),
    ('gui', 'com.apple.intelligenceflowd', 'Apple Intelligence'),
    ('gui', 'com.apple.intelligencecontextd', 'Apple Intelligence'),
    ('gui', 'com.apple.intelligencetasksd', 'Apple Intelligence'),
    ('gui', 'com.apple.generativeexperiencesd', 'Apple Intelligence'),
    ('gui', 'com.apple.knowledgeconstructiond', 'Apple Intelligence knowledge graph'),
    ('gui', 'com.apple.ModelCatalogAgent', 'Apple Intelligence model catalog'),
    ('system', 'com.apple.modelcatalogd', 'Apple Intelligence model catalog'),
    ('system', 'com.apple.modelmanagerd', 'Apple Intelligence models'),
    ('gui', 'com.apple.mediaanalysisd', 'photo and video analysis'),
    ('gui', 'com.apple.photoanalysisd', 'photo analysis'),
    ('gui', 'com.apple.suggestd', 'Siri Suggestions'),
    ('gui', 'com.apple.tipsd', 'Tips'),
    ('gui', 'com.apple.parsecd', 'Spotlight web suggestions'),
    ('system', 'com.apple.analyticsd', 'analytics submission'),
    ('system', 'com.apple.SubmitDiagInfo', 'diagnostics submission'),
    ('gui', 'com.apple.analyticsagent', 'analytics'),
    ('gui', 'com.apple.cloudd', 'iCloud sync'),
    ('gui', 'com.apple.bird', 'iCloud Drive'),
    ('gui', 'com.apple.cloudphotod', 'iCloud Photos'),
    ('system', 'com.apple.XProtect.daemon.scan', 'background XProtect scans; launch assessment stays on'),
    ('system', 'com.apple.XProtect.daemon.scan.startup', 'background XProtect scans at boot'),
    ('gui', 'com.apple.XProtect.agent.scan', 'background XProtect scans'),
    ('gui', 'com.apple.XProtect.agent.scan.startup', 'background XProtect scans at login'),
    ('gui', 'com.apple.notificationcenterui.agent', 'notification banners over the app under test'),
    ('gui', 'com.apple.gamed', 'Game Center'),
    ('gui', 'com.apple.ScreenTimeAgent', 'Screen Time'),
    ('gui', 'com.apple.UsageTrackingAgent', 'Screen Time usage tracking'),
    ('system', 'com.apple.locationd', 'Location Services'),
]
SCREEN_SHARING = '\n'.join([
    'sudo launchctl enable system/com.apple.screensharing',
    'sudo launchctl bootstrap system /System/Library/LaunchDaemons/com.apple.screensharing.plist 2>/dev/null || true',
    *[f"sudo sqlite3 '/Library/Application Support/com.apple.TCC/TCC.db' \"insert or replace into access (service, "
      f"client, client_type, auth_value, auth_reason, auth_version, indirect_object_identifier, flags) values "
      f"('{service}', '{client}', 0, 2, 4, 1, 'UNUSED', 0)\""
      for client in ('com.apple.screensharing.agent', 'com.apple.screensharing.daemon')
      for service in ('kTCCServiceScreenCapture', 'kTCCServicePostEvent', 'kTCCServiceListenEvent')],
])
ACCOUNT = f'''
set -e
echo {GUEST_PASSWORD} | sudo -S sh -c "echo '{GUEST_USER} ALL=(ALL) NOPASSWD: ALL' > /etc/sudoers.d/{GUEST_USER}-nopasswd; chmod 440 /etc/sudoers.d/{GUEST_USER}-nopasswd"
mkdir -p ~/.ssh && chmod 700 ~/.ssh
grep -qf '{SHARED}/key/id_ed25519.pub' ~/.ssh/authorized_keys 2>/dev/null || cat '{SHARED}/key/id_ed25519.pub' >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
'''
SYSTEM = f'''
sudo systemsetup -setsleep Off >/dev/null 2>&1 || true
sudo pmset -a sleep 0 displaysleep 0 disksleep 0
sudo defaults write /Library/Preferences/com.apple.screensaver loginWindowIdleTime 0
defaults -currentHost write com.apple.screensaver idleTime 0
sysadminctl -screenLock off -password {GUEST_PASSWORD} 2>/dev/null || true
sudo mdutil -a -i off >/dev/null || true
sudo softwareupdate --schedule off >/dev/null 2>&1 || true
sudo DevToolsSecurity -enable
sudo automationmodetool enable-automationmode-without-authentication
defaults write com.apple.WindowManager StandardHideWidgets -int 1
defaults write com.apple.WindowManager StageManagerHideWidgets -int 1
'''
DOCK = [f'{GUEST_XCODE}', f'{GUEST_XCODE}/Contents/Applications/DeviceHub.app',
        '/System/Applications/Utilities/Terminal.app']
WALLPAPER = '/System/Library/Desktop Pictures/Solid Colors/Space Gray.png'
FORMULAE = ['cliclick', 'peekaboo']
TOOLS = '\n'.join([
    'set -e',
    # The official installer waits on softwareupdate for Command Line Tools, a service the base disables.
    'if [ ! -x /opt/homebrew/bin/brew ]; then',
    '  sudo rm -rf /opt/homebrew && sudo mkdir -p /opt/homebrew && sudo chown -R "$USER":admin /opt/homebrew',
    '  curl -fsSL https://github.com/Homebrew/brew/tarball/main | tar xz --strip-components 1 -C /opt/homebrew',
    'fi',
    'grep -q "brew shellenv" ~/.zprofile 2>/dev/null || echo \'eval "$(/opt/homebrew/bin/brew shellenv)"\' >> ~/.zprofile',
    'eval "$(/opt/homebrew/bin/brew shellenv)"',
    'export HOMEBREW_NO_AUTO_UPDATE=1 HOMEBREW_NO_INSTALL_CLEANUP=1 HOMEBREW_NO_ANALYTICS=1',
    'brew analytics off',
    'brew update --force --quiet',
    f'brew install {" ".join(FORMULAE)}',
    f'brew upgrade {" ".join(FORMULAE)} || true',
    'cliclick -V',
    'peekaboo --version',
])
DESKTOP = '\n'.join([
    'until pgrep -x Dock >/dev/null; do sleep 1; done',
    'defaults write com.apple.dock persistent-apps -array',
    *[f"defaults write com.apple.dock persistent-apps -array-add '<dict><key>tile-data</key><dict><key>file-data</key>"
      f"<dict><key>_CFURLString</key><string>{app}</string><key>_CFURLStringType</key><integer>0</integer></dict>"
      "</dict></dict>'" for app in DOCK],
    'defaults write com.apple.dock persistent-others -array',
    'defaults write com.apple.dock recent-apps -array',
    'defaults write com.apple.dock show-recents -bool false',
    'defaults write com.apple.loginwindow TALLogoutSavesState -bool false',
    'defaults write com.apple.loginwindow LoginwindowLaunchesRelaunchApps -bool false',
    'killall Dock',
    # System Events would raise an Automation consent prompt that nobody answers; NSWorkspace needs none.
    "osascript -l JavaScript -e 'ObjC.import(\"AppKit\"); const url = $.NSURL.fileURLWithPath(\"" + WALLPAPER + "\"); "
    "const screens = $.NSScreen.screens; for (let i = 0; i < screens.count; i++) "
    "$.NSWorkspace.sharedWorkspace.setDesktopImageURLForScreenOptionsError(url, screens.objectAtIndex(i), $({}), null)'",
])
GRANTS = grants.base_script()
FIRST_LAUNCH = f'''
x='{GUEST_XCODE}'
open -a "$x"
for i in $(seq 120); do pgrep -x Xcode >/dev/null && break; sleep 1; done
sleep 60; osascript -e 'quit app id "com.apple.dt.Xcode"' || true; sleep 5; pkill -x Xcode || true
open -a "$x/Contents/Applications/DeviceHub.app"
for i in $(seq 120); do pgrep -x DeviceHub >/dev/null && break; sleep 1; done
sleep 30; xcrun simctl shutdown all || true
osascript -e 'quit app id "com.apple.dt.Devices"' || true; sleep 5; pkill -x DeviceHub || true
'''
POINTS = {'utilities': (480 / 3360, 28 / 2100), 'terminal': (487 / 3360, 192 / 2100)}


@dataclass
class Integrity:
    broken: list
    quarantined: bool
    sip: str

    @property
    def ok(self):
        return not self.broken and not self.quarantined


def service_commands(services=SERVICES):
    lines = []
    for domain, label, _ in services:
        target = 'system' if domain == 'system' else 'gui/501'
        lines += [f'sudo launchctl disable {target}/{label}', f'sudo launchctl bootout {target}/{label} 2>/dev/null']
    return '\n'.join(lines + ['true'])


def integrity_command(xcode):
    paths = ' '.join(f"'{path}'" for path in INTEGRITY)
    return (f"cd '{xcode}' 2>/dev/null || echo 'BROKEN {xcode}'; "
            f"for item in {paths}; do codesign --verify --strict \"$item\" 2>/dev/null || echo \"BROKEN $item\"; done; "
            f"xattr -p com.apple.quarantine '{xcode}' >/dev/null 2>&1 && echo QUARANTINED; csrutil status")


def integrity(output):
    lines = output.splitlines()
    sip = re.search(r'Protection status: (\w+)', output)
    return Integrity([line[len('BROKEN '):] for line in lines if line.startswith('BROKEN ')],
                     'QUARANTINED' in lines, sip[1] if sip else 'unknown')


def integrity_failure(report):
    problems = report.broken + (['quarantine attribute on Xcode'] if report.quarantined else [])
    return (f'the guest Xcode failed its integrity check ({", ".join(problems)}); '
            'run pnpm verify:mac:vm prepare --repair-xcode')


def point(name, width, height):
    x, y = POINTS[name]
    return round(x * width), round(y * height)


def fingerprint(pixels, width, height, columns=64, rows=40):
    samples = bytes(pixels[((y * height // rows) * width + x * width // columns) * 4 + 1] >> 4
                    for y in range(rows) for x in range(columns))
    return hashlib.md5(samples).hexdigest()


def menu_bar(pixels, width, height):
    band = range(int(height * 0.004), max(1, int(height * 0.02)))
    columns = range(int(width * 0.025), int(width * 0.4), 2)
    lit = sum(1 for y in band for x in columns if max(pixels[(y * width + x) * 4:(y * width + x) * 4 + 3]) > 150)
    return lit >= 20


def glance(capture, width, height):
    try:
        pixels = capture()
    except OSError:
        return 'unavailable', False
    return fingerprint(pixels, width, height), sum(1 for value in pixels[::4096] if value > 40) >= 40


def steady(look, stable=10, timeout=300, now=time.monotonic, sleep=time.sleep, step='recovery screen'):
    deadline = now() + timeout
    last, since = None, now()
    while now() < deadline:
        frame, lit = look()
        if frame != last:
            last, since = frame, now()
        elif lit and now() - since >= stable:
            return frame
        sleep(1)
    raise SystemExit(f'the {step} never settled within {timeout}s; see {VOLUME / "runs/recovery.png"}')


def advance(look, commit, prime=lambda: None, attempts=3, wait=30, now=time.monotonic, sleep=time.sleep,
            step='recovery step'):
    for attempt in range(1, attempts + 1):
        prime()
        before = look()[0]
        commit()
        deadline = now() + wait
        while now() < deadline:
            sleep(1)
            if look()[0] != before:
                return attempt
    raise SystemExit(f'{step} did not respond after {attempts} attempts; see {VOLUME / "runs/recovery.png"}')


def warm_up(guest, host_xcode=False):
    with vm_status.current.step('boot'):
        guest.boot()
    started = time.monotonic()
    xcode = SHARED_XCODE if host_xcode else GUEST_XCODE
    wait = f"for i in $(seq 30); do [ -d '{xcode}' ] && break; sleep 1; done; " if host_xcode else ''
    developer = f"DEVELOPER_DIR='{xcode}/Contents/Developer' " if host_xcode else ''
    with vm_status.current.step('Xcode integrity'):
        result = guest.ssh(f'{wait}{integrity_command(xcode)}; {developer}xcodebuild -version >/dev/null 2>&1 '
                           '|| echo "BROKEN xcodebuild -version"', check=False, timeout=900)
    report = integrity(result.stdout)
    if not report.ok:
        raise SystemExit(integrity_failure(report))
    progress(f'guest Xcode verified and ready after {time.monotonic() - started:.0f}s (SIP {report.sip})')


def archive():
    build = subprocess.run(['/usr/libexec/PlistBuddy', '-c', 'Print ProductBuildVersion',
                            str(XCODE / 'Contents/version.plist')], capture_output=True, text=True, check=True)
    build = build.stdout.strip()
    path = ARCHIVES / f'{XCODE.stem}-{build}.zip'
    if not path.exists():
        ARCHIVES.mkdir(exist_ok=True)
        progress(f'archiving {XCODE} for the guest')
        partial = path.with_suffix('.partial')
        subprocess.run(['ditto', '-c', '-k', '--sequesterRsrc', '--keepParent', '--zlibCompressionLevel', '0',
                        str(XCODE), str(partial)], check=True)
        partial.rename(path)
    for stale in ARCHIVES.iterdir():
        if stale != path:
            progress(f'removing the stale Xcode archive {stale.name}')
            stale.unlink()
    return path, build


def xcode_script(name, build, repair):
    return f'''
x='{GUEST_XCODE}'
sudo find /Applications -maxdepth 1 -name 'Xcode*.app' ! -path "$x" -exec rm -rf {{}} +
current=$(/usr/libexec/PlistBuddy -c 'Print ProductBuildVersion' "$x/Contents/version.plist" 2>/dev/null)
if [ {int(repair)} = 1 ] || [ "$current" != '{build}' ] \\
    || ! codesign --verify --strict "$x/Contents/Developer/usr/bin/xcodebuild" 2>/dev/null; then
  sudo rm -rf "$x"
  sudo ditto -x -k '{SHARED}/xcode/{name}' /Applications
fi
sudo xattr -dr com.apple.quarantine "$x" 2>/dev/null || true
sudo xcode-select -s "$x"
sudo xcodebuild -license accept
sudo xcodebuild -runFirstLaunch
'''


RUNTIME = f'''
version=$(xcrun --sdk iphonesimulator --show-sdk-version)
if ! xcrun simctl list runtimes | grep -q "^iOS $version "; then
  bundle=$(ls -d '{SHARED}/runtimes/'*_"$version"_*.exportedBundle 2>/dev/null | head -1)
  if [ -z "$bundle" ]; then
    xcodebuild -downloadPlatform iOS -buildVersion "$version" -exportPath '{SHARED}/runtimes'
    bundle=$(ls -d '{SHARED}/runtimes/'*_"$version"_*.exportedBundle | head -1)
  fi
  xcodebuild -importPlatform "$bundle"
fi
xcrun simctl runtime list | awk -v v="$version" '$1 == "iOS" && $2 != v {{print $5}}' | while read -r old; do
  xcrun simctl runtime delete "$old"
done
find '{SHARED}/runtimes' -maxdepth 1 -name '*.exportedBundle' ! -name "*_${{version}}_*" -exec rm -rf {{}} +
xcrun simctl list runtimes | grep "^iOS $version "
'''


def step(guest, label, script, timeout=3600):
    started = time.monotonic()
    progress(f'{label}...')
    with vm_status.current.step(label):
        guest.ssh(script, timeout=timeout)
    progress(f'{label}: {time.monotonic() - started:.0f}s')


def shutdown(guest):
    guest.ssh('sudo shutdown -h now', check=False, timeout=30)
    guest.process.wait(timeout=180)


def install(args, log, name):
    progress(f'installing macOS from {IPSW.name}')
    tart('create', '--from-ipsw', str(IPSW), '--disk-size', '80', '--disk-format', 'asif', name, capture=False)
    tart('set', name, '--cpu', str(args.cpu), '--memory', str(args.memory * 1024), '--display', args.display)
    # Provisioning options only take effect on the first boot after creation.
    options = ','.join(['fullName=macvm', f'username={GUEST_USER}', f'password={GUEST_PASSWORD}',
                        'logsInAutomatically=true', 'enablesRemoteLogin=true'])
    guest = Guest(name, {'key': f'{KEY.parent}:ro'}, log, extra=[f'--provisioning-opts={options}'])
    try:
        progress('waiting for the first boot to provision the admin account')
        guest.boot(timeout=1200, password=True)
        guest.ssh(ACCOUNT, password=True, timeout=600)
        shutdown(guest)
    finally:
        guest.stop()


def sip_state(log, name):
    guest = Guest(name, {}, log)
    try:
        guest.boot()
        state = integrity(guest.ssh('csrutil status', check=False).stdout).sip
        shutdown(guest)
    finally:
        guest.stop()
    return state


class Recovery:
    def __init__(self, port, password):
        self.port, self.password = port, password
        self.screen = self.connect()

    def connect(self):
        deadline = time.monotonic() + 300
        while True:
            try:
                return RFB(self.port, self.password)
            except OSError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(2)

    def capture(self):
        try:
            return self.screen.capture().pixels
        except OSError:
            # A recovery display that is not drawing yet never answers the update request; start a clean session.
            self.screen.close()
            self.screen = self.connect()
            raise

    def look(self):
        return glance(self.capture, self.screen.width, self.screen.height)

    def shows_menu_bar(self):
        try:
            return menu_bar(self.capture(), self.screen.width, self.screen.height)
        except OSError:
            return False

    def at(self, name):
        return point(name, self.screen.width, self.screen.height)

    def run(self):
        steady(self.look, step='boot picker')
        advance(self.look, lambda: self.screen.key('return'),
                prime=lambda: (self.screen.key('right').key('right'), time.sleep(1)), step='Options in the boot picker')
        steady(self.look, step='first recovery screen')
        if not self.shows_menu_bar():
            advance(self.look, lambda: self.screen.key('return'), step='the Language chooser')
            steady(self.look, step='recovery app')
        if not self.shows_menu_bar():
            raise SystemExit(f'the recovery app never showed its menu bar; see {VOLUME / "runs/recovery.png"}')
        advance(self.look, lambda: self.screen.click(*self.at('terminal')),
                prime=lambda: (self.screen.click(*self.at('utilities')), time.sleep(2)), step='Utilities > Terminal')
        time.sleep(3)
        self.screen.type('csrutil disable').key('return')
        time.sleep(5)
        self.screen.type('y').key('return')
        time.sleep(4)
        self.screen.type(GUEST_PASSWORD).key('return')
        time.sleep(10)
        self.screen.capture(VOLUME / 'runs/recovery.png')
        self.screen.type('halt').key('return')


def disable_sip(log, name):
    guest = Guest(name, {}, log, extra=['--recovery'], recovery_vnc=True)
    started = time.monotonic()
    try:
        password, port = guest.recovery()
        recovery = Recovery(port, password)
        try:
            recovery.run()
        except SystemExit:
            recovery.screen.capture(VOLUME / 'runs/recovery.png')
            raise
        recovery.screen.close()
        guest.process.wait(timeout=180)
    finally:
        guest.stop()
    progress(f'recovery finished after {time.monotonic() - started:.0f}s')


NEXT = f'{BASE}-next'
MARKS = VOLUME / 'runs/base.json'
NEXT_MARKS = VOLUME / 'runs/base-next.json'
READY = VOLUME / 'runs' / f'{BASE}.ready'
PREPARE_LOCK = VOLUME / 'runs/prepare.lock'


def xcode_build():
    return subprocess.run(['/usr/libexec/PlistBuddy', '-c', 'Print ProductBuildVersion',
                           str(XCODE / 'Contents/version.plist')], capture_output=True, text=True, check=True).stdout.strip()


def simulator_sdk():
    return subprocess.run(['xcrun', '--sdk', 'iphonesimulator', '--show-sdk-version'], capture_output=True, text=True,
                          check=True, env={**os.environ, 'DEVELOPER_DIR': str(XCODE / 'Contents/Developer')}).stdout.strip()


def scripts():
    return {'system': SYSTEM, 'runtime': RUNTIME, 'screen-sharing': SCREEN_SHARING, 'services': service_commands(),
            'tools': TOOLS, 'grants': GRANTS, 'desktop': DESKTOP, 'first-launch': FIRST_LAUNCH}


def current_marks():
    return steps.fingerprints(scripts(), xcode_build(), simulator_sdk())


def outdated(args=None):
    force = ['xcode'] if args is not None and getattr(args, 'repair_xcode', False) else []
    if getattr(args, 'upgrade_tools', False):
        force.append('tools')
    return steps.stale(steps.recorded(MARKS), current_marks(), force)


LABELS = {
    'system': ('system settings: no sleep or lock, no indexing, automation mode', 3600),
    'xcode': (f'Xcode in /Applications/{XCODE.name}', 3600),
    'runtime': ('iOS Simulator runtime', 7200),
    'screen-sharing': ('Screen Sharing for the loopback VNC viewer', 3600),
    'services': (f'disabling {len(SERVICES)} background services', 3600),
    'tools': (f'Homebrew with {", ".join(FORMULAE)}', 1800),
    'grants': ('permission grants for automation clients', 600),
    'desktop': ('Dock with Xcode, DeviceHub and Terminal; solid wallpaper', 300),
    'first-launch': ('first launch of Xcode and DeviceHub', 900),
}


def configure(args, log, name, todo, marks):
    archived = archive() if 'xcode' in todo else None
    RUNTIMES.mkdir(exist_ok=True)
    texts = scripts()
    guest = Guest(name, {'xcode': f'{ARCHIVES}:ro', 'runtimes': RUNTIMES}, log)
    try:
        guest.expose(args)
        guest.boot()
        for item in todo:
            label, timeout = LABELS[item]
            script = xcode_script(archived[0].name, archived[1], args.repair_xcode) if item == 'xcode' else texts[item]
            step(guest, label, script, timeout=timeout)
            steps.record(NEXT_MARKS, item, marks[item])
        report = integrity(guest.ssh(integrity_command(GUEST_XCODE), check=False).stdout)
        if not report.ok:
            raise SystemExit(integrity_failure(report))
        progress(f'integrity check passed (SIP {report.sip})')
        shutdown(guest)
    finally:
        guest.stop()


def settle(args, log, name):
    # One more login lets first-run banners fire in the base, not in every clone.
    guest = Guest(name, {}, log)
    try:
        guest.expose(args)
        progress('settling a second login')
        guest.boot()
        guest.ssh('sleep 90; sudo shutdown -h now', check=False, timeout=180)
        guest.process.wait(timeout=180)
    finally:
        guest.stop()


def ensure_sip_off(args, log, name):
    phase = vm_status.current.step
    with phase('SIP check'):
        state = sip_state(log, name)
    if state == 'disabled':
        return
    if not args.recovery_vnc:
        raise SystemExit('SIP is on in the base. Turning it off boots recovery with Virtualization.framework '
                         'VNC, which listens on every interface for a few minutes; rerun with --recovery-vnc')
    progress('turning SIP off in recovery')
    with phase('recovery'):
        disable_sip(log, name)
    with phase('SIP check'):
        state = sip_state(log, name)
    if state != 'disabled':
        raise SystemExit(f'SIP is still on; see {VOLUME / "runs/recovery.png"}')


def swap():
    old = f'{BASE}-old'
    with exclusive(BASE_LOCK):
        names = vms()
        if old in names and BASE in names:
            tart('delete', old)
        if BASE in names:
            tart('rename', BASE, old)
        tart('rename', NEXT, BASE)
        tart('delete', old, check=False)
        NEXT_MARKS.replace(MARKS)
        READY.touch()


def build_base(args, log):
    marks = current_marks()
    todo = outdated(args)
    action = steps.plan(base=BASE in vms(), next_vm=NEXT in vms(), force=args.force, todo=todo)
    if action == 'resume' and BASE not in vms() and 'installed' not in steps.recorded(NEXT_MARKS):
        action = 'install'
    if action == 'current':
        progress(f'{BASE} is current; nothing to update')
        return
    with exclusive(SLOTS_LOCK):
        free_slot(NEXT)
    phase = vm_status.current.step
    if action == 'install':
        if NEXT in vms():
            tart('delete', NEXT)
        NEXT_MARKS.unlink(missing_ok=True)
        with phase('install'):
            install(args, log, NEXT)
        steps.record(NEXT_MARKS, 'installed', 'yes')
    elif action == 'clone':
        with exclusive(BASE_LOCK):
            tart('clone', BASE, NEXT)
        NEXT_MARKS.write_text(json.dumps(steps.recorded(MARKS)))
    todo = steps.stale(steps.recorded(NEXT_MARKS), marks, ['xcode'] if args.repair_xcode else [])
    progress(f'updating {NEXT}: {", ".join(todo) or "settle only"}')
    ensure_sip_off(args, log, NEXT)
    configure(args, log, NEXT, todo, marks)
    with phase('settle'):
        settle(args, log, NEXT)
    with phase('swap'):
        swap()


def prepare(args):
    mount()
    TART_HOME.mkdir(exist_ok=True)
    VOLUME.joinpath('runs').mkdir(exist_ok=True)
    if not IPSW.exists():
        IPSW.parent.mkdir(exist_ok=True)
        progress(f'downloading {IPSW.name}')
        subprocess.run(['curl', '-fL', '-C', '-', '--retry', '5', '-o', str(IPSW), IPSW_URL], check=True)
    if not KEY.exists():
        KEY.parent.mkdir(exist_ok=True)
        subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-C', 'macvm', '-f', str(KEY)],
                       check=True)
    with exclusive(PREPARE_LOCK), (VOLUME / 'runs/prepare.log').open('w') as log:
        vm_status.current = vm_status.Progress(STATUS, HISTORY)
        vm_status.current.begin('prepare', [], VOLUME / 'runs/prepare.log')
        try:
            build_base(args, log)
        except BaseException as failure:
            vm_status.current.end(vm_status.failure(failure))
            raise
        vm_status.current.end('passed')
    progress(f'{BASE} is ready')
