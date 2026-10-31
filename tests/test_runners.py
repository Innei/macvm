import json
from pathlib import Path
import tempfile
import time
import unittest

from macvm import guest, runner, status


class RunnerPathsTest(unittest.TestCase):
    def tearDown(self):
        runner.use('default')

    def test_each_project_gets_its_own_runner_and_folder(self):
        runner.use('lody')
        self.assertEqual(runner.RUNNER, 'lody-runner')
        self.assertEqual(runner.HOME, guest.VOLUME / 'runners/lody')
        self.assertEqual(runner.RUN_LOCK, guest.VOLUME / 'runners/lody/lock')
        self.assertEqual(runner.VNC, guest.VOLUME / 'runners/lody/vnc.txt')
        self.assertEqual(runner.STATUS, guest.VOLUME / 'runners/lody/status.json')
        runner.use('notes')
        self.assertEqual(runner.RUNNER, 'notes-runner')
        self.assertEqual(runner.HOME, guest.VOLUME / 'runners/notes')

    def test_the_base_lock_is_separate_from_runner_locks(self):
        runner.use('lody')
        self.assertEqual(guest.BASE_LOCK, guest.VOLUME / 'runs/base.lock')
        self.assertNotEqual(guest.BASE_LOCK, runner.RUN_LOCK)

    def test_two_projects_can_hold_their_run_locks_at_once(self):
        root = Path(tempfile.mkdtemp())
        with guest.exclusive(root / 'a.lock'):
            with guest.attempt(root / 'b.lock') as free:
                self.assertTrue(free)

    def test_keeper_command_names_its_project(self):
        runner.use('notes')
        self.assertEqual(runner.keeper_command(None)[-3:], ['keep', '--name', 'notes'])
        self.assertEqual(runner.keeper_command(5910)[-2:], ['--vnc-port', '5910'])


class HistoryProjectTest(unittest.TestCase):
    def test_history_rows_carry_and_show_the_project(self):
        root = Path(tempfile.mkdtemp())
        progress = status.Progress(root / 'status.json', root / 'history.jsonl')
        progress.begin('run', ['sidebar'], '/tmp/out', project='lody')
        progress.end('passed')
        entry = status.history((root / 'history.jsonl').read_text())[0]
        self.assertEqual(entry['project'], 'lody')
        text = status.render({'now': time.time(), 'lock': None, 'run': None, 'history': [entry], 'others': [],
                              'runner': {'name': 'lody-runner', 'state': 'stopped', 'idle_left': None,
                                         'paused': False, 'vnc': None}})
        self.assertRegex(text, r'run +lody +sidebar')

    def test_status_lists_the_other_runners(self):
        text = status.render({'now': time.time(), 'lock': None, 'run': None, 'history': [],
                              'others': [{'name': 'notes-runner', 'state': 'up'}],
                              'runner': {'name': 'lody-runner', 'state': 'stopped', 'idle_left': None,
                                         'paused': False, 'vnc': None}})
        self.assertIn('Others   notes-runner: up', text)



class BaseLineTest(unittest.TestCase):
    def test_status_names_outdated_base_steps(self):
        text = status.render({'now': time.time(), 'lock': None, 'run': None, 'history': [], 'others': [],
                              'base': {'name': 'macos-27.2', 'outdated': ['tools']},
                              'runner': {'name': 'lody-runner', 'state': 'stopped', 'idle_left': None,
                                         'paused': False, 'vnc': None}})
        self.assertIn('Base     macos-27.2: out of date (tools); the next run updates it', text)

    def test_status_calls_a_matching_base_current(self):
        text = status.render({'now': time.time(), 'lock': None, 'run': None, 'history': [], 'others': [],
                              'base': {'name': 'macos-27.2', 'outdated': []},
                              'runner': {'name': 'lody-runner', 'state': 'stopped', 'idle_left': None,
                                         'paused': False, 'vnc': None}})
        self.assertIn('Base     macos-27.2: current', text)


if __name__ == '__main__':
    unittest.main()
