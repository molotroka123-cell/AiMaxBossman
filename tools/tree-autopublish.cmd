@echo off
rem Tree site autopublish, SAFE version (18.10). Replaces the old C:\Users\asd\Bossman\tree-autopublish.cmd, which published from a
rem stale worktree (HEAD 10.10, stale branch) -> the public site showed an old SHA / 13 zones.
rem
rem * builds a throw-away DETACHED worktree of origin/main (never an old checkout), removes it afterwards;
rem * runs tools\tree_publish_site.py from THAT worktree: DRY-RUN unless BOSSMAN_OWNER_APPROVED_PUBLISH=1 is set in the environment
rem   (then --push is added; the publisher still refuses a stale/dirty tree, inconsistent seed/registry/meta, failing site tests);
rem * logs to %BOSSMAN_HOME%\tree-autopublish.log (default C:\Users\asd\Bossman).
rem The scheduled task is NOT changed by this file; the owner decides whether to point it here.
setlocal
if "%BOSSMAN_HOME%"=="" set "BOSSMAN_HOME=C:\Users\asd\Bossman"
if "%BOSSMAN_REPO%"=="" set "BOSSMAN_REPO=%BOSSMAN_HOME%\integration-20261006"
if "%BOSSMAN_PY%"=="" set "BOSSMAN_PY=%BOSSMAN_HOME%\venv-verify-20261009\Scripts\python.exe"
set "LOG=%BOSSMAN_HOME%\tree-autopublish.log"
set "WT=%TEMP%\tree-autopublish-%RANDOM%%RANDOM%"

echo [%DATE% %TIME%] start >> "%LOG%"
git -C "%BOSSMAN_REPO%" fetch -q origin main >> "%LOG%" 2>&1
if errorlevel 1 ( echo [%DATE% %TIME%] REFUSED: fetch origin main failed >> "%LOG%" & exit /b 2 )
git -C "%BOSSMAN_REPO%" worktree add --detach "%WT%" origin/main >> "%LOG%" 2>&1
if errorlevel 1 ( echo [%DATE% %TIME%] REFUSED: cannot create the clean worktree >> "%LOG%" & exit /b 2 )

set "PUSHFLAG="
if "%BOSSMAN_OWNER_APPROVED_PUBLISH%"=="1" set "PUSHFLAG=--push"
if "%PUSHFLAG%"=="" ( echo [%DATE% %TIME%] mode DRY-RUN ^(BOSSMAN_OWNER_APPROVED_PUBLISH is not 1^) >> "%LOG%" ) else ( echo [%DATE% %TIME%] mode PUSH ^(owner approved^) >> "%LOG%" )

"%BOSSMAN_PY%" "%WT%\tools\tree_publish_site.py" --source "%WT%" %PUSHFLAG% >> "%LOG%" 2>&1
set "RC=%ERRORLEVEL%"
echo [%DATE% %TIME%] publisher exit %RC% >> "%LOG%"

git -C "%BOSSMAN_REPO%" worktree remove --force "%WT%" >> "%LOG%" 2>&1
git -C "%BOSSMAN_REPO%" worktree prune >> "%LOG%" 2>&1
endlocal & exit /b %RC%
