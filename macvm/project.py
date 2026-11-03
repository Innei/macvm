from dataclasses import dataclass, field
from pathlib import Path
import re
import tomllib

NAME = re.compile(r'^[a-z0-9][a-z0-9-]*$')
APP_SERVICES = {
    'accessibility': 'kTCCServiceAccessibility',
    'screen-recording': 'kTCCServiceScreenCapture',
    'post-event': 'kTCCServicePostEvent',
    'listen-event': 'kTCCServiceListenEvent',
    'full-disk': 'kTCCServiceSystemPolicyAllFiles',
    'desktop': 'kTCCServiceSystemPolicyDesktopFolder',
    'documents': 'kTCCServiceSystemPolicyDocumentsFolder',
    'downloads': 'kTCCServiceSystemPolicyDownloadsFolder',
    'camera': 'kTCCServiceCamera',
    'microphone': 'kTCCServiceMicrophone',
    'contacts': 'kTCCServiceAddressBook',
    'calendars': 'kTCCServiceCalendar',
    'reminders': 'kTCCServiceReminders',
    'photos': 'kTCCServicePhotos',
    'notifications': None,
}


@dataclass(frozen=True)
class Reset:
    processes: list = field(default_factory=list)
    process_patterns: list = field(default_factory=list)
    defaults: list = field(default_factory=list)
    paths: list = field(default_factory=list)


@dataclass(frozen=True)
class Grants:
    targets: list = field(default_factory=list)
    apps: dict = field(default_factory=dict)
    bypass: bool = False


@dataclass(frozen=True)
class Extra:
    name: str
    command: str
    files: list = field(default_factory=list)
    node: bool = False
    help: str = ''


@dataclass(frozen=True)
class Project:
    root: Path
    name: str
    build: list
    prepare: list
    prepare_when_missing: Path | None
    xctestrun: str
    products: list
    always: list
    suite: str
    cases: dict
    timeout: int
    reset: Reset
    grants: Grants
    extras: list
    output: Path

    @property
    def runner(self):
        return f'{self.name}-runner'

    def check_cases(self, cases):
        unknown = [name for name in cases if name not in self.cases]
        if unknown:
            raise SystemExit(f'unknown case {", ".join(unknown)}; choose from {", ".join(self.cases)}')


def find(start):
    for folder in [start, *start.parents]:
        candidate = folder / 'macvm.toml'
        if candidate.exists():
            return candidate
    raise SystemExit(f'no macvm.toml in {start} or any parent; add one at the project root')


def load(path):
    raw = tomllib.loads(path.read_text())
    root = path.parent
    name = raw.get('name', '')
    if not NAME.match(name):
        raise SystemExit(f'{path}: name must be lowercase letters, digits and hyphens, got {name!r}')
    build, test = raw.get('build', {}), raw.get('test', {})
    grants = raw.get('grants', {})
    bypass = grants.get('bypass', False)
    if not isinstance(bypass, bool):
        raise SystemExit(f'{path}: grants.bypass must be a boolean')
    apps = grants.get('apps', {})
    for bundle, services in apps.items():
        unknown = [service for service in services if service not in APP_SERVICES]
        if unknown:
            raise SystemExit(f'{path}: unknown grant {", ".join(unknown)} for {bundle}; '
                             f'choose from {", ".join(APP_SERVICES)}')
    missing = build.get('prepare_when_missing')
    return Project(
        root=root, name=name,
        build=list(build['command']), prepare=list(build.get('prepare', [])),
        prepare_when_missing=root / missing if missing else None,
        xctestrun=build['xctestrun'], products=list(build['products']),
        always=list(test.get('always', [])), suite=test['suite'], cases=dict(test.get('cases', {})),
        timeout=int(test.get('timeout', 1800)),
        reset=Reset(**raw.get('reset', {})),
        grants=Grants(targets=list(grants.get('targets', [])), apps={k: list(v) for k, v in apps.items()}, bypass=bypass),
        extras=[Extra(**extra) for extra in raw.get('extra', [])],
        output=root / raw.get('output', {}).get('dir', '.artifacts/macvm'),
    )
