from pathlib import Path
from contextlib import nullcontext
import os
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from macvm import cli, guest, project, runner

FIXTURE = Path(__file__).parent / 'fixtures/lody.toml'


class TCCModeTest(unittest.TestCase):
    def test_explicit_manual_override_cannot_change_an_active_tests_tcc_mode(self):
        args = cli.parse(['exec', '--project', str(FIXTURE), '--no-tcc-bypass', '--', 'true'])
        with mock.patch.object(runner, 'prepare_home'), \
             mock.patch.object(runner, 'attempt', return_value=nullcontext(False)), \
             mock.patch.object(runner, 'launch', side_effect=AssertionError('must not attach with an override')):
            with self.assertRaisesRegex(SystemExit, 'TCC.*busy'):
                runner.up(args)

    def test_project_can_enable_bypass_and_command_line_can_disable_it(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'macvm.toml'
            path.write_text(FIXTURE.read_text().replace('[grants]', '[grants]\nbypass = true'))
            loaded = project.load(path)
            self.assertTrue(getattr(loaded.grants, 'bypass', False))
            args = cli.parse(['run', '--project', str(path), '--no-tcc-bypass'])
            self.assertFalse(args.tcc_bypass)

    def test_invalid_bypass_setting_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'macvm.toml'
            path.write_text(FIXTURE.read_text().replace('[grants]', '[grants]\nbypass = "false"'))
            with self.assertRaisesRegex(SystemExit, 'bypass.*boolean'):
                project.load(path)

    def test_unconfigured_project_uses_normal_tcc_unless_flag_enables_bypass(self):
        args = cli.parse(['run', '--project', str(FIXTURE)])
        self.assertFalse(getattr(args, 'tcc_bypass', True))
        args = cli.parse(['run', '--project', str(FIXTURE), '--tcc-bypass'])
        self.assertTrue(args.tcc_bypass)


class TCCScriptTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.tool = self.root / 'tcc tool'
        self.state = self.root / 'state'
        self.tool.write_text('#!/bin/sh\ncase "$1" in\n'
                             'start) echo "$2" > "$TCC_STATE";;\n'
                             'stop) rm -f "$TCC_STATE"; '
                             'printf \'{"ok":false,"error":"%s"}\\n\' "${TCC_STOP_ERROR:-helper_unavailable}"; '
                             'exit "${TCC_STOP_CODE:-0}";;\nesac\n')
        self.tool.chmod(0o755)
        for name, text in {
            'sudo': '#!/bin/sh\n[ "$1" = -n ] && shift\nexec "$@"\n',
            'launchctl': '#!/bin/sh\nexit 0\n',
        }.items():
            path = self.root / name
            path.write_text(text)
            path.chmod(0o755)

    def run_script(self, enabled, **env):
        from macvm import tcc
        return subprocess.run(['/bin/sh', '-c', tcc.mode_script(enabled, 75, tool=str(self.tool))],
                              capture_output=True, text=True,
                              env={**os.environ, 'PATH': f'{self.root}:/usr/bin:/bin',
                                   'TCC_STATE': str(self.state), **env})

    def test_enable_renews_the_requested_duration(self):
        result = self.run_script(True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.state.read_text().strip(), '75')

    def test_switching_to_normal_mode_disables_an_existing_bypass(self):
        self.state.write_text('active')
        result = self.run_script(False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.state.exists())

    def test_normal_mode_accepts_an_absent_broker_but_surfaces_real_errors(self):
        self.assertEqual(self.run_script(False, TCC_STOP_CODE='69').returncode, 0)
        self.assertEqual(self.run_script(False, TCC_STOP_CODE='69', TCC_STOP_ERROR='helper_send_failed').returncode, 69)
        self.assertEqual(self.run_script(False, TCC_STOP_CODE='70').returncode, 70)

    def test_missing_installation_only_blocks_bypass_mode(self):
        self.tool.unlink()
        self.assertEqual(self.run_script(False).returncode, 0)
        result = self.run_script(True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('macvm prepare', result.stderr)

    def test_bypass_cannot_silently_expire_before_the_requested_test_finishes(self):
        from macvm import tcc
        with self.assertRaisesRegex(ValueError, '1440'):
            tcc.mode_script(True, 1441)

    def test_each_test_suite_gets_a_fresh_bypass_after_permission_reset(self):
        from macvm import tcc
        original = tcc.mode_script
        tests = self

        class Machine:
            def ssh(self, command, **_):
                if 'test-without-building' in command or 'extra-test' in command:
                    code = 0 if tests.state.exists() and tests.state.read_text().strip() == '75' else 73
                    tests.state.unlink(missing_ok=True)
                    return SimpleNamespace(returncode=code, stdout='')
                return subprocess.run(['/bin/sh', '-c', command], capture_output=True, text=True,
                                      env={**os.environ, 'PATH': f'{tests.root}:/usr/bin:/bin',
                                           'TCC_STATE': str(tests.state)}, check=True)

        args = SimpleNamespace(case=[], host_xcode=False, timeout=4200, tcc_bypass=True)
        extra = project.Extra(name='extra', command='extra-test')
        with mock.patch.object(tcc, 'mode_script', lambda enabled, minutes: original(enabled, minutes, str(self.tool))):
            result = guest.run_tests(Machine(), project.load(FIXTURE), args, [extra])
        self.assertEqual(result, (0, 0))


if __name__ == '__main__':
    unittest.main()
