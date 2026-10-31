import subprocess
from types import SimpleNamespace
import unittest
from unittest import mock

from macvm import guest


class BootTest(unittest.TestCase):
    def test_ssh_probe_timeout_keeps_waiting_for_boot(self):
        machine = guest.Guest.__new__(guest.Guest)
        machine.name, machine.ip = 'probe', '192.0.2.1'
        machine.process = SimpleNamespace(poll=lambda: None)
        answers = [subprocess.TimeoutExpired('ssh', 15), SimpleNamespace(returncode=0)]

        def ssh(*_, **__):
            answer = answers.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer

        machine.ssh = ssh
        with mock.patch.object(guest.time, 'sleep'):
            self.assertIs(machine.boot(timeout=60), machine)
        self.assertEqual(answers, [])


if __name__ == '__main__':
    unittest.main()
