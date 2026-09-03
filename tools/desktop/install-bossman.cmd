@echo off
REM Однократная установка ярлыка BOSSMAN (двойной клик по этому файлу).
REM Ярлык запускает уже проверенное окно bcc.desktop через pythonw — без консоли.
setlocal
cd /d "%~dp0\..\.."
where py >nul 2>nul && (set PY=py -3) || (set PY=python)
%PY% -m bcc.desktop --install-shortcut
if errorlevel 1 (
  echo.
  echo Не удалось создать ярлык. Убедитесь, что установлен пакет command-center:
  echo     pip install -e command-center
  pause
  exit /b 1
)
echo.
echo Готово: ярлык BOSSMAN на рабочем столе и в меню "Пуск".
pause
