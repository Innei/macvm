from pathlib import Path
import tempfile
import unittest

from macvm import config


class ConfigTest(unittest.TestCase):
    def test_defaults_apply_without_a_file(self):
        settings = config.load(Path('/nonexistent/macvm.toml'))
        self.assertEqual(settings.volume, Path('/Volumes/MacVM'))
        self.assertEqual(settings.bundle, Path('/Volumes/WD/lody-vm.sparsebundle'))
        self.assertEqual(settings.base, 'macos-27.2')
        self.assertEqual(settings.xcode, Path('/Applications/Xcode.app'))
        self.assertEqual((settings.cpu, settings.memory, settings.display), (6, 16, '1680x1050'))
        self.assertEqual((settings.idle_minutes, settings.vnc_port), (30, 5905))

    def test_file_overrides_selected_keys(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'config.toml'
            path.write_text('volume = "/Volumes/Other"\ncpu = 8\nxcode = "/Applications/Xcode-beta.app"\n')
            settings = config.load(path)
        self.assertEqual(settings.volume, Path('/Volumes/Other'))
        self.assertEqual(settings.cpu, 8)
        self.assertEqual(settings.xcode, Path('/Applications/Xcode-beta.app'))
        self.assertEqual(settings.base, 'macos-27.2')

    def test_unknown_key_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'config.toml'
            path.write_text('volumes = "/Volumes/Typo"\n')
            with self.assertRaisesRegex(SystemExit, 'volumes'):
                config.load(path)

    def test_environment_names_the_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'elsewhere.toml'
            path.write_text('base = "macos-28.0"\n')
            self.assertEqual(config.default_path({'MACVM_CONFIG': str(path)}), path)
        self.assertEqual(config.default_path({}), Path.home() / '.config/macvm/config.toml')


if __name__ == '__main__':
    unittest.main()
