# Runs the e2e tests on a Windows runner as a separate user whose outbound
# network traffic is limited to the Claude API by Windows Firewall. GitHub's
# egress-firewall runner is Linux only, so this stands in for it on Windows.
#
# The runner account is an administrator, so the tests (and anything Claude
# runs during them) must not run as that account: they could turn the firewall
# off. The e2e user is a standard user. It runs through a scheduled task, which
# works from the runner's non-interactive session.
#
# Limits: DNS lookups go through the DNS Client service, not the e2e user's own
# process, so they are not filtered.
#
# Usage: run-e2e-firewalled-windows.ps1 <pytest args...>
# Needs ANTHROPIC_IDENTITY_TOKEN_FILE and the ANTHROPIC_* federation variables,
# `python` from actions/setup-python and `claude` on PATH.
$ErrorActionPreference = 'Stop'

$E2EUser = 'claude-e2e'
$E2EDir = 'C:\claude-e2e'
$BinDir = Join-Path $E2EDir 'bin'
$PytestArgs = $args

# Complement of the published address ranges of api.anthropic.com
# (160.79.104.0/23, 2607:6bc0::/48) and of loopback. The block rule covers
# these; Windows Firewall lets a block rule win over any allow rule.
$BlockedRanges = @(
  '0.0.0.0-126.255.255.255',
  '128.0.0.0-160.79.103.255',
  '160.79.106.0-255.255.255.255',
  '::2-2607:6bbf:ffff:ffff:ffff:ffff:ffff:ffff',
  '2607:6bc0:1::-ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff'
)

$python = (& python -c 'import sys; print(sys.executable)').Trim()
$claude = (Get-Command claude).Source

Write-Host '::group::Create the e2e user'
$bytes = New-Object byte[] 24
[System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
# Random, and meets the local complexity policy (upper, lower, digit, symbol).
$password = 'Aa1!' + [Convert]::ToBase64String($bytes)
$secure = ConvertTo-SecureString $password -AsPlainText -Force
New-LocalUser -Name $E2EUser -Password $secure -PasswordNeverExpires -AccountNeverExpires | Out-Null
# Scheduled tasks that run whether or not the user is logged on need the
# "log on as a batch job" right, which this built-in group has.
Add-LocalGroupMember -Group 'Performance Log Users' -Member $E2EUser
$sid = (Get-LocalUser -Name $E2EUser).SID.Value
New-Item -ItemType Directory -Force -Path $BinDir, (Join-Path $E2EDir 'tmp'), (Join-Path $E2EDir 'config') | Out-Null
Copy-Item $claude (Join-Path $BinDir 'claude.exe')
icacls $E2EDir /grant "${E2EUser}:(OI)(CI)M" /T /Q | Out-Null
icacls $env:ANTHROPIC_IDENTITY_TOKEN_FILE /grant "${E2EUser}:R" /Q | Out-Null
# Inherited ACLs can let any local user write under these, and the runner
# account runs code from them after this step (the checkout's .git, action code
# in _actions, step scripts in _temp, the toolcache, the runner itself). Deny
# the e2e user every kind of write there; reading stays allowed. The rights are
# listed one by one: icacls' W also carries SYNCHRONIZE and READ_CONTROL, and
# denying those stops the user from running anything there. WDAC and WO keep
# the user from granting itself write access back.
$worker = Get-Process -Name Runner.Worker -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $worker) { throw 'Could not find the Runner.Worker process to locate the runner directory.' }
$runnerDir = Split-Path (Split-Path $worker.Path)
foreach ($dir in @((Split-Path $env:RUNNER_WORKSPACE), $env:RUNNER_TOOL_CACHE, $runnerDir)) {
  if (-not $dir -or -not (Test-Path $dir)) { throw "Directory to protect not found: '$dir'" }
  icacls $dir /deny "${E2EUser}:(OI)(CI)(WD,AD,WEA,WA,DE,DC,WDAC,WO)" /Q | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "icacls could not deny writes on $dir (exit $LASTEXITCODE)" }
}
Write-Host '::endgroup::'

Write-Host "::group::Limit the e2e user's outbound traffic to the Claude API"
Set-NetFirewallProfile -Profile Domain, Private, Public -Enabled True
New-NetFirewallRule -DisplayName 'claude-e2e: outbound only to the Claude API' `
  -Direction Outbound -Action Block -LocalUser "D:(A;;CC;;;$sid)" `
  -RemoteAddress $BlockedRanges | Out-Null
Get-NetFirewallRule -DisplayName 'claude-e2e*' | Format-List DisplayName, Enabled, Direction, Action
Write-Host '::endgroup::'

# The task runs this script as the e2e user. It checks the firewall, then runs
# the tests, and leaves its exit code and output in files the runner reads.
$logFile = Join-Path $E2EDir 'e2e.log'
$exitFile = Join-Path $E2EDir 'exit-code'
$inner = Join-Path $E2EDir 'run.ps1'
$quotedArgs = ($PytestArgs | ForEach-Object { "'" + ($_ -replace "'", "''") + "'" }) -join ', '
@"
`$ErrorActionPreference = 'Continue'
# Keep the task's own PATH (the machine PATH, which has Git and its bash for
# Claude Code's Bash tool) and put the copied CLI and the job's Python first.
`$env:PATH = '$BinDir;$(Split-Path $python);$(Split-Path $python)\Scripts;' + `$env:PATH
`$env:TEMP = '$E2EDir\tmp'
`$env:TMP = '$E2EDir\tmp'
`$env:CLAUDE_CONFIG_DIR = '$E2EDir\config'
`$env:ANTHROPIC_FEDERATION_RULE_ID = '$env:ANTHROPIC_FEDERATION_RULE_ID'
`$env:ANTHROPIC_ORGANIZATION_ID = '$env:ANTHROPIC_ORGANIZATION_ID'
`$env:ANTHROPIC_SERVICE_ACCOUNT_ID = '$env:ANTHROPIC_SERVICE_ACCOUNT_ID'
`$env:ANTHROPIC_WORKSPACE_ID = '$env:ANTHROPIC_WORKSPACE_ID'
`$env:ANTHROPIC_IDENTITY_TOKEN_FILE = '$env:ANTHROPIC_IDENTITY_TOKEN_FILE'
`$env:PYTHONUNBUFFERED = '1'
Set-Location '$env:GITHUB_WORKSPACE'
& {
  `$code = 0
  # --ssl-no-revoke: Windows curl checks certificate revocation online, which the
  # firewall blocks for this user too, and that would read as a failure here.
  `$blocked = curl.exe --ssl-no-revoke -sS -m 10 -o NUL https://example.com 2>&1 | Out-String
  `$blockedExit = `$LASTEXITCODE
  `$reached = curl.exe --ssl-no-revoke -sS -m 15 -o NUL -w '%{http_code}' https://api.anthropic.com/ 2>&1 | Out-String
  `$status = if (`$reached -match '(\d{3})\s*`$') { `$Matches[1] } else { '000' }
  # 7: could not connect, 28: timed out. Anything else is not the firewall's doing.
  if (`$blockedExit -ne 7 -and `$blockedExit -ne 28) {
    Write-Output "::error::https://example.com was not refused by the OS firewall (curl exit `$blockedExit): `$(`$blocked.Trim())"
    `$code = 97
  } elseif (`$status -notmatch '^[1-5][0-9][0-9]`$' -or `$status -eq '000') {
    Write-Output "::error::The e2e user could not reach https://api.anthropic.com: `$(`$reached.Trim())"
    `$code = 98
  } else {
    Write-Output "Blocked https://example.com; reached https://api.anthropic.com (HTTP `$status)."
    & '$python' scripts/trust_workspace.py
    # 99 stays if pytest never starts, so that case can't read as a pass.
    `$global:LASTEXITCODE = 99
    & '$python' -m pytest -p no:cacheprovider $quotedArgs
    `$code = `$global:LASTEXITCODE
  }
  # Write, then rename, so the runner never reads a half-written file.
  Set-Content -Path '$exitFile.tmp' -Value `$code
  Move-Item -Force '$exitFile.tmp' '$exitFile'
} *>&1 | Out-File -FilePath '$logFile' -Encoding utf8
"@ | Set-Content -Path $inner -Encoding utf8

$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$inner`""
Register-ScheduledTask -TaskName 'claude-e2e' -Action $action -User $E2EUser -Password $password -RunLevel Limited | Out-Null
Start-ScheduledTask -TaskName 'claude-e2e'

# Stream the log while the task runs.
$shown = 0
$started = Get-Date
function Show-NewLines {
  if (Test-Path $logFile) {
    $lines = @(Get-Content $logFile -Encoding utf8)
    if ($lines.Count -gt $script:shown) {
      $lines[$script:shown..($lines.Count - 1)] | ForEach-Object { Write-Host $_ }
      $script:shown = $lines.Count
    }
  }
}
while (-not (Test-Path $exitFile)) {
  Start-Sleep -Seconds 5
  Show-NewLines
  $state = (Get-ScheduledTask -TaskName 'claude-e2e').State
  $elapsed = ((Get-Date) - $started).TotalSeconds
  if ($state -ne 'Running' -and $state -ne 'Queued' -and $elapsed -gt 30 -and -not (Test-Path $exitFile)) {
    $result = (Get-ScheduledTaskInfo -TaskName 'claude-e2e').LastTaskResult
    Write-Host "::error::The e2e task stopped without an exit code (state $state, last result $result)."
    exit 1
  }
}
Show-NewLines

# Nothing the e2e user started may outlive this step.
Get-CimInstance Win32_Process | Where-Object {
  (Invoke-CimMethod -InputObject $_ -MethodName GetOwner -ErrorAction SilentlyContinue).User -eq $E2EUser
} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Disable-LocalUser -Name $E2EUser

$raw = (Get-Content $exitFile -Raw).Trim()
if ($raw -notmatch '^-?\d+$') {
  Write-Host "::error::The e2e task left no exit code (got '$raw')."
  exit 1
}
$code = [int]$raw
Write-Host "e2e tests exited with $code"
if ($code -ne 0 -and (Test-Path $logFile)) {
  # Repeat the failures and pytest's summary as an annotation, where they show
  # without opening the log.
  $lines = @(Get-Content $logFile -Encoding utf8)
  $summary = @($lines | Where-Object { $_ -match '^(FAILED|ERROR) ' }) + @($lines | Select-Object -Last 5)
  $text = ($summary -join "`n") -replace '%', '%25' -replace "`r", '%0D' -replace "`n", '%0A'
  Write-Host "::error title=e2e tests failed::$text"
}
exit $code
