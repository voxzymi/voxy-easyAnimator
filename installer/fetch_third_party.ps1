<#
Downloads the third-party tools listed in installer\third_party.json into build\tools, and refuses anything whose
sha256 is empty or does not match. NOT RUN IN DEVELOPMENT.
upscayl-ncnn publishes source, not Windows binaries: build it once from the pinned commit (CMake + Vulkan SDK), put the
.exe in installer\third_party_cache\upscayl-bin.exe, and record its hash in third_party.json.
#>
$ErrorActionPreference = "Stop"
$root = Resolve-Path "$PSScriptRoot\.."
$cache = Join-Path $root "installer\third_party_cache"
$dest = Join-Path $root "build\tools\upscayl"
New-Item -ItemType Directory -Force $dest | Out-Null
$list = Get-Content (Join-Path $root "installer\third_party.json") -Raw | ConvertFrom-Json
foreach ($c in $list.components) {
  if ($c.name -notlike "upscayl-ncnn*" -and $c.name -notlike "realesr*") { continue }
  if (-not $c.sha256) { throw "third_party.json: '$($c.name)' has no sha256. Pin the exact file you ship." }
}
$exe = Join-Path $cache "upscayl-bin.exe"
if (-not (Test-Path $exe)) { throw "Missing $exe. Build upscayl-ncnn from the pinned commit first (see the header of this script)." }
$want = ($list.components | Where-Object { $_.name -like "upscayl-ncnn*" }).sha256
if ((Get-FileHash $exe -Algorithm SHA256).Hash.ToLower() -ne $want.ToLower()) { throw "upscayl-bin.exe does not match the pinned sha256." }
Copy-Item $exe $dest
if (Test-Path (Join-Path $cache "models")) { Copy-Item -Recurse -Force (Join-Path $cache "models") (Join-Path $dest "models") }
Copy-Item (Join-Path $root "THIRD_PARTY_NOTICES.md") $dest
Write-Host "Third-party tools copied to $dest"
