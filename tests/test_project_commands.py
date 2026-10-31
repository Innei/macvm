from pathlib import Path
import shutil
import tempfile
import unittest

from macvm import guest, project, runner

LODY = project.load(Path(__file__).parent / 'fixtures/lody.toml')


class ProjectCommandTest(unittest.TestCase):
    def test_tests_run_from_the_staged_xctestrun_into_a_neutral_result(self):
        command = guest.test_command(LODY, ['sidebar'])
        self.assertIn('-xctestrun ~/run/Run.xctestrun', command)
        self.assertIn('-resultBundlePath ~/run/Result.xcresult', command)
        self.assertIn('-only-testing:LodyMacUITests/SidebarTests', command)
        self.assertNotIn('LodyMac.xcresult', command)

    def test_reset_always_stops_xctest_and_clears_the_run_folder(self):
        bare = project.load(self.minimal())
        command = runner.reset_command(bare)
        self.assertIn('pkill -9 -x xctest', command)
        self.assertIn('[x]codebuild test-without-building', command)
        self.assertIn('rm -rf ~/run', command)
        self.assertNotIn('Lody', command)

    def test_reset_quotes_paths_with_spaces_but_keeps_tilde_and_globs(self):
        command = runner.reset_command(LODY)
        self.assertIn(' ~/"Library/Application Support/app.innei.lody.mac" ', command)
        self.assertIn('~/"Library/Containers/app.innei.lody.mac"*', command)

    def test_extra_command_fills_output_and_cases(self):
        command = guest.extra_command(LODY.extras[0], ['sidebar', 'sign-in'])
        self.assertIn("--output '/Volumes/My Shared Files/out'/in-process", command)
        self.assertIn('--case sidebar --case sign-in', command)
        self.assertTrue(command.endswith("> '/Volumes/My Shared Files/out/in-process.log' 2>&1"))

    def test_host_build_pins_the_settings_xcode(self):
        environment = guest.build_environment(Path('/Applications/Xcode-27.app'), {'PATH': '/bin'})
        self.assertEqual(environment['DEVELOPER_DIR'], '/Applications/Xcode-27.app/Contents/Developer')
        self.assertEqual(environment['PATH'], '/bin')

    def test_stage_copies_the_newest_xctestrun_products_and_extra_files(self):
        products = Path(tempfile.mkdtemp())
        target = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, products)
        self.addCleanup(shutil.rmtree, target)
        (products / 'LodyMac_old.xctestrun').write_text('old')
        newest = products / 'LodyMac_new.xctestrun'
        newest.write_text('new')
        (products / 'Debug/Lody.app/Contents').mkdir(parents=True)
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root)
        for name in ['run.mts', 'lib/a.mts', 'cases/b.mts']:
            (root / 'verification/mac' / name).parent.mkdir(parents=True, exist_ok=True)
            (root / 'verification/mac' / name).write_text(name)
        import os
        os.utime(products / 'LodyMac_old.xctestrun', (1, 1))
        staged = project.Project(**{**LODY.__dict__, 'root': root})
        guest.stage(staged, products, target, [staged.extras[0]], node=__import__('sys').executable)
        self.assertEqual((target / 'Run.xctestrun').read_text(), 'new')
        self.assertTrue((target / 'Debug/Lody.app/Contents').is_dir())
        self.assertEqual((target / 'files/run.mts').read_text(), 'run.mts')
        self.assertTrue((target / 'files/lib/a.mts').exists())
        self.assertTrue((target / 'node').exists())

    def minimal(self):
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder)
        path = folder / 'macvm.toml'
        path.write_text('name = "x"\n[build]\ncommand=["true"]\nxctestrun="*.xctestrun"\nproducts=[]\n[test]\nsuite="T"\n')
        return path


if __name__ == '__main__':
    unittest.main()
