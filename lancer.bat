@echo off
rem Lance le serveur MIDI vers MP3 (le navigateur s'ouvre tout seul).
rem Arguments possibles : --output-dir "D:\Musique\MP3"   --no-browser   --port 5000
cd /d "%~dp0"
python app.py %*
echo.
echo Le serveur s'est arrete.
pause
