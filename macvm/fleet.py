import json
import os
import signal
import subprocess
import time

from .guest import VOLUME, Alive, attempt, progress, tart
from . import slots


def project_of(runner):
    return VOLUME / 'runners' / runner.removesuffix('-runner')


def running():
    return [vm['Name'] for vm in json.loads(tart('list', '--format', 'json').stdout) if vm.get('Running')]


def idle_since(runner):
    marker = project_of(runner) / 'active'
    return marker.stat().st_mtime if marker.exists() else 0.0


def busy(runner):
    lock = project_of(runner) / 'lock'
    if not lock.exists():
        return False
    with attempt(lock) as free:
        return not free


def stop(runner):
    try:
        pid = json.loads((project_of(runner) / 'keeper.json').read_text())['pid']
    except (OSError, ValueError, KeyError):
        pid = None
    command = subprocess.run(['ps', '-p', str(pid), '-o', 'command='], capture_output=True, text=True).stdout if pid else ''
    if 'macvm.runner keep' in command:
        os.kill(pid, signal.SIGTERM)
        deadline = time.monotonic() + 120
        while Alive(pid).poll() is None and time.monotonic() < deadline:
            time.sleep(0.5)
    tart('stop', runner, '--timeout', '60', check=False)


def free_slot(wanted):
    slots.make_room(wanted, listed=running, idle=idle_since, busy=busy, stop=stop, sleep=time.sleep, notice=progress)
