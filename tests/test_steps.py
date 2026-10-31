import json
from pathlib import Path
import tempfile
import unittest

from macvm import steps

SCRIPTS = {'system': 'a', 'screen-sharing': 'b', 'services': 'c', 'tools': 'd', 'grants': 'e', 'desktop': 'f',
           'runtime': 'g', 'first-launch': 'h'}


class FingerprintTest(unittest.TestCase):
    def current(self, build='27B52', sdk='27.1', **changes):
        return steps.fingerprints({**SCRIPTS, **changes}, build, sdk)

    def test_every_step_has_a_fingerprint_in_order(self):
        self.assertEqual(list(self.current()), steps.ORDER)

    def test_nothing_is_stale_when_nothing_changed(self):
        self.assertEqual(steps.stale(self.current(), self.current()), [])

    def test_a_new_xcode_reruns_its_runtime_and_first_launch(self):
        self.assertEqual(steps.stale(self.current(), self.current(build='27C10')), ['xcode', 'runtime', 'first-launch'])

    def test_a_new_sdk_reruns_only_the_runtime(self):
        self.assertEqual(steps.stale(self.current(), self.current(sdk='27.2')), ['runtime'])

    def test_an_edited_script_reruns_only_its_step(self):
        self.assertEqual(steps.stale(self.current(), self.current(tools='d2')), ['tools'])

    def test_an_unrecorded_base_reruns_everything(self):
        self.assertEqual(steps.stale({}, self.current()), steps.ORDER)

    def test_forced_steps_bring_their_dependents(self):
        self.assertEqual(steps.stale(self.current(), self.current(), force=['xcode']), ['xcode', 'runtime', 'first-launch'])


class PlanTest(unittest.TestCase):
    def test_current_base_needs_no_work(self):
        self.assertEqual(steps.plan(base=True, next_vm=False, force=False, todo=[]), 'current')

    def test_stale_base_is_cloned_to_next(self):
        self.assertEqual(steps.plan(base=True, next_vm=False, force=False, todo=['tools']), 'clone')

    def test_a_leftover_next_is_resumed(self):
        self.assertEqual(steps.plan(base=True, next_vm=True, force=False, todo=[]), 'resume')

    def test_no_base_installs_into_next(self):
        self.assertEqual(steps.plan(base=False, next_vm=False, force=False, todo=steps.ORDER), 'install')

    def test_force_reinstalls_even_with_a_leftover_next(self):
        self.assertEqual(steps.plan(base=True, next_vm=True, force=True, todo=[]), 'install')


class RecordTest(unittest.TestCase):
    def test_recording_a_step_persists_immediately(self):
        path = Path(tempfile.mkdtemp()) / 'base-next.json'
        steps.record(path, 'tools', 'abc')
        steps.record(path, 'desktop', 'def')
        self.assertEqual(json.loads(path.read_text()), {'tools': 'abc', 'desktop': 'def'})
        self.assertEqual(steps.recorded(path), {'tools': 'abc', 'desktop': 'def'})
        self.assertEqual(steps.recorded(path.with_name('missing.json')), {})


if __name__ == '__main__':
    unittest.main()
