@echo off
rem Lance l'installation des dependances (FFmpeg, FluidSynth, soundfont) sans droits administrateur.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer-dependances.ps1" %*
