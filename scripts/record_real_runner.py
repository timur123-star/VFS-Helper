"""Optional screen recording while the helper fills a real VFS form.

Called by record_real.bat / record_real.sh. Does:

  1. Start ffmpeg as a child process recording the whole screen.
  2. Start vfs_helper.py as a child process with stdin attached to a pipe.
  3. Wait for the human to:
       - pass Cloudflare in the browser (one-time)
       - log in with their VFS account
       - navigate to the form they want filled
     The human presses Enter in THIS terminal when they're on the form.
  4. Auto-type into the helper's REPL:
       inspect -> scroll -> fill -> screenshot -> quit
  5. Stop ffmpeg, exit.

Result: artifacts/vfs-helper-real-vfs-<timestamp>.mp4
"""

from __future__ import annotations

import argparse
import datetime as _dt
import os
import platform
import signal
import subprocess
import sys
import time
from pathlib import Path


HERE = Path(__file__).resolve().parent.parent


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--config",
        type=Path,
        default=HERE / "config.json",
        help="vfs_helper config to use during recording.",
    )
    p.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output MP4 path. Defaults to artifacts/vfs-helper-real-vfs-<ts>.mp4",
    )
    p.add_argument(
        "--platform",
        choices=("win", "linux", "auto"),
        default="auto",
        help="ffmpeg capture backend (auto = pick by OS).",
    )
    p.add_argument(
        "--no-fill-applicant",
        action="store_true",
        help="Skip the profile-specific fill step (use if profiles not configured).",
    )
    p.add_argument(
        "--profile",
        default="applicant",
        help="Profile name for the extra fill step (default: applicant).",
    )
    p.add_argument(
        "--fill-passes",
        type=int,
        default=1,
        metavar="N",
        help="How many full 'fill' runs after scrolling (default: 1).",
    )
    p.add_argument(
        "--fill-delay",
        type=float,
        default=28.0,
        help="Seconds to wait after each 'fill' command (default: 28).",
    )
    p.add_argument(
        "--step-delay",
        type=float,
        default=3.0,
        help="Seconds between minor REPL steps like inspect/scroll (default: 3).",
    )
    p.add_argument(
        "--with-login-fill",
        action="store_true",
        help="Run 'fill login' before the main fill (use on the login page only).",
    )
    return p.parse_args()


def find_ffmpeg() -> str:
    """Return ffmpeg executable path, or die with a friendly message."""
    from shutil import which
    exe = which("ffmpeg")
    if exe:
        return exe
    sys.stderr.write(
        "\n[ERROR] ffmpeg is not in PATH.\n"
        "Install it once:\n"
        "  Windows: winget install Gyan.FFmpeg\n"
        "           (or: choco install ffmpeg)\n"
        "  macOS:   brew install ffmpeg\n"
        "  Linux:   sudo apt install ffmpeg\n"
        "Then re-run this script.\n"
    )
    sys.exit(2)


def pick_capture_args(plat: str) -> list[str]:
    """Return the ffmpeg capture-input args for the chosen platform."""
    if plat == "auto":
        sys_plat = platform.system().lower()
        if sys_plat.startswith("win"):
            plat = "win"
        else:
            plat = "linux"

    if plat == "win":
        return ["-f", "gdigrab", "-framerate", "25", "-i", "desktop"]
    # linux: capture display via x11grab
    display = os.environ.get("DISPLAY", ":0")
    return ["-f", "x11grab", "-framerate", "25", "-video_size", "1920x1080", "-i", display]


def start_ffmpeg(ffmpeg: str, plat: str, out_path: Path) -> subprocess.Popen:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg, "-y", "-loglevel", "error",
        *pick_capture_args(plat),
        "-c:v", "libx264", "-preset", "veryfast",
        "-pix_fmt", "yuv420p", "-crf", "24",
        str(out_path),
    ]
    print(f"[ffmpeg] {' '.join(cmd)}")
    # On Windows we want to be able to send Ctrl+C to it cleanly.
    if platform.system().lower().startswith("win"):
        proc = subprocess.Popen(cmd, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
    else:
        proc = subprocess.Popen(cmd, preexec_fn=os.setsid)
    return proc


def stop_ffmpeg(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    print("[ffmpeg] stopping...")
    try:
        if platform.system().lower().startswith("win"):
            proc.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGINT)
    except Exception:
        pass
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def write_repl(helper: subprocess.Popen, line: str, label: str | None = None) -> None:
    """Send a line into the helper's REPL with a visible heading on stdout."""
    if helper.stdin is None or helper.stdin.closed:
        return
    if label:
        print(f"\n>>> {label}")
    print(f"  $ {line}")
    try:
        helper.stdin.write(line + "\n")
        helper.stdin.flush()
    except Exception as exc:
        print(f"  (failed to send: {exc})")


def run_fill_sequence(helper: subprocess.Popen, args: argparse.Namespace) -> None:
    """Drive the REPL through inspect, scrolls, and multiple fill passes."""
    d = args.step_delay
    fd = args.fill_delay

    write_repl(helper, "url", "Sanity: print current URL")
    time.sleep(d)

    write_repl(helper, "inspect", "Inspect visible form fields on this step")
    time.sleep(d + 1)

    if args.with_login_fill:
        write_repl(helper, "fill login", "AUTOFILL login fields (profiles.login)")
        time.sleep(fd)

    write_repl(helper, "scroll top", "Jump to top of the form")
    time.sleep(d)

    write_repl(helper, "scroll down", "Scroll so fields below the fold are visible")
    time.sleep(d)

    for n in range(1, args.fill_passes + 1):
        write_repl(
            helper,
            "fill",
            f"AUTOFILL all config.user fields on the REAL form — pass {n}/{args.fill_passes}",
        )
        time.sleep(fd)
        if n < args.fill_passes:
            write_repl(helper, "scroll down", "Scroll for any remaining fields")
            time.sleep(d)

    write_repl(helper, "scroll top", "Scroll back up for a clean screenshot")
    time.sleep(d)
    write_repl(helper, "screenshot", "Full-page screenshot of the filled form")
    time.sleep(d + 1)
    write_repl(helper, "quit", "Close browser and exit helper")
    time.sleep(4)


def main() -> int:
    args = parse_args()
    ffmpeg = find_ffmpeg()

    if not args.config.exists():
        sys.stderr.write(
            f"\n[ERROR] config not found: {args.config}\n"
            f"Run 'python vfs_helper.py --setup' first, or copy config.demo-full.json\n"
            f"to config.json and edit your personal data.\n"
        )
        return 2

    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out_path = args.out or (HERE / "artifacts" / f"vfs-helper-real-vfs-{stamp}.mp4")

    est = (
        args.step_delay * 6
        + args.fill_delay * (args.fill_passes + (0 if args.no_fill_applicant else 1))
        + (args.fill_delay if args.with_login_fill else 0)
    )

    print("=" * 60)
    print("VFS Helper - REAL site autofill recorder")
    print("=" * 60)
    print(f"  Config      : {args.config}")
    print(f"  Output      : {out_path}")
    print(f"  Fill passes : {args.fill_passes}  (delay {args.fill_delay}s each)")
    print(f"  Profile fill: {'no' if args.no_fill_applicant else args.profile}")
    print(f"  ~{est:.0f}s of auto-typed REPL after you press Enter")
    print()
    print("  Now starting screen recording...")
    print()

    ffmpeg_proc = start_ffmpeg(ffmpeg, args.platform, out_path)
    time.sleep(2.5)

    print("\n[helper] launching vfs_helper.py against the configured start_url...")
    helper_cmd = [sys.executable, str(HERE / "vfs_helper.py"), "--config", str(args.config)]
    helper_proc = subprocess.Popen(
        helper_cmd,
        stdin=subprocess.PIPE,
        text=True,
        bufsize=1,
        cwd=str(HERE),
    )

    time.sleep(6)

    print()
    print(">>> >>> >>> MANUAL STEPS (do these in the browser): <<< <<< <<<")
    print("    1. Tick the Cloudflare 'I am human' checkbox if shown.")
    print("    2. Log in to your VFS account.")
    print("    3. Open the FULL application form (personal data, passport,")
    print("       travel, documents — not only the country dropdown on the home page).")
    print("    4. Scroll once so the first empty field is visible.")
    print()
    print(">>> When the APPLICATION FORM is on screen, press Enter here.")
    print("    The script will run inspect -> scroll -> fill (x2) -> fill applicant")
    print("    -> screenshot. It does NOT click Submit / Pay / Confirm.")
    print()

    try:
        input(">>> Press Enter to start the auto-typed REPL sequence: ")
    except KeyboardInterrupt:
        print("\n[cancelled]")
        helper_proc.terminate()
        stop_ffmpeg(ffmpeg_proc)
        return 1

    run_fill_sequence(helper_proc, args)

    try:
        helper_proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        helper_proc.terminate()

    stop_ffmpeg(ffmpeg_proc)
    time.sleep(2)

    if out_path.exists():
        size = out_path.stat().st_size
        print()
        print("=" * 60)
        print(" DONE.")
        print(f" Video : {out_path}")
        print(f" Size  : {size:,} bytes")
        print(" Tip: if some fields stayed empty, add selectors under config.fields")
        print("      (run 'inspect' on that page) and re-record.")
        print("=" * 60)
        return 0
    print("\n[WARN] Output file not found. Recording may have failed.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
