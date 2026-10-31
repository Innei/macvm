from pathlib import Path
import unittest

from macvm import cli, migrate, runner

FIXTURE = Path(__file__).parent / 'fixtures/lody.toml'


class ExtraAliasTest(unittest.TestCase):
    def test_each_extra_becomes_its_own_flag(self):
        arguments = cli.parse(['run', '--project', str(FIXTURE), '--in-process', '--case', 'sidebar'])
        self.assertEqual([extra.name for extra in arguments.extras], ['in-process'])
        self.assertEqual(arguments.case, ['sidebar'])

    def test_exec_keeps_everything_after_the_separator(self):
        arguments = cli.parse(['exec', '--project', str(FIXTURE), '--', 'cliclick', 'c:400,300', '--help'])
        self.assertEqual(arguments.remote, ['cliclick', 'c:400,300', '--help'])


class ExecTest(unittest.TestCase):
    def test_remote_command_has_homebrew_and_wakes_the_display(self):
        command = runner.exec_command(['peekaboo', 'see', '--app', "Lody's window"])
        self.assertIn('eval "$(/opt/homebrew/bin/brew shellenv)"', command)
        self.assertIn('caffeinate -u -t 2', command)
        self.assertTrue(command.endswith("peekaboo see --app 'Lody'\"'\"'s window'"))


class ScreenshotTest(unittest.TestCase):
    def test_default_path_lands_in_the_project_output(self):
        path = cli.screenshot_path(None, Path('/p/.artifacts/mac-verify-vm'), now=lambda: 0)
        self.assertEqual(path.parent, Path('/p/.artifacts/mac-verify-vm'))
        self.assertTrue(path.name.startswith('screenshot-') and path.suffix == '.png')
        self.assertEqual(cli.screenshot_path('shot.png', Path('/p'), now=lambda: 0), Path('shot.png').resolve())

    def test_the_port_comes_from_the_runner_vnc_line(self):
        self.assertEqual(runner.vnc_port('VNC: vnc://admin@localhost:5907  login admin / admin.'), 5907)
        with self.assertRaises(SystemExit):
            runner.vnc_port(None)


class MigratePlanTest(unittest.TestCase):
    def test_renames_volume_vms_and_runner_folder(self):
        operations = migrate.plan(volume_name='LodyVM', base='lody-macos-27.2', runner='lody-verify-runner',
                                  project='lody', vms={'lody-macos-27.2', 'lody-verify-runner'},
                                  target_volume='MacVM', target_base='macos-27.2')
        self.assertEqual(operations, [
            ('stop-all',),
            ('rename-vm', 'lody-macos-27.2', 'macos-27.2'),
            ('rename-vm', 'lody-verify-runner', 'lody-runner'),
            ('move', 'runner', 'runners/lody'),
            ('move', 'runs/lody-macos-27.2.ready', 'runs/macos-27.2.ready'),
            ('record', ['system', 'xcode', 'runtime', 'screen-sharing', 'services', 'desktop']),
            ('rename-volume', 'LodyVM', 'MacVM'),
        ])

    def test_is_a_no_op_once_migrated(self):
        self.assertEqual(migrate.plan(volume_name='MacVM', base='lody-macos-27.2', runner='lody-verify-runner',
                                      project='lody', vms={'macos-27.2', 'lody-runner'},
                                      target_volume='MacVM', target_base='macos-27.2'), [])



class HelpTest(unittest.TestCase):
    def test_help_lists_the_project_extras(self):
        import contextlib, io
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
            cli.parse(['run', '--project', str(FIXTURE), '--help'])
        self.assertIn('--in-process', out.getvalue())



class HelpOutsideProjectTest(unittest.TestCase):
    def test_help_works_without_a_project(self):
        import contextlib, io, os, tempfile
        here = os.getcwd()
        os.chdir(tempfile.mkdtemp())
        self.addCleanup(os.chdir, here)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as raised:
            cli.parse(['--help'])
        self.assertEqual(raised.exception.code, 0)
        self.assertIn('usage: macvm', out.getvalue())


if __name__ == '__main__':
    unittest.main()
