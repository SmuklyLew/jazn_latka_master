[CmdletBinding()]
param(
    [string]$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [string]$RecallCases,
    [string]$PythonCommand = "py",
    [int]$SearchLimit = 20,
    [switch]$RestartDaemon,
    [int]$RestartTimeoutSeconds = 60,
    [switch]$AllowDirty,
    [switch]$WriteTemplate
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RequiredMasterCommit = "109b6823ac23eefa7174b570b851bae106c04d5f"
$SchemaVersion = "jazn_private_memory_validation/v1"
$Root = [System.IO.Path]::GetFullPath($Root)
$WorkspaceRoot = Join-Path $Root "workspace_runtime\private_memory_validation"
$MemoryRebuildCli = "from latka_jazn.tools.memory_rebuild import main; raise SystemExit(main())"

function Write-Utf8NoBom {
    param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][string]$Text)
    $parent = Split-Path -Parent $Path
    if ($parent) {
        [System.IO.Directory]::CreateDirectory($parent) | Out-Null
    }
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $Text, $encoding)
}

function Save-Json {
    param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)]$Value)
    $json = $Value | ConvertTo-Json -Depth 100
    Write-Utf8NoBom -Path $Path -Text ($json + [Environment]::NewLine)
}

function Get-Sha256Text {
    param([AllowEmptyString()][string]$Text)
    $bytes = (New-Object System.Text.UTF8Encoding($false)).GetBytes($Text)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        return ([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace("-", "").ToLowerInvariant()
    }
    finally {
        $sha.Dispose()
    }
}

function Get-Sha256File {
    param([Parameter(Mandatory)][string]$Path)
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Normalize-RecallText {
    param([AllowEmptyString()][string]$Text)
    if ($null -eq $Text) { return "" }
    $decomposed = $Text.ToLowerInvariant().Normalize([Text.NormalizationForm]::FormD)
    $builder = New-Object System.Text.StringBuilder
    foreach ($char in $decomposed.ToCharArray()) {
        $category = [Globalization.CharUnicodeInfo]::GetUnicodeCategory($char)
        if ($category -ne [Globalization.UnicodeCategory]::NonSpacingMark) {
            [void]$builder.Append($char)
        }
    }
    return ([regex]::Replace($builder.ToString(), "\s+", " ")).Trim()
}

function Add-ObjectStrings {
    param($Value, [Parameter(Mandatory)][AllowEmptyCollection()][System.Collections.Generic.List[string]]$Target)
    if ($null -eq $Value) { return }
    if ($Value -is [string]) {
        [void]$Target.Add([string]$Value)
        return
    }
    if ($Value -is [System.Collections.IDictionary]) {
        foreach ($key in $Value.Keys) {
            Add-ObjectStrings -Value $Value[$key] -Target $Target
        }
        return
    }
    if (($Value -is [System.Collections.IEnumerable]) -and -not ($Value -is [string])) {
        foreach ($item in $Value) {
            Add-ObjectStrings -Value $item -Target $Target
        }
        return
    }
    foreach ($property in $Value.PSObject.Properties) {
        Add-ObjectStrings -Value $property.Value -Target $Target
    }
}

function Get-NestedValue {
    param($Value, [Parameter(Mandatory)][string]$Path)
    $current = $Value
    foreach ($part in $Path.Split(".")) {
        if ($null -eq $current) { return $null }
        $property = $current.PSObject.Properties[$part]
        if ($null -eq $property) { return $null }
        $current = $property.Value
    }
    return $current
}

function Find-KeyValues {
    param($Value, [Parameter(Mandatory)][string[]]$Keys)
    $result = [ordered]@{}
    function Visit-KeyValues {
        param($Node, [string]$Prefix)
        if ($null -eq $Node) { return }
        if ($Node -is [string] -or $Node -is [ValueType]) { return }
        if ($Node -is [System.Collections.IDictionary]) {
            foreach ($key in $Node.Keys) {
                $name = [string]$key
                $child = $Node[$key]
                $path = if ($Prefix) { "$Prefix.$name" } else { $name }
                if ($Keys -contains $name) { $result[$path] = $child }
                Visit-KeyValues -Node $child -Prefix $path
            }
            return
        }
        if ($Node -is [System.Collections.IEnumerable]) {
            $index = 0
            foreach ($child in $Node) {
                Visit-KeyValues -Node $child -Prefix "$Prefix[$index]"
                $index++
            }
            return
        }
        foreach ($property in $Node.PSObject.Properties) {
            $name = $property.Name
            $path = if ($Prefix) { "$Prefix.$name" } else { $name }
            if ($Keys -contains $name) { $result[$path] = $property.Value }
            Visit-KeyValues -Node $property.Value -Prefix $path
        }
    }
    Visit-KeyValues -Node $Value -Prefix ""
    return [pscustomobject]$result
}

function Invoke-Git {
    param([Parameter(Mandatory)][string[]]$Arguments, [switch]$AllowFailure)
    $output = & git -C $Root @Arguments 2>&1
    $code = $LASTEXITCODE
    if ($code -ne 0 -and -not $AllowFailure) {
        throw "git $($Arguments -join ' ') failed with exit code $code."
    }
    return [pscustomobject]@{ ExitCode = $code; Output = @($output) }
}

function Invoke-CapturedJson {
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string[]]$Arguments,
        [Parameter(Mandatory)][string]$RunDirectory,
        [switch]$AllowNonZero,
        [switch]$Ephemeral
    )
    $safeName = [regex]::Replace($Name, "[^A-Za-z0-9._-]", "_")
    $stdout = Join-Path $RunDirectory "$safeName.stdout.json"
    $stderr = Join-Path $RunDirectory "$safeName.stderr.log"
    Remove-Item -LiteralPath $stdout, $stderr -Force -ErrorAction SilentlyContinue

    Push-Location -LiteralPath $Root
    try {
        & $PythonCommand -X utf8 @Arguments 1> $stdout 2> $stderr
        $code = $LASTEXITCODE
    }
    finally {
        Pop-Location
    }

    $rawValue = if (Test-Path -LiteralPath $stdout) {
        Get-Content -LiteralPath $stdout -Raw -Encoding UTF8
    } else {
        ""
    }
    [string]$raw = if ($null -eq $rawValue) { "" } else { [string]$rawValue }

    $stderrValue = if (Test-Path -LiteralPath $stderr) {
        Get-Content -LiteralPath $stderr -Raw -Encoding UTF8
    } else {
        ""
    }
    [string]$stderrText = if ($null -eq $stderrValue) { "" } else { [string]$stderrValue }

    $payload = $null
    $parseError = $null
    if (-not [string]::IsNullOrWhiteSpace($raw)) {
        try {
            $payload = $raw | ConvertFrom-Json
        }
        catch {
            $parseError = $_.Exception.Message
        }
    }
    if (($code -ne 0 -and -not $AllowNonZero) -or $null -ne $parseError -or $null -eq $payload) {
        $reason = if ($parseError) {
            "invalid JSON: $parseError"
        }
        elseif ($null -eq $payload) {
            "empty JSON output"
        }
        else {
            "exit code $code"
        }
        if (-not [string]::IsNullOrWhiteSpace($stderrText)) {
            $stderrPreview = [regex]::Replace($stderrText.Trim(), "\s+", " ")
            if ($stderrPreview.Length -gt 1200) {
                $stderrPreview = $stderrPreview.Substring(0, 1200) + "..."
            }
            $reason = "$reason; stderr: $stderrPreview"
        }
        if ($Ephemeral) {
            Remove-Item -LiteralPath $stdout, $stderr -Force -ErrorAction SilentlyContinue
        }
        throw "$Name failed: $reason"
    }
    if ($Ephemeral) {
        Remove-Item -LiteralPath $stdout, $stderr -Force -ErrorAction SilentlyContinue
    } else {
        Remove-Item -LiteralPath $stdout -Force -ErrorAction SilentlyContinue
    }
    return [pscustomobject]@{
        Name = $Name
        ExitCode = $code
        Payload = $payload
        StderrFile = if ($Ephemeral) { $null } else { $stderr }
    }
}

function Invoke-CapturedCommand {
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string[]]$Arguments,
        [Parameter(Mandatory)][string]$RunDirectory
    )
    $safeName = [regex]::Replace($Name, "[^A-Za-z0-9._-]", "_")
    $stdout = Join-Path $RunDirectory "$safeName.stdout.log"
    $stderr = Join-Path $RunDirectory "$safeName.stderr.log"
    & $PythonCommand -X utf8 @Arguments 1> $stdout 2> $stderr
    return [pscustomobject]@{
        Name = $Name
        ExitCode = $LASTEXITCODE
        StdoutFile = $stdout
        StderrFile = $stderr
    }
}

function Get-SourceHitCounts {
    param($SearchPayload)
    $counts = [ordered]@{}
    $results = $SearchPayload.results
    if ($null -eq $results) { return [pscustomobject]$counts }
    foreach ($property in $results.PSObject.Properties) {
        $value = $property.Value
        $count = 0
        if ($null -ne $value) {
            if (($value -is [System.Collections.IEnumerable]) -and -not ($value -is [string])) {
                $count = @($value).Count
            }
            else {
                $count = 1
            }
        }
        $counts[$property.Name] = $count
    }
    return [pscustomobject]$counts
}

function Test-RecallCase {
    param(
        [Parameter(Mandatory)]$Case,
        [Parameter(Mandatory)][string]$RunDirectory,
        [Parameter(Mandatory)][int]$Ordinal
    )
    $id = [string]$Case.id
    if ($id -notmatch "^[A-Za-z0-9._-]{1,80}$") {
        throw "Recall case #$Ordinal has an invalid id."
    }
    $query = [string]$Case.query
    if ([string]::IsNullOrWhiteSpace($query)) {
        throw "Recall case '$id' has an empty query."
    }
    $limit = if ($Case.PSObject.Properties["limit"]) { [int]$Case.limit } else { $SearchLimit }
    $searchRun = Invoke-CapturedJson `
        -Name "recall-$Ordinal" `
        -Arguments @(
            "-c",
            $MemoryRebuildCli,
            "--root", $Root,
            "--json",
            "--no-progress",
            "search",
            $query,
            "--limit", [string]$limit
        ) `
        -RunDirectory $RunDirectory `
        -AllowNonZero `
        -Ephemeral

    $strings = New-Object "System.Collections.Generic.List[string]"
    Add-ObjectStrings -Value $searchRun.Payload.results -Target $strings
    $joined = Normalize-RecallText ($strings -join "`n")
    $expectedAny = @($Case.expected_any | ForEach-Object { [string]$_ } | Where-Object { $_ })
    $expectedAll = @($Case.expected_all | ForEach-Object { [string]$_ } | Where-Object { $_ })
    $forbiddenAny = @($Case.forbidden_any | ForEach-Object { [string]$_ } | Where-Object { $_ })
    $expectedSources = @($Case.expected_sources | ForEach-Object { [string]$_ } | Where-Object { $_ })
    $minimumHits = if ($Case.PSObject.Properties["minimum_hits"]) { [int]$Case.minimum_hits } else { 1 }

    $anyMatches = @($expectedAny | Where-Object { $joined.Contains((Normalize-RecallText $_)) })
    $allMatches = @($expectedAll | Where-Object { $joined.Contains((Normalize-RecallText $_)) })
    $forbiddenMatches = @($forbiddenAny | Where-Object { $joined.Contains((Normalize-RecallText $_)) })
    $sourceCounts = Get-SourceHitCounts -SearchPayload $searchRun.Payload
    $totalHits = 0
    foreach ($property in $sourceCounts.PSObject.Properties) {
        $totalHits += [int]$property.Value
    }
    $expectedSourceMatch = $expectedSources.Count -eq 0
    foreach ($source in $expectedSources) {
        $property = $sourceCounts.PSObject.Properties[$source]
        if ($null -ne $property -and [int]$property.Value -gt 0) {
            $expectedSourceMatch = $true
            break
        }
    }
    $anyOk = $expectedAny.Count -eq 0 -or $anyMatches.Count -gt 0
    $allOk = $expectedAll.Count -eq $allMatches.Count
    $forbiddenOk = $forbiddenMatches.Count -eq 0
    $hitsOk = $totalHits -ge $minimumHits
    $ok = ($searchRun.ExitCode -eq 0) -and $anyOk -and $allOk -and $forbiddenOk -and $hitsOk -and $expectedSourceMatch

    return [pscustomobject][ordered]@{
        id = $id
        query_sha256 = Get-Sha256Text $query
        ok = $ok
        search_exit_code = $searchRun.ExitCode
        total_hits = $totalHits
        source_hit_counts = $sourceCounts
        minimum_hits = $minimumHits
        minimum_hits_met = $hitsOk
        expected_source_match = $expectedSourceMatch
        expected_any_count = $expectedAny.Count
        expected_any_match_count = $anyMatches.Count
        expected_all_count = $expectedAll.Count
        expected_all_match_count = $allMatches.Count
        forbidden_count = $forbiddenAny.Count
        forbidden_match_count = $forbiddenMatches.Count
        raw_query_persisted = $false
        raw_results_persisted = $false
    }
}

function Get-RuntimeStateFileHash {
    $path = Join-Path $Root "workspace_runtime\runtime_session_state.json"
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { return $null }
    return Get-Sha256File $path
}

function Get-WakeFingerprint {
    param($StatusPayload)
    return Find-KeyValues -Value $StatusPayload -Keys @(
        "snapshot_id",
        "snapshot_sha256",
        "wake_state_snapshot_id",
        "wake_state_snapshot_sha256",
        "validation_status",
        "invalidates_wake_state"
    )
}

function Compare-Fingerprint {
    param($Before, $After)
    $beforeJson = $Before | ConvertTo-Json -Depth 30 -Compress
    $afterJson = $After | ConvertTo-Json -Depth 30 -Compress
    return $beforeJson -eq $afterJson
}

function New-Template {
    $templatePath = Join-Path $WorkspaceRoot "recall-cases.template.json"
    $template = [ordered]@{
        schema_version = "jazn_private_recall_cases/v1"
        minimums = [ordered]@{
            "counts.archive_chats.conversations" = 1
            "counts.archive_chats.nodes" = 1
        }
        source_files = @(
            "D:\PRIVATE\chat-export.zip"
        )
        recall_cases = @(
            [ordered]@{
                id = "case-001"
                query = "Wpisz prywatne pytanie kontrolne."
                expected_any = @("oczekiwany termin A", "oczekiwany termin B")
                expected_all = @()
                forbidden_any = @("termin, którego nie powinno być")
                expected_sources = @("archive_chats")
                minimum_hits = 1
                limit = 20
            }
        )
    }
    Save-Json -Path $templatePath -Value $template
    Write-Host "Utworzono prywatny szablon: $templatePath"
    Write-Host "Nie commituj tego pliku. Uzupełnij go i uruchom skrypt z -RecallCases."
}

if (-not (Test-Path -LiteralPath (Join-Path $Root "run.py") -PathType Leaf)) {
    throw "Nie znaleziono run.py pod rootem: $Root"
}
if (-not (Test-Path -LiteralPath (Join-Path $Root "latka_jazn\tools\memory_rebuild.py") -PathType Leaf)) {
    throw "Nie znaleziono latka_jazn\tools\memory_rebuild.py pod rootem: $Root"
}
[System.IO.Directory]::CreateDirectory($WorkspaceRoot) | Out-Null

if ($WriteTemplate) {
    New-Template
    exit 0
}
if ([string]::IsNullOrWhiteSpace($RecallCases)) {
    throw "Podaj -RecallCases albo użyj -WriteTemplate."
}
if (-not [System.IO.Path]::IsPathRooted($RecallCases)) {
    $RecallCases = Join-Path $Root $RecallCases
}
$RecallCases = [System.IO.Path]::GetFullPath($RecallCases)
if (-not (Test-Path -LiteralPath $RecallCases -PathType Leaf)) {
    throw "Nie znaleziono pliku przypadków recall."
}

$gitHead = (Invoke-Git -Arguments @("rev-parse", "HEAD")).Output[0].Trim()
$gitBranch = (Invoke-Git -Arguments @("branch", "--show-current")).Output[0].Trim()
$ancestor = Invoke-Git -Arguments @("merge-base", "--is-ancestor", $RequiredMasterCommit, "HEAD") -AllowFailure
if ($ancestor.ExitCode -ne 0) {
    throw "Repo nie zawiera scalonego PR #63 ($RequiredMasterCommit). Najpierw zaktualizuj master."
}
if (-not $AllowDirty) {
    $dirty = (Invoke-Git -Arguments @("status", "--porcelain", "--untracked-files=no")).Output
    if (@($dirty).Count -gt 0) {
        throw "Śledzone pliki repo są zmienione. Użyj czystego worktree albo jawnego -AllowDirty."
    }
}

$stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
$runDirectory = Join-Path $WorkspaceRoot $stamp
[System.IO.Directory]::CreateDirectory($runDirectory) | Out-Null
$cases = Get-Content -LiteralPath $RecallCases -Raw -Encoding UTF8 | ConvertFrom-Json
if ([string]$cases.schema_version -ne "jazn_private_recall_cases/v1") {
    throw "Nieobsługiwany schema_version przypadków recall."
}
if ($null -eq $cases.minimums) {
    throw "Plik przypadków recall nie zawiera obiektu minimums."
}
$sourceItems = @($cases.source_files | Where-Object {
    $null -ne $_ -and -not [string]::IsNullOrWhiteSpace([string]$_)
})
$recallItems = @($cases.recall_cases | Where-Object { $null -ne $_ })
if ($sourceItems.Count -eq 0) {
    throw "Plik przypadków recall nie zawiera source_files."
}
if ($recallItems.Count -eq 0) {
    throw "Plik przypadków recall nie zawiera recall_cases."
}
$templateRecallCases = @($recallItems | Where-Object {
    [string]$_.query -eq "Wpisz prywatne pytanie kontrolne." -or
    @($_.expected_any) -contains "oczekiwany termin A" -or
    @($_.expected_any) -contains "oczekiwany termin B"
})
if ($templateRecallCases.Count -gt 0) {
    throw "Plik recall nadal zawiera wpis szablonowy. Wstaw prywatne przypadki kontrolne przed audytem."
}
$casesDirectory = Split-Path -Parent $RecallCases

Write-Host "[1/7] Doctor i stan runtime"
$doctorRun = Invoke-CapturedJson `
    -Name "doctor" `
    -Arguments @((Join-Path $Root "run.py"), "doctor", "--root", $Root, "--json", "--no-progress") `
    -RunDirectory $runDirectory `
    -AllowNonZero `
    -Ephemeral

$statusBeforeRun = Invoke-CapturedJson `
    -Name "status-before" `
    -Arguments @((Join-Path $Root "run.py"), "status", "--root", $Root, "--json", "--no-progress") `
    -RunDirectory $runDirectory `
    -AllowNonZero `
    -Ephemeral
$wakeBefore = Get-WakeFingerprint $statusBeforeRun.Payload
$runtimeStateHashBefore = Get-RuntimeStateFileHash

Write-Host "[2/7] Pełna walidacja wszystkich SQLite"
$fullReportRelative = "workspace_runtime/private_memory_validation/$stamp/full-memory-report.json"
$memoryRun = Invoke-CapturedJson `
    -Name "memory-validate" `
    -Arguments @(
        (Join-Path $Root "run.py"),
        "memory-validate",
        "--root", $Root,
        "--full",
        "--include-all-sqlite",
        "--table-counts",
        "--hash-files",
        "--output", $fullReportRelative,
        "--json",
        "--no-progress"
    ) `
    -RunDirectory $runDirectory `
    -AllowNonZero `
    -Ephemeral

Write-Host "[3/7] Pięć baz L0/L1/L2/L3 i katalog importu"
$rebuildStatusRun = Invoke-CapturedJson `
    -Name "memory-rebuild-status" `
    -Arguments @(
        "-c",
        $MemoryRebuildCli,
        "--root", $Root,
        "--json",
        "--no-progress",
        "status"
    ) `
    -RunDirectory $runDirectory `
    -AllowNonZero `
    -Ephemeral

$rebuildVerifyRun = Invoke-CapturedJson `
    -Name "memory-rebuild-verify" `
    -Arguments @(
        "-c",
        $MemoryRebuildCli,
        "--root", $Root,
        "--json",
        "--no-progress",
        "verify"
    ) `
    -RunDirectory $runDirectory `
    -AllowNonZero `
    -Ephemeral

Write-Host "[4/7] Wykaz prywatnych źródeł L0 bez zapisywania nazw"
$sourceReports = @()
$sourceIndex = 0
foreach ($rawPath in $sourceItems) {
    $sourceIndex++
    $sourcePath = [string]$rawPath
    if (-not [System.IO.Path]::IsPathRooted($sourcePath)) {
        $sourcePath = Join-Path $casesDirectory $sourcePath
    }
    $sourcePath = [System.IO.Path]::GetFullPath($sourcePath)
    $exists = Test-Path -LiteralPath $sourcePath -PathType Leaf
    Write-Host "  źródło $sourceIndex/$($sourceItems.Count): hash i rozmiar"
    $sourceReports += [pscustomobject][ordered]@{
        ordinal = $sourceIndex
        exists = $exists
        size_bytes = if ($exists) { (Get-Item -LiteralPath $sourcePath).Length } else { 0 }
        sha256 = if ($exists) { Get-Sha256File $sourcePath } else { $null }
        path_persisted = $false
    }
}
$uniqueSourceHashes = @($sourceReports | Where-Object { $_.sha256 } | Select-Object -ExpandProperty sha256 -Unique)

Write-Host "[5/7] Benchmark recall bez utrwalania zapytań i wyników"
$recallReports = @()
$ordinal = 0
foreach ($case in $recallItems) {
    $ordinal++
    Write-Host "  przypadek $ordinal/$($recallItems.Count): $($case.id)"
    $recallReports += Test-RecallCase -Case $case -RunDirectory $runDirectory -Ordinal $ordinal
}

$minimumReports = @()
foreach ($property in $cases.minimums.PSObject.Properties) {
    $actual = Get-NestedValue -Value $rebuildStatusRun.Payload -Path $property.Name
    $expected = [double]$property.Value
    $actualNumber = if ($null -eq $actual) { $null } else { [double]$actual }
    $minimumReports += [pscustomobject][ordered]@{
        metric = $property.Name
        expected_minimum = $expected
        actual = $actualNumber
        ok = ($null -ne $actualNumber -and $actualNumber -ge $expected)
    }
}

Write-Host "[6/7] Kontrola manifestu L3 bez promocji"
$l3ManifestPath = Join-Path $Root "workspace_runtime\memory_recovery\l3_approval_manifest.json"
$l3Report = [ordered]@{
    present = $false
    file_sha256 = $null
    stored_manifest_sha256 = $null
    candidate_count = 0
    automatic_commit_allowed = $null
    l3_apply_attempted = $false
    private_excerpts_persisted = $false
}
if (Test-Path -LiteralPath $l3ManifestPath -PathType Leaf) {
    $l3Payload = Get-Content -LiteralPath $l3ManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $l3Report.present = $true
    $l3Report.file_sha256 = Get-Sha256File $l3ManifestPath
    $l3Report.stored_manifest_sha256 = [string]$l3Payload.manifest_sha256
    $l3Report.candidate_count = [int]$l3Payload.candidate_count
    $l3Report.automatic_commit_allowed = $l3Payload.automatic_commit_allowed
}

$restartReport = [ordered]@{
    requested = [bool]$RestartDaemon
    attempted = $false
    restart_exit_code = $null
    status_recovered = $false
    wake_fingerprint_equal = $null
    runtime_session_state_sha256_equal = $null
}
if ($RestartDaemon) {
    Write-Host "[7/7] Restart daemona i porównanie wake-state/checkpointu"
    $restartReport.attempted = $true
    $restartRun = Invoke-CapturedCommand `
        -Name "restart-daemon" `
        -Arguments @((Join-Path $Root "run.py"), "restart", "--root", $Root, "--no-progress") `
        -RunDirectory $runDirectory
    $restartReport.restart_exit_code = $restartRun.ExitCode

    $deadline = (Get-Date).AddSeconds([Math]::Max(5, $RestartTimeoutSeconds))
    $statusAfterRun = $null
    do {
        Start-Sleep -Seconds 2
        try {
            $statusAfterRun = Invoke-CapturedJson `
                -Name "status-after" `
                -Arguments @((Join-Path $Root "run.py"), "status", "--root", $Root, "--json", "--no-progress") `
                -RunDirectory $runDirectory `
                -AllowNonZero `
                -Ephemeral
            if ($statusAfterRun.ExitCode -eq 0) { break }
        }
        catch {
            $statusAfterRun = $null
        }
    } while ((Get-Date) -lt $deadline)

    if ($null -ne $statusAfterRun) {
        $restartReport.status_recovered = $statusAfterRun.ExitCode -eq 0
        $wakeAfter = Get-WakeFingerprint $statusAfterRun.Payload
        $restartReport.wake_fingerprint_equal = Compare-Fingerprint -Before $wakeBefore -After $wakeAfter
        $runtimeStateHashAfter = Get-RuntimeStateFileHash
        $restartReport.runtime_session_state_sha256_equal = (
            $null -ne $runtimeStateHashBefore -and
            $null -ne $runtimeStateHashAfter -and
            $runtimeStateHashBefore -eq $runtimeStateHashAfter
        )
    }
}
else {
    Write-Host "[7/7] Restart pominięty; uruchom ponownie z -RestartDaemon"
}

$doctorOk = ($doctorRun.ExitCode -eq 0 -and $doctorRun.Payload.ok -eq $true)
$memoryOk = ($memoryRun.ExitCode -eq 0 -and $memoryRun.Payload.ok -eq $true)
$rebuildStatusOk = ($rebuildStatusRun.ExitCode -eq 0 -and $rebuildStatusRun.Payload.ok -eq $true)
$rebuildVerifyOk = ($rebuildVerifyRun.ExitCode -eq 0 -and $rebuildVerifyRun.Payload.ok -eq $true)
$recallOk = @($recallReports | Where-Object { -not $_.ok }).Count -eq 0
$minimumsOk = @($minimumReports | Where-Object { -not $_.ok }).Count -eq 0
$sourcesOk = @($sourceReports | Where-Object { -not $_.exists }).Count -eq 0
$restartOk = (-not $RestartDaemon) -or (
    $restartReport.restart_exit_code -eq 0 -and
    $restartReport.status_recovered -eq $true -and
    $restartReport.wake_fingerprint_equal -eq $true -and
    $restartReport.runtime_session_state_sha256_equal -eq $true
)
$automatedOk = $doctorOk -and $memoryOk -and $rebuildStatusOk -and $rebuildVerifyOk -and $recallOk -and $minimumsOk -and $sourcesOk -and $restartOk

$summary = [ordered]@{
    schema_version = $SchemaVersion
    generated_at_utc = (Get-Date).ToUniversalTime().ToString("o")
    repository = [ordered]@{
        head = $gitHead
        branch = $gitBranch
        required_pr63_merge_ancestor = $true
        tracked_worktree_clean_required = -not [bool]$AllowDirty
        root_sha256 = Get-Sha256Text $Root
        root_path_persisted = $false
    }
    checks = [ordered]@{
        doctor = [ordered]@{ ok = $doctorOk; exit_code = $doctorRun.ExitCode }
        memory_validate_full = [ordered]@{
            ok = $memoryOk
            exit_code = $memoryRun.ExitCode
            report_relative_path = $fullReportRelative
            target_count = $memoryRun.Payload.summary.target_count
            failed_count = $memoryRun.Payload.summary.failed_count
            required_missing_count = $memoryRun.Payload.summary.required_missing_count
            wake_state_ready = $memoryRun.Payload.summary.wake_state_ready
            memory_tiers_ready = $memoryRun.Payload.summary.memory_tiers_ready
        }
        memory_rebuild_status = [ordered]@{ ok = $rebuildStatusOk; exit_code = $rebuildStatusRun.ExitCode }
        memory_rebuild_verify = [ordered]@{ ok = $rebuildVerifyOk; exit_code = $rebuildVerifyRun.ExitCode }
        source_inventory = [ordered]@{
            ok = $sourcesOk
            source_count = $sourceReports.Count
            unique_sha256_count = $uniqueSourceHashes.Count
            missing_count = @($sourceReports | Where-Object { -not $_.exists }).Count
            sources = $sourceReports
        }
        minimums = [ordered]@{ ok = $minimumsOk; results = $minimumReports }
        recall = [ordered]@{
            ok = $recallOk
            case_count = $recallReports.Count
            passed_count = @($recallReports | Where-Object { $_.ok }).Count
            failed_count = @($recallReports | Where-Object { -not $_.ok }).Count
            cases = $recallReports
            private_query_text_persisted = $false
            private_expected_terms_persisted = $false
            private_result_content_persisted = $false
        }
        l3_manifest = [pscustomobject]$l3Report
        restart_continuity = [pscustomobject]$restartReport
    }
    automated_ok = $automatedOk
    manual_checks_remaining = @(
        "Przeprowadzić rozmowę wieloturową i ocenić naturalność użycia pamięci bez podpowiadania odpowiedzi.",
        "Ręcznie przejrzeć kandydatów L3; ten skrypt nigdy ich nie promuje.",
        "Zatwierdzić promocję L3 osobnym jawnym poleceniem dopiero po akceptacji treści i SHA manifestu."
    )
    issue_59_ready_to_close = $false
    truth_boundary = "Raport potwierdza integralność, liczniki i deterministyczny benchmark recall. Nie dowodzi kompletności wszystkich wspomnień, naturalności rozmowy ani autoryzacji L3."
}
$summaryPath = Join-Path $runDirectory "summary.sanitized.json"
Save-Json -Path $summaryPath -Value $summary

Write-Host ""
Write-Host "Raport zanonimizowany: $summaryPath"
Write-Host "Automated OK: $automatedOk"
Write-Host "Recall: $(@($recallReports | Where-Object { $_.ok }).Count)/$($recallReports.Count)"
Write-Host "Źródła L0: $($sourceReports.Count), unikalne SHA: $($uniqueSourceHashes.Count)"
Write-Host "L3 apply attempted: false"
Write-Host "Issue #59 pozostaje otwarte do ręcznej rozmowy wieloturowej i jawnego przeglądu L3."

if ($automatedOk) { exit 0 }
exit 2
