@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem Папка .venv привязана к Python того компьютера, где её создали.
rem Если её скопировали с другого компьютера или Python удалили, создаём заново.
if exist .venv\ (
  .venv\Scripts\python.exe -c "import flask" >nul 2>nul
  if errorlevel 1 (
    echo Окружение .venv не работает на этом компьютере, создаю заново...
    rmdir /s /q .venv
  )
)

if not exist .venv\Scripts\python.exe (
  echo Создаю окружение и ставлю библиотеки, это займёт минуту...
  python -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>nul && python -m venv .venv
)
if not exist .venv\Scripts\python.exe (
  py -3 -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>nul && py -3 -m venv .venv
)
.venv\Scripts\python.exe -c "import sys" >nul 2>nul
if errorlevel 1 (
  if exist .venv\ rmdir /s /q .venv
  echo Не найден рабочий Python 3.10 или новее.
  echo Установите Python с python.org, отметьте "Add python.exe to PATH" и запустите run.bat снова.
  pause
  exit /b 1
)

echo Проверяю библиотеки...
.venv\Scripts\python.exe -m pip install -q --disable-pip-version-check -r requirements.txt
.venv\Scripts\python.exe app.py %*
pause
