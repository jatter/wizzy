#!/bin/bash
# Run on 175.24.197.8: bash enable-tencent-https.sh /absolute/path/to/conf.d/wizzy.conf admin@example.com
set -euo pipefail
CONFIG=${1:?Provide the absolute path to the gateway wizzy.conf}
EMAIL=${2:?Provide an email address for certificate expiry notices}
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
[[ $EUID == 0 ]] || { echo 'Run as root on the Tencent gateway.' >&2; exit 1; }
[[ $CONFIG == /* && -f $CONFIG && $(basename "$CONFIG") == wizzy.conf ]] || { echo 'Expected the existing absolute wizzy.conf path.' >&2; exit 1; }
for executable in nginx python3 certbot curl; do
    command -v "$executable" >/dev/null || { echo "Install $executable before running this script." >&2; exit 1; }
done
nginx -t
BACKUP="${CONFIG}.before-https-$(date +%Y%m%d%H%M%S)"
cp -p "$CONFIG" "$BACKUP"
restore_on_failure() {
    local status=$?
    if [[ $status != 0 ]]; then
        cp -p "$BACKUP" "$CONFIG"
        nginx -t && nginx -s reload
        echo "HTTPS setup failed; restored $BACKUP" >&2
    fi
}
trap restore_on_failure EXIT
mkdir -p /var/www/wizzy-acme/.well-known/acme-challenge
python3 - "$CONFIG" <<'PY'
import re
import sys
from pathlib import Path
path = Path(sys.argv[1])
source = path.read_text()
if 'server_name  wizzy.refineview.com' not in source and not re.search(r'server_name\s+wizzy\.refineview\.com\s*;', source):
    raise SystemExit('This config does not contain the expected Wizzy domain')
if '/var/www/wizzy-acme' not in source:
    source, count = re.subn(r'location\s+/\s*\{', '''location ^~ /.well-known/acme-challenge/ {
        root /var/www/wizzy-acme;
    }
    location / {''', source, count=1)
    if count != 1:
        raise SystemExit('Cannot locate the existing proxy location')
    path.write_text(source)
PY
nginx -t
nginx -s reload
certbot certonly --webroot --webroot-path /var/www/wizzy-acme \
    --cert-name wizzy.refineview.com --domain wizzy.refineview.com \
    --email "$EMAIL" --agree-tos --non-interactive --keep-until-expiring
cp "$SCRIPT_DIR/tencent-wizzy-https.conf" "$CONFIG"
nginx -t
nginx -s reload
HEALTH_FILE=$(mktemp)
curl --fail --silent --show-error --noproxy '*' --resolve wizzy.refineview.com:443:127.0.0.1 \
    https://wizzy.refineview.com/health/ready > "$HEALTH_FILE"
python3 - "$HEALTH_FILE" <<'PY'
import json
import sys
from pathlib import Path
path = Path(sys.argv[1])
try:
    if json.loads(path.read_text()).get('status') != 'ok':
        raise SystemExit('The public HTTPS API is not ready')
finally:
    path.unlink(missing_ok=True)
PY
mkdir -p /etc/letsencrypt/renewal-hooks/deploy
cat > /etc/letsencrypt/renewal-hooks/deploy/wizzy-nginx <<'HOOK'
#!/bin/sh
nginx -t && nginx -s reload
HOOK
chmod 755 /etc/letsencrypt/renewal-hooks/deploy/wizzy-nginx
if ! systemctl list-timers --all --no-pager 2>/dev/null | grep -Eq 'certbot|letsencrypt'; then
    printf '15 3,15 * * * root %s renew --cert-name wizzy.refineview.com --quiet\n' "$(command -v certbot)" > /etc/cron.d/wizzy-certbot
    chmod 644 /etc/cron.d/wizzy-certbot
fi
trap - EXIT
echo "HTTPS enabled. Original configuration: $BACKUP"
echo 'Allow TCP 443 in the Tencent security group/firewall; keep TCP 80 for renewal.'
