@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem Проверка ИИ: отправляет одну фразу в GigaChat и показывает ответ и время.
rem Можно передать свою фразу: check_ai.bat папе на день рождения
if not exist .venv\Scripts\python.exe (
  echo Сначала один раз запустите run.bat, он поставит всё нужное.
  pause
  exit /b 1
)
set PYTHONIOENCODING=utf-8
.venv\Scripts\python.exe ai.py %*
pause
