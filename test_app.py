"""Tests : python -m unittest -v   (le test de bout en bout exige fluidsynth, ffmpeg et une soundfont)."""
import contextlib
import io
import shutil
import socket
import sys
import tempfile
import time
import unittest
from io import BytesIO
from pathlib import Path
from unittest import mock

import app as web
import midi2mp3 as engine


def make_midi(tempo_us=500000, division=480, beats=4, tempo_change=None) -> bytes:
    """MIDI format 0 : une note qui dure `beats` noires. tempo_change=(beat, µs) ajoute un changement."""
    def vlq(n):
        out = [n & 0x7F]
        n >>= 7
        while n:
            out.append((n & 0x7F) | 0x80)
            n >>= 7
        return bytes(reversed(out))

    ev = b"\x00\xff\x51\x03" + tempo_us.to_bytes(3, "big") + b"\x00\xc0\x00" + b"\x00\x90\x3c\x64"
    if tempo_change:
        beat, us = tempo_change
        ev += vlq(division * beat) + b"\xff\x51\x03" + us.to_bytes(3, "big")
        ev += vlq(division * (beats - beat)) + b"\x80\x3c\x40"
    else:
        ev += vlq(division * beats) + b"\x80\x3c\x40"
    ev += b"\x00\xff\x2f\x00"
    return (b"MThd" + (6).to_bytes(4, "big") + (0).to_bytes(2, "big") + (1).to_bytes(2, "big")
            + division.to_bytes(2, "big") + b"MTrk" + len(ev).to_bytes(4, "big") + ev)


class MidiDuration(unittest.TestCase):
    def duration(self, data):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "t.mid"
            p.write_bytes(data)
            return engine.midi_duration(p)

    def test_constant_tempo(self):
        self.assertAlmostEqual(self.duration(make_midi()), 2.0, places=3)          # 4 noires à 120 BPM

    def test_tempo_change(self):
        # 2 noires à 120 BPM (1 s) puis 2 noires à 60 BPM (2 s)
        self.assertAlmostEqual(self.duration(make_midi(tempo_change=(2, 1000000))), 3.0, places=3)

    def test_invalid_is_none(self):
        self.assertIsNone(self.duration(b"pas un midi"))
        truncated = self.duration(make_midi()[:-9])                                  # coupé en plein événement
        self.assertTrue(truncated is None or truncated >= 0)                         # jamais d'exception


class Startup(unittest.TestCase):
    def test_free_port_skips_busy_port(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            s.listen()
            busy = s.getsockname()[1]
            free = web.find_free_port("127.0.0.1", busy)
            self.assertNotEqual(free, busy)
            self.assertGreater(free, busy)

    def test_config_exposes_version(self):
        self.assertEqual(web.app.test_client().get("/api/config").get_json()["version"], web.__version__)

    def test_missing_tools_are_named(self):
        with mock.patch("midi2mp3.shutil.which", return_value=None):
            self.assertEqual(engine.missing_tools(), ["fluidsynth", "ffmpeg"])
        with mock.patch("midi2mp3.shutil.which", side_effect=lambda t: "x" if t == "ffmpeg" else None):
            self.assertEqual(engine.missing_tools(), ["fluidsynth"])

    def test_main_names_missing_tool_and_waits_before_exit(self):
        out = io.StringIO()
        with mock.patch.object(engine, "missing_tools", return_value=["ffmpeg"]), \
             mock.patch.object(web, "wait_for_key") as wait, \
             mock.patch.object(sys, "argv", ["app.py", "--no-browser"]), \
             contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as raised:
            web.main()
        self.assertEqual(raised.exception.code, 1)
        self.assertIn("ffmpeg", out.getvalue())
        self.assertIn("installer-dependances", out.getvalue())     # dit quoi faire
        wait.assert_called_once()                                   # la console ne se ferme pas aussitôt

    def test_resources_follow_frozen_mode(self):
        self.assertTrue((web.RESOURCE_DIR / "index.html").is_file())       # index.html trouvé hors mode figé
        self.assertEqual(web.DEFAULT_OUTPUT.name, "output" if not web.FROZEN else "MIDI vers MP3")


class PathSafety(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = Path(self.tmp).resolve()
        web.app.config["OUTPUT_ROOT"] = self.root

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_rejects_traversal_and_depth(self):
        for bad in ("..", "../x", "a/../../b", "a/./b", "a/b/c/d"):
            self.assertIsNone(web.resolve_folder(bad, self.root), bad)

    def test_stays_under_root(self):
        for raw in ("", "album", "a/b", "C:\\evil", "/etc/passwd", "con", 'x<>:"|?*'):
            p = web.resolve_folder(raw, self.root)
            self.assertIsNotNone(p, raw)
            self.assertTrue(p == self.root or p.is_relative_to(self.root), raw)

    def test_filename_sanitized(self):
        self.assertEqual(web.safe_segment('a/b\\c:d*e'), "a_b_c_d_e")
        self.assertEqual(web.safe_segment("  ..nom.. "), "nom")
        self.assertEqual(web.safe_segment("NUL"), "_NUL")

    def test_reserve_never_overwrites(self):
        a = web.reserve_path(self.root, "song")
        b = web.reserve_path(self.root, "song")
        self.assertEqual((a.name, b.name), ("song.mp3", "song (2).mp3"))


class FolderPicker(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = Path(self.tmp).resolve()
        (self.root / "zeta").mkdir()
        (self.root / "Alpha").mkdir()
        (self.root / ".cache").mkdir()
        (self.root / "fichier.txt").write_text("x")
        web.app.config["OUTPUT_ROOT"] = self.root
        web.app.config["ALLOW_BROWSE"] = True
        self.client = web.app.test_client()

    def tearDown(self):
        web.app.config["ALLOW_BROWSE"] = False
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_lists_only_visible_folders_sorted(self):
        j = self.client.get("/api/browse", query_string={"path": str(self.root)}).get_json()
        self.assertEqual([d["name"] for d in j["dirs"]], ["Alpha", "zeta"])   # ni fichiers ni dossiers cachés
        self.assertEqual(j["parent"], str(self.root.parent))
        self.assertTrue(j["writable"])

    def test_root_level_lists_drives_and_top_has_no_parent_above(self):
        j = self.client.get("/api/browse").get_json()
        self.assertTrue(j["dirs"] and j["parent"] is None and not j["writable"])
        top = Path(self.root.anchor)
        self.assertEqual(self.client.get("/api/browse", query_string={"path": str(top)}).get_json()["parent"], "")

    def test_rejects_bad_paths(self):
        for p in ("relatif/dossier", str(self.root / "fichier.txt"), str(self.root / "absent")):
            self.assertEqual(self.client.get("/api/browse", query_string={"path": p}).status_code, 400, p)

    def test_mkdir(self):
        r = self.client.post("/api/mkdir", json={"parent": str(self.root), "name": "Nouveau/../x"})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(Path(r.get_json()["path"]).parent, self.root)       # le nom est assaini, pas de remontée
        self.assertEqual(self.client.post("/api/mkdir", json={"parent": str(self.root), "name": "zeta"}).status_code, 409)
        self.assertEqual(self.client.post("/api/mkdir", json={"parent": "rel", "name": "a"}).status_code, 400)

    def test_disabled_when_exposed(self):
        web.app.config["ALLOW_BROWSE"] = False
        self.assertEqual(self.client.get("/api/browse").status_code, 403)
        self.assertEqual(self.client.post("/api/mkdir", json={"parent": str(self.root), "name": "a"}).status_code, 403)
        self.assertIsNone(web.resolve_output_dir(str(self.root)))             # pas de dossier arbitraire
        self.assertEqual(web.resolve_output_dir(""), self.root)

    def test_dns_rebinding_refused(self):
        r = self.client.get("/api/browse", headers={"Host": "evil.example:5000"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.client.get("/api/config", headers={"Host": "127.0.0.1:5000"}).status_code, 200)


@unittest.skipUnless(shutil.which("fluidsynth") and shutil.which("ffmpeg") and web.list_soundfonts(),
                     "fluidsynth, ffmpeg ou soundfont absents")
class EndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = Path(self.tmp).resolve()
        web.app.config["OUTPUT_ROOT"] = self.root
        self.client = web.app.test_client()
        self.sf = next(iter(web.list_soundfonts()))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def post(self, data=None, folder="", title="mon morceau", origin=None):
        headers = {"Origin": origin} if origin else {}
        return self.client.post("/api/jobs", headers=headers, data={
            "file": (BytesIO(data if data is not None else make_midi(beats=8)), "x.mid"),
            "title": title, "soundfont": self.sf, "folder": folder, "quality": "cbr192", "gain": "0.8",
        }, content_type="multipart/form-data")

    def wait(self, job_id, timeout=60):
        seen, end = [], time.time() + timeout
        while time.time() < end:
            s = self.client.get(f"/api/jobs/{job_id}").get_json()
            seen.append((s["state"], s["progress"]))
            if s["state"] in ("done", "error"):
                return s, seen
            time.sleep(0.1)
        self.fail("délai dépassé")

    def test_convert_saves_in_subfolder_and_streams(self):
        r = self.post(folder="album/jour1")
        self.assertEqual(r.status_code, 202)
        status, seen = self.wait(r.get_json()["id"])
        self.assertEqual(status["state"], "done", status)
        self.assertEqual(status["saved_as"], "album/jour1/mon morceau.mp3")
        saved = self.root / "album" / "jour1" / "mon morceau.mp3"
        self.assertTrue(saved.is_file() and saved.stat().st_size > 1000)
        self.assertFalse(list(self.root.rglob("*.part")))
        progresses = [p for _, p in seen]
        self.assertEqual(progresses, sorted(progresses), "la progression ne doit jamais reculer")
        self.assertEqual(status["progress"], 1.0)

        audio = self.client.get(f"/api/jobs/{r.get_json()['id']}/audio", headers={"Range": "bytes=0-99"})
        self.assertEqual(audio.status_code, 206)                  # lecture / déplacement dans le navigateur
        self.assertEqual(audio.mimetype, "audio/mpeg")
        audio.close()

    def test_saves_in_folder_chosen_in_interface(self):
        chosen = Path(tempfile.mkdtemp()).resolve()          # un dossier totalement hors de la racine par défaut
        web.app.config["ALLOW_BROWSE"] = True
        try:
            r = self.client.post("/api/jobs", data={
                "file": (BytesIO(make_midi()), "x.mid"), "title": "choisi", "soundfont": self.sf,
                "quality": "cbr192", "output_dir": str(chosen), "folder": "sous"}, content_type="multipart/form-data")
            self.assertEqual(r.status_code, 202)
            status, _ = self.wait(r.get_json()["id"])
            self.assertEqual(status["state"], "done", status)
            self.assertEqual(Path(status["saved_path"]), chosen / "sous" / "choisi.mp3")
            self.assertTrue((chosen / "sous" / "choisi.mp3").is_file())
            self.assertFalse(any(self.root.rglob("*.mp3")))
            bad = self.client.post("/api/jobs", data={
                "file": (BytesIO(make_midi()), "x.mid"), "title": "t", "soundfont": self.sf,
                "output_dir": str(chosen / "absent")}, content_type="multipart/form-data")
            self.assertEqual(bad.status_code, 400)
        finally:
            web.app.config["ALLOW_BROWSE"] = False
            shutil.rmtree(chosen, ignore_errors=True)

    def test_output_dir_ignored_refused_when_exposed(self):
        r = self.client.post("/api/jobs", data={
            "file": (BytesIO(make_midi()), "x.mid"), "title": "t", "soundfont": self.sf,
            "output_dir": str(self.root)}, content_type="multipart/form-data")
        self.assertEqual(r.status_code, 400)

    def test_same_name_twice_keeps_both(self):
        ids = [self.post().get_json()["id"] for _ in range(2)]
        names = sorted(self.wait(i)[0]["saved_as"] for i in ids)
        self.assertEqual(names, ["mon morceau (2).mp3", "mon morceau.mp3"])

    def test_rejections(self):
        self.assertEqual(self.post(folder="../dehors").status_code, 400)
        self.assertEqual(self.post(data=b"not midi").status_code, 400)
        self.assertEqual(self.post(origin="http://evil.example").status_code, 403)
        self.assertFalse(any(self.root.rglob("*.mp3")))

    def test_corrupt_midi_fails_cleanly(self):
        r = self.post(data=make_midi()[:20] + b"\xff" * 10)       # en-tête valide, piste cassée
        status, _ = self.wait(r.get_json()["id"])
        if status["state"] == "error":                            # FluidSynth peut aussi produire un fichier vide
            self.assertEqual(list(self.root.rglob("*.mp3")), [])
            self.assertEqual(self.client.get(f"/api/jobs/{r.get_json()['id']}/audio").status_code, 404)


if __name__ == "__main__":
    unittest.main()
