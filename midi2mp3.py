#!/usr/bin/env python3
"""
midi2mp3 - Convertit des fichiers MIDI (.mid/.midi) en MP3 haute qualité.

Moteur : FluidSynth (synthèse) + ffmpeg (encodage MP3).
La qualité du son dépend surtout de la SOUNDFONT (.sf2/.sf3) utilisée.

Exemples :
  python midi2mp3.py morceau.mid
  python midi2mp3.py morceau.mid -s GeneralUser_GS.sf2
  python midi2mp3.py ./dossier_midi -o ./dossier_mp3 --quality vbr
  python midi2mp3.py morceau.mid --gain 0.8 --normalize
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

MIDI_EXT = {".mid", ".midi", ".kar"}
SF_EXT = {".sf2", ".sf3"}

# Emplacements usuels des soundfonts (Linux, macOS, Windows)
SF_SEARCH_DIRS = [
    Path.cwd(),
    Path(__file__).resolve().parent,
    Path.home() / "soundfonts",
    Path("/usr/share/sounds/sf2"),
    Path("/usr/share/sounds/sf3"),
    Path("/usr/share/soundfonts"),
    Path("/usr/local/share/soundfonts"),
    Path("/opt/homebrew/share/soundfonts"),
    Path("C:/soundfonts"),
]
# Ordre de préférence quand plusieurs soundfonts sont trouvées
SF_PREFERRED = ["generaluser", "fluidr3", "sgm", "default"]


def die(msg: str, code: int = 1):
    print(f"Erreur : {msg}", file=sys.stderr)
    sys.exit(code)


def refresh_windows_path():
    """Windows : reprend le PATH utilisateur du registre. Un programme lancé depuis l'Explorateur ne voit
    pas un PATH modifié après sa session ; ainsi FluidSynth et ffmpeg fraîchement installés sont trouvés."""
    if os.name != "nt":
        return
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            user_path, _ = winreg.QueryValueEx(key, "Path")
    except OSError:
        return
    known = {p.lower() for p in os.environ.get("PATH", "").split(os.pathsep)}
    extra = [os.path.expandvars(p) for p in user_path.split(os.pathsep) if p and os.path.expandvars(p).lower() not in known]
    if extra:
        os.environ["PATH"] = os.environ.get("PATH", "") + os.pathsep + os.pathsep.join(extra)


def missing_tools():
    return [t for t in ("fluidsynth", "ffmpeg") if shutil.which(t) is None]


def check_tools():
    refresh_windows_path()
    missing = missing_tools()
    if missing:
        die(
            f"outil(s) introuvable(s) : {', '.join(missing)}.\n"
            "  Linux : sudo apt install fluidsynth ffmpeg\n"
            "  macOS : brew install fluid-synth ffmpeg\n"
            "  Windows : installer FluidSynth et ffmpeg, puis les ajouter au PATH."
        )


def find_soundfont(explicit):
    if explicit:
        p = Path(explicit).expanduser()
        if not p.is_file():
            die(f"soundfont introuvable : {p}")
        return p
    found = []
    for d in SF_SEARCH_DIRS:
        if d.is_dir():
            found += [f for f in d.iterdir() if f.suffix.lower() in SF_EXT]
    if not found:
        die(
            "aucune soundfont trouvée. Téléchargez-en une (ex. GeneralUser GS) "
            "et passez-la avec --soundfont chemin/vers/fichier.sf2"
        )
    for key in SF_PREFERRED:
        for f in found:
            if key in f.name.lower():
                return f
    return found[0]


def collect_inputs(src: Path, recursive: bool):
    if src.is_file():
        return [src]
    if src.is_dir():
        it = src.rglob("*") if recursive else src.iterdir()
        return sorted(f for f in it if f.suffix.lower() in MIDI_EXT)
    die(f"entrée introuvable : {src}")


WAV_HEADER = 44
WAV_BYTES_PER_FRAME = 4   # FluidSynth écrit du PCM 16 bits stéréo par défaut


def _read_vlq(data: bytes, i: int):
    value = 0
    while True:
        b = data[i]
        i += 1
        value = (value << 7) | (b & 0x7F)
        if not b & 0x80:
            return value, i


def midi_duration(path: Path):
    """Durée du morceau en secondes (carte des tempos incluse), ou None si illisible.

    Sert uniquement à estimer l'avancement du rendu : une erreur ne doit jamais bloquer."""
    try:
        data = Path(path).read_bytes()
        if data[:4] != b"MThd":
            return None
        hlen = int.from_bytes(data[4:8], "big")
        ntrks = int.from_bytes(data[10:12], "big")
        div = int.from_bytes(data[12:14], "big")
        if div == 0:
            return None
        pos = 8 + hlen
        tempos, end_tick = [], 0
        for _ in range(ntrks):
            if data[pos:pos + 4] != b"MTrk":
                break
            length = int.from_bytes(data[pos + 4:pos + 8], "big")
            i, stop = pos + 8, min(pos + 8 + length, len(data))
            pos += 8 + length
            tick, status = 0, 0
            while i < stop:
                delta, i = _read_vlq(data, i)
                tick += delta
                if data[i] >= 0x80:
                    status = data[i]
                    i += 1
                if status == 0xFF:
                    mtype = data[i]
                    n, i = _read_vlq(data, i + 1)
                    if mtype == 0x51 and n == 3:
                        tempos.append((tick, int.from_bytes(data[i:i + 3], "big")))
                    i += n
                    if mtype == 0x2F:
                        break
                elif status in (0xF0, 0xF7):
                    n, i = _read_vlq(data, i)
                    i += n
                else:
                    i += 1 if (status & 0xF0) in (0xC0, 0xD0) else 2
            end_tick = max(end_tick, tick)
        if div & 0x8000:                      # temps SMPTE : ticks par image
            fps, per_frame = 256 - (div >> 8), div & 0xFF
            return end_tick / (fps * per_frame) if fps * per_frame else None
        seconds, last, cur = 0.0, 0, 500000   # 120 BPM par défaut
        for t, us in sorted(tempos):
            if t >= end_tick:
                break
            seconds += (t - last) * cur / 1e6 / div
            last, cur = t, us
        return seconds + (end_tick - last) * cur / 1e6 / div
    except (IndexError, ValueError, OSError):
        return None


def _tail(path: Path, n: int = 800) -> str:
    try:
        return Path(path).read_text(errors="replace").strip()[-n:]
    except OSError:
        return ""


def run_synth(cmd, wav: Path, log: Path, expected_s, rate, progress, timeout=180):
    """Lance FluidSynth et suit l'avancement via la taille du WAV qui grossit."""
    start = time.monotonic()
    with open(log, "wb") as lf:
        proc = subprocess.Popen(cmd, stdout=lf, stderr=subprocess.STDOUT)
        try:
            while proc.poll() is None:
                if time.monotonic() - start > timeout:
                    proc.kill()
                    proc.wait()
                    raise RuntimeError(f"délai dépassé ({timeout} s)")
                if expected_s and wav.exists():
                    total = expected_s * rate * WAV_BYTES_PER_FRAME
                    progress("synth", min(0.99, max(0.0, (wav.stat().st_size - WAV_HEADER) / total)))
                time.sleep(0.2)
        except BaseException:
            proc.kill()
            raise
    if proc.returncode != 0:
        raise RuntimeError(_tail(log))
    progress("synth", 1.0)


def run_encode(cmd, log: Path, total_s, progress, timeout=180):
    """Lance ffmpeg avec -progress pour obtenir l'avancement réel de l'encodage."""
    cmd = [cmd[0], "-nostats", "-progress", "pipe:1", *cmd[1:]]
    timed_out = threading.Event()
    with open(log, "wb") as lf:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=lf, text=True)

        def kill():
            timed_out.set()
            proc.kill()

        timer = threading.Timer(timeout, kill)
        timer.start()
        try:
            for line in proc.stdout:
                key, _, value = line.strip().partition("=")
                if key in ("out_time_us", "out_time_ms") and total_s:
                    try:
                        progress("encode", min(0.99, max(0.0, int(value) / 1e6 / total_s)))
                    except ValueError:
                        pass          # "N/A" en début de flux
            proc.wait()
        finally:
            timer.cancel()
            if proc.poll() is None:
                proc.kill()
            proc.stdout.close()
    if timed_out.is_set():
        raise RuntimeError(f"délai dépassé ({timeout} s)")
    if proc.returncode != 0:
        raise RuntimeError(_tail(log))
    progress("encode", 1.0)


def convert(midi: Path, out: Path, sf: Path, args, progress=None) -> None:
    """Convertit un MIDI en MP3. progress(étape, fraction|None) : étape 'synth' puis 'encode'."""
    progress = progress or (lambda stage, frac: None)
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "render.wav"
        log = Path(tmp) / "tool.log"
        fs_cmd = [
            "fluidsynth", "-ni",
            "-g", str(args.gain),
            "-r", str(args.rate),
            "-R", "1" if args.reverb else "0",
            "-C", "1" if args.chorus else "0",
            "-F", str(wav),
            str(sf), str(midi),
        ]
        progress("synth", None)
        run_synth(fs_cmd, wav, log, midi_duration(midi), args.rate, progress)
        if not wav.exists() or wav.stat().st_size < 1000:
            raise RuntimeError("rendu audio vide (MIDI sans notes ?)")
        wav_seconds = (wav.stat().st_size - WAV_HEADER) / (args.rate * WAV_BYTES_PER_FRAME)

        ff_cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav)]
        filters = []
        if args.normalize:
            filters.append("loudnorm=I=-16:TP=-1.5:LRA=11")
        if filters:
            ff_cmd += ["-af", ",".join(filters)]
        ff_cmd += ["-ar", str(args.rate), "-codec:a", "libmp3lame"]
        if args.quality == "vbr":
            ff_cmd += ["-qscale:a", "0"]          # VBR V0 (~245 kbps, excellent)
        else:
            ff_cmd += ["-b:a", f"{args.bitrate}k"]  # CBR
        ff_cmd += ["-id3v2_version", "3",
                   "-metadata", f"title={getattr(args, 'title', None) or midi.stem}",
                   str(out)]
        progress("encode", 0.0)
        run_encode(ff_cmd, log, wav_seconds, progress)


def main():
    ap = argparse.ArgumentParser(description="MIDI -> MP3 haute qualité (FluidSynth + ffmpeg)")
    ap.add_argument("input", help="fichier .mid ou dossier contenant des .mid")
    ap.add_argument("-o", "--output", help="fichier .mp3 (si entrée = fichier) ou dossier de sortie")
    ap.add_argument("-s", "--soundfont", help="chemin d'une soundfont .sf2/.sf3 (sinon auto-détection)")
    ap.add_argument("-q", "--quality", choices=["cbr", "vbr"], default="cbr",
                    help="cbr = débit constant (défaut), vbr = variable V0")
    ap.add_argument("-b", "--bitrate", type=int, default=320, help="débit CBR en kbps (défaut 320)")
    ap.add_argument("-r", "--rate", type=int, default=44100, help="fréquence d'échantillonnage (défaut 44100)")
    ap.add_argument("-g", "--gain", type=float, default=0.8,
                    help="gain du synthé 0.1-2.0 (défaut 0.8, baissez si saturation)")
    ap.add_argument("--normalize", action="store_true", help="normalise le volume (loudnorm -16 LUFS)")
    ap.add_argument("--no-reverb", dest="reverb", action="store_false", help="désactive la réverbération")
    ap.add_argument("--no-chorus", dest="chorus", action="store_false", help="désactive le chorus")
    ap.add_argument("--recursive", action="store_true", help="parcourt aussi les sous-dossiers")
    ap.add_argument("--overwrite", action="store_true", help="écrase les MP3 existants")
    args = ap.parse_args()

    check_tools()
    sf = find_soundfont(args.soundfont)
    src = Path(args.input).expanduser()
    files = collect_inputs(src, args.recursive)
    if not files:
        die("aucun fichier MIDI trouvé.")

    print(f"Soundfont : {sf.name}")
    ok = fail = skipped = 0
    for f in files:
        if src.is_file():
            if args.output and Path(args.output).suffix.lower() == ".mp3":
                out = Path(args.output)
            else:
                out = Path(args.output or f.parent) / (f.stem + ".mp3")
        else:
            out_root = Path(args.output) if args.output else src
            out = out_root / f.relative_to(src).with_suffix(".mp3")
        if out.exists() and not args.overwrite:
            print(f"  = {f.name} (existe déjà, ignoré)")
            skipped += 1
            continue
        try:
            convert(f, out, sf, args)
            print(f"  ✓ {f.name} -> {out}")
            ok += 1
        except Exception as e:
            print(f"  ✗ {f.name} : {e}", file=sys.stderr)
            fail += 1
    print(f"\nTerminé : {ok} converti(s), {skipped} ignoré(s), {fail} en échec.")
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    main()
