#!/usr/bin/env bash
set -euo pipefail

repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
work=$(mktemp -d /var/tmp/symphony-dockur.XXXXXX)
name="symphony-dockur-$$"
cleanup() {
  docker rm -f "$name" >/dev/null 2>&1 || true
  rm -rf -- "$work"
}
trap cleanup EXIT

available_kib=$(df -Pk "$work" | awk 'NR == 2 { print $4 }')
if (( available_kib < 32 * 1024 * 1024 )); then
  echo "Dockur needs at least 32 GiB free; runner has $((available_kib / 1024 / 1024)) GiB" >&2
  exit 1
fi

mkdir -p "$work/oem/plugins" "$work/shared" "$work/storage"
cp -a "$repo/plugins/symphony" "$work/oem/plugins/"
cp "$repo/.github/scripts/dockur_install.bat" "$work/oem/install.bat"
cp "$repo/.github/scripts/dockur_run.ps1" "$work/oem/"
curl -fL --retry 3 --output "$work/oem/python-3.12.10-amd64.exe" \
  https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe

kvm=(--env KVM=N)
if [[ -e /dev/kvm ]]; then
  kvm=(--device /dev/kvm)
fi
echo "Starting Dockur Windows 10 LTSC ($([[ -e /dev/kvm ]] && echo KVM || echo software-emulated))"
docker run -d --name "$name" --stop-timeout 120 \
  --env VERSION=10l --env DISK_SIZE=40G --env DISK_FMT=qcow2 \
  --env CPU_CORES=4 --env RAM_SIZE=6G \
  "${kvm[@]}" --device /dev/net/tun --cap-add NET_ADMIN \
  --volume "$work/storage:/storage" \
  --volume "$work/shared:/shared" \
  --volume "$work/oem:/oem:ro" docker.io/dockurr/windows:latest >/dev/null

deadline=$((SECONDS + 7200))
while (( SECONDS < deadline )); do
  if [[ -f "$work/shared/receipt.txt" ]]; then
    cat "$work/shared/guest.log"
    result=$(tr -d '\r\n' < "$work/shared/receipt.txt")
    [[ "$result" == PASS ]] || { echo "Windows guest reported $result" >&2; exit 1; }
    echo 'Windows guest hook test PASS'
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
