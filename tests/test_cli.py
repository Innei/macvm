import unittest

from macvm import cli


class EntryPointTest(unittest.TestCase):
    def test_parser_accepts_status_follow(self):
        arguments = cli.parser.parse_args(['status', '--follow'])
        self.assertEqual(arguments.command, 'status')
        self.assertTrue(arguments.follow)



class AttachProjectTest(unittest.TestCase):
    FIXTURE = __import__('pathlib').Path(__file__).parent / 'fixtures/lody.toml'

    def test_in_process_selects_the_matching_extra(self):
        arguments = cli.parse(['run', '--project', str(self.FIXTURE), '--in-process'])
        self.assertEqual([extra.name for extra in arguments.extras], ['in-process'])
        self.assertEqual(arguments.project.name, 'lody')

    def test_unknown_extra_is_rejected(self):
        with self.assertRaisesRegex(SystemExit, 'unknown extra nope; choose from in-process'):
            cli.attach_project(cli.parser.parse_args(['run', '--project', str(self.FIXTURE), '--extra', 'nope']))



class RunnerSelectionTest(unittest.TestCase):
    def test_attaching_a_project_selects_its_runner(self):
        from macvm import runner
        self.addCleanup(runner.use, 'default')
        cli.attach_project(cli.parser.parse_args(['status', '--project', str(AttachProjectTest.FIXTURE)]))
        self.assertEqual(runner.RUNNER, 'lody-runner')



class AutoUpdateTest(unittest.TestCase):
    def arguments(self, *extra):
        return cli.parser.parse_args(['run', *extra])

    def test_a_stale_base_is_updated_before_the_run(self):
        prepared, notices = [], []
        cli.update_base(self.arguments(), outdated=lambda args: ['xcode', 'runtime'], prepare=prepared.append,
                        notice=notices.append)
        self.assertEqual(len(prepared), 1)
        self.assertIn('xcode, runtime', notices[0])

    def test_no_update_runs_on_the_old_base_with_a_warning(self):
        prepared, notices = [], []
        cli.update_base(self.arguments('--no-update'), outdated=lambda args: ['tools'], prepare=prepared.append,
                        notice=notices.append)
        self.assertEqual(prepared, [])
        self.assertIn('--no-update', notices[0])

    def test_a_current_base_is_not_touched(self):
        prepared = []
        cli.update_base(self.arguments(), outdated=lambda args: [], prepare=prepared.append, notice=print)
        self.assertEqual(prepared, [])

    def test_upgrade_tools_is_a_prepare_flag(self):
        self.assertTrue(cli.parser.parse_args(['prepare', '--upgrade-tools']).upgrade_tools)


if __name__ == '__main__':
    unittest.main()
