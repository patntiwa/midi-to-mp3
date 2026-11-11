# Composants tiers

Ce projet est sous licence MIT (voir `LICENSE`). Il s'appuie sur les composants suivants, qui ont leurs propres licences.

## Inclus dans l'exécutable `MIDI-vers-MP3.exe`

| Composant | Licence |
|---|---|
| Python | PSF License |
| Flask, Werkzeug, Jinja2, Click, ItsDangerous, MarkupSafe, Blinker | BSD 3 clauses |
| PyInstaller (chargeur de l'exécutable) | GPL 2.0 avec exception qui autorise la distribution des programmes produits |

## Non inclus, installés séparément par `installer-dependances`

Ces programmes ne sont ni dans le dépôt, ni dans le zip, ni liés au code du projet : ils sont lancés comme des programmes indépendants.

| Composant | Licence | Source |
|---|---|---|
| FluidSynth | LGPL 2.1 ou plus récente | https://github.com/FluidSynth/fluidsynth |
| FFmpeg (compilation installée par winget, paquet `Gyan.FFmpeg`) | GPL 3 (selon la compilation) | https://ffmpeg.org et https://www.gyan.dev/ffmpeg/builds/ |
| GeneralUser GS (soundfont) | Licence GeneralUser GS v2.0 : usage libre, privé ou commercial, modification et redistribution permises | https://github.com/mrbumpy409/GeneralUser-GS |

Vous pouvez utiliser une autre soundfont : déposez un fichier `.sf2` ou `.sf3` dans `%USERPROFILE%\soundfonts` et vérifiez sa licence.
