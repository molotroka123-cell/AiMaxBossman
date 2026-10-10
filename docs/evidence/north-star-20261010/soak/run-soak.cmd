@echo off
rem 24h UX/API/CLI soak of Bossman source snapshot 44caf013 (fake model, isolated data dir, port 8871)
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
cd /d C:\Users\asd\Bossman\soak-20261010
"C:\Users\asd\AppData\Local\Programs\Python\Python312\python.exe" -X utf8 "C:\Users\asd\Bossman\soak-20261010\src\tools\ux_soak\soak.py" --launch server --port 8871 --stub-port 8878 --dead-port 8879 --cdp-port 8877 --data-dir "C:\Users\asd\Bossman\soak-20261010\data" --out "C:\Users\asd\Bossman\soak-20261010\out" --profile "C:\Users\asd\Bossman\soak-20261010\rc19-ux-profile-soak" --pylib "C:\Users\asd\Bossman\soak-20261010\pylib" --minutes 1440 --max-interactions 1000000 --restart-every 25 --pause 1 > "C:\Users\asd\Bossman\soak-20261010\out\soak.stdout.log" 2>&1
echo exit=%ERRORLEVEL% >> "C:\Users\asd\Bossman\soak-20261010\out\soak.exit.txt"
