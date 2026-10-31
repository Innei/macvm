import unittest

from macvm import slots


class EvictionTest(unittest.TestCase):
    def test_nothing_to_stop_while_a_slot_is_free(self):
        self.assertEqual(slots.choose(['lody-runner'], {}, set(), wanted='notes-runner'), ('start', None))

    def test_already_running_needs_no_slot(self):
        self.assertEqual(slots.choose(['a-runner', 'notes-runner'], {}, set(), wanted='notes-runner'), ('start', None))

    def test_stops_the_longest_idle_runner_without_a_held_lock(self):
        running = ['a-runner', 'b-runner']
        idle_since = {'a-runner': 200.0, 'b-runner': 100.0}
        self.assertEqual(slots.choose(running, idle_since, set(), wanted='c-runner'), ('stop', 'b-runner'))

    def test_skips_runners_that_are_running_tests(self):
        running = ['a-runner', 'b-runner']
        idle_since = {'a-runner': 200.0, 'b-runner': 100.0}
        self.assertEqual(slots.choose(running, idle_since, {'b-runner'}, wanted='c-runner'), ('stop', 'a-runner'))

    def test_waits_when_every_running_vm_is_busy_or_foreign(self):
        running = ['a-runner', 'macos-27.2-next']
        self.assertEqual(slots.choose(running, {'a-runner': 1.0}, {'a-runner'}, wanted='c-runner'), ('wait', None))

    def test_never_stops_a_vm_macvm_does_not_own(self):
        running = ['someone-else', 'a-runner']
        self.assertEqual(slots.choose(running, {'a-runner': 5.0}, set(), wanted='c-runner'), ('stop', 'a-runner'))
        self.assertEqual(slots.choose(['someone-else', 'other'], {}, set(), wanted='c-runner'), ('wait', None))



class MakeRoomTest(unittest.TestCase):
    def test_stops_the_chosen_runner_then_returns(self):
        listings = [['a-runner', 'b-runner'], ['a-runner']]
        stopped = []
        slots.make_room('c-runner', listed=lambda: listings.pop(0), idle=lambda name: {'a-runner': 9, 'b-runner': 1}[name],
                        busy=lambda name: False, stop=stopped.append, sleep=lambda _: None, notice=lambda _: None)
        self.assertEqual(stopped, ['b-runner'])

    def test_waits_while_every_slot_is_busy(self):
        listings = [['a-runner', 'b-runner'], ['a-runner', 'b-runner'], ['a-runner']]
        notices, naps = [], []
        slots.make_room('c-runner', listed=lambda: listings.pop(0), idle=lambda name: 1, busy=lambda name: True,
                        stop=lambda name: self.fail('stopped a busy runner'), sleep=naps.append, notice=notices.append)
        self.assertEqual(len(naps), 2)
        self.assertIn('waiting', notices[0])


if __name__ == '__main__':
    unittest.main()
