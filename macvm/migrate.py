import json
import os
import subprocess

from .guest import BASE, BUNDLE, VOLUME, progress
from . import steps

OLD_VOLUME, OLD_BASE, OLD_RUNNER, PROJECT = 'LodyVM', 'lody-macos-27.2', 'lody-verify-runner', 'lody'
# These step scripts are byte-identical to the ones lody-ios last ran on lody-macos-27.2 (checked 2026-11-01).
CARRIED = ['system', 'xcode', 'runtime', 'screen-sharing', 'services', 'desktop']


def plan(volume_name, base, runner, project, vms, target_volume, target_base):
    renames = []
    if base in vms and target_base not in vms:
        renames.append(('rename-vm', base, target_base))
    if runner in vms and f'{project}-runner' not in vms:
        renames.append(('rename-vm', runner, f'{project}-runner'))
    volume = [('rename-volume', volume_name, target_volume)] if volume_name != target_volume else []
    if not renames and not volume:
        return []
    return [('stop-all',), *renames, ('move', 'runner', f'runners/{project}'),
            ('move', f'runs/{base}.ready', f'runs/{target_base}.ready'), ('record', CARRIED), *volume]


def tart_in(home, *args):
    return subprocess.run(['tart', *args], capture_output=True, text=True, check=True,
                          env={**os.environ, 'TART_HOME': str(home)}).stdout


def execute(operations, root, current):
    home = root / 'tart'
    for operation in operations:
        progress(' '.join(str(part) for part in operation))
        kind = operation[0]
        if kind == 'stop-all':
            for vm in json.loads(tart_in(home, 'list', '--format', 'json')):
                if vm.get('Running'):
                    tart_in(home, 'stop', vm['Name'], '--timeout', '60')
        elif kind == 'rename-vm':
            tart_in(home, 'rename', operation[1], operation[2])
        elif kind == 'move':
            source, target = root / operation[1], root / operation[2]
            if source.exists() and not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                source.rename(target)
        elif kind == 'record':
            for name in operation[1]:
                steps.record(root / 'runs/base.json', name, current[name])
        elif kind == 'rename-volume':
            subprocess.run(['diskutil', 'rename', str(root), operation[2]], check=True)


def run(args):
    from .base import current_marks
    old = VOLUME.parent / OLD_VOLUME
    if not old.exists() and not VOLUME.exists():
        subprocess.run(['hdiutil', 'attach', '-nobrowse', '-owners', 'on', str(BUNDLE)], check=True,
                       stdout=subprocess.DEVNULL)
    root = old if old.exists() else VOLUME
    names = {vm['Name'] for vm in json.loads(tart_in(root / 'tart', 'list', '--format', 'json'))}
    operations = plan(root.name, OLD_BASE, OLD_RUNNER, PROJECT, names, VOLUME.name, BASE)
    if not operations:
        progress('nothing to migrate')
        return
    execute(operations, root, current_marks())
    progress(f'migrated to {VOLUME} with base {BASE}')
