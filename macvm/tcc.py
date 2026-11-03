"""Timed TCC bypass inside disposable macOS guests, using unixzii/tcc-bypass."""
import math
import shlex

REVISION = '78da98104bafff7ecb96935cf198cb4196a28ffd'
ARCHIVE_SHA256 = 'caec48d3d4e1a4308e2c6a2ad2c1c743b0e7791f111f7c02d789b3dc6dd24794'
DIRECTORY = f'/usr/local/libexec/macvm/tcc-bypass/{REVISION}'
TOOL = '/usr/local/bin/macvm-tcc-bypass'


def install_script(xcode):
    developer = shlex.quote(f'{xcode}/Contents/Developer')
    wrapper = shlex.quote(f'exec /usr/bin/env DEVELOPER_DIR={developer} /usr/bin/python3 '
                          f'{DIRECTORY}/tcc-bypass.py "$@"')
    return f'''set -eu
if [ ! -x {DIRECTORY}/tccd_control_helper ]; then
  work=$(mktemp -d /tmp/macvm-tcc.XXXXXX)
  trap 'rm -rf "$work"' EXIT HUP INT TERM
  curl -fL --retry 3 https://codeload.github.com/unixzii/tcc-bypass/tar.gz/{REVISION} -o "$work/source.tar.gz"
  echo '{ARCHIVE_SHA256}  '"$work/source.tar.gz" | shasum -a 256 -c -
  mkdir "$work/source" "$work/built"
  tar -xzf "$work/source.tar.gz" --strip-components 1 -C "$work/source"
  DEVELOPER_DIR={developer} /usr/bin/make -C "$work/source"
  cp "$work/source/tcc-bypass.py" "$work/source/libtccd_inject.dylib" "$work/source/tccd_control_helper" "$work/built/"
  sudo chown -R root:wheel "$work/built"
  sudo mkdir -p /usr/local/libexec/macvm/tcc-bypass
  sudo mv "$work/built" {DIRECTORY}
fi
sudo mkdir -p /usr/local/bin
printf '%s\\n' '#!/bin/sh' {wrapper} | sudo tee {TOOL} >/dev/null
sudo chmod 755 {TOOL}
'''


def mode_script(enabled, minutes=60, tool=TOOL):
    tool = shlex.quote(tool)
    if not enabled:
        # A missing broker means there is no active bypass. Other errors must surface.
        return f'''if [ -x {tool} ]; then
  reply=$(sudo -n {tool} stop --json)
  code=$?
  if [ "$code" -eq 69 ] && printf '%s\\n' "$reply" | grep -Eq '"error"[[:space:]]*:[[:space:]]*"helper_unavailable"'; then
    exit 0
  fi
  printf '%s\\n' "$reply"
  exit "$code"
fi'''
    if not 1 <= minutes <= 1440:
        raise ValueError('TCC bypass duration must be 1 to 1440 minutes')
    return f'''set -e
[ -x {tool} ] || {{ echo 'TCC bypass is not installed; run macvm prepare and use an updated runner' >&2; exit 1; }}
launchctl kickstart gui/$(id -u)/com.apple.tccd
sudo -n launchctl kickstart system/com.apple.tccd.system
sudo -n {tool} start {minutes} --json'''


def apply(guest, enabled, seconds=3600):
    # Leave headroom for command setup; renew before each test suite and manual operation.
    minutes = max(60, math.ceil(seconds / 60) + 5) if enabled else 60
    result = guest.ssh(mode_script(enabled, minutes), timeout=180)
    return result.stdout
