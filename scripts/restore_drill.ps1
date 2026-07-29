param(
    [Parameter(Mandatory = $true)][string]$BackupFile,
    [Parameter(Mandatory = $true)][string]$TargetDatabaseUrl
)

$ErrorActionPreference = "Stop"
$target = [System.Uri]$TargetDatabaseUrl
$databaseName = $target.AbsolutePath.TrimStart("/")
if ($databaseName -notmatch "(?i)(restore|drill)") {
    throw "Refusing restore: target database name must contain 'restore' or 'drill'."
}
if (-not (Test-Path -LiteralPath $BackupFile -PathType Leaf)) {
    throw "Backup file does not exist: $BackupFile"
}
if (-not (Get-Command pg_restore -ErrorAction SilentlyContinue)) {
    throw "pg_restore is required and was not found on PATH."
}
if (-not (Get-Command psql -ErrorAction SilentlyContinue)) {
    throw "psql is required and was not found on PATH."
}

$startedAt = Get-Date
& pg_restore --exit-on-error --no-owner --no-privileges `
    --dbname $TargetDatabaseUrl --format custom $BackupFile
if ($LASTEXITCODE -ne 0) {
    throw "pg_restore failed with exit code $LASTEXITCODE"
}

$env:DATABASE_URL = $TargetDatabaseUrl
python migrate.py
if ($LASTEXITCODE -ne 0) {
    throw "Application migrations failed with exit code $LASTEXITCODE"
}

& psql $TargetDatabaseUrl -v ON_ERROR_STOP=1 -c `
    "SELECT COUNT(*) AS clients FROM clients; SELECT COUNT(*) AS jobs FROM async_jobs;"
if ($LASTEXITCODE -ne 0) {
    throw "Restore verification queries failed with exit code $LASTEXITCODE"
}

$duration = (Get-Date) - $startedAt
Write-Output "Restore drill completed in $([math]::Round($duration.TotalMinutes, 2)) minutes."
