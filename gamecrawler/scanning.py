import io, json
from pathlib import Path
from typing import List, Optional, Tuple
from collections import deque
from dataclasses import asdict

from .models import Game, GameMeta, Launcher
from .utils import pick_best_image
from .utils import b64url_encode, b64url_decode, pick_best_image, b64url_encode, b64url_decode, load_ignore_patterns, is_dir_ignored

def game_id_for(rel: str) -> str:
    return b64url_encode(rel)

def rel_for_id(gid: str) -> str:
    return b64url_decode(gid)

def detect_root_files(game_dir: Path, exts: set) -> List[str]:
    items: List[str] = []
    try:
        for p in game_dir.iterdir():
            if p.is_file() and p.suffix.lower() in exts:
                items.append(p.name)
    except PermissionError:
        pass
    return sorted(items, key=lambda n: n.lower())

def detect_files_bfs(game_dir: Path, exts: set, max_depth: int, *,
                     root: Optional[Path]=None, patterns: Optional[List[str]]=None) -> List[str]:  # NEW params
    """Breadth-first; once we hit the first executable depth, restrict to that depth."""
    results: List[str] = []
    q = deque([(game_dir, 0)])
    found_depth: Optional[int] = None
    root = root or game_dir
    patterns = patterns or []

    while q:
        cur, depth = q.popleft()
        if depth > max_depth:
            continue
        # skip ignored dirs (but never skip the game_dir itself)
        if cur != game_dir and is_dir_ignored(root, cur, patterns):
            continue
        try:
            entries = list(cur.iterdir())
        except PermissionError:
            continue

        files_here = [e for e in entries if e.is_file() and e.suffix.lower() in exts]
        if files_here:
            if found_depth is None:
                found_depth = depth
            if depth == found_depth:
                for f in files_here:
                    results.append(f.relative_to(game_dir).as_posix())
            continue

        if found_depth is None:
            for d in entries:
                if d.is_dir():
                    q.append((d, depth + 1))

    results.sort(key=lambda s: s.lower())
    return results

def new_id() -> str:
    import os
    return os.urandom(6).hex()

def load_meta(game_dir: Path, metafile: str, default_title: str) -> GameMeta:
    meta_path = game_dir / metafile
    if meta_path.exists():
        try:
            data = json.loads(meta_path.read_text("utf-8"))
            sb = data.get("sandboxed", None)
            if sb in ("", "global"): sb_val = None
            elif isinstance(sb, bool): sb_val = sb
            else: sb_val = None
            l_raw = data.get("launchers", [])
            launchers: List[Launcher] = []
            for item in l_raw:
                if not isinstance(item, dict): continue
                lid = item.get("id") or new_id()
                name = item.get("name") or "Launch"
                relp = item.get("relpath") or ""
                args = item.get("args") or ""
                if relp:
                    launchers.append(Launcher(id=lid, name=name, relpath=relp, args=args))
            last = data.get("last_launcher")
            cmd = data.get("command", "")
            m = GameMeta(
                title=data.get("title", default_title),
                cover_image=data.get("cover_image", ""),
                sandboxed=sb_val,
                launchers=launchers,
                last_launcher=last,
                command=cmd
            )
            if not m.launchers and m.command:
                from pathlib import Path as _P
                base = _P(m.command).name
                m.launchers = [Launcher(id=new_id(), name=_P(base).stem, relpath=m.command, args="")]
                m.command = ""
            return m
        except Exception:
            pass
    return GameMeta(title=default_title, cover_image="", sandboxed=None,
                    launchers=[], last_launcher=None, command="")

def save_meta(game_dir: Path, metafile: str, meta: GameMeta) -> None:
    data = asdict(meta)
    (game_dir / metafile).write_text(json.dumps(data, indent=2), encoding="utf-8")

def effective_sandbox(game_meta: GameMeta, global_default: bool) -> bool:
    return game_meta.sandboxed if game_meta.sandboxed is not None else global_default

def build_games(games_root: Path, settings: dict, exts_img: set, exts_exec: set,
                max_depth: int, metafile: str, target_ar: float) -> List[Game]:
    games: List[Game] = []
    if not games_root.exists():
        return games

    patterns = load_ignore_patterns(games_root, ".gamecrawlerignore")  # NEW

    for p in sorted(games_root.iterdir()):
        if not p.is_dir():
            continue
        if is_dir_ignored(games_root, p, patterns):  # NEW
            continue
        rel = str(p.relative_to(games_root))
        meta = load_meta(p, metafile, default_title=p.name)
        images = detect_root_files(p, exts_img)
        execs = detect_files_bfs(p, exts_exec, max_depth, root=games_root, patterns=patterns)  # NEW args
        if not meta.cover_image and images:
            chosen = pick_best_image(p, images, target_ar)
            if chosen:
                meta.cover_image = chosen
                save_meta(p, metafile, meta)
        games.append(Game(folder=p, rel=rel, id=game_id_for(rel),
                          meta=meta, detected_execs=execs, detected_images=images))
    return games

def get_game_or_404(games_root: Path, gid: str, exts_img: set, exts_exec: set,
                    max_depth: int, metafile: str, target_ar: float) -> Game:
    from flask import abort
    root = Path(games_root).resolve()
    rel = rel_for_id(gid)
    folder = (root / rel).resolve()

    try:
        if not folder.is_relative_to(root):  # type: ignore[attr-defined]
            abort(404)
    except AttributeError:
        try:
            folder.relative_to(root)
        except Exception:
            abort(404)

    if not folder.exists() or not folder.is_dir():
        abort(404)

    meta = load_meta(folder, metafile, default_title=folder.name)
    images = detect_root_files(folder, exts_img)
    patterns = load_ignore_patterns(root, ".gamecrawlerignore")  # NEW
    execs = detect_files_bfs(folder, exts_exec, max_depth, root=root, patterns=patterns)  # NEW

    if not meta.cover_image and images:
        chosen = pick_best_image(folder, images, target_ar)
        if chosen:
            meta.cover_image = chosen
            save_meta(folder, metafile, meta)

    return Game(
        folder=folder,
        rel=str(folder.relative_to(root)),
        id=game_id_for(str(folder.relative_to(root))),
        meta=meta,
        detected_execs=execs,
        detected_images=images
    )