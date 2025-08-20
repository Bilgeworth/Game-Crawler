#!/usr/bin/env python3
"""
Smoke test for the split-only Game Crawler.

Checks:
- BFS depth lock
- Cover auto-pick + persist
- Launcher wiring & sandbox flow (Popen mocked)
- effective_sandbox (global default)
- .gamecrawlerignore behavior           << NEW
- SANDBOXED_WSL_PS1 PowerShell path   << NEW
"""
import os, shutil, tempfile
from pathlib import Path

from gamecrawler import create_app, ensure_root
from gamecrawler.settings import load_settings, save_settings
from gamecrawler.scanning import build_games, get_game_or_404, save_meta, load_meta, effective_sandbox
from gamecrawler.launch import run_resolved_command
from gamecrawler.utils import sandboxie_available
from gamecrawler.models import Launcher


def _touch(p: Path, data: bytes = b""):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data or b"stub")


def _png(w=600, h=800):
    from PIL import Image
    import io
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (12, 34, 56)).save(buf, format="PNG")
    return buf.getvalue()


def mock_popen_calls():
    calls = []
    class _P:
        def __init__(self, *a, **kw):
            calls.append((a, kw))
    return _P, calls


def main():
    tmp = Path(tempfile.mkdtemp(prefix="gcrawler_test_"))
    try:
        games = tmp / "Games"
        games.mkdir()

        ensure_root(str(games))
        app = create_app(str(games))
        cfg = app.config

        settings_file = games / "_gamecrawler.json"
        save_settings(settings_file, {"default_sandboxed": True})

        # Game A (has image + .exe + .sh)
        gA = games / "GameA"
        _touch(gA / "box.png", _png(600, 800))
        _touch(gA / "bin" / "play.exe")
        _touch(gA / "bin" / "alt.sh")  # .sh target for PS wrapper test

        # Game B (two sibling exec dirs)
        gB = games / "GameB"
        _touch(gB / "v1" / "chapter1.exe")
        _touch(gB / "v2" / "chapter2.exe")

        # Initial build
        settings = load_settings(settings_file)
        built = build_games(
            games_root=games,
            settings=settings,
            exts_img=cfg["ALLOWED_IMG_EXT"],
            exts_exec=cfg["ALLOWED_EXEC_EXT"],
            max_depth=cfg["MAX_SCAN_DEPTH"],
            metafile=cfg["METAFILE"],
            target_ar=cfg["DEFAULT_TARGET_AR"],
        )
        assert len(built) == 2, f"expected 2 games, got {len(built)}"
        A = next(g for g in built if g.rel == "GameA")
        B = next(g for g in built if g.rel == "GameB")

        # Cover auto-pick persisted
        mA = load_meta(A.folder, cfg["METAFILE"], "GameA")
        assert mA.cover_image.lower().endswith(".png"), "cover not auto-picked/saved"

        # Depth lock
        assert sorted(A.detected_execs) == ["bin/alt.sh", "bin/play.exe"]
        assert sorted(B.detected_execs) == ["v1/chapter1.exe", "v2/chapter2.exe"]

        # Configure launchers
        mB = load_meta(B.folder, cfg["METAFILE"], "GameB")
        mB.launchers = [Launcher(id="L1", name="Chapter 2", relpath="v2/chapter2.exe", args="")]
        mB.sandboxed = None
        save_meta(B.folder, cfg["METAFILE"], mB)

        # Mock Popen (1): sandboxed exe launch path
        import gamecrawler.launch as L
        PopenSaved = L.subprocess.Popen
        FakePopen, calls = mock_popen_calls()
        L.subprocess.Popen = FakePopen  # type: ignore
        try:
            gameB = get_game_or_404(
                games_root=games,
                gid=B.id,
                exts_img=cfg["ALLOWED_IMG_EXT"],
                exts_exec=cfg["ALLOWED_EXEC_EXT"],
                max_depth=cfg["MAX_SCAN_DEPTH"],
                metafile=cfg["METAFILE"],
                target_ar=cfg["DEFAULT_TARGET_AR"],
            )
            ok, msg = run_resolved_command(gameB, "v2/chapter2.exe", "", sandbox=True, default_box=cfg["SANDBOX_BOX"])
            assert ok, f"sandboxed launch failed: {msg}"
            assert calls, "no Popen call captured for exe launch"
        finally:
            L.subprocess.Popen = PopenSaved

        # .gamecrawlerignore behavior  << NEW
        (games / ".gamecrawlerignore").write_text("GameB/\n", encoding="utf-8")
        built2 = build_games(
            games_root=games,
            settings=settings,
            exts_img=cfg["ALLOWED_IMG_EXT"],
            exts_exec=cfg["ALLOWED_EXEC_EXT"],
            max_depth=cfg["MAX_SCAN_DEPTH"],
            metafile=cfg["METAFILE"],
            target_ar=cfg["DEFAULT_TARGET_AR"],
        )
        assert [g.rel for g in built2] == ["GameA"], f"ignore file not respected: {built2}"

        # SANDBOXED_WSL_PS1 path for .sh  << NEW
        os.environ["SANDBOXED_WSL_PS1"] = str(tmp / "sandboxed_wsl.ps1")
        _touch(Path(os.environ["SANDBOXED_WSL_PS1"]), b'Write-Output "mock";')

        # Ensure GameA has a launcher pointing to a .sh
        mA = load_meta(A.folder, cfg["METAFILE"], "GameA")
        mA.launchers = [Launcher(id="S1", name="Alt SH", relpath="bin/alt.sh", args="")]
        save_meta(A.folder, cfg["METAFILE"], mA)

        # Mock Popen (2): .sh should invoke PowerShell wrapper
        PopenSaved = L.subprocess.Popen
        FakePopen, calls = mock_popen_calls()
        L.subprocess.Popen = FakePopen  # type: ignore
        try:
            gameA = get_game_or_404(
                games_root=games,
                gid=A.id,
                exts_img=cfg["ALLOWED_IMG_EXT"],
                exts_exec=cfg["ALLOWED_EXEC_EXT"],
                max_depth=cfg["MAX_SCAN_DEPTH"],
                metafile=cfg["METAFILE"],
                target_ar=cfg["DEFAULT_TARGET_AR"],
            )
            ok, msg = run_resolved_command(gameA, "bin/alt.sh", "", sandbox=False, default_box=cfg["SANDBOX_BOX"])
            assert ok, f".sh launch via PS wrapper failed: {msg}"
            assert calls and isinstance(calls[0][0][0], list), "no Popen call captured for .sh"
            
            # After capturing calls = [(args, kwargs), ...]
            argv = calls[0][0][0]
            env = calls[0][1].get("env", {})
            assert argv[0].lower().endswith("powershell.exe")
            assert env.get("SANDBOXED_WSL_CWD"), "SANDBOXED_WSL_CWD not set"
            assert env.get("SANDBOXED_WSL_CMD"), "SANDBOXED_WSL_CMD not set"
        finally:
            L.subprocess.Popen = PopenSaved
            os.environ.pop("SANDBOXED_WSL_PS1", None)

        # Flip global default and check effective_sandbox
        save_settings(settings_file, {"default_sandboxed": False})
        settings2 = load_settings(settings_file)
        assert effective_sandbox(mB, settings2["default_sandboxed"]) is False

        # Report
        print("[OK] Games built:", [g.rel for g in built])
        print("[OK] Execs A:", A.detected_execs)
        print("[OK] Execs B:", B.detected_execs)
        print("[OK] Cover picked for A:", mA.cover_image)
        print("[OK] .gamecrawlerignore respected (GameB hidden).")
        print("[OK] .sh routed to PowerShell wrapper (SANDBOXED_WSL_PS1).")
        print("[OK] Launch path composed and dispatched (mocked).")
        print("[OK] effective_sandbox honored global default:", settings2["default_sandboxed"])
        print("[Info] Sandboxie available on this host?:", sandboxie_available())

    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
