<#
.SYNOPSIS
Makes a ScanTailor project for each book folder under a root folder and runs filters 1-5 only.

.DESCRIPTION
A book folder is a folder that contains page images (jpg, jpeg, tif, tiff, png).
The script looks at the root folder and at all its subfolders, at any depth.
Folders with the names "out", "out-*" and "cache" are not book folders.

For each book folder the script runs scantailor-universal-cli.exe with automatic settings,
filters 1 to 5 (Fix Orientation, Split Pages, Deskew, Select Content, Margins).
It does not run filter 6 (Output), so it writes no output images.
The result is <book folder>\projektas.ScanTailor, ready for a review before the output step.

The script does not touch a book folder that already has projektas.ScanTailor.
To make the project again, delete or rename the old project file.

With -OutputOnly the script runs filters 5 and 6 on each book folder that has projektas.ScanTailor,
and writes the output images to the output folder of the project. It does not make new projects.
Filter 5 runs again because a change of one page can change the common page size.
The CLI writes again only the pages that changed: it compares the output settings, the fill zones and
the size of the output file with the values that the project keeps from the last output run.
The CLI saves these values into the project (--output-project), so the next run can skip a page.
A project without these values (made before this change) gets all its pages once.
With -OutputOnly -Force the script removes these values first (it keeps a copy of the project),
so the CLI writes all pages again.

Logs go to <root>\st-prepare-logs: one log for each book and summary.csv.

.EXAMPLE
powershell -ExecutionPolicy Bypass -File C:\Portable\ScanTailor-Universal\st-prepare-books.ps1 -Root "C:\nes\Serijos_HIGH"

.EXAMPLE
.\st-prepare-books.ps1 -Root "C:\nes\Serijos_HIGH\(Mokinio skaitiniai)" -OutputOnly
Makes the output images for all projects after the review.

.EXAMPLE
.\st-prepare-books.ps1 -Root "C:\nes\Serijos_HIGH\(Mokinio skaitiniai)" -OutputOnly -Force
Makes the output images of all pages again, also of the pages that did not change.

.EXAMPLE
.\st-prepare-books.ps1 -Root "C:\nes\Serijos_HIGH" -DryRun
Shows the book folders and the commands, and changes nothing.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Root,

    # The CLI next to this script (in the "windows" folder) is the default.
    [string]$Cli = '',

    [string]$ProjectName = 'projektas.ScanTailor',

    # The output folder name in the book folder; filter 6 writes the images there later.
    [string]$OutName = 'out',

    # 0: auto, 1: one page, 1.5: one page with a cut, 2: two pages.
    [ValidateSet('0', '1', '1.5', '2')]
    [string]$Layout = '0',

    [ValidateSet('auto', 'none', 'left', 'right', 'upsidedown')]
    [string]$Orientation = 'auto',

    # The DPI for images that have no usable DPI in the file (none, 72 or 96).
    [int]$FallbackDpi = 300,

    # Pages of one filter that run at the same time: a number, or auto (one per CPU core).
    [string]$Threads = 'auto',

    # Other CLI options, for example: -ExtraArgs '--content-detection=cautious'
    [string[]]$ExtraArgs = @(),

    # Run only filters 5-6 on the existing projects.
    [switch]$OutputOnly,

    # With -OutputOnly: write all output images again, not only the pages that changed.
    [switch]$Force,

    [switch]$DryRun
)

Set-StrictMode -Version 2
$ErrorActionPreference = 'Stop'

$imageExt = @('.jpg', '.jpeg', '.tif', '.tiff', '.png')

function Test-SkipDir([string]$name) {
    # The review and log folders of the agent tools hold images too, but they are not books.
    return ($name -eq 'cache' -or $name -eq $OutName -or $name -like 'out-*' -or $name -eq 'out' -or
            $name -eq 'st-review' -or $name -eq 'st-prepare-logs')
}

function Get-BookFolders([string]$dir) {
    $item = Get-Item -LiteralPath $dir
    $images = @(Get-ChildItem -LiteralPath $dir -File | Where-Object { $imageExt -contains $_.Extension.ToLower() })
    if ($images.Count -gt 0) { $item }
    Get-ChildItem -LiteralPath $dir -Directory | Where-Object { -not (Test-SkipDir $_.Name) } |
        Sort-Object Name | ForEach-Object { Get-BookFolders $_.FullName }
}

# Pad the numbers in a file name, so that 9.jpg comes before 10.jpg.
function Get-NaturalKey([string]$name) {
    return [regex]::Replace($name.ToLower(), '\d+', { param($m) $m.Value.PadLeft(20, '0') })
}

Add-Type -AssemblyName System.Drawing

# Read only the image header, because a full decode of each page is slow.
function Get-ImageDpi([string]$path) {
    $fs = [System.IO.File]::OpenRead($path)
    try {
        $img = [System.Drawing.Image]::FromStream($fs, $false, $false)
        try { return [math]::Round($img.HorizontalResolution) } finally { $img.Dispose() }
    } catch {
        return 0
    } finally {
        $fs.Dispose()
    }
}

function Write-Log([string]$text) {
    $line = '[{0}] {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $text
    Write-Host $line
    if (-not $DryRun) { Add-Content -LiteralPath $mainLog -Value $line -Encoding UTF8 }
}

$Root = (Resolve-Path -LiteralPath $Root).Path
# Windows PowerShell 5.1 has no $PSScriptRoot in the param block, so the default is set here.
if (-not $Cli) { $Cli = Join-Path $PSScriptRoot 'windows\scantailor-universal-cli.exe' }
if ($Force -and -not $OutputOnly) { throw '-Force works only together with -OutputOnly' }
if (-not (Test-Path -LiteralPath $Cli)) { throw "CLI not found: $Cli (use -Cli <path>)" }
$Cli = (Resolve-Path -LiteralPath $Cli).Path

$logDir = Join-Path $Root 'st-prepare-logs'
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$mainLog = Join-Path $logDir "run-$stamp.log"
$summaryCsv = Join-Path $logDir 'summary.csv'
if (-not $DryRun) { New-Item -ItemType Directory -Force -Path $logDir | Out-Null }

# One OpenMP thread for each page is faster and gives the same result in each run.
$env:OMP_NUM_THREADS = '1'

$books = @(Get-BookFolders $Root)
Write-Log ("{0} book folder(s) under {1}; CLI: {2}" -f $books.Count, $Root, $Cli)

$summary = @()
$n = 0
foreach ($book in $books) {
    $n++
    $dir = $book.FullName
    $proj = Join-Path $dir $ProjectName
    $out = Join-Path $dir $OutName
    $row = [ordered]@{ Book = $dir; Status = ''; Images = 0; Pages = ''; Dpi = ''; Minutes = ''; Log = ''; Note = '' }

    if ($OutputOnly) {
        if (-not (Test-Path -LiteralPath $proj)) {
            $row.Status = 'skipped'
            $row.Note = "no $ProjectName"
            Write-Log ("{0}/{1} SKIP (no project): {2}" -f $n, $books.Count, $dir)
            $summary += [pscustomobject]$row
            continue
        }
        if (Test-Path -LiteralPath (Join-Path $dir 'out-orig-antspaudai')) {
            # Old cleanups changed only the output files, so a page that the CLI writes again gets its marks back.
            $row.Note = 'some pages were cleaned only in the output files: check them, or add fill zones'
        }
        [xml]$px = Get-Content -LiteralPath $proj -Encoding UTF8 -Raw
        $projOut = $px.project.outputDirectory
        if (-not [System.IO.Path]::IsPathRooted($projOut)) { $projOut = Join-Path $dir $projOut }
        $kept = @($px.SelectNodes('/project/filters/output/page/output-params')).Count
        if ($Force -and -not $DryRun -and $kept -gt 0) {
            # Without the values of the last output run the CLI cannot skip a page, so it writes all pages.
            Copy-Item -LiteralPath $proj -Destination "$proj.before-force-$stamp" -Force
            foreach ($node in @($px.SelectNodes('/project/filters/output/page/output-params'))) {
                [void]$node.ParentNode.RemoveChild($node)
            }
            $px.Save($proj)
        }
        $row.Note = (@($row.Note, ("pages with values of the last output run: {0}" -f $kept)) | Where-Object { $_ }) -join '; '
        # The CLI saves the values of this run into the project, so the next run writes only the pages that changed.
        $cliArgs = @('--verbose', '--start-filter=5', '--end-filter=6', "--threads=$Threads") + $ExtraArgs +
                   @("--output-project=$proj", $proj, $projOut)
    } elseif (Test-Path -LiteralPath $proj) {
        $row.Status = 'skipped'
        $row.Note = "$ProjectName exists"
        Write-Log ("{0}/{1} SKIP (project exists): {2}" -f $n, $books.Count, $dir)
        $summary += [pscustomobject]$row
        continue
    }

    $images = @(Get-ChildItem -LiteralPath $dir -File | Where-Object { $imageExt -contains $_.Extension.ToLower() } |
        Sort-Object { Get-NaturalKey $_.Name })
    $row.Images = $images.Count

    if (-not $OutputOnly) {

    $dpis = @($images | ForEach-Object { Get-ImageDpi $_.FullName })
    $bad = @($dpis | Where-Object { $_ -le 0 -or $_ -eq 72 -or $_ -eq 96 })
    $dpiArgs = @()
    if ($bad.Count -gt 0) {
        # The CLI can only force one DPI on all images, so a book with any image without DPI gets the fallback.
        $dpiArgs = @("--dpi=$FallbackDpi")
        $row.Dpi = "forced $FallbackDpi ($($bad.Count) image(s) without DPI)"
    } else {
        $row.Dpi = (($dpis | Group-Object | Sort-Object Count -Descending | ForEach-Object { "$($_.Name) x$($_.Count)" }) -join ', ')
    }

    $names = @($images | ForEach-Object { $_.Name })
    $lenSum = ($names | Measure-Object -Property Length -Sum).Sum + 3 * $names.Count
    if ($lenSum -gt 25000) {
        # A very long command line fails, so the CLI reads the folder itself in plain name order.
        $inputArgs = @($dir)
        $plain = @($images | Sort-Object Name | ForEach-Object { $_.Name })
        if (($plain -join '|') -ne ($names -join '|')) { $row.Note = 'WARNING: page order is plain name order, not natural order' }
    } else {
        $inputArgs = $names
    }

    $cliArgs = @('--verbose', '--start-filter=1', '--end-filter=5', "--orientation=$Orientation", "--layout=$Layout",
                 "--threads=$Threads") + $dpiArgs + $ExtraArgs + @("--output-project=$proj") + $inputArgs + @($out)
    }

    $safe = [regex]::Replace($book.Name, '[^\w.-]+', '_')
    if ($safe.Length -gt 60) { $safe = $safe.Substring(0, 60) }
    $log = Join-Path $logDir ('{0:D3}-{1}.log' -f $n, $safe)
    $row.Log = $log

    if ($DryRun -and $OutputOnly) {
        Write-Host ("{0}/{1} {2}`n    `"{3}`" {4}" -f $n, $books.Count, $dir, $Cli, ($cliArgs -join ' '))
        $row.Status = 'dry-run'
        $summary += [pscustomobject]$row
        continue
    }
    if ($DryRun) {
        $shown = if ($inputArgs.Count -gt 4) { @($inputArgs[0..1]) + @("... ($($inputArgs.Count) files) ...") + @($inputArgs[-1]) } else { $inputArgs }
        $pre = @('--verbose', '--start-filter=1', '--end-filter=5', "--orientation=$Orientation", "--layout=$Layout",
                 "--threads=$Threads") + $dpiArgs + $ExtraArgs + @("--output-project=$proj")
        Write-Host ("{0}/{1} {2}`n    cd `"{2}`"`n    `"{3}`" {4} {5} `"{6}`"`n    DPI: {7} {8}" -f $n, $books.Count, $dir, $Cli,
            ($pre -join ' '), ($shown -join ' '), $out, $row.Dpi, $row.Note)
        $row.Status = 'dry-run'
        $summary += [pscustomobject]$row
        continue
    }

    Write-Log ("{0}/{1} START {2} ({3} images, DPI {4})" -f $n, $books.Count, $dir, $images.Count, $row.Dpi)
    $t0 = Get-Date
    Push-Location -LiteralPath $dir
    $oldPref = $ErrorActionPreference
    # The CLI writes progress to stderr, and PowerShell must not stop on it.
    $ErrorActionPreference = 'Continue'
    try {
        & $Cli @cliArgs 2>&1 | ForEach-Object { "$_" } | Out-File -LiteralPath $log -Encoding UTF8
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $oldPref
        Pop-Location
    }
    $row.Minutes = [math]::Round(((Get-Date) - $t0).TotalMinutes, 1)

    if ($code -ne 0 -or -not (Test-Path -LiteralPath $proj)) {
        $row.Status = 'failed'
        $row.Note = "exit code $code; see the log"
        Write-Log ("{0}/{1} FAILED (exit code {2}): {3}" -f $n, $books.Count, $code, $dir)
    } else {
        [xml]$x = Get-Content -LiteralPath $proj -Encoding UTF8 -Raw
        $row.Pages = @($x.project.pages.page).Count
        $row.Status = 'done'
        if ($OutputOnly) {
            $all = @(Get-ChildItem -LiteralPath $projOut -Filter *.tif -File -ErrorAction SilentlyContinue)
            $new = @($all | Where-Object { $_.LastWriteTime -ge $t0 }).Count
            $row.Note = (@($row.Note, ("written again: {0} of {1}" -f $new, $row.Pages)) | Where-Object { $_ }) -join '; '
            if ($all.Count -lt $row.Pages) { $row.Status = 'failed'; $row.Note = "only $($all.Count) output files for $($row.Pages) pages" }
        }
        Write-Log ("{0}/{1} {2} in {3} min: {4} pages {5}" -f $n, $books.Count, $row.Status.ToUpper(), $row.Minutes, $row.Pages, $row.Note)
    }
    $summary += [pscustomobject]$row
}

if (-not $DryRun) {
    $summary | Export-Csv -LiteralPath $summaryCsv -NoTypeInformation -Encoding UTF8
    Write-Log "summary: $summaryCsv"
}
$summary | Format-Table Status, Images, Pages, Minutes, Book -AutoSize | Out-String -Width 250 | Write-Host
if (@($summary | Where-Object { $_.Status -eq 'failed' }).Count -gt 0) { exit 1 }
exit 0
