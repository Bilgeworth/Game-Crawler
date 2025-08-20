param(
  # Legacy mode: optional now (kept for backward-compat)
  [string]$GamePath
)

$ErrorActionPreference = 'Stop'

function Test-Admin {
  try {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p  = New-Object Security.Principal.WindowsPrincipal($id)
    return $p.IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)
  } catch { return $false }
}

function Test-WSLDistro([string]$Name) {
  return (wsl -l -q) -contains $Name
}

function To-WSLPath([string]$WinPath) {
  $p = (Resolve-Path $WinPath).Path
  $drive = $p.Substring(0,1).ToLower()
  $rest  = $p.Substring(2).Replace('\','/')
  return "/mnt/$drive/$rest"
}

# ─────────────────────────────────────────────────────────────────────────────
# Input selection:
#   Env mode (preferred): SANDBOXED_WSL_CWD + SANDBOXED_WSL_CMD
#   Legacy mode:          -GamePath
# ─────────────────────────────────────────────────────────────────────────────
$EnvCwd = $env:SANDBOXED_WSL_CWD
$EnvCmd = $env:SANDBOXED_WSL_CMD
$UsingEnv = -not [string]::IsNullOrWhiteSpace($EnvCwd) -and -not [string]::IsNullOrWhiteSpace($EnvCmd)

if ($UsingEnv) {
  $GameDir = (Resolve-Path $EnvCwd).Path
  $CmdLine = $EnvCmd
} elseif (-not [string]::IsNullOrWhiteSpace($GamePath)) {
  $GamePath = (Resolve-Path $GamePath).Path
  $GameDir  = (Split-Path $GamePath -Parent).TrimEnd('\')
  # Legacy: synthesize a simple command from the given file (no extra args)
  $RelFromGameDir = ($GamePath.Substring($GameDir.Length + 1)).TrimStart('\')
  $CmdLine = ($RelFromGameDir -replace '\\','/')
} else {
  Write-Error "No input provided. Expected env SANDBOXED_WSL_CWD and SANDBOXED_WSL_CMD, or -GamePath."
  exit 64
}

# ─────────────────────────────────────────────────────────────────────────────
# Preflight
# ─────────────────────────────────────────────────────────────────────────────
if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
  Write-Error "WSL not installed. In an elevated PowerShell:  wsl --install  (then reboot)"
  exit 1
}
if (-not (Test-Admin)) {
  Write-Host "[!] Tip: If this is first-time WSL install, use an elevated PowerShell for: wsl --install -d Ubuntu"
}

$Distro      = "games"
$InstallDir  = Join-Path $env:USERPROFILE "wsl\$Distro"
$TempTar     = Join-Path $env:TEMP "ubuntu-export-$Distro.tar"

# Ensure base Ubuntu and clone/import 'games' distro if missing
if (-not (Test-WSLDistro $Distro)) {
  $hasUbuntu = ((wsl -l -q) | Where-Object { $_ -match '^Ubuntu' }).Count -gt 0
  if (-not $hasUbuntu) {
    Write-Host "[*] Installing 'Ubuntu' base (may enable features; reboot afterwards)..."
    wsl --install -d Ubuntu
    Write-Host "[*] Reboot if prompted, then re-run this script."
    exit 0
  }

  New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
  Write-Host "[*] Exporting 'Ubuntu' base..."
  wsl --export Ubuntu "$TempTar"
  Write-Host "[*] Importing '$Distro'..."
  wsl --import $Distro "$InstallDir" "$TempTar" --version 2
  try { Remove-Item -Force "$TempTar" } catch {}
}

# Configure 'games' distro: enable systemd
$wslConfCmd = "printf '[boot]\nsystemd=true\n' > /etc/wsl.conf"
wsl -d $Distro --cd / -u root -- bash -lc "$wslConfCmd"

# Net lock/unlock helpers (CRLF-safe)
$netlock = @'
#!/bin/sh
set -eu
# iptables IPv4 + IPv6
if command -v iptables >/dev/null 2>&1; then
  /usr/sbin/iptables -F OUTPUT || true
  /usr/sbin/iptables -P OUTPUT DROP || true
  /usr/sbin/iptables -A OUTPUT -o lo -j ACCEPT || true
  /usr/sbin/iptables -A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT || true
fi
if command -v ip6tables >/dev/null 2>&1; then
  /usr/sbin/ip6tables -F OUTPUT || true
  /usr/sbin/ip6tables -P OUTPUT DROP || true
  /usr/sbin/ip6tables -A OUTPUT -o lo -j ACCEPT || true
  /usr/sbin/ip6tables -A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT || true
fi
# nft fallback (own table only)
if command -v nft >/dev/null 2>&1; then
  nft -f - <<'EOF'
table inet games_netlock {
  chain output {
    type filter hook output priority 0;
    policy drop;
    ct state established,related accept
    oifname "lo" accept
  }
}
EOF
fi
'@
$netunlock = @'
#!/bin/sh
set -eu
if command -v iptables >/dev/null 2>&1; then
  /usr/sbin/iptables -P OUTPUT ACCEPT || true
  /usr/sbin/iptables -F OUTPUT || true
fi
if command -v ip6tables >/dev/null 2>&1; then
  /usr/sbin/ip6tables -P OUTPUT ACCEPT || true
  /usr/sbin/ip6tables -F OUTPUT || true
fi
if command -v nft >/dev/null 2>&1; then
  nft delete table inet games_netlock 2>/dev/null || true
fi
'@

foreach($pair in @(@('netlock',$netlock),@('netunlock',$netunlock))){
  $name = $pair[0]; $content = $pair[1]
  $b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($content))
  wsl -d $Distro --cd / -u root -- bash -lc "echo '$b64' | base64 -d | tr -d '\r' > /usr/local/sbin/$name && chmod +x /usr/local/sbin/$name"
}

# systemd unit to apply lock after network is up
$svcContent = @'
[Unit]
Description=Apply egress block (no-network) for games distro
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/netlock
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
'@
$svcB64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($svcContent))
$svcCmd = "mkdir -p /etc/systemd/system && echo '$svcB64' | base64 -d > /etc/systemd/system/games-nonet.service"
wsl -d $Distro --cd / -u root -- bash -lc "$svcCmd"

# Restart, enable+start service
wsl --terminate $Distro | Out-Null
wsl -d $Distro --cd / -- true | Out-Null
wsl -d $Distro --cd / -u root -- systemctl enable games-nonet.service
wsl -d $Distro --cd / -u root -- systemctl start games-nonet.service

# Helper scripts: sync + runner + ensure-deps (CRLF-safe)
$syncMirror = @'
#!/bin/bash
set -euo pipefail
src="$1"; dst="$2"
if [ -z "${src:-}" ] || [ -z "${dst:-}" ]; then echo "usage: sync-mirror SRC DST" >&2; exit 64; fi
mkdir -p "$dst"
rsync -a --delete -- "$src"/ "$dst"/
'@
$syncUpdate = @'
#!/bin/bash
set -euo pipefail
src="$1"; dst="$2"
if [ -z "${src:-}" ] || [ -z "${dst:-}" ]; then echo "usage: sync-update SRC DST" >&2; exit 64; fi
mkdir -p "$dst"
rsync -a -- "$src"/ "$dst"/
'@

# UPDATED: runner supports env SANDBOXED_WSL_CMD_B64 OR exe + args
$runner = @'
#!/bin/bash
set -euo pipefail

# WSLg audio/display
[ -z "${PULSE_SERVER:-}" ] && [ -S /mnt/wslg/PulseServer ] && export PULSE_SERVER="unix:/mnt/wslg/PulseServer"
[ -z "${WAYLAND_DISPLAY:-}" ] && [ -S /mnt/wslg/wayland-0 ] && export WAYLAND_DISPLAY="wayland-0"
[ -z "${DISPLAY:-}" ] && [ -S /tmp/.X11-unix/X0 ] && export DISPLAY=":0"
export SDL_AUDIODRIVER=${SDL_AUDIODRIVER:-pulseaudio}
export PULSE_LATENCY_MSEC=${PULSE_LATENCY_MSEC:-60}

# Prefer D3D12 GPU paths
export LD_LIBRARY_PATH="/usr/lib/wsl/lib:${LD_LIBRARY_PATH:-}"
export MESA_LOADER_DRIVER_OVERRIDE=${MESA_LOADER_DRIVER_OVERRIDE:-d3d12}
export GALLIUM_DRIVER=${GALLIUM_DRIVER:-d3d12}
# Don’t force software
unset LIBGL_ALWAYS_SOFTWARE || true

# Vulkan ICD (try dzn then d3d12 json)
if [ -z "${VK_ICD_FILENAMES:-}" ]; then
  for f in /usr/share/vulkan/icd.d/dzn_icd.*.json /usr/share/vulkan/icd.d/d3d12_icd.*.json ; do
    [ -f "$f" ] && export VK_ICD_FILENAMES="$f" && break
  done
fi

# One-time GPU diag (helps confirm we’re not on llvmpipe)
GPU_LOG=/var/tmp/gamesandbox/_gpu_info.txt
if [ ! -s "$GPU_LOG" ]; then
  {
    echo "=== glxinfo -B ==="
    command -v glxinfo >/dev/null && glxinfo -B || echo "(glxinfo not present)"
    echo
    echo "=== vulkaninfo --summary ==="
    command -v vulkaninfo >/dev/null && vulkaninfo --summary || echo "(vulkaninfo not present)"
  } >"$GPU_LOG" 2>&1 || true
fi

# Prefer command from env (base64) to preserve quoting
if [ -n "${SANDBOXED_WSL_CMD_B64:-}" ]; then
  CMD="$(printf '%s' "$SANDBOXED_WSL_CMD_B64" | base64 -d)"
  exec bash -lc "$CMD"
fi

# Fallback: exe + args
exe="${1:-}"
[ -z "$exe" ] && { echo "usage: run-game EXE [ARGS...]" >&2; exit 64; }
shift || true
case "$exe" in /*) ;; *) exe="./$exe";; esac
[ ! -f "$exe" ] && { echo "ERROR: not found: $exe"; ls -la; exit 127; }
chmod +x "$exe" 2>/dev/null || true
exec "$exe" "$@"
'@

$ensureDeps = @'
#!/bin/bash
set -euo pipefail
need=(rsync libasound2t64 libpulse0 libsdl2-2.0-0 libgl1 libgl1-mesa-dri mesa-vulkan-drivers libvulkan1 mesa-utils vulkan-tools pulseaudio-utils alsa-utils libx11-6 libxrandr2 libxinerama1 libxcursor1 libxi6 libnss3 ca-certificates coreutils)
missing=()
for p in "${need[@]}"; do
  dpkg -s "$p" >/dev/null 2>&1 || missing+=("$p")
done
if [ "${#missing[@]}" -eq 0 ]; then
  exit 0
fi
# Temporarily allow egress
if command -v /usr/local/sbin/netunlock >/dev/null 2>&1; then /usr/local/sbin/netunlock || true; fi
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends "${missing[@]}"
# Re-apply lock
if command -v /usr/local/sbin/netlock >/dev/null 2>&1; then /usr/local/sbin/netlock || true; fi
'@

$ensureGpu = @'
#!/bin/bash
set -euo pipefail
changed=0
# Make /usr/lib/wsl/lib permanent for the dynamic linker
if ! grep -qs "^/usr/lib/wsl/lib$" /etc/ld.so.conf.d/99-wslg.conf 2>/dev/null; then
  echo "/usr/lib/wsl/lib" > /etc/ld.so.conf.d/99-wslg.conf
  changed=1
fi
# Also export at login for any shells
if [ ! -f /etc/profile.d/99-wslg.sh ] || ! grep -qs "LD_LIBRARY_PATH=.*\/usr\/lib\/wsl\/lib" /etc/profile.d/99-wslg.sh; then
  cat >/etc/profile.d/99-wslg.sh <<'EOF'
# WSLg GPU libs
export LD_LIBRARY_PATH="/usr/lib/wsl/lib:${LD_LIBRARY_PATH:-}"
EOF
  changed=1
fi
[ $changed -eq 1 ] && ldconfig || true

# Ensure Mesa/Vulkan bits exist
if [ ! -e /usr/lib/x86_64-linux-gnu/dri/d3d12_dri.so ]; then
  echo "WARNING: d3d12_dri.so missing; (re)installing libgl1-mesa-dri…" >&2
  DEBIAN_FRONTEND=noninteractive apt-get update && apt-get install -y --no-install-recommends libgl1-mesa-dri
fi
if ! ls /usr/share/vulkan/icd.d/dzn_icd.*.json /usr/share/vulkan/icd.d/d3d12_icd.*.json >/dev/null 2>&1; then
  echo "Ensuring mesa-vulkan-drivers is installed…" >&2
  DEBIAN_FRONTEND=noninteractive apt-get update && apt-get install -y --no-install-recommends mesa-vulkan-drivers
fi
'@

# Install helpers inside the distro
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($ensureGpu))
wsl -d $Distro --cd / -u root -- bash -lc "echo '$b64' | base64 -d | tr -d '\r' > /usr/local/bin/ensure-gpu && chmod +x /usr/local/bin/ensure-gpu"

foreach($name in @('sync-mirror','sync-update','run-game','ensure-deps')){
  $content = switch($name){ 'sync-mirror'{$syncMirror} 'sync-update'{$syncUpdate} 'run-game'{$runner} default{$ensureDeps} }
  $b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($content))
  wsl -d $Distro --cd / -u root -- bash -lc "echo '$b64' | base64 -d | tr -d '\r' > /usr/local/bin/$name && chmod +x /usr/local/bin/$name"
}

# ─────────────────────────────────────────────────────────────────────────────
# Stage to C: if needed
# ─────────────────────────────────────────────────────────────────────────────
$srcDrive = $GameDir.Substring(0,1).ToUpper()
$UseStaging = ($srcDrive -ne 'C')

if ($UseStaging) {
  $hash = [BitConverter]::ToString([Security.Cryptography.SHA1]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes($GameDir))).Replace("-", "").Substring(0,12)
  $StageDir = Join-Path $env:LOCALAPPDATA "GameSandbox\$hash"
  New-Item -ItemType Directory -Force -Path $StageDir | Out-Null

  Write-Host "[*] Staging to $StageDir"
  & robocopy "$GameDir" "$StageDir" /MIR /COPY:DAT /DCOPY:DAT /R:2 /W:1 /NFL /NDL | Out-Null
  if ($LASTEXITCODE -gt 7) { Write-Error "Staging copy failed (robocopy exit $LASTEXITCODE)"; exit $LASTEXITCODE }
} else {
  $StageDir = $GameDir
}

# Ensure deps (may briefly unlock → apt → relock), then prepare GPU config
wsl -d $Distro --cd / -- /usr/local/bin/ensure-deps
wsl -d $Distro --cd / -u root -- /usr/local/bin/ensure-gpu

# Build paths
$WSLDir      = To-WSLPath $StageDir
$Hash        = [BitConverter]::ToString([Security.Cryptography.SHA1]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes($GameDir))).Replace("-", "").Substring(0,12)
$LinuxWork   = "/var/tmp/gamesandbox/$Hash"

# Mirror Windows-stage -> fast ext4 work dir
wsl -d $Distro --cd / -- /usr/local/bin/sync-mirror "$WSLDir" "$LinuxWork"

# ─────────────────────────────────────────────────────────────────────────────
# Launch (egress locked)
# In env mode: pass command via base64 env var to avoid quoting hell
# In legacy mode: pass relative exe path (no args)
# ─────────────────────────────────────────────────────────────────────────────
if ($UsingEnv) {
  $CmdB64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($CmdLine))
  Write-Host "[*] Launching command in distro '$Distro' (network disabled)"
  wsl -d $Distro --cd "$LinuxWork" -- env "SANDBOXED_WSL_CMD_B64=$CmdB64" /usr/local/bin/run-game
} else {
  $RelFromGameDir = ($CmdLine) # already relative, from legacy branch above
  $RelPathWSL  = ($RelFromGameDir -replace '\\','/')
  Write-Host "[*] Launching '$RelPathWSL' in distro '$Distro' (network disabled)"
  wsl -d $Distro --cd "$LinuxWork" -- /usr/local/bin/run-game "$RelPathWSL"
}
$GameExit = $LASTEXITCODE

# Update Windows-stage from the fast work dir
wsl -d $Distro --cd / -- /usr/local/bin/sync-update "$LinuxWork" "$WSLDir"

# Sync changes back to original if we staged
if ($UseStaging) {
  Write-Host "[*] Syncing changes back to $GameDir"
  & robocopy "$StageDir" "$GameDir" /E /COPY:DAT /DCOPY:DAT /XO /XN /XC /FFT /R:2 /W:1 | Out-Null
  if ($LASTEXITCODE -gt 7) { Write-Warning "Post-run sync reported robocopy exit $LASTEXITCODE" }
}

exit $GameExit
