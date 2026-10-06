param(
    [Parameter(Mandatory = $true)][string]$Link,
    [Parameter(Mandatory = $true)][string]$Target
)

$ErrorActionPreference = 'Stop'
New-Item -ItemType Junction -Path $Link -Target $Target -ErrorAction Stop | Out-Null
$created = Get-Item -LiteralPath $Link -Force
if ($created.LinkType -ne 'Junction' -or
    -not [System.IO.Path]::GetFullPath($created.Target).Equals(
        [System.IO.Path]::GetFullPath($Target),
        [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'Created reparse point does not match the requested target'
}
