"""Run a Mac app's XCUITest suite inside a headless macOS VM.

The base VM is shared by every project; each project keeps a warm runner cloned from it. A run
builds for testing on the host, boots the project's runner (or attaches to it), resets the app's
state, runs test-without-building over SSH and exports the result bundle and its screenshots.
The project describes how to build, which cases exist and what to reset in macvm.toml.

    macvm run [--case NAME] [--EXTRA]   # build, test in the runner, export results
    macvm prepare [--recovery-vnc]      # build or update the shared base (incremental)
    macvm status [--follow]             # base, runners, lock, current run, history
    macvm watch                         # open Screen Sharing for this project's runner
    macvm up | exec -- CMD | screenshot # drive the runner directly (cliclick, peekaboo)
    macvm reset                         # delete this project's runner; the next run reclones

The host's screen, pointer and keyboard are never touched. Global settings live in
~/.config/macvm/config.toml (MACVM_CONFIG overrides the path).
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import shutil
import sys
import time

from .base import outdated, prepare, warm_up
from .guest import (SETTINGS, BASE, SLOTS_LOCK, HISTORY, STATUS, VOLUME, XCODE, Guest, build, collect, exclusive, mount,
                      progress, run_tests, stage, tart, vms)
from .fleet import free_slot
from .grants import app_script
from . import migrate
from . import project as projects
from . import runner as vm_runner
from . import status as vm_status
from .vnc import VNC_PORT, hold

def run(args):
    if not shutil.which('tart') and not (Path.home() / '.local/bin/tart').exists():
        raise SystemExit('tart is not installed; put the signed release from github.com/cirruslabs/tart on PATH')
    args.project.check_cases(args.case)
    mount()
    if BASE not in vms():
        raise SystemExit(f'no base VM {BASE}; run macvm prepare first')
    update_base(args)
    output = Path(args.output or args.project.output).resolve()
    shutil.rmtree(output, ignore_errors=True)
    output.mkdir(parents=True)
    raise SystemExit(run_fresh(args, output) if args.fresh or args.host_xcode else vm_runner.run(args, output))


def update_base(args, outdated=outdated, prepare=prepare, notice=progress):
    todo = outdated(args)
    if not todo:
        return
    if args.no_update:
        notice(f'{BASE} is out of date ({", ".join(todo)}); running on it anyway because of --no-update')
        return
    notice(f'{BASE} is out of date ({", ".join(todo)}); updating it before the run')
    try:
        prepare(args)
    except (SystemExit, Exception) as failure:
        notice(f'updating {BASE} failed ({failure}); running on the existing base')


PROJECT_COMMANDS = {'run', 'reset', 'watch', 'status', 'up', 'exec', 'screenshot'}


def load_project(args):
    path = Path(args.project_path).resolve() if args.project_path else projects.find(Path.cwd())
    return projects.load(path)


def parse(argv):
    remote = []
    if '--' in argv:
        argv, remote = argv[:argv.index('--')], argv[argv.index('--') + 1:]
    known, _ = parser.parse_known_args([arg for arg in argv if arg not in ('-h', '--help')])
    if known.command not in PROJECT_COMMANDS:
        arguments = parser.parse_args(argv)
        arguments.remote = remote
        return arguments
    try:
        project = load_project(known)
    except SystemExit:
        if '-h' in argv or '--help' in argv:
            parser.parse_args(argv)
        raise
    full = build_parser(project.extras)
    arguments = full.parse_args(argv)
    arguments.remote = remote
    arguments.extra += [extra.name for extra in project.extras if getattr(arguments, flag_dest(extra.name))]
    return attach_project(arguments, project)


def attach_project(args, project=None):
    args.project = project or load_project(args)
    args.tcc_override = args.tcc_bypass is not None
    if args.tcc_bypass is None:
        args.tcc_bypass = args.project.grants.bypass
    vm_runner.use(args.project.name)
    known = {extra.name: extra for extra in args.project.extras}
    unknown = [name for name in args.extra if name not in known]
    if unknown:
        raise SystemExit(f'unknown extra {", ".join(unknown)}; choose from {", ".join(known) or "none"}')
    args.extras = [known[name] for name in dict.fromkeys(args.extra)]
    return args


def extra_log(args, folder):
    return str(folder / f'{args.extras[0].name}.log') if args.extras else None


def reset(args):
    mount()
    vm_runner.reset()


def watch(args):
    mount()
    vm_runner.watch(args)


def status(args):
    mount()
    vm_runner.status(args)


def up(args):
    mount()
    vm_runner.up(args)


def exec_remote(args):
    if not args.remote:
        raise SystemExit('nothing to run; use macvm exec -- <command>')
    mount()
    raise SystemExit(vm_runner.exec_remote(args))


def screenshot_path(given, output, now=time.time):
    if given:
        return Path(given).resolve()
    return output / f'screenshot-{time.strftime("%Y%m%d-%H%M%S", time.localtime(now()))}.png'


def screenshot(args):
    mount()
    vm_runner.screenshot(args, screenshot_path(args.target, args.project.output))


def run_fresh(args, output):
    name = f'{args.project.name}-fresh-{os.getpid()}'
    work = VOLUME / 'runs' / name
    (work / 'products').mkdir(parents=True)
    (work / 'out').mkdir()
    vm_status.current = vm_status.Progress(STATUS, HISTORY)
    vm_status.current.begin('fresh', args.case, output, project=args.project.name, log=str(work / 'out/test.log'),
                            in_process_log=extra_log(args, work / 'out'))
    with exclusive():
        tart('clone', BASE, name)
    log = (work / 'tart.log').open('w')
    shares = {'products': work / 'products', 'out': work / 'out'}
    if args.host_xcode:
        shares['Xcode.app'] = f'{XCODE}:ro'
    with exclusive(SLOTS_LOCK):
        free_slot(name)
        guest = Guest(name, shares, log)
    started = time.monotonic()
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        progress(f'booting {name} headless while the host builds')
        ready = pool.submit(lambda: (guest.expose(args), warm_up(guest, args.host_xcode),
                                     guest.ssh(app_script(args.project), check=False, timeout=60)))
        stage(args.project, build(args.project, output / 'build.log'), work / 'products', args.extras)
        ready.result()
        _, outcome = run_tests(guest, args.project, args, args.extras)
        code = collect(work / 'out', output, started, outcome, [extra.name for extra in args.extras])
        if args.hold:
            hold(guest.process)
    except BaseException as failure:
        vm_status.current.end(vm_status.failure(failure))
        raise
    finally:
        guest.stop()
        pool.shutdown()
        tart('delete', name, check=False)
        log.close()
        shutil.move(work / 'tart.log', output / 'tart.log')
        shutil.rmtree(work, ignore_errors=True)
    vm_status.current.end('passed' if code == 0 else 'failed')
    return code


COMMANDS = ['run', 'prepare', 'reset', 'watch', 'status', 'up', 'exec', 'screenshot', 'migrate']


def flag_dest(name):
    return 'extra_' + name.replace('-', '_')


def build_parser(extras=()):
    parser = argparse.ArgumentParser(prog='macvm', description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', nargs='?', default='run', choices=COMMANDS)
    parser.add_argument('target', nargs='?', help='screenshot: where to save the PNG')
    parser.add_argument('--case', action='append', default=[], help="a case name from macvm.toml's [test.cases]; repeatable")
    parser.add_argument('--output', help="results folder (default: macvm.toml's [output].dir)")
    parser.add_argument('--project', dest='project_path', help='path to macvm.toml (default: nearest one above the current folder)')
    parser.add_argument('--extra', action='append', default=[], help='also run a [[extra]] suite from macvm.toml; repeatable')
    parser.add_argument('--vnc-port', type=int, help=f'fixed loopback port for the VNC viewer (default {VNC_PORT}, '
                        'or the next free one)')
    parser.add_argument('--follow', action='store_true', help='status: redraw every second until Ctrl-C')
    parser.add_argument('--print-only', action='store_true', help='watch: print the viewer URL instead of opening it')
    parser.add_argument('--hold', action='store_true', help='keep the VM and VNC up after the run until Ctrl-C')
    parser.add_argument('--fresh', action='store_true', help='run in a disposable clone instead of the warm runner')
    parser.add_argument('--timeout', type=int, default=1800, help='seconds allowed for the guest test run')
    parser.add_argument('--host-xcode', action='store_true',
                        help="run the host's Xcode from a read-only share in a disposable clone (fallback)")
    parser.add_argument('--force', action='store_true', help='prepare: reinstall and reprovision the base VM')
    parser.add_argument('--repair-xcode', action='store_true', help='prepare: copy Xcode into the base again')
    parser.add_argument('--upgrade-tools', action='store_true', help='prepare: brew upgrade the base tools')
    parser.add_argument('--no-update', action='store_true', help='run: skip updating an out-of-date base')
    tcc = parser.add_mutually_exclusive_group()
    tcc.add_argument('--tcc-bypass', dest='tcc_bypass', action='store_true', default=None,
                     help='temporarily bypass TCC for every process inside the guest')
    tcc.add_argument('--no-tcc-bypass', dest='tcc_bypass', action='store_false',
                     help='disable the guest TCC bypass for permission-flow tests')
    parser.add_argument('--recovery-vnc', action='store_true',
                        help="prepare: allow the recovery boot's LAN-reachable VNC server to turn SIP off")
    parser.add_argument('--cpu', type=int, default=SETTINGS.cpu)
    parser.add_argument('--memory', type=int, default=SETTINGS.memory, help='GB')
    parser.add_argument('--display', default=SETTINGS.display)
    for extra in extras:
        parser.add_argument(f'--{extra.name}', dest=flag_dest(extra.name), action='store_true',
                            help=extra.help or f'also run the {extra.name} suite from macvm.toml')
    return parser


parser = build_parser()


def main(argv=None):
    arguments = parse(sys.argv[1:] if argv is None else argv)
    {'prepare': prepare, 'reset': reset, 'run': run, 'watch': watch, 'status': status, 'up': up,
     'exec': exec_remote, 'screenshot': screenshot, 'migrate': migrate.run}[arguments.command](arguments)


if __name__ == '__main__':
    main()
