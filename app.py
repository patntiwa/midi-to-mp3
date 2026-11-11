#!/usr/bin/env python3
"""
Interface web pour midi2mp3.

Lancement :
    pip install -r requirements.txt
    python app.py                                   # http://127.0.0.1:5000 (machine locale uniquement)
    python app.py --output-dir D:\\Musique\\MP3      # dossier racine des MP3 produits
    python app.py --host 0.0.0.0                    # accessible sur le réseau (voir README : sécurité)

Le navigateur envoie un fichier MIDI : le serveur crée une « tâche », la convertit en arrière-plan
avec FluidSynth + ffmpeg (via midi2mp3.py), enregistre le MP3 dans le dossier de sortie et
expose l'avancement (/api/jobs/<id>) puis le fichier audio (/api/jobs/<id>/audio).
"""
import argparse
import logging
import os
import re
import shutil
import socket
import string
import sys
import tempfile
import threading
import time
import uuid
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

from flask import Flask, abort, jsonify, request, send_file, send_from_directory

import midi2mp3 as engine

__version__ = "1.0.0"

# Mode « figé » : exécutable créé par PyInstaller. Les ressources (index.html) sont alors décompressées dans un
# dossier temporaire (_MEIPASS), alors que les dossiers de l'utilisateur sont à côté de l'exécutable.
FROZEN = getattr(sys, "frozen", False)
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
APP_DIR = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent
DEFAULT_OUTPUT = Path.home() / "Music" / "MIDI vers MP3" if FROZEN else APP_DIR / "output"
MAX_UPLOAD_MB = 10
MAX_PARALLEL = 2          # conversions simultanées (FluidSynth consomme du CPU)
MAX_PENDING = 40          # tâches en attente ou en cours, au maximum
JOB_TTL_SECONDS = 6 * 3600
SYNTH_WEIGHT = 0.8        # part de la synthèse dans la progression globale d'un fichier
MAX_FOLDER_DEPTH = 3

QUALITIES = {
    "cbr320": ("cbr", 320),
    "cbr256": ("cbr", 256),
    "cbr192": ("cbr", 192),
    "vbr": ("vbr", 0),
}
RESERVED_NAMES = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)),
                  *(f"lpt{i}" for i in range(1, 10))}

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024
app.config["OUTPUT_ROOT"] = DEFAULT_OUTPUT
app.config["ALLOW_BROWSE"] = False   # vrai uniquement si le serveur n'écoute qu'en local (voir main)
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
MAX_DIR_ENTRIES = 1000
log = logging.getLogger("midi2mp3-web")
slots = threading.BoundedSemaphore(MAX_PARALLEL)
jobs_lock = threading.Lock()
name_lock = threading.Lock()


@dataclass
class Job:
    id: str
    name: str
    state: str = "queued"          # queued | synth | encode | done | error
    progress: float = 0.0          # 0..1, avancement global du fichier
    error: str = ""
    rel_path: str = ""             # chemin relatif à la racine de sortie, séparateur "/"
    size: int = 0
    out_path: Path | None = None
    base: Path | None = None       # dossier de sortie choisi pour cette tâche
    created: float = field(default_factory=time.time)

    def as_dict(self) -> dict:
        d = {"id": self.id, "state": self.state, "progress": round(self.progress, 4)}
        if self.state == "error":
            d["error"] = self.error
        if self.state == "done":
            d.update(saved_as=self.rel_path, saved_path=str(self.out_path), size=self.size)
        return d


jobs: dict[str, Job] = {}


# --------------------------------------------------------------------------- utilitaires

def list_soundfonts() -> dict:
    """Nom de fichier -> chemin. Le client ne choisit jamais un chemin, seulement un nom."""
    dirs = [APP_DIR / "soundfonts", *engine.SF_SEARCH_DIRS]
    found = {}
    for d in dirs:
        if d.is_dir():
            for f in sorted(d.iterdir()):
                if f.suffix.lower() in engine.SF_EXT and f.is_file():
                    found.setdefault(f.name, f)

    def rank(name):
        low = name.lower()
        for i, key in enumerate(engine.SF_PREFERRED):
            if key in low:
                return (i, low)
        return (len(engine.SF_PREFERRED), low)

    return dict(sorted(found.items(), key=lambda kv: rank(kv[0])))


def clean_title(raw: str) -> str:
    """Titre ID3 issu du nom du fichier d'origine : texte brut, sans caractères de contrôle."""
    text = "".join(ch for ch in raw if ch.isprintable())
    return text.strip()[:100]


def safe_segment(raw: str) -> str:
    """Un nom de fichier ou de dossier valide sous Windows, Linux et macOS."""
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", raw)
    text = re.sub(r"\s+", " ", text).strip(" .")[:100].strip(" .")
    if text.lower().split(".")[0] in RESERVED_NAMES:
        text = "_" + text
    return text


def resolve_output_dir(raw: str) -> Path | None:
    """Dossier de sortie choisi dans l'interface. Vide = racine du serveur. None si refusé."""
    raw = raw.strip()
    if not raw:
        return app.config["OUTPUT_ROOT"]
    if not app.config["ALLOW_BROWSE"]:
        return None
    p = Path(raw)
    if not p.is_absolute() or not p.is_dir():
        return None
    return p.resolve()


def resolve_folder(raw: str, root: Path) -> Path | None:
    """Sous-dossier demandé par le client -> chemin absolu SOUS `root`, ou None."""
    parts = [p for p in re.split(r"[\\/]+", raw.strip()) if p.strip()]
    if len(parts) > MAX_FOLDER_DEPTH or any(p.strip() in (".", "..") for p in parts):
        return None
    cleaned = [safe_segment(p) for p in parts]
    if any(not p for p in cleaned):
        return None
    target = root.joinpath(*cleaned).resolve()
    return target if target == root or target.is_relative_to(root) else None


def reserve_path(folder: Path, stem: str) -> Path:
    """Crée un fichier vide au premier nom libre ('x', 'x (2)', ...) : jamais d'écrasement."""
    folder.mkdir(parents=True, exist_ok=True)
    with name_lock:
        n = 1
        while True:
            path = folder / (f"{stem}.mp3" if n == 1 else f"{stem} ({n}).mp3")
            try:
                with open(path, "x"):
                    pass
                return path
            except FileExistsError:
                n += 1


def purge_old_jobs():
    limit = time.time() - JOB_TTL_SECONDS
    with jobs_lock:
        for jid in [j for j, job in jobs.items() if job.created < limit and job.state in ("done", "error")]:
            del jobs[jid]


def error(message: str, status: int):
    return jsonify({"error": message}), status


# --------------------------------------------------------------------------- travail en arrière-plan

def run_job(job: Job, data: bytes, sf: Path, opts, target: Path):
    try:
        with slots:                                 # reste « queued » tant qu'aucun créneau n'est libre
            with tempfile.TemporaryDirectory() as tmp:
                src = Path(tmp) / "input.mid"       # nom fixe : jamais le nom envoyé par le client
                out = Path(tmp) / "output.mp3"
                src.write_bytes(data)

                def on_progress(stage, frac):
                    job.state = stage
                    if frac is None:
                        return
                    job.progress = max(job.progress, frac * SYNTH_WEIGHT if stage == "synth"
                                       else SYNTH_WEIGHT + frac * (1 - SYNTH_WEIGHT))

                engine.convert(src, out, sf, opts, on_progress)
                part = target.with_name(target.name + ".part")
                shutil.copyfile(out, part)          # le dossier cible peut être sur un autre disque
                os.replace(part, target)
        job.out_path = target
        job.rel_path = target.relative_to(job.base).as_posix()
        job.size = target.stat().st_size
        job.progress = 1.0
        job.state = "done"
    except Exception:
        log.exception("Échec de conversion")
        target.unlink(missing_ok=True)
        target.with_name(target.name + ".part").unlink(missing_ok=True)
        job.error = "La conversion a échoué (MIDI vide ou corrompu ?)."
        job.state = "error"


# --------------------------------------------------------------------------- routes

@app.before_request
def same_origin_only():
    """Refuse les POST venant d'une autre origine (une page tierce ne doit pas piloter ce serveur local),
    et, en mode local, toute requête dont l'hôte n'est pas localhost (protection contre le DNS rebinding)."""
    if request.method == "POST":
        origin = request.headers.get("Origin")
        if origin and urlparse(origin).netloc != request.host:
            abort(403)
    if app.config["ALLOW_BROWSE"] and urlparse("//" + request.host).hostname not in LOOPBACK:
        abort(403)


@app.get("/")
def index():
    return send_from_directory(RESOURCE_DIR, "index.html")


@app.get("/api/config")
def api_config():
    return jsonify({"output_root": str(app.config["OUTPUT_ROOT"]), "max_upload_mb": MAX_UPLOAD_MB,
                    "can_browse": app.config["ALLOW_BROWSE"], "version": __version__})


def list_drives() -> list[str]:
    if os.name == "nt":
        return [f"{c}:\\" for c in string.ascii_uppercase if os.path.exists(f"{c}:\\")]
    return ["/"]


@app.get("/api/browse")
def api_browse():
    """Liste les sous-dossiers d'un dossier (jamais les fichiers) pour le sélecteur de l'interface."""
    if not app.config["ALLOW_BROWSE"]:
        abort(403)
    raw = request.args.get("path", "").strip()
    if not raw:                                      # niveau « racines » : lecteurs / "/"
        return jsonify({"path": "", "parent": None, "writable": False,
                        "dirs": [{"name": d, "path": d} for d in list_drives()]})
    p = Path(raw)
    if not p.is_absolute() or not p.is_dir():
        return error("Dossier introuvable.", 400)
    p = p.resolve()
    dirs = []
    try:
        for e in sorted(os.scandir(p), key=lambda e: e.name.lower()):
            if e.name.startswith((".", "$")):
                continue
            try:
                if e.is_dir():
                    dirs.append({"name": e.name, "path": str(Path(e.path))})
            except OSError:
                continue
            if len(dirs) >= MAX_DIR_ENTRIES:
                break
    except PermissionError:
        return error("Accès refusé à ce dossier.", 403)
    except OSError:
        return error("Dossier illisible.", 400)
    return jsonify({"path": str(p), "parent": "" if p.parent == p else str(p.parent),
                    "writable": os.access(p, os.W_OK), "dirs": dirs})


@app.post("/api/mkdir")
def api_mkdir():
    if not app.config["ALLOW_BROWSE"]:
        abort(403)
    body = request.get_json(silent=True) or {}
    parent, name = Path(str(body.get("parent", ""))), safe_segment(str(body.get("name", "")))
    if not name or not parent.is_absolute() or not parent.is_dir():
        return error("Nom ou dossier parent invalide.", 400)
    target = parent.resolve() / name
    try:
        target.mkdir()
    except FileExistsError:
        return error("Ce dossier existe déjà.", 409)
    except OSError:
        return error("Impossible de créer ce dossier ici.", 403)
    return jsonify({"path": str(target)}), 201


@app.get("/api/soundfonts")
def api_soundfonts():
    return jsonify([
        {"name": name, "size_mb": round(p.stat().st_size / 1048576, 1)}
        for name, p in list_soundfonts().items()
    ])


@app.errorhandler(413)
def too_large(_):
    return error(f"Fichier trop volumineux (maximum {MAX_UPLOAD_MB} Mo).", 413)


@app.errorhandler(403)
def forbidden(_):
    return error("Requête refusée.", 403)


@app.post("/api/jobs")
def api_create_job():
    upload = request.files.get("file")
    if upload is None:
        return error("Aucun fichier reçu.", 400)

    data = upload.read()
    if not data.startswith(b"MThd"):
        return error("Ce fichier n’est pas un MIDI valide.", 400)

    fonts = list_soundfonts()
    sf_name = request.form.get("soundfont", "")
    if sf_name not in fonts:
        return error("Soundfont inconnue ou absente sur le serveur.", 400)

    quality_key = request.form.get("quality", "cbr320")
    if quality_key not in QUALITIES:
        return error("Qualité invalide.", 400)
    mode, bitrate = QUALITIES[quality_key]

    try:
        gain = float(request.form.get("gain", "0.8"))
    except ValueError:
        return error("Volume invalide.", 400)
    if not 0.1 <= gain <= 2.0:
        return error("Volume hors limites (0,1 à 2,0).", 400)

    base = resolve_output_dir(request.form.get("output_dir", ""))
    if base is None:
        return error("Dossier de sortie refusé ou introuvable.", 400)
    folder = resolve_folder(request.form.get("folder", ""), base)
    if folder is None:
        return error(f"Sous-dossier invalide (au plus {MAX_FOLDER_DEPTH} niveaux, sans « .. »).", 400)

    title = clean_title(request.form.get("title", "")) or "morceau"
    opts = SimpleNamespace(
        gain=gain,
        rate=44100,
        reverb=request.form.get("reverb") == "1",
        chorus=request.form.get("chorus") == "1",
        normalize=request.form.get("normalize") == "1",
        quality=mode,
        bitrate=bitrate,
        title=title,
    )

    purge_old_jobs()
    with jobs_lock:
        if sum(1 for j in jobs.values() if j.state not in ("done", "error")) >= MAX_PENDING:
            return error("Trop de conversions en attente, réessayez dans un instant.", 503)
        job = Job(id=uuid.uuid4().hex, name=title, base=base)
        jobs[job.id] = job

    try:
        target = reserve_path(folder, safe_segment(title) or "morceau")
    except OSError:
        log.exception("Dossier de sortie inaccessible")
        job.state, job.error = "error", "Impossible d’écrire dans le dossier de sortie."
        return jsonify(job.as_dict()), 500

    threading.Thread(target=run_job, args=(job, data, fonts[sf_name], opts, target), daemon=True).start()
    return jsonify(job.as_dict()), 202


def get_job(job_id: str) -> Job:
    with jobs_lock:
        job = jobs.get(job_id)
    if job is None:
        abort(404)
    return job


@app.get("/api/jobs/<job_id>")
def api_job(job_id):
    return jsonify(get_job(job_id).as_dict())


@app.get("/api/jobs/<job_id>/audio")
def api_job_audio(job_id):
    job = get_job(job_id)
    if job.state != "done" or job.out_path is None or not job.out_path.is_file():
        abort(404)
    return send_file(job.out_path, mimetype="audio/mpeg", conditional=True,   # Range : lecture/déplacement
                     as_attachment=request.args.get("download") == "1",
                     download_name=job.out_path.name, max_age=0)


@app.errorhandler(404)
def not_found(_):
    return error("Introuvable.", 404)


def find_free_port(host: str, start: int, tries: int = 50) -> int:
    """Premier port libre à partir de `start`. On vérifie qu'on peut s'y lier ET que personne n'y écoute déjà."""
    check_host = "127.0.0.1" if host in ("", "0.0.0.0") else host
    for port in range(start, start + tries):
        with socket.socket(socket.AF_INET6 if ":" in check_host else socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.3)
            if probe.connect_ex((check_host, port)) == 0:      # quelqu'un écoute déjà
                continue
        with socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((host, port))
            except OSError:
                continue
            return port
    raise OSError(f"Aucun port libre entre {start} et {start + tries - 1}.")


def wait_for_key():
    """Dans l'exécutable, la console se fermerait aussitôt : on laisse le temps de lire le message."""
    if FROZEN:
        try:
            input("\nAppuyez sur Entrée pour fermer cette fenêtre…")
        except EOFError:
            pass


def main():
    ap = argparse.ArgumentParser(description="MIDI vers MP3 : interface web locale")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5000, help="port souhaité (le suivant libre est pris s'il est occupé)")
    ap.add_argument("--output-dir", default=str(DEFAULT_OUTPUT),
                    help=f"dossier racine où les MP3 sont enregistrés (défaut : {DEFAULT_OUTPUT})")
    ap.add_argument("--no-browser", action="store_true", help="n'ouvre pas le navigateur au démarrage")
    ap.add_argument("--version", action="version", version=f"MIDI vers MP3 {__version__}")
    args = ap.parse_args()

    try:
        engine.refresh_windows_path()
        missing = engine.missing_tools()
        if missing:
            print(f"\nOutil(s) introuvable(s) : {', '.join(missing)}.\n"
                  "Double-cliquez sur « installer-dependances.bat » (dans le même dossier que ce programme), puis relancez-le.\n"
                  "Installation manuelle : voir le README (FluidSynth et ffmpeg doivent être dans le PATH).")
            wait_for_key()
            sys.exit(1)

        root = Path(args.output_dir).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        app.config["OUTPUT_ROOT"] = root
        app.config["ALLOW_BROWSE"] = args.host in LOOPBACK   # sélecteur de dossiers : local uniquement

        port = find_free_port(args.host, args.port)
        shown_host = "127.0.0.1" if args.host in ("", "0.0.0.0") else args.host
        url = f"http://{shown_host}:{port}"

        logging.basicConfig(level=logging.INFO)
        logging.getLogger("werkzeug").setLevel(logging.WARNING)   # pas de bruit : on affiche notre propre bandeau
        print(f"MIDI vers MP3 {__version__}")
        if port != args.port:
            print(f"Le port {args.port} est occupé : utilisation du port {port}.")
        print(f"Adresse            : {url}")
        print(f"Dossier de sortie  : {root}")
        if not list_soundfonts():
            print("Attention : aucune soundfont trouvée. Lancez « installer-dependances.bat » ou placez un fichier "
                  f".sf2 dans {Path.home() / 'soundfonts'}")
        print("\nFermez cette fenêtre (ou Ctrl+C) pour arrêter le serveur.\n")

        if not args.no_browser:
            threading.Timer(1.0, webbrowser.open, args=(url,)).start()
        app.run(host=args.host, port=port, threaded=True)
    except KeyboardInterrupt:
        pass
    except SystemExit:
        raise
    except Exception as e:                      # démarrage impossible : message lisible plutôt qu'une console qui se ferme
        print(f"\nErreur au démarrage : {e}")
        wait_for_key()
        sys.exit(1)


if __name__ == "__main__":
    main()
