from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

@dataclass
class Launcher:
    id: str
    name: str
    relpath: str
    args: str = ""

@dataclass
class GameMeta:
    title: str
    cover_image: str
    sandboxed: Optional[bool]       # None=global default
    launchers: List[Launcher]
    last_launcher: Optional[str]
    command: str = ""               # back-compat seed

@dataclass
class Game:
    folder: Path
    rel: str
    id: str
    meta: GameMeta
    detected_execs: List[str]
    detected_images: List[str]
