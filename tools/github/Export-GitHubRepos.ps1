<#
.SYNOPSIS
    Exporte la liste des repos d'une organisation GitHub vers un fichier CSV (et XLSX optionnel).

.DESCRIPTION
    Utilise la GitHub CLI (gh) pour interroger l'API GitHub REST et recuperer les depots
    d'une organisation, puis genere un export CSV. Si le module ImportExcel est disponible,
    un export XLSX est egalement genere.

.PARAMETER Organization
    Nom de l'organisation GitHub (ex: exakis-nelite).

.PARAMETER OutputPath
    Chemin du fichier CSV de sortie. Par defaut: .\repos-<organization>-<date>.csv

.PARAMETER IncludeArchived
    Inclut les depots archives dans l'export. Par defaut, ils sont inclus (gh les retourne tous);
    utilisez ce switch pour filtrer explicitement si besoin d'exclure via -ExcludeArchived.

.PARAMETER ExcludeArchived
    Exclut les depots archives de l'export.

.EXAMPLE
    ./Export-GitHubRepos.ps1 -Organization exakis-nelite

.EXAMPLE
    ./Export-GitHubRepos.ps1 -Organization exakis-nelite -OutputPath C:\temp\repos.csv -ExcludeArchived

.NOTES
    Prerequis:
      - GitHub CLI installee (https://cli.github.com/) et authentifiee: gh auth login
      - Droits de lecture sur l'organisation cible
      - (Optionnel) Module PowerShell ImportExcel pour l'export XLSX: Install-Module ImportExcel -Scope CurrentUser
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Organization,

    [Parameter(Mandatory = $false)]
    [string]$OutputPath,

    [Parameter(Mandatory = $false)]
    [switch]$ExcludeArchived
)

$ErrorActionPreference = 'Stop'

# Verifie la presence de la GitHub CLI
if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    throw "La GitHub CLI (gh) est introuvable. Installez-la depuis https://cli.github.com/ puis executez 'gh auth login'."
}

# Verifie l'authentification
gh auth status *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Vous n'etes pas authentifie sur GitHub CLI. Executez 'gh auth login' avant de relancer ce script."
}

if (-not $OutputPath) {
    $dateStamp = Get-Date -Format 'yyyyMMdd'
    $OutputPath = Join-Path -Path (Get-Location) -ChildPath "repos-$Organization-$dateStamp.csv"
}

Write-Host "Recuperation des depots de l'organisation '$Organization' via GitHub CLI..." -ForegroundColor Cyan

# Champs recuperes via l'API GitHub (gh repo list) au format JSON
$fields = @(
    'name',
    'description',
    'visibility',
    'isArchived',
    'isFork',
    'isPrivate',
    'primaryLanguage',
    'diskUsage',
    'pushedAt',
    'createdAt',
    'updatedAt',
    'url',
    'defaultBranchRef',
    'licenseInfo',
    'stargazerCount',
    'forkCount',
    'hasWikiEnabled',
    'issues'
) -join ','

$rawJson = gh repo list $Organization --limit 1000 --json $fields 2>$null

if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($rawJson)) {
    throw "Echec de la recuperation des depots. Verifiez le nom de l'organisation et vos droits d'acces."
}

$repos = $rawJson | ConvertFrom-Json

if ($ExcludeArchived) {
    $repos = $repos | Where-Object { -not $_.isArchived }
}

if (-not $repos -or $repos.Count -eq 0) {
    Write-Warning "Aucun depot trouve pour l'organisation '$Organization'."
    return
}

# Mise en forme des colonnes pour l'export (aplatir les objets imbriques)
$exportData = $repos | ForEach-Object {
    [PSCustomObject]@{
        Name             = $_.name
        Description      = $_.description
        Visibility       = $_.visibility
        IsArchived       = $_.isArchived
        IsFork           = $_.isFork
        IsPrivate        = $_.isPrivate
        PrimaryLanguage  = $_.primaryLanguage.name
        DiskUsageKB      = $_.diskUsage
        DefaultBranch    = $_.defaultBranchRef.name
        License          = $_.licenseInfo.name
        Stars            = $_.stargazerCount
        Forks            = $_.forkCount
        WikiEnabled      = $_.hasWikiEnabled
        OpenIssuesCount  = $_.issues.totalCount
        CreatedAt        = $_.createdAt
        UpdatedAt        = $_.updatedAt
        PushedAt         = $_.pushedAt
        Url              = $_.url
    }
}

$exportData | Export-Csv -Path $OutputPath -NoTypeInformation -Encoding UTF8

Write-Host "Export CSV genere : $OutputPath ($($exportData.Count) depots)" -ForegroundColor Green

# Export XLSX optionnel si le module ImportExcel est disponible
if (Get-Module -ListAvailable -Name ImportExcel) {
    $xlsxPath = [System.IO.Path]::ChangeExtension($OutputPath, 'xlsx')
    Import-Module ImportExcel -ErrorAction SilentlyContinue
    $exportData | Export-Excel -Path $xlsxPath -WorksheetName 'Repos' -AutoSize -FreezeTopRow -BoldTopRow
    Write-Host "Export XLSX genere : $xlsxPath" -ForegroundColor Green
} else {
    Write-Host "Module 'ImportExcel' non installe. Pour generer directement un XLSX, executez : Install-Module ImportExcel -Scope CurrentUser" -ForegroundColor Yellow
}
