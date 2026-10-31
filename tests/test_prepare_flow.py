import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from macvm import base, steps

MARKS = {name: f'{name}-1' for name in steps.ORDER}


class PrepareFlowTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.calls = []
        self.vms = {base.BASE}
        patches = {
            'MARKS': self.root / 'base.json', 'NEXT_MARKS': self.root / 'base-next.json', 'READY': self.root / 'ready',
            'current_marks': lambda: dict(MARKS), 'vms': lambda: set(self.vms), 'free_slot': lambda name: None,
            'tart': self.tart, 'install': lambda args, log, name: self.calls.append(('install', name)),
            'ensure_sip_off': lambda args, log, name: None,
            'configure': self.configure, 'settle': lambda args, log, name: self.calls.append(('settle', name)),
            'exclusive': mock.MagicMock(),
        }
        for name, value in patches.items():
            patcher = mock.patch.object(base, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.args = SimpleNamespace(force=False, repair_xcode=False, upgrade_tools=False, recovery_vnc=False)
        self.fail_on = None

    def tart(self, *command, **_):
        self.calls.append(command)
        if command[0] == 'clone':
            self.vms.add(command[2])
        if command[0] == 'delete':
            self.vms.discard(command[1])
        if command[0] == 'rename':
            self.vms.discard(command[1])
            self.vms.add(command[2])

    def configure(self, args, log, name, todo, marks):
        self.calls.append(('configure', name, tuple(todo)))
        for item in todo:
            if item == self.fail_on:
                raise SystemExit(f'{item} failed')
            steps.record(base.NEXT_MARKS, item, marks[item])

    def test_a_current_base_is_left_alone(self):
        base.MARKS.write_text(json.dumps(MARKS))
        base.build_base(self.args, None)
        self.assertEqual(self.calls, [])

    def test_a_stale_step_runs_on_a_clone_that_replaces_the_base(self):
        base.MARKS.write_text(json.dumps({**MARKS, 'tools': 'old'}))
        base.build_base(self.args, None)
        self.assertEqual(self.calls, [('clone', base.BASE, base.NEXT), ('configure', base.NEXT, ('tools',)),
                                      ('settle', base.NEXT), ('rename', base.BASE, f'{base.BASE}-old'),
                                      ('rename', base.NEXT, base.BASE), ('delete', f'{base.BASE}-old')])
        self.assertEqual(json.loads(base.MARKS.read_text()), MARKS)
        self.assertTrue(base.READY.exists())
        self.assertEqual(self.vms, {base.BASE})

    def test_a_failed_update_keeps_the_base_and_resumes_from_the_failed_step(self):
        base.MARKS.write_text(json.dumps({**MARKS, 'tools': 'old', 'desktop': 'old'}))
        base.READY.touch()
        ready = base.READY.stat().st_mtime_ns
        self.fail_on = 'desktop'
        with self.assertRaises(SystemExit):
            base.build_base(self.args, None)
        self.assertEqual(self.vms, {base.BASE, base.NEXT})
        self.assertEqual(base.READY.stat().st_mtime_ns, ready)
        self.assertEqual(json.loads(base.MARKS.read_text())['tools'], 'old')
        self.fail_on = None
        self.calls.clear()
        base.build_base(self.args, None)
        self.assertEqual(self.calls[0], ('configure', base.NEXT, ('desktop',)))
        self.assertEqual(self.vms, {base.BASE})

    def test_no_base_installs_into_next_then_swaps_in(self):
        self.vms.clear()
        base.build_base(self.args, None)
        self.assertEqual(self.calls[0], ('install', base.NEXT))
        self.assertEqual(self.calls[1], ('configure', base.NEXT, tuple(steps.ORDER)))
        self.assertEqual(self.vms, {base.BASE})


if __name__ == '__main__':
    unittest.main()
