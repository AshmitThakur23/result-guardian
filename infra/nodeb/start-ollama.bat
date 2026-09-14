@echo off
REM ===================================================================
REM  Result Guardian - NODE B - start Ollama at login
REM
REM  Drop a shortcut to this in:
REM      shell:startup      (per-user)
REM
REM  WHY THIS FILE EXISTS
REM  -------------------
REM  On 2026-09-15 NODE B was checked for every autostart mechanism --
REM  HKCU\Run, HKLM\Run, WOW6432Node, the user and all-users Startup
REM  folders, scheduled tasks, and Windows services. Ollama was in NONE
REM  of them. What kept it running was a manual `ollama serve` in a
REM  shell that happened to carry the right variables.
REM
REM  So the failure after a reboot is not "the models went missing".
REM  It is CONNECTION REFUSED -- no server at all, and nothing to bring
REM  one back.
REM
REM  WHY THE VARIABLES ARE SET HERE AND NOT IN THE REGISTRY
REM  ------------------------------------------------------
REM  Because the registry was already tried and it did not work.
REM  OLLAMA_MODELS sat at Machine scope for four days while the tray app
REM  went on using C:\Users\asus\.ollama\models -- server-1.log from
REM  2026-09-11 shows `total blobs: 0`. `ollama app.exe --hide
REM  --fast-startup` hands its child a sanitised environment, so SCOPE
REM  IS NOT THE MECHANISM. Setting them inline, in the same shell that
REM  launches the server, is what actually reaches the process.
REM
REM  This bypasses the tray app entirely, which is the point.
REM
REM  HOW TO VERIFY -- and do not trust the registry for this
REM  ------------------------------------------------------
REM      ollama ps            -> mistral:7b, UNTIL: Forever
REM      ollama list          -> qwen3:4b AND mistral:7b
REM
REM  `UNTIL: Forever` is the only honest proof KEEP_ALIVE reached the
REM  server, because it comes from the process that consumed it rather
REM  than from the place somebody wrote it. A config value is not
REM  verified until something has READ it.
REM
REM  TO UNDO: delete the shortcut from shell:startup. Nothing installed,
REM  nothing elevated, nothing to uninstall.
REM ===================================================================

REM D:, not C:. The `Nexus AI` project's mistral:7b lives alongside ours
REM and repointing this without moving the blobs makes both vanish from
REM `ollama list`.
set "OLLAMA_MODELS=D:\Nexus AI\.ollama\models"

REM Bind to the LAN, not just localhost -- NODE A cannot reach 127.0.0.1.
set "OLLAMA_HOST=0.0.0.0:11434"

REM Pin the model in VRAM. Without this the first request after ~5 min
REM idle stalls 20+ seconds while the model reloads, which during a demo
REM looks exactly like the system being broken.
set "OLLAMA_KEEP_ALIVE=-1"

set "OLLAMA_NUM_PARALLEL=2"

REM One at a time. The RTX 3050 has 4 GB and mistral:7b is 4.4 GB, so it
REM already runs partly on the CPU; a second resident model would make
REM that considerably worse.
set "OLLAMA_MAX_LOADED_MODELS=1"

start "" /B "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" serve
