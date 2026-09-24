#!/usr/bin/env bash
set -euo pipefail
# Default: boot the digest-pinned private GHCR snapshot. DOCKUR_FRESH=1 rebuilds
# from Windows media; DOCKUR_SNAPSHOT_ARCHIVE uses a local saved disk instead.

repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
work=$(mktemp -d /var/tmp/symphony-dockur.XXXXXX)
name="symphony-dockur-$$"
snapshot_name="${name}-snapshot"
deadline=$((SECONDS + 7200))
bounded() {
  local remaining=$((deadline - SECONDS))
  if (( remaining < 1 )); then echo 'Dockur test exceeded two hours' >&2; return 124; fi
  timeout --signal=TERM --kill-after=30 "${remaining}s" "$@"
}
cleanup() {
  docker rm -f "$name" >/dev/null 2>&1 || true
  docker rm -f "$snapshot_name" >/dev/null 2>&1 || true
  rm -rf -- "$work"
}
trap cleanup EXIT

available_kib=$(df -Pk "$work" | awk 'NR == 2 { print $4 }')
if (( available_kib < 32 * 1024 * 1024 )); then
  echo "Dockur needs at least 32 GiB free; runner has $((available_kib / 1024 / 1024)) GiB" >&2
  exit 1
fi

mkdir -p "$work/oem" "$work/shared/plugins" "$work/storage"
cp -a "$repo/plugins/symphony" "$work/shared/plugins/"
cp "$repo/.github/scripts/dockur_install.bat" "$work/oem/install.bat"
cp "$repo/.github/scripts/dockur_run.ps1" "$work/oem/"
snapshot_archive=${DOCKUR_SNAPSHOT_ARCHIVE:-}
if [[ -z "$snapshot_archive" && ${DOCKUR_FRESH:-0} != 1 ]]; then
  # Startup uses these OEM files baked into the image, not the new /oem mount.
  # A changed guest runner requires a new snapshot instead of a stale PASS.
  if ! (cd "$repo" && printf '%s\n' \
    '60f8f0040f6667c82f8905e8ff1e825cd38ea9a93f94ce05414c944141e0f329  .github/scripts/dockur_run.ps1' \
    'bbc468ae81742c9e609700febd8cb58873d80a251de842d9e04f48fa411f732b  .github/scripts/dockur_install.bat' \
    | sha256sum --check --status); then
    echo 'Guest runner changed; rebuild and repin the Windows snapshot' >&2
    exit 1
  fi
  snapshot_image=ghcr.io/opennoor/symphony-windows-base@sha256:22345e50e345b8bb5b2813993cae1ec0bade2c3b32c325744084f2cc95cda065
  bounded docker pull "$snapshot_image"
  bounded docker create --name "$snapshot_name" "$snapshot_image" /not-executed >/dev/null
  snapshot_archive="$work/windows-storage.tar.zst"
  bounded docker cp "$snapshot_name:/windows-storage.tar.zst" "$snapshot_archive"
  docker rm "$snapshot_name" >/dev/null
fi
if [[ -n "$snapshot_archive" ]]; then
  bounded tar -I zstd -xf "$snapshot_archive" -C "$work/storage"
  [[ -f "$work/storage/windows.boot" ]] || { echo 'Snapshot lacks Dockur boot marker' >&2; exit 1; }
else
  bounded curl -fL --retry 3 --output "$work/oem/python-3.12.10-amd64.exe" \
    https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe
fi

kvm=(--env KVM=N)
if [[ -e /dev/kvm ]]; then
  kvm=(--device /dev/kvm)
fi
echo "Starting Dockur Windows 10 LTSC ($([[ -e /dev/kvm ]] && echo KVM || echo software-emulated))"
bounded docker run -d --name "$name" --stop-timeout 120 \
  --env VERSION=10l --env DISK_SIZE=40G --env DISK_FMT=qcow2 \
  --env CPU_CORES=4 --env RAM_SIZE=6G \
  "${kvm[@]}" --device /dev/net/tun --cap-add NET_ADMIN \
  --volume "$work/storage:/storage" \
  --volume "$work/shared:/shared" \
  --volume "$work/oem:/oem:ro" \
  docker.io/dockurr/windows@sha256:0cff9eb0e7aee9953e55bc682852ca4fdca233145a58ae1ec94f0b0c01a2ed30 >/dev/null

while (( SECONDS < deadline )); do
  if [[ -f "$work/shared/receipt.txt" ]]; then
    if [[ -f "$work/shared/guest.stdout.log" ]]; then cat "$work/shared/guest.stdout.log"; fi
    if [[ -f "$work/shared/guest.log" ]]; then cat "$work/shared/guest.log"; fi
    result=$(tr -d '\r\n' < "$work/shared/receipt.txt")
    [[ "$result" == PASS ]] || { echo "Windows guest reported $result" >&2; exit 1; }
    echo 'Windows guest hook test PASS'
    if [[ -n ${DOCKUR_SNAPSHOT_OUT:-} ]]; then
      # Dockur records a completed disk boot during its graceful QEMU shutdown.
      docker stop --timeout 120 "$name" >/dev/null
      [[ -f "$work/storage/windows.boot" ]] || { echo 'Windows install marker missing after shutdown' >&2; exit 1; }
      tar -C "$work/storage" --exclude='*.iso' --exclude='setup.img' \
        -I 'zstd -T0 -3' -cf "$DOCKUR_SNAPSHOT_OUT" .
      sha256sum "$DOCKUR_SNAPSHOT_OUT"
    fi
    exit 0
  fi
  if [[ $(docker inspect -f '{{.State.Running}}' "$name") != true ]]; then
    docker logs "$name" >&2
    echo 'Dockur exited before Windows wrote a test receipt' >&2
    exit 1
  fi
  sleep 15
done
docker logs --tail 100 "$name" >&2
echo 'Timed out waiting for Windows guest test receipt' >&2
exit 1
