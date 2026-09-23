#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${BREV_API_KEY:-}" ]] && command -v security >/dev/null; then
  BREV_API_KEY="$(security find-generic-password \
    -a "${USER:-$(id -un)}" \
    -s "hack-e3bf6094-jigas-brev-api-key" \
    -w 2>/dev/null || true)"
  export BREV_API_KEY
fi
if [[ -z "${BREV_API_KEY:-}" ]]; then
  echo "Set BREV_API_KEY before deploying." >&2
  exit 1
fi
for command in docker brev ssh tar id; do
  command -v "$command" >/dev/null || { echo "Missing required command: $command" >&2; exit 1; }
done

export HACKALEM_CPUS="${HACKALEM_CPUS:-2}"
uid="$(id -u)"
gid="$(id -g)"

docker compose build pipeline
mkdir -p data out
docker compose run --rm --user "$uid:$gid" prepare
docker compose run --rm --user "$uid:$gid" pipeline --data /app/data --out /app/out
docker compose run --rm --user "$uid:$gid" --entrypoint python pipeline test_contract.py --data /app/data --out /app/out

tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT
key="$tmp_dir/brev-deploy"
known_hosts="$tmp_dir/known_hosts"
brev mint-cert \
  --env sffvn5xhk \
  --port nport-3Jj2brKSYF0D2lwNGtkBglac4WC \
  --linux-user ubuntu \
  --out-key "$key"
printf '%s\n' '[global.prd.ga.run.brev.nvidia.com]:23803 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIHYK0bvgG1WrYnjSogvlfz33KFQ0+c6EgpJjDnHlfYLl' > "$known_hosts"

tar -czf - -C out . | ssh \
  -i "$key" \
  -p 23803 \
  -o "CertificateFile=$key-cert.pub" \
  -o IdentitiesOnly=yes \
  -o BatchMode=yes \
  -o StrictHostKeyChecking=yes \
  -o "UserKnownHostsFile=$known_hosts" \
  -o GlobalKnownHostsFile=/dev/null \
  ubuntu@global.prd.ga.run.brev.nvidia.com \
  'sudo mkdir -p /home/ubuntu/jigas-demo/out && sudo tar -xzf - -C /home/ubuntu/jigas-demo/out --no-same-owner && sudo chown -R ubuntu:ubuntu /home/ubuntu/jigas-demo/out'

echo "Deployed report: https://8080-sffvn5xhk.gobrev.dev/report.html"
