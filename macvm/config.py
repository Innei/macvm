from dataclasses import dataclass, fields
import os
from pathlib import Path
import tomllib


@dataclass(frozen=True)
class Settings:
    bundle: Path = Path('/Volumes/WD/macvm.sparsebundle')
    volume: Path = Path('/Volumes/MacVM')
    base: str = 'macos-27.2'
    ipsw: str = 'UniversalMac_27.2_26B123_Restore.ipsw'
    ipsw_url: str = ('https://updates.cdn-apple.com/2026FallFCS/c9671975-52db-4fb5-9af2-00217169ba8d/'
                     'UniversalMac_27.2_26B123_Restore.ipsw')
    xcode: Path = Path('/Applications/Xcode.app')
    cpu: int = 6
    memory: int = 16
    display: str = '1680x1050'
    idle_minutes: int = 30
    vnc_port: int = 5905


def default_path(environ=os.environ):
    configured = environ.get('MACVM_CONFIG')
    return Path(configured) if configured else Path.home() / '.config/macvm/config.toml'


def load(path=None):
    path = path or default_path()
    if not path.exists():
        return Settings()
    values = tomllib.loads(path.read_text())
    known = {field.name: field.type for field in fields(Settings)}
    unknown = sorted(set(values) - set(known))
    if unknown:
        raise SystemExit(f'unknown keys in {path}: {", ".join(unknown)}')
    return Settings(**{key: Path(value) if known[key] is Path else value for key, value in values.items()})
