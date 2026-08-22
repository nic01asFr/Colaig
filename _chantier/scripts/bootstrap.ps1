# bootstrap.ps1 — Lot L0.1 : import du tronc et assainissement
#
# STATUT: COMPLET · VERSION: 2026-08-22 - v1.0 · LOT: L0.1
#
# Importe colaig-v3 dans ce dossier EN PRÉSERVANT SON HISTORIQUE GIT,
# nettoie les scories, et restaure les fichiers de chantier.
#
# À exécuter depuis la racine du dossier "Colaig 220826" :
#     powershell -ExecutionPolicy Bypass -File _chantier\scripts\bootstrap.ps1
#
# Idempotent : relançable sans dégât. Ne touche JAMAIS au dossier source colaig-v3.

$ErrorActionPreference = "Stop"
$Root   = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$Source = Join-Path (Split-Path -Parent $Root) "colaig-v3"
$Branch = "feat/reflexive-self-config"

# PowerShell n'applique pas $ErrorActionPreference aux commandes natives : un git en
# echec passerait inapercu. Toute invocation de git passe par ce wrapper.
function Invoke-Git {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$GitArgs)
    & git @GitArgs
    if ($LASTEXITCODE -ne 0) {
        throw "git $($GitArgs -join ' ') a echoue (code $LASTEXITCODE)"
    }
}

Write-Host "== Colaig L0.1 — import du tronc ==" -ForegroundColor Cyan
Write-Host "   cible  : $Root"
Write-Host "   source : $Source"

if (-not (Test-Path (Join-Path $Source ".git"))) {
    throw "colaig-v3 introuvable ou sans .git : $Source"
}

Set-Location $Root

# 1. Sauvegarder le chantier (il n'est pas dans l'arbre v3, mais on est prudent)
$Backup = Join-Path $env:TEMP ("colaig-chantier-" + (Get-Date -Format "yyyyMMddHHmmss"))
Copy-Item -Recurse -Force (Join-Path $Root "_chantier") $Backup
Copy-Item -Force (Join-Path $Root "CLAUDE.md") "$Backup\CLAUDE.chantier.md"
Write-Host "   chantier sauvegardé -> $Backup" -ForegroundColor DarkGray

# 2. Importer v3 avec son historique
if (-not (Test-Path (Join-Path $Root ".git"))) {
    Invoke-Git init -q
}
# Le remote peut deja exister si le script a ete relance : on ne le rajoute qu'une fois.
$Remotes = @(& git remote)
if ($Remotes -notcontains "v3") {
    Invoke-Git remote add v3 $Source
}
Invoke-Git fetch v3 --quiet
Invoke-Git checkout -f -B main "v3/$Branch"
Write-Host "   historique v3 importé sur 'main'" -ForegroundColor Green

# 3. Le CLAUDE.md de v3 devient une archive, le nôtre reprend sa place
if (Test-Path (Join-Path $Root "CLAUDE.md")) {
    New-Item -ItemType Directory -Force -Path (Join-Path $Root "docs") | Out-Null
    Move-Item -Force (Join-Path $Root "CLAUDE.md") (Join-Path $Root "docs\CLAUDE.v3-original.md")
}
Copy-Item -Force "$Backup\CLAUDE.chantier.md" (Join-Path $Root "CLAUDE.md")

# Restaurer le CONTENU du chantier. Copier $Backup lui-meme imbriquerait un dossier
# horodate dans _chantier a chaque relance : on copie donc son contenu.
$Chantier = Join-Path $Root "_chantier"
New-Item -ItemType Directory -Force -Path $Chantier | Out-Null
Copy-Item -Recurse -Force (Join-Path $Backup "*") $Chantier
Remove-Item -Force (Join-Path $Chantier "CLAUDE.chantier.md") -ErrorAction SilentlyContinue

# 4. Assainissement — scories identifiées à l'analyse du 22/08/2026
#
# RIEN N'EST SUPPRIMÉ. Chaque élément est DÉPLACÉ dans un dossier de quarantaine
# horodaté, hors du dépôt. Si une scorie s'avère contenir quelque chose d'utile
# (secrets/box-config.json par exemple), elle reste récupérable.
$Quarantaine = Join-Path $Backup "quarantaine"
New-Item -ItemType Directory -Force -Path $Quarantaine | Out-Null

$Scories = @("colaig;C", "config;C", "tests;C", "secrets", ".claude-session")
foreach ($s in $Scories) {
    $p = Join-Path $Root $s
    if (Test-Path $p) {
        Move-Item -Force -Path $p -Destination (Join-Path $Quarantaine $s.Replace(";", "_"))
        Write-Host "   mis en quarantaine : $s" -ForegroundColor Yellow
    }
}
Write-Host "   quarantaine -> $Quarantaine" -ForegroundColor DarkGray

# Le .env du chantier ne doit jamais disparaître ni être commité.
$EnvFile = Join-Path $Root ".env"
if (Test-Path $EnvFile) {
    Copy-Item -Force $EnvFile (Join-Path $Backup "env.sauvegarde")
    Write-Host "   .env présent, sauvegardé, et ignoré par git" -ForegroundColor DarkGray
}

# 5. .gitignore durci
$GitIgnore = @"
__pycache__/
*.py[cod]
.pytest_cache/
.ruff_cache/
.coverage
htmlcov/
*.egg-info/

# Secrets — jamais commités
.env
.env.*
!.env.example
secrets/
*.key
*.pem
**/matrix_token.json
**/e2e_store/

# Données locales
data/
_local/
*.faiss
*.pkl

# Overlays d'outillage
.wikichat/
.claude-session
"@
Set-Content -Path (Join-Path $Root ".gitignore") -Value $GitIgnore -Encoding UTF8

# 6. Vérification du critère de fin
Write-Host "`n== Critère de fin L0.1 ==" -ForegroundColor Cyan
Invoke-Git add -A
& git status --short | Select-Object -First 25
Write-Host "`n-> Vérifier ensuite : pytest -q" -ForegroundColor Cyan
Write-Host "-> Puis commiter :   git commit -m 'L0.1 import du tronc v3 et assainissement'"
Write-Host "-> Puis remote :     git remote add origin https://github.com/nic01asFr/Colaig.git"
Write-Host "`n-> Ne pas oublier : mettre à jour _chantier\AVANCEMENT.md" -ForegroundColor Yellow
