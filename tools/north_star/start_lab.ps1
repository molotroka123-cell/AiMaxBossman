# North Star lab instance (10.10.2026): candidate Bossman from the lab clone, own port and data dir.
# Never the owner's :8801 / CommandCenter data. The self-repair harness restarts it (restart/resume stage).
param([int]$Port = 8835, [string]$Lab = 'C:\Users\asd\Bossman\ns-lab-20261010',
      # NOT the installed bundle runtime (isolated, loads the installed bcc) and NOT bare Python312: the cloud-worker
      # sidecar starts with `-I`, which ignores PYTHONPATH, and Python312's site-packages carries a .pth to an OLD
      # checkout (first lab run 10.10: every worker failed "model call failed: HTTPError" in that old sidecar).
      # The lab venv (--system-site-packages) has 00_ns_lab.pth -> the lab clone, so `-I` resolves to the clone.
      [string]$Python = 'C:\Users\asd\Bossman\ns-lab-20261010\venv\Scripts\python.exe')
$repo = "$Lab\repo"
New-Item -ItemType Directory -Force "$Lab\data", "$Lab\logs" | Out-Null
$env:PYTHONPATH = "$repo\command-center;$repo\bossman-core;$repo"
$env:BCC_DATA_DIR = "$Lab\data"
$env:BCC_PORT = "$Port"
$env:PYTHONIOENCODING = 'utf-8'
$env:BOSSMAN_VERIFY_PYTHON = 'C:\Users\asd\AppData\Local\Programs\Python\Python312\python.exe'
$stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
$p = Start-Process -FilePath $Python -ArgumentList '-m', 'bcc', '--port', "$Port" -WorkingDirectory "$Lab\data" `
    -RedirectStandardOutput "$Lab\logs\backend-$stamp.out.log" -RedirectStandardError "$Lab\logs\backend-$stamp.err.log" `
    -WindowStyle Hidden -PassThru
$p.Id | Set-Content "$Lab\backend.pid"
"started pid=$($p.Id) port=$Port at $stamp"
