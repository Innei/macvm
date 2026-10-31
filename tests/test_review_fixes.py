import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from macvm import base, cli, grants, guest, project, runner, steps

FIXTURE = Path(__file__).parent / 'fixtures/lody.toml'
LODY = project.load(FIXTURE)


class OutputDefaultTest(unittest.TestCase):
    def test_expose_without_an_output_writes_the_vnc_line_beside_the_runs(self):
        machine = guest.Guest.__new__(guest.Guest)
        machine.name, machine.process, machine.log = 'probe', SimpleNamespace(pid=1), SimpleNamespace(name='x')
        root = Path(tempfile.mkdtemp())
        with mock.patch.object(guest, 'tart', lambda *a, **k: SimpleNamespace(returncode=0, stdout='192.0.2.1\n')), \
                mock.patch.object(guest, 'Forwarder', lambda *a: None), \
                mock.patch.object(guest, 'pick_port', lambda *a, **k: 5999), \
                mock.patch.object(guest, 'VOLUME', root), \
                mock.patch.object(guest.subprocess, 'run', lambda *a, **k: SimpleNamespace(stdout='')):
            machine.expose(cli.parser.parse_args(['prepare']))
        self.assertIn('5999', (root / 'runs/vnc.txt').read_text())


class LeftoverNextTest(unittest.TestCase):
    def test_a_leftover_next_is_resumed_even_without_a_base(self):
        self.assertEqual(steps.plan(base=False, next_vm=True, force=False, todo=[]), 'resume')

    def test_force_still_reinstalls(self):
        self.assertEqual(steps.plan(base=False, next_vm=True, force=True, todo=[]), 'install')


class SwapOrderTest(unittest.TestCase):
    def test_the_old_base_moves_aside_before_next_takes_its_name(self):
        calls, root = [], Path(tempfile.mkdtemp())
        (root / 'base-next.json').write_text('{}')
        with mock.patch.object(base, 'tart', lambda *a, **k: calls.append(a)), \
                mock.patch.object(base, 'vms', lambda: {base.BASE, base.NEXT}), \
                mock.patch.object(base, 'exclusive', mock.MagicMock()), \
                mock.patch.object(base, 'NEXT_MARKS', root / 'base-next.json'), \
                mock.patch.object(base, 'MARKS', root / 'base.json'), \
                mock.patch.object(base, 'READY', root / 'ready'):
            base.swap()
        self.assertEqual(calls, [('rename', base.BASE, f'{base.BASE}-old'), ('rename', base.NEXT, base.BASE),
                                 ('delete', f'{base.BASE}-old')])


class FailedUpdateTest(unittest.TestCase):
    def test_a_failed_update_warns_and_runs_on_the_existing_base(self):
        notices = []

        def broken(args):
            raise SystemExit('desktop failed')

        cli.update_base(cli.parser.parse_args(['run']), outdated=lambda args: ['desktop'], prepare=broken,
                        notice=notices.append)
        self.assertIn('desktop failed', notices[-1])
        self.assertIn('existing', notices[-1])


class RunnerFolderTest(unittest.TestCase):
    def test_runner_folders_are_created_with_parents(self):
        root = Path(tempfile.mkdtemp()) / 'fresh-volume'
        with mock.patch.object(runner, 'VOLUME', root):
            runner.use('notes')
            self.addCleanup(runner.use, 'default')
            runner.prepare_home()
        self.assertTrue((root / 'runners/notes').is_dir())


class GrantsFailLoudTest(unittest.TestCase):
    def test_the_base_grants_fail_when_no_user_database_is_found(self):
        script = grants.base_script()
        self.assertIn('set -e', script)
        self.assertIn('no per-user TCC database', script)

    def test_the_base_grants_confirm_a_row_landed(self):
        self.assertIn("select count(*) from access where client='/usr/libexec/sshd-keygen-wrapper'", grants.base_script())


class GlobResetTest(unittest.TestCase):
    def test_reset_survives_a_glob_that_matches_nothing(self):
        command = runner.reset_command(LODY)
        self.assertTrue(command.startswith('setopt nonomatch 2>/dev/null; '))
        home = Path(tempfile.mkdtemp())
        (home / 'Library/Application Support/app.innei.lody.mac').mkdir(parents=True)
        script = command.split('; ', 1)[1].replace('pkill', ': pkill').replace('defaults delete', ': defaults delete')
        subprocess.run(['zsh', '-c', 'setopt nonomatch 2>/dev/null; ' + script], env={'HOME': str(home), 'PATH': '/usr/bin:/bin'},
                       check=False)
        self.assertFalse((home / 'Library/Application Support/app.innei.lody.mac').exists())


class UpWithoutLockTest(unittest.TestCase):
    def test_up_attaches_when_a_run_holds_the_lock(self):
        root = Path(tempfile.mkdtemp())
        with mock.patch.object(runner, 'VOLUME', root):
            runner.use('notes')
            self.addCleanup(runner.use, 'default')
            runner.prepare_home()
            runner.VNC.write_text('VNC: vnc://admin@localhost:5907\n')
            calls = []
            with guest.exclusive(runner.RUN_LOCK), \
                    mock.patch.object(runner, 'ensure', lambda: calls.append('ensure')), \
                    mock.patch.object(runner, 'launch', lambda args: calls.append('launch') or SimpleNamespace(poll=lambda: None)), \
                    mock.patch.object(runner.Guest, 'boot', lambda self: self):
                runner.up(SimpleNamespace(vnc_port=None))
        self.assertEqual(calls, ['launch'])


class SlotLockTest(unittest.TestCase):
    def test_counting_and_starting_happen_under_one_lock(self):
        self.assertEqual(runner.SLOTS_LOCK, guest.VOLUME / 'runs/slots.lock')



class NoRestoredWindowsTest(unittest.TestCase):
    def test_first_launch_quits_devicehub_instead_of_killing_it(self):
        self.assertIn('quit app id "com.apple.dt.Devices"', base.FIRST_LAUNCH)
        self.assertNotIn('pkill -x DeviceHub || true\n', base.FIRST_LAUNCH.replace('sleep 5; pkill -x DeviceHub || true', ''))

    def test_logins_do_not_reopen_apps(self):
        self.assertIn('defaults write com.apple.loginwindow TALLogoutSavesState -bool false', base.DESKTOP)
        self.assertIn('defaults write com.apple.loginwindow LoginwindowLaunchesRelaunchApps -bool false', base.DESKTOP)


if __name__ == '__main__':
    unittest.main()
