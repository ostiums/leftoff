# Installs leftoff on Windows. Safe to re-run (also used by `leftoff update`).
#
#   irm https://raw.githubusercontent.com/ostiums/leftoff/main/install.ps1 | iex
#   iex "& {$(irm https://raw.githubusercontent.com/ostiums/leftoff/main/install.ps1)} -NoAutosync"
#
# Run through iex, it clones (or fast-forwards) the repo into ~\.local\share\leftoff and continues
# from there. Autosync is switched on only on the first install, so an update never turns it back
# on after `leftoff autosync off`.
#
# iex runs this in the user's own session, so the body sits in a child scope (its settings and
# helpers don't stay behind) and errors throw instead of calling exit (which would close the window).
param([switch]$NoAutosync)

& {
    $ErrorActionPreference = 'Stop'

    function Find-Python {
        # sys.executable, not the command name: the shims and the hook then work without a PATH
        # lookup, and the Microsoft Store python.exe stub (prints nothing, exits 9009) is skipped.
        $probe = "import sys; print(sys.executable if sys.version_info >= (3, 9) else '')"
        foreach ($candidate in 'py -3', 'python', 'python3') {
            $parts = $candidate -split ' '
            $cmd = Get-Command $parts[0] -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
            if (-not $cmd) { continue }
            $pyArgs = @($parts | Select-Object -Skip 1) + @('-c', $probe)
            # All output, not `| Select-Object -First 1`: that stops python early and leaves $LASTEXITCODE unset.
            try { $out = @(& $cmd.Source @pyArgs 2>$null) } catch { continue }
            $exe = "$($out | Select-Object -First 1)".Trim()
            if ($LASTEXITCODE -eq 0 -and $exe -and (Test-Path -LiteralPath $exe)) { return $exe }
        }
        return $null
    }

    # Only when the content changes: `leftoff update` runs this installer from inside leftoff.cmd,
    # and cmd keeps reading a batch file while it runs.
    function Write-Utf8 ($path, $text) {
        if ((Test-Path -LiteralPath $path) -and [IO.File]::ReadAllText($path) -ceq $text) { return }
        [IO.File]::WriteAllText($path, $text, (New-Object Text.UTF8Encoding $false))
    }

    # The .cmd shim refers to %USERPROFILE% rather than the literal path: cmd reads batch files in
    # the OEM code page, so a non-ASCII user name written out literally would break it.
    function To-CmdPath ($path) {
        $home_ = $HOME.TrimEnd('\') + '\'
        if ($path.StartsWith($home_, [StringComparison]::OrdinalIgnoreCase)) {
            return '%USERPROFILE%\' + $path.Substring($home_.Length)
        }
        return $path
    }

    # `autosync status` output. Under Windows PowerShell 5.1 with $ErrorActionPreference = 'Stop',
    # anything the command writes to stderr would otherwise end the installer.
    function Get-AutosyncStatus {
        try { return "$(& $python $leftoffPy autosync status 2>$null)" } catch { return '' }
    }

    $python = Find-Python
    if (-not $python) {
        throw 'Python 3.9 or newer is required (winget install Python.Python.3.13, or python.org)'
    }

    $share = Join-Path $HOME '.local\share\leftoff'
    $repo = if ($env:LEFTOFF_REPO) { $env:LEFTOFF_REPO } else { 'https://github.com/ostiums/leftoff.git' }

    if ($PSCommandPath -and (Split-Path -Leaf $PSCommandPath) -eq 'install.ps1' -and
            (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'leftoff.py'))) {
        $here = $PSScriptRoot
    } else {
        $here = $share
        if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
            throw 'git is required (winget install Git.Git)'
        }
        if (Test-Path -LiteralPath (Join-Path $here '.git')) {
            git -C $here pull --ff-only -q
        } else {
            New-Item -ItemType Directory -Force -Path (Split-Path $here) | Out-Null
            git clone -q $repo $here
        }
        if ($LASTEXITCODE -ne 0) { throw 'git failed' }
    }

    $leftoffPy = Join-Path $here 'leftoff.py'
    $binDir = Join-Path $HOME '.local\bin'
    $configDir = if ($env:CLAUDE_CONFIG_DIR) { $env:CLAUDE_CONFIG_DIR } else { Join-Path $HOME '.claude' }
    $commandsDir = Join-Path $configDir 'commands'
    $shim = Join-Path $binDir 'leftoff.cmd'
    $firstInstall = -not (Test-Path -LiteralPath $shim)

    New-Item -ItemType Directory -Force -Path $binDir, $commandsDir | Out-Null

    # leftoff.cmd for PowerShell and cmd, and an extensionless sh script for Git Bash, which Claude
    # Code uses for `!` commands when Git for Windows is installed. npm installs the same pair.
    # `& exit /b` keeps cmd from reading on in the file (it may have been rewritten meanwhile, by
    # `leftoff update`) and passes python's exit code through.
    Write-Utf8 $shim ("@`"{0}`" `"{1}`" %* & exit /b`r`n" -f (To-CmdPath $python), (To-CmdPath $leftoffPy))
    Write-Utf8 (Join-Path $binDir 'leftoff') ("#!/bin/sh`nexec `"{0}`" `"{1}`" `"`$@`"`n" -f
        $python.Replace('\', '/'), $leftoffPy.Replace('\', '/'))

    # The leftoff plugin brings its own /leftoff and hook; a copy here would only duplicate them.
    $command = Join-Path $commandsDir 'leftoff.md'
    if ((Get-AutosyncStatus) -match 'leftoff plugin') {
        Remove-Item -LiteralPath $command -ErrorAction SilentlyContinue
    } else {
        Copy-Item -LiteralPath (Join-Path $here 'commands\leftoff.md') -Destination $command -Force
    }

    if (-not (Get-Command fzf -ErrorAction SilentlyContinue)) {
        if (Get-Command winget -ErrorAction SilentlyContinue) {
            winget install --id junegunn.fzf --exact --silent --accept-package-agreements --accept-source-agreements
            if ($LASTEXITCODE -eq 0) {
                Write-Host 'fzf is available in new terminal windows'
            } else {
                Write-Host 'Could not install fzf: chats will be picked from a numbered list (you can install fzf later)'
            }
        } else {
            Write-Host 'fzf not found and no winget: chats will be picked from a numbered list (you can install fzf later)'
        }
    }

    # The user PATH is read and written raw, so entries like %USERPROFILE%\bin stay unexpanded.
    $envKey = (Get-Item 'HKCU:\').OpenSubKey('Environment', $true)
    $userPath = [string]$envKey.GetValue('Path', '', 'DoNotExpandEnvironmentNames')
    $entries = @($env:Path -split ';') + @($userPath -split ';') |
        ForEach-Object { [Environment]::ExpandEnvironmentVariables($_).TrimEnd('\') }
    if ($entries -notcontains $binDir) {
        $newPath = if ($userPath) { $userPath.TrimEnd(';') + ';' + $binDir } else { $binDir }
        $envKey.SetValue('Path', $newPath, 'ExpandString')
        # Setting any user variable through .NET broadcasts the change to Explorer and new terminals.
        [Environment]::SetEnvironmentVariable('LEFTOFF_INSTALL', '1', 'User')
        [Environment]::SetEnvironmentVariable('LEFTOFF_INSTALL', $null, 'User')
        $env:Path = $env:Path.TrimEnd(';') + ';' + $binDir
        Write-Host 'Added ~\.local\bin to your user PATH: open a new terminal window'
    }
    $envKey.Close()

    if ($firstInstall -and -not $NoAutosync) {
        & $python $leftoffPy autosync on  # also runs the first sync
    } elseif ((Get-AutosyncStatus) -match 'is on') {
        & $python $leftoffPy autosync on | Out-Null  # rewrites a hook left by an older version to the current command
    }

    Write-Host 'Done: leftoff  +  /leftoff in Claude Code'
}
