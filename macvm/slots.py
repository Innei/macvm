LIMIT = 2


def choose(running, idle_since, busy, wanted, limit=LIMIT):
    if wanted in running or len(running) < limit:
        return 'start', None
    candidates = [name for name in running if name.endswith('-runner') and name not in busy and name in idle_since]
    if not candidates:
        return 'wait', None
    return 'stop', min(candidates, key=idle_since.__getitem__)


def make_room(wanted, listed, idle, busy, stop, sleep, notice):
    waiting = False
    while True:
        running = listed()
        owned = [name for name in running if name.endswith('-runner')]
        idle_since = {name: idle(name) for name in owned}
        action, victim = choose(running, idle_since, {name for name in owned if busy(name)}, wanted)
        if action == 'start':
            return
        if action == 'stop':
            notice(f'stopping {victim}, idle the longest, to free a VM slot for {wanted}')
            stop(victim)
            continue
        if not waiting:
            notice(f'{", ".join(running)} are busy and macOS runs at most {LIMIT} VMs; waiting for a free slot')
            waiting = True
        sleep(10)
