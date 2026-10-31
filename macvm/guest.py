from contextlib import contextmanager
import fcntl
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

from . import config
from . import status as vm_status
from .vnc import VNC_PORT, Forwarder, exposed, pick_port, recovery_line, vnc_endpoint, vnc_line

SETTINGS = config.load()
BUNDLE = SETTINGS.bundle
VOLUME = SETTINGS.volume
TART_HOME = VOLUME / 'tart'
STATUS = VOLUME / 'runs/status.json'
HISTORY = VOLUME / 'runs/history.jsonl'
BASE = SETTINGS.base
IPSW = VOLUME / 'ipsw' / SETTINGS.ipsw
IPSW_URL = SETTINGS.ipsw_url
XCODE = SETTINGS.xcode
GUEST_XCODE = f'/Applications/{XCODE.name}'
SHARED_XCODE = '/Volumes/My Shared Files/Xcode.app'
KEY = VOLUME / 'ssh/id_ed25519'
GUEST_USER = 'admin'
GUEST_PASSWORD = 'admin'
SHARED = '/Volumes/My Shared Files'


def progress(message):
    print(message, file=sys.stderr, flush=True)


def tart(*args, check=True, capture=True, timeout=None):
    binary = shutil.which('tart') or str(Path.home() / '.local/bin/tart')
    env = {**os.environ, 'TART_HOME': str(TART_HOME)}
    result = subprocess.run([binary, *args], env=env, text=True, capture_output=capture, timeout=timeout)
    if check and result.returncode:
        raise SystemExit(f'tart {" ".join(args)} failed: {(result.stderr or "").strip()}')
    return result


def mount():
    if VOLUME.is_dir():
        return
    if not BUNDLE.exists():
        raise SystemExit(f'no VM bundle at {BUNDLE}; create it with: hdiutil create -size 200g -type SPARSEBUNDLE '
                         f'-fs APFS -volname {VOLUME.name} {BUNDLE}')
    subprocess.run(['hdiutil', 'attach', '-nobrowse', '-owners', 'on', str(BUNDLE)], check=True,
                   stdout=subprocess.DEVNULL)


BASE_LOCK = VOLUME / 'runs/base.lock'
SLOTS_LOCK = VOLUME / 'runs/slots.lock'


@contextmanager
def exclusive(path=BASE_LOCK, notice=progress):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'a+') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.seek(0)
            owner = handle.read().strip() or 'another run'
            notice(f'{owner} holds {path.name}; waiting for it to finish')
            fcntl.flock(handle, fcntl.LOCK_EX)
        handle.seek(0)
        handle.truncate()
        handle.write(f'macvm pid {os.getpid()}')
        handle.flush()
        yield


@contextmanager
def attempt(path=BASE_LOCK):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'a+') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        yield True


def vms():
    return {vm['Name'] for vm in json.loads(tart('list', '--format', 'json').stdout)}


class Alive:
    def __init__(self, pid):
        self.pid = pid

    def poll(self):
        try:
            os.kill(self.pid, 0)
        except ProcessLookupError:
            return 0
        return None


class Guest:
    def __init__(self, name, shares, log, extra=(), recovery_vnc=False):
        self.name = name
        self.log = log
        dirs = [f'--dir={tag}:{path}' for tag, path in shares.items()]
        binary = shutil.which('tart') or str(Path.home() / '.local/bin/tart')
        vnc = ['--vnc-experimental'] if recovery_vnc else []
        self.process = subprocess.Popen(
            [binary, 'run', '--no-graphics', '--no-audio', *vnc, *extra, *dirs, name],
            env={**os.environ, 'TART_HOME': str(TART_HOME)},
            stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
        )
        self.ip = None
        self.forwarder = None

    @classmethod
    def attach(cls, name, log, process):
        guest = cls.__new__(cls)
        guest.name, guest.log, guest.process, guest.ip, guest.forwarder = name, log, process, None, None
        return guest

    def expose(self, args):
        found = tart('ip', self.name, '--wait', '180', check=False)
        if found.returncode:
            raise SystemExit(f'the VM never got an address; see {self.log.name}')
        self.ip = found.stdout.strip()
        port = pick_port(args.vnc_port or VNC_PORT, fixed=args.vnc_port is not None)
        self.forwarder = Forwarder(port, (self.ip, 5900))
        line = vnc_line(port)
        progress(line)
        output = Path(args.output).resolve() if args.output else VOLUME / 'runs'
        output.mkdir(parents=True, exist_ok=True)
        (output / 'vnc.txt').write_text(line + '\n')
        listeners = [exposed(subprocess.run(['lsof', '-nP', '-a', '-iTCP', '-sTCP:LISTEN', '-p', str(pid)],
                                            capture_output=True, text=True).stdout)
                     for pid in (self.process.pid, os.getpid())]
        if any(listeners):
            raise SystemExit(f'refusing to run with a listener off loopback: {", ".join(sum(listeners, []))}')

    def recovery(self):
        deadline = time.monotonic() + 60
        while not (endpoint := vnc_endpoint(Path(self.log.name).read_text(errors='replace'))):
            if self.process.poll() is not None or time.monotonic() > deadline:
                raise SystemExit(f'the recovery boot never reported its VNC server; see {self.log.name}')
            time.sleep(0.2)
        progress(recovery_line(*endpoint))
        return endpoint

    def boot(self, timeout=600, password=False):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise SystemExit(f'the VM exited during boot; see {self.log.name}')
            if not self.ip:
                found = tart('ip', self.name, '--wait', '5', check=False)
                self.ip = found.stdout.strip() if found.returncode == 0 else None
            if self.ip and self.answers(password):
                return self
            time.sleep(3)
        raise SystemExit(f'the VM did not accept SSH within {timeout}s')

    def answers(self, password=False):
        try:
            return self.ssh('true', check=False, timeout=15, password=password).returncode == 0
        except subprocess.TimeoutExpired:
            return False

    def ssh(self, command, check=True, timeout=None, password=False, stdout=None):
        options = ['-o', 'StrictHostKeyChecking=no', '-o', 'UserKnownHostsFile=/dev/null', '-o', 'LogLevel=ERROR',
                   '-o', 'ConnectTimeout=10', '-o', 'ServerAliveInterval=15']
        env = dict(os.environ)
        if password:
            askpass = Path(tempfile.mkdtemp()) / 'askpass'
            askpass.write_text(f'#!/bin/sh\necho {GUEST_PASSWORD}\n')
            askpass.chmod(0o700)
            env.update(SSH_ASKPASS=str(askpass), SSH_ASKPASS_REQUIRE='force', DISPLAY=':0')
            options += ['-o', 'PubkeyAuthentication=no', '-o', 'PreferredAuthentications=password,keyboard-interactive']
        else:
            options += ['-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes', '-i', str(KEY)]
        result = subprocess.run(['ssh', *options, f'{GUEST_USER}@{self.ip}', command], env=env, text=True,
                                stdin=subprocess.DEVNULL, stdout=stdout or subprocess.PIPE,
                                stderr=subprocess.STDOUT, timeout=timeout)
        if check and result.returncode:
            raise SystemExit(f'guest command failed ({result.returncode}): {command}\n{result.stdout or ""}')
        return result

    def stop(self):
        if self.process.poll() is None:
            tart('stop', self.name, '--timeout', '60', check=False)
            try:
                self.process.wait(timeout=90)
            except subprocess.TimeoutExpired:
                self.process.kill()
        if self.forwarder:
            self.forwarder.close()


def build_environment(xcode, environ=os.environ):
    return {**environ, 'DEVELOPER_DIR': str(xcode / 'Contents/Developer')}


def build(project, log, xcode=XCODE):
    environment = build_environment(xcode)
    if project.prepare and project.prepare_when_missing and not project.prepare_when_missing.exists():
        subprocess.run(project.prepare, cwd=project.root, env=environment, check=True)
    progress(f'building {project.name} for testing')
    started = time.monotonic()
    with vm_status.current.step('host build'), log.open('w') as handle:
        result = subprocess.run(project.build + ['build-for-testing'], cwd=project.root, env=environment,
                                stdout=handle, stderr=subprocess.STDOUT)
    if result.returncode:
        errors = [line for line in log.read_text().splitlines() if 'error:' in line]
        raise SystemExit('\n'.join(errors[-20:]) + f'\nbuild-for-testing failed; log {log}')
    progress(f'host build-for-testing: {time.monotonic() - started:.0f}s')
    settings = subprocess.run(project.build + ['-showBuildSettings'], cwd=project.root, env=environment,
                              capture_output=True, text=True, check=True).stdout
    values = dict(line.strip().partition(' = ')[::2] for line in settings.splitlines() if ' = ' in line)
    return Path(values['BUILD_DIR'])


def stage(project, products, target, extras, node=None):
    runs = sorted(products.glob(project.xctestrun), key=lambda path: path.stat().st_mtime)
    if not runs:
        raise SystemExit(f'no {project.xctestrun} in {products}')
    shutil.copy2(runs[-1], target / 'Run.xctestrun')
    for name in project.products:
        source = products / name
        if source.exists():
            subprocess.run(['ditto', str(source), str(target / name)], check=True)
    if any(extra.node for extra in extras):
        node = node or shutil.which('node')
        if not node:
            raise SystemExit('this suite needs node on the host to copy into the guest')
        shutil.copy2(Path(node).resolve(), target / 'node')
    for extra in extras:
        for name in extra.files:
            source = project.root / name
            subprocess.run(['ditto', str(source), str(target / 'files' / source.name)], check=True)


def export(result, output):
    attachments = output / 'attachments'
    subprocess.run(['xcrun', 'xcresulttool', 'export', 'attachments', '--path', str(result),
                    '--output-path', str(attachments)], check=True, stdout=subprocess.DEVNULL)
    manifest = json.loads((attachments / 'manifest.json').read_text())
    named = []
    for test in manifest:
        for attachment in test.get('attachments', []):
            source = attachments / attachment['exportedFileName']
            human = Path(attachment.get('suggestedHumanReadableName') or source.name).stem
            stem = re.sub(r'_\d+_[0-9A-F-]{36}$', '', human).replace('/', '-')
            target = output / f'{stem}{source.suffix}'
            shutil.move(source, target)
            named.append(target.name)
    shutil.rmtree(attachments)
    summary = json.loads(subprocess.run(['xcrun', 'xcresulttool', 'get', 'test-results', 'summary', '--path',
                                         str(result)], capture_output=True, text=True, check=True).stdout)
    return summary, sorted(named)


def collect(out, output, started, status, extras=()):
    for item in ['test.log', 'Result.xcresult', *extras, *[f'{name}.log' for name in extras]]:
        if (out / item).exists():
            shutil.move(out / item, output / item)
    result = output / 'Result.xcresult'
    if not result.exists():
        progress(f'the guest produced no result bundle; see {output / "test.log"}')
        return 1
    summary, files = export(result, output)
    print(json.dumps({
        'result': summary.get('result'),
        'passed': summary.get('passedTests'),
        'failed': summary.get('failedTests'),
        'failures': [f"{item.get('testName')}: {item.get('failureText')}" for item in summary.get('testFailures', [])],
        'seconds': round(time.monotonic() - started, 1),
        'output': str(output),
        'attachments': len(files),
    }, indent=1, ensure_ascii=False), flush=True)
    return 0 if status == 0 and summary.get('result') == 'Passed' else 1


def test_command(project, cases, host_xcode=False):
    only = [f'-only-testing:{target}' for target in project.always]
    if cases:
        only += [f'-only-testing:{project.suite}/{project.cases[case]}' for case in cases]
    else:
        only.append(f'-only-testing:{project.suite}')
    developer = [f"export DEVELOPER_DIR='{SHARED_XCODE}/Contents/Developer';"] if host_xcode else []
    return ' '.join([
        *developer,
        f"rm -rf ~/run && mkdir ~/run && (cd '{SHARED}/products' && tar --no-mac-metadata -cf - .) | (cd ~/run && tar -xpf -) &&",
        'xcodebuild test-without-building -xctestrun ~/run/Run.xctestrun -destination platform=macOS',
        '-resultBundlePath ~/run/Result.xcresult', *only,
        f"> '{SHARED}/out/test.log' 2>&1; code=$?;",
        f"(cd ~/run && tar --no-mac-metadata -cf - Result.xcresult) | (cd '{SHARED}/out' && tar -xf -); exit $code",
    ])


def extra_command(extra, cases):
    command = extra.command.format(out=f"'{SHARED}/out'", cases=' '.join(f'--case {case}' for case in cases))
    return f"{command} > '{SHARED}/out/{extra.name}.log' 2>&1"


def run_tests(guest, project, args, extras=()):
    progress('running XCUITest in the guest')
    started = time.monotonic()
    with vm_status.current.step('XCUITest'):
        finished = guest.ssh(test_command(project, args.case, args.host_xcode), check=False, timeout=args.timeout)
    progress(f'guest XCUITest: {time.monotonic() - started:.0f}s')
    if finished.stdout.strip():
        progress(finished.stdout.strip())
    status = finished.returncode
    for extra in extras:
        progress(f'running {extra.name} in the guest')
        with vm_status.current.step(extra.name):
            outcome = guest.ssh(extra_command(extra, args.case), check=False, timeout=args.timeout).returncode
        status = status or outcome
    return finished.returncode, status
