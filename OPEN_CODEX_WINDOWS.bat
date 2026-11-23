@echo off
setlocal
title EmoLabel 2.0 - Codex
wsl.exe -d Ubuntu -- bash -lc "cd ~/emolabel2 && if command -v codex >/dev/null 2>&1; then exec codex; else echo 'Codex CLI is not installed in WSL Ubuntu'; exit 2; fi"
if errorlevel 1 pause
