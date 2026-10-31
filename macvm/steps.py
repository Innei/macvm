import hashlib
import json

ORDER = ['system', 'xcode', 'runtime', 'screen-sharing', 'services', 'tools', 'grants', 'desktop', 'first-launch']
DEPENDS = {'runtime': ['xcode'], 'first-launch': ['xcode']}


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def fingerprints(scripts, xcode_build, sdk):
    marks = {name: digest(scripts[name]) for name in ORDER if name in scripts}
    marks['xcode'] = xcode_build
    marks['runtime'] = f'{xcode_build}:{sdk}:{digest(scripts["runtime"])}'
    marks['first-launch'] = f'{xcode_build}:{digest(scripts["first-launch"])}'
    return {name: marks[name] for name in ORDER}


def stale(recorded, current, force=()):
    todo = set(force) | {name for name in ORDER if recorded.get(name) != current[name]}
    changed = True
    while changed:
        changed = False
        for name, needs in DEPENDS.items():
            if name not in todo and todo.intersection(needs):
                todo.add(name)
                changed = True
    return [name for name in ORDER if name in todo]


def plan(base, next_vm, force, todo):
    if force:
        return 'install'
    if next_vm:
        return 'resume'
    if not base:
        return 'install'
    return 'clone' if todo else 'current'


def recorded(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def record(path, name, mark):
    marks = recorded(path)
    marks[name] = mark
    partial = path.with_suffix('.partial')
    partial.write_text(json.dumps(marks))
    partial.replace(path)
