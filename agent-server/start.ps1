param([switch]$Background, [switch]$Stop)
$ErrorActionPreference = 'Stop'
$agentWorkspace = Split-Path -Parent $PSScriptRoot
$agentPython = Join-Path $agentWorkspace '.local/agent-venv/Scripts/python.exe'
$agentScript = Join-Path $PSScriptRoot 'main.py'
$agentPidFile = Join-Path $agentWorkspace '.local/agent-server.pid'
if (Test-Path -LiteralPath $agentPidFile) {
    $agentProcessId = [int]([System.IO.File]::ReadAllText($agentPidFile).Trim())
    $agentProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $agentProcessId" -ErrorAction Stop
    if ($agentProcess) {
        if ($agentProcess.ExecutablePath -ne $agentPython -or $agentProcess.CommandLine -notlike ('*' + $agentScript + '*')) {
            throw 'PID belongs to another process; refusing to stop it.'
        }
        if ($Stop) {
            # Windows venv python.exe forwards to a child interpreter. Stop the
            # verified application child as well, otherwise it may keep the port.
            $agentChildren = Get-CimInstance Win32_Process -Filter "ParentProcessId = $agentProcessId"
            foreach ($agentChild in $agentChildren) {
                if ($agentChild.Name -eq 'python.exe' -and $agentChild.CommandLine -like ('*' + $agentScript + '*')) {
                    Stop-Process -Id $agentChild.ProcessId -ErrorAction SilentlyContinue
                }
            }
            Stop-Process -Id $agentProcessId
            Remove-Item -LiteralPath $agentPidFile
            Write-Output 'Local agent server stopped.'
            return
        }
        Write-Output ('Local agent server is already running; PID=' + $agentProcessId)
        return
    }
    Remove-Item -LiteralPath $agentPidFile
}
if ($Stop) { Write-Output 'Local agent server is not running.'; return }
if (-not (Test-Path -LiteralPath $agentPython)) {
    throw 'Create .local/agent-venv and install agent-server/requirements.txt first. See README.'
}
if ($Background) {
    $agentProcess = Start-Process -FilePath $agentPython -ArgumentList @('-B', ('"' + $agentScript + '"')) `
        -WorkingDirectory $agentWorkspace -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $agentWorkspace '.local/agent-server.stdout.log') `
        -RedirectStandardError (Join-Path $agentWorkspace '.local/agent-server.stderr.log') -PassThru
    [System.IO.File]::WriteAllText($agentPidFile, [string]$agentProcess.Id)
    Write-Output ('Local agent server launched; PID=' + $agentProcess.Id + '. Check /healthz for readiness.')
    return
}
& $agentPython '-B' $agentScript
if ($LASTEXITCODE -ne 0) { throw 'Agent server stopped with an error.' }
