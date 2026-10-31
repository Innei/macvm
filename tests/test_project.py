from pathlib import Path
import shutil
import tempfile
import unittest

from macvm import project

FIXTURE = Path(__file__).parent / 'fixtures/lody.toml'


class ProjectTest(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder)

    def write(self, text):
        path = self.folder / 'macvm.toml'
        path.write_text(text)
        return path

    def test_find_walks_up_to_the_nearest_config(self):
        shutil.copy(FIXTURE, self.folder / 'macvm.toml')
        nested = self.folder / 'verification/mac'
        nested.mkdir(parents=True)
        self.assertEqual(project.find(nested), self.folder / 'macvm.toml')

    def test_find_without_config_explains(self):
        with self.assertRaisesRegex(SystemExit, 'macvm.toml'):
            project.find(self.folder)

    def test_lody_example_parses(self):
        shutil.copy(FIXTURE, self.folder / 'macvm.toml')
        loaded = project.load(self.folder / 'macvm.toml')
        self.assertEqual(loaded.name, 'lody')
        self.assertEqual(loaded.root, self.folder)
        self.assertEqual(loaded.runner, 'lody-runner')
        self.assertEqual(loaded.cases, {'sign-in': 'SignInTests', 'sidebar': 'SidebarTests'})
        self.assertEqual(loaded.always, ['LodyMacTests'])
        self.assertEqual(loaded.suite, 'LodyMacUITests')
        self.assertEqual(loaded.timeout, 1800)
        self.assertEqual(loaded.reset.processes, ['Lody'])
        self.assertEqual(loaded.grants.apps, {'app.innei.lody.mac': []})
        self.assertEqual(loaded.grants.targets, ['app.innei.lody.mac'])
        self.assertEqual([extra.name for extra in loaded.extras], ['in-process'])
        self.assertTrue(loaded.extras[0].node)
        self.assertEqual(loaded.output, self.folder / '.artifacts/mac-verify-vm')
        self.assertEqual(loaded.prepare_when_missing, self.folder / 'Lody.xcodeproj')

    def test_name_must_be_a_vm_safe_slug(self):
        with self.assertRaisesRegex(SystemExit, 'name'):
            project.load(self.write('name = "Lody App"\n[build]\ncommand=["true"]\nxctestrun="*.xctestrun"\n'
                                    'products=[]\n[test]\nsuite="T"\n'))

    def test_unknown_case_is_rejected(self):
        shutil.copy(FIXTURE, self.folder / 'macvm.toml')
        loaded = project.load(self.folder / 'macvm.toml')
        with self.assertRaisesRegex(SystemExit, 'unknown case nope; choose from sign-in, sidebar'):
            loaded.check_cases(['nope'])

    def test_grants_name_only_known_services(self):
        with self.assertRaisesRegex(SystemExit, 'teleport'):
            project.load(self.write('name = "x"\n[build]\ncommand=["true"]\nxctestrun="*.xctestrun"\nproducts=[]\n'
                                    '[test]\nsuite="T"\n[grants.apps]\n"a.b" = ["teleport"]\n'))

    def test_minimal_config_gets_defaults(self):
        loaded = project.load(self.write('name = "x"\n[build]\ncommand=["true"]\nxctestrun="*.xctestrun"\n'
                                         'products=[]\n[test]\nsuite="T"\n'))
        self.assertEqual((loaded.always, loaded.cases, loaded.extras, loaded.timeout), ([], {}, [], 1800))
        self.assertEqual(loaded.output, self.folder / '.artifacts/macvm')
        self.assertEqual(loaded.grants.apps, {})


if __name__ == '__main__':
    unittest.main()
