# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  git sync  ·  Stream Toolkit                                                ║
# ║  repository : https://github.com/chaiz64/douyin-downloaderz.git                ║
# ╚══════════════════════════════════════════════════════════════════════════════╝
#
#  Architecture
#  ────────────
#  1. Config          – single source-of-truth for all tunables
#  2. Logger          – structured, leveled output with spinners
#  3. Git primitives  – thin wrappers; never swallow exit codes silently
#  4. Pre-flight      – init, remote, master→main rename, rebase-cleanup, .gitignore, nul-file
#  5. Large-file gate – scan + auto-gitignore dangerous paths before staging
#  6. Stage & commit  – interactive commit message, skip when tree is clean
#  7. Sync            – fetch → merge → push, with upstream auto-link
#  8. Error recovery  – diverged-history prompt with force-push option
#
#  NOTE: ErrorActionPreference stays "Continue".  Git writes informational text
#  to stderr on success; letting PowerShell treat that as a fatal error would
#  break every push.  Exit codes are checked manually via $LASTEXITCODE.

$ErrorActionPreference = "Continue"

# ══════════════════════════════════════════════════════════════════════════════
#  CONFIG
# ══════════════════════════════════════════════════════════════════════════════
# Get active repository remote URL or fallback
$remoteUrl = "https://github.com/chaiz64/douyin-downloaderz.git"
if (Get-Command git -ErrorAction SilentlyContinue) {
    $gitRemote = git remote get-url origin 2>$null
    if ($gitRemote) { $remoteUrl = $gitRemote.Trim() }
}

$cfg = @{
    ProjectDir   = if ($PSScriptRoot) { $PSScriptRoot } else { "C:\MyCodes\d0uyin_t00ls\douyin-downloader" }
    RepoUrl      = $remoteUrl
    LargeFileMB  = 95          # GitHub hard limit is 100 MB; stay safely under
    GitHubHardMB = 100
}

# ══════════════════════════════════════════════════════════════════════════════
#  THEME  (Claude palette: coral · sand · charcoal · warm-white)
# ══════════════════════════════════════════════════════════════════════════════
$t = @{
    Primary   = "DarkYellow"    # coral / amber
    Secondary = "Yellow"        # warm sand
    Muted     = "DarkGray"      # charcoal
    Text      = "White"         # warm-white
    Ok        = "Green"
    Warn      = "Yellow"
    Error     = "Red"
    Subtle    = "Gray"
}

# ══════════════════════════════════════════════════════════════════════════════
#  LOGGER
# ══════════════════════════════════════════════════════════════════════════════
function Get-Timestamp { Get-Date -Format "HH:mm:ss" }

function Write-Rule {
    param([string]$Color = $t.Muted)
    Write-Host ("  " + ("─" * 75)) -ForegroundColor $Color
}

function Write-Blank { Write-Host "" }

function Write-Log {
    param(
        [string]$Level,
        [string]$Message,
        [string]$LevelColor,
        [string]$MessageColor = $t.Text
    )
    $ts = Get-Timestamp
    $pad = "        "   # aligns message column
    Write-Host "  $ts  " -ForegroundColor $t.Muted -NoNewline
    Write-Host $Level     -ForegroundColor $LevelColor -NoNewline
    Write-Host $pad       -NoNewline
    Write-Host $Message   -ForegroundColor $MessageColor
}

function Log-Step { param($m) Write-Log "●" $m $t.Primary  $t.Text }
function Log-Ok { param($m) Write-Log "✓" $m $t.Ok       $t.Text }
function Log-Warn { param($m) Write-Log "!" $m $t.Warn     $t.Warn }
function Log-Error { param($m) Write-Log "✗" $m $t.Error    $t.Error }
function Log-Dim { param($m) Write-Log "·" $m $t.Muted    $t.Muted }

function Write-Detail {
    param([string]$Text)
    Write-Host ("  " + (" " * 18) + $Text) -ForegroundColor $t.Muted
}

function Write-PanelLine {
    param([string]$Key, [string]$Value, [string]$ValueColor = $t.Secondary)
    $label = "{0,-12}" -f $Key
    Write-Host "  │  " -ForegroundColor $t.Muted -NoNewline
    Write-Host $label -ForegroundColor $t.Muted -NoNewline

    $valStr = $Value
    if ($valStr.Length -gt 57) { $valStr = $valStr.Substring(0, 54) + "..." }
    $valPad = "{0,-59}" -f $valStr

    Write-Host $valPad -ForegroundColor $ValueColor -NoNewline
    Write-Host "│" -ForegroundColor $t.Muted
}

# ══════════════════════════════════════════════════════════════════════════════
#  BANNER & WORKSPACE PANEL
# ══════════════════════════════════════════════════════════════════════════════
$global:ScriptVersion = "v1.1.0"

function Write-Banner {
    Clear-Host
    Write-Blank
    $logo1 = "   ██████╗ ██╗████████╗    ███████╗██╗   ██╗███╗   ██╗ ██████╗ "
    $logo2 = "  ██╔════╝ ██║╚══██╔══╝    ██╔════╝╚██╗ ██╔╝████╗  ██║██╔════╝ "
    $logo3 = "  ██║  ███╗██║   ██║█████╗ ███████╗ ╚████╔╝ ██╔██╗ ██║██║      "
    $logo4 = "  ██║   ██║██║   ██║╚════╝ ╚════██║  ╚██╔╝  ██║╚██╗██║██║      "
    $logo5 = "  ╚██████╔╝██║   ██║       ███████║   ██║   ██║ ╚████║╚██████╗ "
    $logo6 = "   ╚═════╝ ╚═╝   ╚═╝       ╚══════╝   ╚═╝   ╚═╝  ╚═══╝ ╚═════╝ "

    Write-Host $logo1 -ForegroundColor $t.Primary
    Write-Host $logo2 -ForegroundColor $t.Primary
    Write-Host $logo3 -ForegroundColor $t.Primary
    Write-Host $logo4 -ForegroundColor $t.Primary
    Write-Host $logo5 -ForegroundColor $t.Primary
    Write-Host $logo6 -ForegroundColor $t.Primary
    Write-Blank
}

function Write-WorkspacePanel {
    param([string]$Branch)

    $panelTop = "╭─────────────────────────────────────────────────────────────────────────╮"
    $panelMiddle = "├─────────────────────────────────────────────────────────────────────────┤"
    $panelBottom = "╰─────────────────────────────────────────────────────────────────────────╯"

    $branchColor = if ($Branch -match "^(main|master)$") { $t.Ok } else { $t.Secondary }

    Write-Host "  $panelTop" -ForegroundColor $t.Muted

    Write-Host "  │" -ForegroundColor $t.Muted -NoNewline
    Write-Host "  STREAM TOOLKIT" -ForegroundColor $t.Primary -NoNewline

    $versionStr = "GIT SYNC $global:ScriptVersion"
    $padLength = 73 - 2 - 14 - $versionStr.Length - 1
    Write-Host (" " * $padLength) -NoNewline
    Write-Host "$versionStr " -ForegroundColor $t.Secondary -NoNewline
    Write-Host "│" -ForegroundColor $t.Muted

    Write-Host "  $panelMiddle" -ForegroundColor $t.Muted

    Write-PanelLine "Project" $cfg.ProjectDir $t.Text
    Write-PanelLine "Remote"  $cfg.RepoUrl $t.Text
    Write-PanelLine "Branch"  $Branch $branchColor
    Write-PanelLine "Size Gate" "$($cfg.LargeFileMB) MB (GitHub Limit: $($cfg.GitHubHardMB) MB)" $t.Muted

    Write-Host "  $panelBottom" -ForegroundColor $t.Muted
    Write-Blank
}

# ══════════════════════════════════════════════════════════════════════════════
#  GIT PRIMITIVES
# ══════════════════════════════════════════════════════════════════════════════

# Run git; suppress all output; return $LASTEXITCODE
function Invoke-Git {
    git @args *>&1 | Out-Null
    return $LASTEXITCODE
}

# Run git; capture stdout; discard stderr
function Get-GitOutput {
    $result = git @args 2>$null
    return $result
}

# Run git; stream output to host with indent; return $LASTEXITCODE
function Invoke-GitVerbose {
    $output = git @args 2>&1
    foreach ($line in $output) {
        if ($line -is [System.Management.Automation.ErrorRecord]) {
            Write-Detail $line.ToString()
        }
        else {
            Write-Detail $line
        }
    }
    return $LASTEXITCODE
}

# ══════════════════════════════════════════════════════════════════════════════
#  PRE-FLIGHT CHECKS
# ══════════════════════════════════════════════════════════════════════════════
function Invoke-Preflight {

    # 1. Ensure we are in the project directory
    if (-not (Test-Path $cfg.ProjectDir)) {
        throw "Project directory not found: $($cfg.ProjectDir)"
    }
    Set-Location $cfg.ProjectDir

    # 2. Git init
    if (-not (Test-Path ".git")) {
        Log-Warn "No git repository found — initialising..."
        Invoke-Git init          | Out-Null
        Invoke-Git remote add origin $cfg.RepoUrl | Out-Null
        Log-Ok "Repository initialised."
    }

    # 3. Remote sanity check
    $remotes = Get-GitOutput remote -v
    if ("$remotes" -notmatch [regex]::Escape($cfg.RepoUrl)) {
        Log-Warn "Remote mismatch — resetting origin..."
        Invoke-Git remote remove origin | Out-Null
        Invoke-Git remote add origin $cfg.RepoUrl | Out-Null
        Log-Ok "Remote updated to correct URL."
    }

    # 4. Rename master → main (GitHub default; avoids pushing to wrong branch)
    $localBranches = Get-GitOutput branch
    $hasMaster = ($localBranches -match "\bmaster\b")
    $hasMain = ($localBranches -match "\bmain\b")
    if ($hasMaster -and -not $hasMain) {
        Log-Warn "Local branch is 'master' — renaming to 'main' to match GitHub default..."
        Invoke-Git branch -m master main | Out-Null
        Log-Ok "Branch renamed: master → main"
    }

    # 5. Abort stale rebase
    if (Test-Path ".git\rebase-merge") {
        Log-Warn "Interrupted rebase detected — aborting cleanly..."
        Invoke-Git rebase --abort | Out-Null
        Log-Ok "Rebase aborted."
    }

    # 6. .gitignore
    if (-not (Test-Path ".gitignore")) {
        Log-Step "Generating .gitignore..."
        New-Gitignore
        Log-Ok ".gitignore created."
    }

    # 7. Purge the 'nul' device file if it leaked into the worktree
    if (Test-Path "nul") {
        Remove-Item "nul" -Force -ErrorAction SilentlyContinue
        Log-Ok "Removed stray 'nul' device file."
    }
}

function New-Gitignore {
    @"
# Python
__pycache__/
*.pyc
*.pyo
*.pyd
.Python
*.so
*.egg
*.egg-info/
dist/
build/

# Output directories
output/
*.log

# Config
config.json

# IDE
.vscode/
.idea/
*.swp
*.swo
*~

# OS
.DS_Store
Thumbs.db

# Tools – large executables are NEVER committed
tools/*.exe
tools/*.dll
tools/ffmpeg.exe
tools/yt-dlp.exe
tools/N_m3u8DL-RE.exe
!tools/.gitkeep

# Large media files
*.mp4
*.mkv
*.ts
*.webm
*.avi
*.mov

# Logs
tools/Logs/
*.log
"@ | Set-Content -Path ".gitignore" -Encoding utf8
}

# ══════════════════════════════════════════════════════════════════════════════
#  LARGE-FILE GATE
#  Scans the working tree BEFORE staging.  Any file that exceeds the configured
#  limit and is not yet tracked by .gitignore triggers an auto-ignore or a hard
#  stop, preventing a rejected push from GitHub.
# ══════════════════════════════════════════════════════════════════════════════
function Invoke-LargeFileGate {
    Log-Step "Scanning for files larger than $($cfg.LargeFileMB) MB..."

    $candidates = Get-ChildItem -Recurse -File -ErrorAction SilentlyContinue |
    Where-Object {
        $_.Length -gt ($cfg.LargeFileMB * 1MB) -and
        $_.FullName -notmatch "\\\\.git\\\\" -and
        $_.FullName -notmatch "\\\\output" -and
        $_.FullName -notmatch "\\\\graphify-out\\\\"
    }

    if (-not $candidates) {
        Log-Ok "No oversized files detected."
        return
    }

    # Separate files already covered by .gitignore from unprotected ones
    $unprotected = [System.Collections.Generic.List[System.IO.FileInfo]]::new()

    foreach ($f in $candidates) {
        $relPath = $f.FullName.Replace($cfg.ProjectDir + "\", "").Replace("\", "/")
        $checkOutput = git check-ignore -q $relPath 2>$null
        if ($LASTEXITCODE -ne 0) {
            # git check-ignore exits non-zero when the file is NOT ignored
            $unprotected.Add($f)
        }
    }

    # Report anything that is ignored (safe — just informational)
    $safeCount = $candidates.Count - $unprotected.Count
    if ($safeCount -gt 0) {
        Log-Dim "$safeCount large file(s) already covered by .gitignore — skipped."
    }

    if ($unprotected.Count -eq 0) {
        Log-Ok "All large files are gitignored."
        return
    }

    # We have unprotected large files — auto-append patterns to .gitignore
    Write-Blank
    Log-Warn "$($unprotected.Count) UNIGNORED large file(s) would be rejected by GitHub:"
    Write-Blank

    $appendLines = [System.Collections.Generic.List[string]]::new()
    $appendLines.Add("")
    $appendLines.Add("# Auto-added by git_sync — files exceeding GitHub's $($cfg.GitHubHardMB) MB limit")

    foreach ($f in $unprotected) {
        $relPath = $f.FullName.Replace($cfg.ProjectDir + "\", "").Replace("\", "/")
        $sizeMb = [math]::Round($f.Length / 1MB, 1)
        Write-Host ("    {0,-52}  {1,7} MB  ← will be ignored" -f $relPath, $sizeMb) `
            -ForegroundColor $t.Warn
        $appendLines.Add($relPath)
    }

    $appendLines | Add-Content -Path ".gitignore" -Encoding utf8

    Write-Blank
    Log-Ok "Appended $($unprotected.Count) pattern(s) to .gitignore — re-staging will exclude them."

    # Un-track any of these files that git already knows about
    foreach ($f in $unprotected) {
        $relPath = $f.FullName.Replace($cfg.ProjectDir + "\", "").Replace("\", "/")
        $tracked = Get-GitOutput ls-files $relPath
        if ($tracked) {
            Invoke-Git rm --cached $relPath | Out-Null
            Log-Dim "Un-tracked from index: $relPath"
        }
    }
    Write-Blank
}

# ══════════════════════════════════════════════════════════════════════════════
#  STAGE & COMMIT
# ══════════════════════════════════════════════════════════════════════════════
function Invoke-Commit {
    Log-Step "Staging all changes..."
    Invoke-Git add . | Out-Null

    $rawStatus = Get-GitOutput status --porcelain
    $lines = @($rawStatus -split "`n" | Where-Object { $_.Trim() -ne "" })

    if ($lines.Count -eq 0) {
        Log-Ok "Nothing to commit — working tree is clean."
        return
    }

    # Show changed files
    Write-Blank
    Log-Step "$($lines.Count) changed file(s) staged:"
    foreach ($line in $lines) {
        Write-Detail $line.Trim()
    }
    Write-Blank

    # Prompt for commit message
    Write-Host "  commit message" -ForegroundColor $t.Primary -NoNewline
    Write-Host "  (press Enter for auto-timestamp)" -ForegroundColor $t.Muted
    Write-Host "  ❯ " -ForegroundColor $t.Primary -NoNewline
    $msg = Read-Host

    if ([string]::IsNullOrWhiteSpace($msg)) {
        $msg = "Update @ $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
    }

    $exitCode = Invoke-Git commit -m $msg
    if ($exitCode -ne 0) {
        throw "Commit failed (exit $exitCode)"
    }

    Write-Blank
    Log-Ok "Committed: $msg"
}

# ══════════════════════════════════════════════════════════════════════════════
#  SYNC  (fetch → set-upstream → merge → push)
# ══════════════════════════════════════════════════════════════════════════════
function Invoke-Sync {
    param([string]$Branch)

    Write-Blank
    Log-Step "Synchronising with GitHub..."
    Write-Blank

    # Does the remote branch already exist?
    $lsRemote = Get-GitOutput ls-remote --heads origin $Branch
    $remoteExists = ("$lsRemote" -match [regex]::Escape($Branch))

    if (-not $remoteExists) {
        # ── First push ──────────────────────────────────────────────────────
        Log-Warn "Remote branch '$Branch' does not exist — creating..."
        $exitCode = Invoke-GitVerbose push -u origin $Branch
        if ($exitCode -ne 0) {
            throw "Initial push failed (exit $exitCode)"
        }
        Log-Ok "Remote branch created and code pushed."
        return
    }

    # ── Fetch ───────────────────────────────────────────────────────────────
    Log-Step "Fetching origin/$Branch..."
    $exitCode = Invoke-GitVerbose fetch origin $Branch
    if ($exitCode -ne 0) {
        throw "Fetch failed (exit $exitCode)"
    }

    # ── Ensure upstream is set ──────────────────────────────────────────────
    git rev-parse --abbrev-ref "@{upstream}" 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Log-Warn "Upstream not configured — linking to origin/$Branch..."
        Invoke-Git branch --set-upstream-to="origin/$Branch" $Branch | Out-Null
    }

    # ── Merge ───────────────────────────────────────────────────────────────
    Log-Step "Merging origin/$Branch..."
    $exitCode = Invoke-GitVerbose merge "origin/$Branch" --no-edit --allow-unrelated-histories
    if ($exitCode -ne 0) {
        throw "Merge conflict on '$Branch' — resolve conflicts, then re-run."
    }

    # ── Push ────────────────────────────────────────────────────────────────
    Log-Step "Pushing to origin/$Branch..."
    $exitCode = Invoke-GitVerbose push origin $Branch
    if ($exitCode -ne 0) {
        throw "Push failed (exit $exitCode)"
    }
}

# ══════════════════════════════════════════════════════════════════════════════
#  ERROR RECOVERY  – diverged-history / rejected push
# ══════════════════════════════════════════════════════════════════════════════
function Invoke-ErrorRecovery {
    param([string]$ErrorMessage, [string]$Branch)

    $isDiverged = $ErrorMessage -match "rejected|diverged|non-fast-forward|Push failed"
    if (-not $isDiverged) { return }

    Write-Blank
    Log-Warn "Remote and local histories have diverged (or the push was rejected)."
    Write-Blank
    Write-Host "  Choose an action:" -ForegroundColor $t.Text
    Write-Blank
    Write-Host "    [1]  Force push" -ForegroundColor $t.Primary -NoNewline
    Write-Host "  — overwrites the remote (destructive, use with care)" -ForegroundColor $t.Muted
    Write-Host "    [2]  Cancel" -ForegroundColor $t.Muted
    Write-Blank
    Write-Host "  ❯ " -ForegroundColor $t.Primary -NoNewline
    $choice = Read-Host

    if ($choice -eq "1") {
        Log-Warn "Force pushing to origin/$Branch..."
        $exitCode = Invoke-GitVerbose push origin $Branch --force
        if ($exitCode -eq 0) {
            Log-Ok "Force push complete."
        }
        else {
            Log-Error "Force push failed — check your credentials and repository permissions."
        }
    }
    else {
        Log-Ok "Cancelled — no changes pushed to remote."
    }
}

# ══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════════
$script:currentBranch = "main"
$script:syncOk = $false

Write-Banner

try {

    Invoke-Preflight

    # Detect active branch after init/checkout
    $b = Get-GitOutput branch --show-current
    if (-not [string]::IsNullOrWhiteSpace($b)) {
        $script:currentBranch = $b.Trim()
    }

    Write-WorkspacePanel -Branch $script:currentBranch

    Invoke-LargeFileGate

    Invoke-Commit

    Invoke-Sync -Branch $script:currentBranch

    $script:syncOk = $true

    Write-Blank
    Write-Rule $t.Ok
    Log-Ok "All systems synchronised.  $($cfg.RepoUrl)"
    Write-Rule $t.Ok
    Write-Blank

}
catch {

    Write-Blank
    Write-Rule $t.Error
    Log-Error "Sync failed"
    Write-Detail $_.Exception.Message
    Write-Rule $t.Error

    Invoke-ErrorRecovery -ErrorMessage $_.Exception.Message -Branch $script:currentBranch

}
finally {

    Write-Blank
    Write-Host "  Press any key to exit..." -ForegroundColor $t.Muted
    $null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")

}
