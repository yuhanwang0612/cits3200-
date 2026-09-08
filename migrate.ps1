# Move the pipeline from src/ into the folder structure the group agreed, and
# put the updated files in place. One command, because the two halves must not
# be able to happen separately: copying the new files into the empty PR 16
# folders without the move having happened leaves a repo that looks right and
# cannot import anything.
#
#   cd "C:\Users\tasni\OneDrive\Desktop\cits3200-"
#   powershell -ExecutionPolicy Bypass -File .\migrate.ps1
#
#   src/adapters  -> base_scrapers/     the names from the agreed diagram
#   src/retrieve  -> info/
#   src/enrich    -> enrichment/
#   src/core      -> core/
#   src/run.py    -> run.py             the diagram has no src/, so the files
#   src/export.py -> export.py          that drive everything move up
#   src/screen.py -> screen.py
#
# PR 16 added 22 empty files at those same paths. They are removed first,
# because `git mv src/core core` cannot move onto a folder that already
# exists, and because an empty core/config.py at the repo root shadows the
# real one for anything run from there. That failure is silent: TYPES comes
# back empty rather than raising.
#
# `git mv` rather than a plain move, so history follows the files and the diff
# reads as renames instead of 18 deletions and 18 additions.

param(
    [string]$From = "..\prof comp project\_migrate"
)

$ErrorActionPreference = "Stop"

function Fail($message) {
    Write-Host ""
    Write-Host $message -ForegroundColor Red
    Write-Host ""
    exit 1
}

# --- checks -------------------------------------------------------------

if (-not (Test-Path "src\run.py")) {
    Fail "src\run.py is not here. Either this is the wrong folder, or the migration already ran."
}
if (-not (Test-Path $From)) {
    Fail "Cannot find the updated files at: $From`nPass the right path with -From <folder>"
}

# Only TRACKED changes matter. Untracked files (this script, cache\, output\)
# cannot be disturbed by a git mv, and the previous version of this check
# counted itself as a reason to refuse.
$dirty = git status --porcelain --untracked-files=no
if ($dirty) {
    Write-Host "You have uncommitted changes to tracked files:" -ForegroundColor Yellow
    git status --short --untracked-files=no
    Fail "Commit or stash them first."
}

# --- 1. remove the empty placeholders -----------------------------------

# Compiled caches first. `git rm -r core` removes the tracked files but leaves
# the FOLDER on disk if anything untracked is still in it, and __pycache__
# always is once the tests have run. `git mv src\core core` then moves INTO
# that leftover folder and you get core\core\schema.py, which imports as
# nothing and reads like the move worked.
Write-Host "`n0. clearing compiled caches" -ForegroundColor Cyan
$caches = Get-ChildItem -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue
foreach ($c in $caches) { Remove-Item $c.FullName -Recurse -Force -ErrorAction SilentlyContinue }
Write-Host "   removed $($caches.Count) __pycache__ folder(s)"

Write-Host "`n1. removing the empty placeholder files from PR 16" -ForegroundColor Cyan
foreach ($d in @("base_scrapers", "core", "enrichment", "info")) {
    if (Test-Path $d) {
        git rm -q -r $d
        if ($LASTEXITCODE -ne 0) { Fail "git rm failed on $d" }
        # And the folder itself, whatever untracked leftovers are in it.
        Remove-Item $d -Recurse -Force -ErrorAction SilentlyContinue
        if (Test-Path $d) {
            Fail "$d still exists after removing it. Something in it is locked - close any editor or terminal sitting in that folder and try again."
        }
        Write-Host "   removed the empty $d/"
    }
}

# --- 2. move the real code ----------------------------------------------

Write-Host "`n2. moving the real code into place" -ForegroundColor Cyan
$moves = @(
    @("src\adapters", "base_scrapers"),
    @("src\retrieve", "info"),
    @("src\enrich",   "enrichment"),
    @("src\core",     "core"),
    @("src\run.py",   "run.py"),
    @("src\export.py","export.py"),
    @("src\screen.py","screen.py")
)
foreach ($m in $moves) {
    if (Test-Path $m[0]) {
        # If the destination exists, git mv puts the source INSIDE it rather
        # than becoming it. Refuse instead, and say what to delete.
        if (Test-Path $m[1]) {
            Fail "$($m[1]) already exists, so git mv would move $($m[0]) inside it. Delete $($m[1]) and run this again."
        }
        git mv $m[0] $m[1]
        if ($LASTEXITCODE -ne 0) { Fail "git mv failed: $($m[0]) -> $($m[1])" }
        Write-Host ("   {0,-16} -> {1}" -f $m[0], $m[1])
    }
}

Remove-Item src -Recurse -Force -ErrorAction SilentlyContinue

# --- 3. prove the move worked before copying anything on top ------------

Write-Host "`n3. checking the move landed" -ForegroundColor Cyan
foreach ($f in @("core\schema.py", "core\http.py", "core\titles.py",
                 "enrichment\abdc.py", "info\orcid.py", "export.py")) {
    if (-not (Test-Path $f)) { Fail "$f is missing. Stopping before anything is copied." }
    $size = (Get-Item $f).Length
    if ($size -lt 500) {
        Fail "$f is only $size bytes, so it is still one of the empty placeholders. Stopping before anything is copied over it."
    }
    Write-Host ("   {0,-22} {1,7} bytes" -f $f, $size)
}

# --- 4. the files whose contents change ---------------------------------

Write-Host "`n4. copying in the updated files" -ForegroundColor Cyan
$files = @(
    @("run.py",                  "."),
    @("screen.py",               "."),
    @("core\config.py",          "core"),
    @("base_scrapers\unsw.py",   "base_scrapers"),
    @("enrichment\openalex.py",  "enrichment"),
    @("info\openalex.py",        "info"),
    @("requirements.txt",        ".")
)
foreach ($f in $files) {
    $src = Join-Path $From $f[0]
    if (-not (Test-Path $src)) { Fail "missing from ${From}: $($f[0])" }
    Copy-Item $src $f[1] -Force
    Write-Host "   $($f[0])"
}
Copy-Item (Join-Path $From "tests\*.py") "tests" -Force
Write-Host "   tests\*.py"

Write-Host "`ndone. Now:" -ForegroundColor Green
Write-Host ""
Write-Host "    python -m pytest tests/ -q                  expect 136 passed"
Write-Host "    python run.py --uni unsw --ror 03r8z3t63"
Write-Host ""
