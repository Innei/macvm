from .guest import GUEST_XCODE
from .project import APP_SERVICES

SYSTEM_DB = '/Library/Application Support/com.apple.TCC/TCC.db'
SYSTEM_SERVICES = ['kTCCServiceAccessibility', 'kTCCServicePostEvent', 'kTCCServiceListenEvent',
                   'kTCCServiceScreenCapture', 'kTCCServiceSystemPolicyAllFiles', 'kTCCServiceDeveloperTool']
USER_SERVICES = {'kTCCServiceCamera', 'kTCCServiceMicrophone', 'kTCCServiceAddressBook', 'kTCCServiceCalendar',
                 'kTCCServiceReminders', 'kTCCServicePhotos', 'kTCCServiceSystemPolicyDesktopFolder',
                 'kTCCServiceSystemPolicyDocumentsFolder', 'kTCCServiceSystemPolicyDownloadsFolder',
                 'kTCCServiceAppleEvents'}
CONTROLLERS = [
    ('/usr/libexec/sshd-keygen-wrapper', 1),
    ('com.apple.Terminal', 0),
    ('/usr/bin/osascript', 1),
    (f'{GUEST_XCODE}/Contents/Developer/Platforms/MacOSX.platform/Developer/Library/Xcode/Agents/xctest', 1),
]
LINKED = ['/opt/homebrew/bin/cliclick', '/opt/homebrew/bin/peekaboo']
EVENT_TARGETS = ['com.apple.systemevents', 'com.apple.finder']
# macOS 27 moved the per-user database out of ~/Library; the user's tccd holds it open.
PRELUDE = r'''SYSTEM_DB='/Library/Application Support/com.apple.TCC/TCC.db'
user_dbs() {
  pid=$(pgrep -u "$USER" -x tccd | head -1)
  { [ -n "$pid" ] && sudo lsof -p "$pid" -Fn 2>/dev/null | sed -n 's/^n//p' | grep '/com.apple.TCC/TCC.db$'
    sudo find /private/var/containers/Data/ProtectedSystem -path '*/Library/Application Support/com.apple.TCC/TCC.db' 2>/dev/null
    [ -f "$HOME/Library/Application Support/com.apple.TCC/TCC.db" ] && echo "$HOME/Library/Application Support/com.apple.TCC/TCC.db"
  } | sort -u
}
grant() {
  row="insert or replace into access (service, client, client_type, auth_value, auth_reason, auth_version, indirect_object_identifier, flags) values ('$2', '$3', $4, 2, 4, 1, '$5', 0)"
  if [ "$1" = system ]; then sudo sqlite3 "$SYSTEM_DB" "$row"
  else user_dbs | while read -r db; do sudo sqlite3 "$db" "$row"; done; fi
}
'''


def quote(text):
    return "'" + text.replace("'", "'\\''") + "'"


def row(service, client, kind, target='UNUSED'):
    where = 'user' if service in USER_SERVICES else 'system'
    return f'grant {where} {service} {quote(client)} {kind} {quote(target)}'


def base_script():
    lines = ['set -e', PRELUDE,
             '[ -n "$(user_dbs)" ] || { echo "no per-user TCC database found; is the user logged in?" >&2; exit 1; }']
    for client, kind in CONTROLLERS:
        lines += [row(service, client, kind) for service in SYSTEM_SERVICES]
        lines += [row('kTCCServiceAppleEvents', client, kind, target) for target in EVENT_TARGETS]
    for link in LINKED:
        lines.append(f'client=$(readlink -f {link} 2>/dev/null || true); if [ -n "$client" ]; then')
        lines += [f'  grant system {service} "$client" 1 UNUSED' for service in SYSTEM_SERVICES]
        lines += [f'  grant user kTCCServiceAppleEvents "$client" 1 {target}' for target in EVENT_TARGETS]
        lines.append('fi')
    lines.append('sudo sqlite3 "$SYSTEM_DB" "select count(*) from access where client=\'/usr/libexec/sshd-keygen-wrapper\'" '
                 '| grep -qv "^0$" || { echo "grants did not land in $SYSTEM_DB" >&2; exit 1; }')
    lines.append('sudo killall tccd 2>/dev/null; true')
    return '\n'.join(lines)


NOTIFICATIONS = r'''osascript -l JavaScript -e '
ObjC.import("Foundation")
const bundle = "%s", allow = %s
const defaults = $.NSUserDefaults.standardUserDefaults
const domain = ObjC.deepUnwrap(defaults.persistentDomainForName("com.apple.ncprefs")) || {}
const apps = (domain.apps || []).filter(app => app["bundle-id"] !== bundle)
if (allow) apps.push({"bundle-id": bundle, "auth": 7, "flags": 8396814})
domain.apps = apps
defaults.setPersistentDomainForName($(domain), "com.apple.ncprefs")
' >/dev/null'''


def app_script(project):
    apps, targets = project.grants.apps, project.grants.targets
    if not apps and not targets:
        return 'true'
    lines = [PRELUDE]
    for bundle, services in apps.items():
        lines += [f'tccutil reset All {quote(bundle)} >/dev/null 2>&1', f'sudo tccutil reset All {quote(bundle)} >/dev/null 2>&1']
        lines += [row(APP_SERVICES[name], bundle, 0) for name in services if APP_SERVICES[name]]
        lines.append(NOTIFICATIONS % (bundle, 'true' if 'notifications' in services else 'false'))
    for target in targets:
        lines += [row('kTCCServiceAppleEvents', client, kind, target) for client, kind in CONTROLLERS]
    lines.append('killall usernoted 2>/dev/null; sudo killall tccd 2>/dev/null; true')
    return '\n'.join(lines)
