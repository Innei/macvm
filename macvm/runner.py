"""The warm runner: one persistent clone of the base VM that verify:mac:vm runs reuse.

A detached keeper process (``python -m macvm.runner keep``) owns the runner's ``tart run``, its
VNC forwarder and the idle timer, so the VM and the viewer outlive each run and stop
after IDLE_SECONDS without a run. Each run resets the app's state in the guest instead
of cloning; the runner is recycled only when the base changes, an infrastructure
failure happened, or ``pnpm verify:mac:vm reset`` is run.
"""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
from types import SimpleNamespace

from .base import outdated, warm_up
from .fleet import free_slot
from .grants import app_script
from .rfb import RFB
from .guest import (SETTINGS, BASE, BASE_LOCK, SLOTS_LOCK, GUEST_PASSWORD, GUEST_USER, HISTORY, VOLUME, Alive, Guest, attempt, build,
                      collect, exclusive, progress, run_tests, stage, tart, vms)
from . import status as vm_status
from . import tcc
from .vnc import hold

IDLE_SECONDS = SETTINGS.idle_minutes * 60


def use(name):
    global PROJECT, RUNNER, HOME, MARKER, STATE, KEEPER, TART_LOG, VNC, RUN_LOCK, STATUS
    PROJECT, RUNNER = name, f'{name}-runner'
    HOME = VOLUME / 'runners' / name
    MARKER, STATE, KEEPER = HOME / 'active', HOME / 'state.json', HOME / 'keeper.json'
    TART_LOG, VNC, RUN_LOCK, STATUS = HOME / 'tart.log', HOME / 'vnc.txt', HOME / 'lock', HOME / 'status.json'


use('default')


def prepare_home():
    HOME.mkdir(parents=True, exist_ok=True)
# xcodebuild exits 65 when tests ran and some failed; that is the suite's verdict, not a broken runner.
TEST_CODES = (0, 65)
def shell_path(path):
    prefix, rest = ('~/', path[2:]) if path.startswith('~/') else ('', path)
    escaped = re.sub(r'(["$`\\])', r'\\\1', rest)
    return prefix + re.sub(r'[^*?]+', lambda part: f'"{part.group(0)}"', escaped)


def reset_command(project):
    # The bracketed patterns keep pkill -f from matching the shell that runs this command.
    reset = project.reset
    return '; '.join([
        'setopt nonomatch 2>/dev/null',
        *[f'pkill -9 -x {name}' for name in reset.processes],
        *[f'pkill -9 -f "{pattern}"' for pattern in reset.process_patterns],
        'pkill -9 -x xctest',
        'pkill -9 -f "[x]codebuild test-without-building"',
        *[f'defaults delete {domain} 2>/dev/null' for domain in reset.defaults],
        'rm -rf ' + ' '.join([*map(shell_path, reset.paths), '~/run']),
        'true',
    ])


def recycle_reason(exists, base, recorded, infra, requested=False):
    if requested:
        return 'reset requested'
    if not exists:
        return 'no runner yet'
    if base != recorded:
        return 'the base VM changed'
    if infra:
        return f'the last run hit an infrastructure failure: {infra}'
    return None


def infra_failure(code, has_result, timed_out):
    if timed_out:
        return 'the guest test run timed out'
    if not has_result:
        return f'xcodebuild exited {code} without a result bundle'
    if code not in TEST_CODES:
        return f'xcodebuild exited {code}'
    return None


def plan(running, keeper):
    if running and keeper:
        return 'attach'
    if running:
        return 'restart'
    return 'start'


def idle_expired(now, last, idle=IDLE_SECONDS):
    return now - last >= idle


def base_stamp():
    return (VOLUME / 'runs' / f'{BASE}.ready').stat().st_mtime_ns


def load_state():
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {}


def save_state(**state):
    STATE.write_text(json.dumps(state))


def running():
    listed = json.loads(tart('list', '--format', 'json').stdout)
    return any(vm['Name'] == RUNNER and vm.get('Running') for vm in listed)


def keeper_pid():
    try:
        pid = json.loads(KEEPER.read_text())['pid']
    except (OSError, ValueError, KeyError):
        return None
    command = subprocess.run(['ps', '-p', str(pid), '-o', 'command='], capture_output=True, text=True).stdout
    return pid if 'macvm.runner keep' in command else None


def clear(directory):
    # A running VM keeps sharing the original directory, so empty it rather than replace it.
    directory.mkdir(parents=True, exist_ok=True)
    for item in directory.iterdir():
        if item.is_dir() and not item.is_symlink():
            shutil.rmtree(item)
        else:
            item.unlink()


def shares():
    return {'products': HOME / 'products', 'out': HOME / 'out'}


def stop_keeper():
    pid = keeper_pid()
    if pid:
        os.kill(pid, signal.SIGTERM)
        deadline = time.monotonic() + 120
        while Alive(pid).poll() is None and time.monotonic() < deadline:
            time.sleep(0.5)
    if running():
        tart('stop', RUNNER, '--timeout', '60', check=False)


def discard():
    stop_keeper()
    if RUNNER in vms():
        tart('delete', RUNNER)
    STATE.unlink(missing_ok=True)


def keeper_command(vnc_port):
    command = [sys.executable, '-m', 'macvm.runner', 'keep', '--name', PROJECT]
    return command + ['--vnc-port', str(vnc_port)] if vnc_port else command


def start_keeper(args):
    VNC.unlink(missing_ok=True)
    command = keeper_command(args.vnc_port)
    with (HOME / 'keeper.log').open('w') as log:
        keeper = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                  start_new_session=True)
    deadline = time.monotonic() + 240
    while not VNC.exists():
        if keeper.poll() is not None or time.monotonic() > deadline:
            raise SystemExit(f'the runner keeper did not start the VM; see {HOME / "keeper.log"}')
        time.sleep(0.2)
    return keeper


def launch(args):
    pid = keeper_pid()
    action = plan(running(), pid is not None)
    if action == 'attach':
        progress(f'attaching to the running {RUNNER}')
        return Alive(pid)
    if action == 'restart':
        progress(f'{RUNNER} is running without its keeper; restarting it')
        tart('stop', RUNNER, '--timeout', '60', check=False)
    with exclusive(SLOTS_LOCK):
        free_slot(RUNNER)
        progress(f'starting {RUNNER} headless')
        return start_keeper(args)


def ready(args, output):
    with vm_status.current.step('start VM'):
        keeper = launch(args)
    line = VNC.read_text().strip()
    progress(line)
    (output / 'vnc.txt').write_text(line + '\n')
    guest = Guest.attach(RUNNER, SimpleNamespace(name=str(TART_LOG)), keeper)
    warm_up(guest)
    with vm_status.current.step('reset'):
        guest.ssh(reset_command(args.project), check=False, timeout=60)
        granted = guest.ssh(app_script(args.project), check=False, timeout=60)
        if granted.returncode:
            progress(f'resetting app permissions failed ({granted.returncode}): {granted.stdout.strip()[-300:]}')
    return guest


def ensure():
    state = load_state()
    base = base_stamp()
    reason = recycle_reason(RUNNER in vms(), base, state.get('base'), state.get('infra'))
    if reason:
        progress(f'cloning {RUNNER} from {BASE} ({reason})')
        discard()
        with exclusive(BASE_LOCK):
            tart('clone', BASE, RUNNER)
        save_state(base=base, infra=None)
    return base


def viewer_url(line):
    found = re.search(r'vnc://admin@localhost:(\d+)', line or '')
    if not found:
        raise SystemExit(f'no VNC address in {line!r}')
    return f'vnc://{GUEST_USER}:{GUEST_PASSWORD}@localhost:{found[1]}'


def watch_line(running, keeper, line):
    state = vm_status.runner_state(running, keeper, bool(line and line.strip()))
    if state != 'up':
        raise SystemExit(f'{RUNNER} is {state}; nothing to watch')
    return viewer_url(line)


def watch(args):
    line = read(VNC)
    url = watch_line(running(), keeper_pid() is not None, line)
    MARKER.touch()
    progress(line.strip())
    if args.print_only:
        print(url)
        return
    subprocess.run(['open', url], check=True)


def holder():
    with attempt(RUN_LOCK) as free:
        if free:
            return None
    pid = vm_status.lock_owner(RUN_LOCK.read_text()) if RUN_LOCK.exists() else None
    if not pid:
        return {'pid': '?', 'started': 'unknown', 'tty': None, 'command': 'unknown', 'cwd': None}
    info = subprocess.run(['ps', '-o', 'lstart=,tty=,command=', '-p', str(pid)], capture_output=True, text=True).stdout
    fields = info.strip()[24:].split(None, 1)
    cwd = subprocess.run(['lsof', '-a', '-p', str(pid), '-d', 'cwd', '-Fn'], capture_output=True, text=True).stdout
    return {'pid': pid, 'started': info.strip()[:24] or 'unknown', 'tty': fields[0] if fields else None,
            'command': fields[1] if len(fields) > 1 else 'exited', 'cwd': next(
                (line[1:] for line in cwd.splitlines() if line.startswith('n')), None)}


def read(path):
    return Path(path).read_text(errors='replace') if path and Path(path).exists() else None


def others():
    folder = VOLUME / 'runners'
    names = sorted(path.name for path in folder.iterdir() if path.is_dir()) if folder.exists() else []
    listed = {vm['Name']: vm.get('Running') for vm in json.loads(tart('list', '--format', 'json').stdout)}
    return [{'name': f'{name}-runner', 'state': 'up' if listed.get(f'{name}-runner') else 'stopped'}
            for name in names if name != PROJECT and f'{name}-runner' in listed]


def snapshot():
    now = time.time()
    state = vm_status.runner_state(running(), keeper_pid() is not None, VNC.exists())
    lock = holder()
    try:
        record = json.loads(STATUS.read_text())
    except (OSError, ValueError):
        record = {}
    run = None
    if vm_status.live(record):
        tests, in_process = read(record.get('log')), read(record.get('in_process_log'))
        run = {**record, 'tests': vm_status.parse_tests(tests) if tests is not None else None,
               'in_process': vm_status.parse_in_process(in_process) if in_process is not None else None}
    idle = None
    if state == 'up' and not lock and MARKER.exists():
        idle = vm_status.idle_left(now, MARKER.stat().st_mtime, IDLE_SECONDS)
    line = read(VNC) if state == 'up' else read(run and Path(run['output']) / 'vnc.txt')
    found = re.search(r'vnc://admin@localhost:\d+', line or '')
    return {
        'now': now, 'lock': lock, 'run': run, 'history': vm_status.history(read(HISTORY) or ''),
        'others': others(),
        'runner': {'name': RUNNER, 'state': state, 'idle_left': idle, 'paused': bool(lock) and state == 'up',
                   'vnc': found[0] if found else None},
    }


def status(args):
    prepare_home()
    try:
        base = {'name': BASE, 'outdated': outdated()}
    except (OSError, subprocess.CalledProcessError):
        base = None
    if not args.follow:
        print(vm_status.render({**snapshot(), 'base': base}))
        return
    try:
        while True:
            started = time.monotonic()
            print('\033[H\033[2J' + vm_status.render({**snapshot(), 'base': base}), flush=True)
            time.sleep(max(0.0, 1 - (time.monotonic() - started)))
    except KeyboardInterrupt:
        pass


def session(args, output):
    base = ensure()
    clear(HOME / 'products')
    clear(HOME / 'out')
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=1) as pool:
        booting = pool.submit(ready, args, output)
        stage(args.project, build(args.project, output / 'build.log'), HOME / 'products', args.extras)
        try:
            guest = booting.result()
        except SystemExit as failure:
            save_state(base=base, infra=str(failure).splitlines()[0])
            raise
    try:
        code, status = run_tests(guest, args.project, args, args.extras)
        timed_out = False
    except subprocess.TimeoutExpired:
        code, status, timed_out = None, 1, True
    infra = infra_failure(code, (HOME / 'out/Result.xcresult').exists(), timed_out)
    save_state(base=base, infra=infra)
    if infra:
        progress(f'{infra}; the runner will be recycled on the next run')
    result = collect(HOME / 'out', output, started, status, [extra.name for extra in args.extras])
    if TART_LOG.exists():
        shutil.copy2(TART_LOG, output / 'tart.log')
    if args.hold:
        hold(guest.process, f'holding {RUNNER}; Ctrl-C releases it, and it stops after {IDLE_SECONDS // 60} idle min')
    return result


def run(args, output):
    prepare_home()
    with exclusive(RUN_LOCK):
        MARKER.touch()
        vm_status.current = vm_status.Progress(STATUS, HISTORY)
        vm_status.current.begin('run', args.case, output, project=PROJECT, log=str(HOME / 'out/test.log'),
                                in_process_log=args.extras and str(HOME / 'out' / f'{args.extras[0].name}.log'))
        try:
            code = session(args, output)
        except BaseException as failure:
            vm_status.current.end(vm_status.failure(failure))
            raise
        finally:
            MARKER.touch()
        vm_status.current.end('passed' if code == 0 else 'failed')
        return code


def reset():
    prepare_home()
    with exclusive(RUN_LOCK):
        discard()
    progress(f'{RUNNER} deleted; the next run clones a fresh one from {BASE}')


def keep(vnc_port):
    progress(f'keeper {os.getpid()} starting {RUNNER}')
    directories = shares()
    for directory in directories.values():
        directory.mkdir(parents=True, exist_ok=True)
    log = TART_LOG.open('w')
    guest = Guest(RUNNER, directories, log)

    def interrupt(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupt)
    try:
        guest.expose(SimpleNamespace(vnc_port=vnc_port, output=HOME))
        KEEPER.write_text(json.dumps({'pid': os.getpid()}))
        MARKER.touch()
        while guest.process.poll() is None:
            time.sleep(10)
            with attempt(RUN_LOCK) as free:
                if free and idle_expired(time.time(), MARKER.stat().st_mtime):
                    progress(f'idle for {IDLE_SECONDS // 60} min; stopping {RUNNER}')
                    guest.stop()
    except KeyboardInterrupt:
        progress('stopping on request')
    finally:
        guest.stop()
        KEEPER.unlink(missing_ok=True)
        VNC.unlink(missing_ok=True)
        log.close()


if __name__ == '__main__' and sys.argv[1:2] == ['keep']:
    options = dict(zip(sys.argv[2::2], sys.argv[3::2]))
    use(options['--name'])
    keep(int(options['--vnc-port']) if '--vnc-port' in options else None)


def exec_command(argv):
    return ' '.join(['eval "$(/opt/homebrew/bin/brew shellenv)";', '(caffeinate -u -t 2 >/dev/null 2>&1 &);',
                     *map(shlex.quote, argv)])


def vnc_port(line):
    found = re.search(r'localhost:(\d+)', line or '')
    if not found:
        raise SystemExit(f'{RUNNER} has no VNC address; is it up?')
    return int(found[1])


def up(args):
    prepare_home()
    with attempt(RUN_LOCK) as free:
        if free:
            ensure()
            keeper = launch(args)
            guest = Guest.attach(RUNNER, SimpleNamespace(name=str(TART_LOG)), keeper)
            guest.boot()
            tcc.apply(guest, args.tcc_bypass)
    if not free:
        if getattr(args, 'tcc_override', False):
            raise SystemExit('cannot change TCC mode while the runner is busy; retry after the test run')
        keeper = launch(args)
        guest = Guest.attach(RUNNER, SimpleNamespace(name=str(TART_LOG)), keeper)
        guest.boot()
    MARKER.touch()
    progress(VNC.read_text().strip())
    return guest


def exec_remote(args):
    guest = up(args)
    MARKER.touch()
    result = guest.ssh(exec_command(args.remote), check=False, stdout=sys.stdout)
    MARKER.touch()
    return result.returncode


def screenshot(args, path):
    guest = up(args)
    guest.ssh('caffeinate -u -t 2', check=False, timeout=30)
    path.parent.mkdir(parents=True, exist_ok=True)
    screen = RFB(vnc_port(VNC.read_text()), GUEST_PASSWORD, username=GUEST_USER)
    try:
        screen.capture(path)
    finally:
        screen.close()
    progress(f'saved {path}')
