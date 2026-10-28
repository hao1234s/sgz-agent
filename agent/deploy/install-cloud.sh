#!/usr/bin/env bash
# Installs only this application's files. Run as root via deploy.py --apply.
set -Eeuo pipefail
domain=${1:?domain required}
release_id=${2:?release id required}
[[ $domain =~ ^[A-Za-z0-9][A-Za-z0-9.-]*$ && $domain != *..* ]] || exit 2
[[ $release_id =~ ^[0-9]{8}T[0-9]{6}Z-[a-f0-9]{8}$ ]] || exit 2
[[ $EUID -eq 0 ]] || { echo 'Root or passwordless sudo is required.' >&2; exit 1; }
stage=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
app=/var/www/sgz-agent
release="$app/releases/$release_id"
nginx_config=/etc/nginx/conf.d/sgz-agent.conf
unit=/etc/systemd/system/sgz-agent.service
environment=/etc/sgz-agent/dsh.env
backup="/var/backups/sgz-agent/$release_id"
runtime=/opt/sgz-agent/dsh
dsh_version=0.2.1-rc.3

for command in npm nginx systemctl curl cmp flock useradd install; do
    command -v "$command" >/dev/null || { echo "Missing server prerequisite: $command" >&2; exit 1; }
done
[[ -x /usr/bin/node ]] || { echo 'Install Node.js 24+ at /usr/bin/node.' >&2; exit 1; }
/usr/bin/node -e 'if (+process.versions.node.split(".")[0] < 24) process.exit(1)' || {
    echo 'Node.js 24+ is required by this deployment.' >&2; exit 1;
}
[[ -f "/etc/letsencrypt/live/$domain/fullchain.pem" && -f "/etc/letsencrypt/live/$domain/privkey.pem" ]] || {
    echo 'Provision the domain TLS certificate before deployment; see README.' >&2; exit 1;
}
[[ -s "$stage/dsh.env" || -s "$environment" ]] || {
    echo 'Provide DEEPSEEK_API_KEY or an existing /etc/sgz-agent/dsh.env.' >&2; exit 1;
}
[[ -f "$stage/frontend/index.html" && -f "$stage/nginx.conf" && -f "$stage/sgz-agent.service" ]] || exit 1
if [[ ! -f /etc/sgz-agent/managed-by-deploy ]]; then
    for target in "$app" "$runtime" "$nginx_config" "$unit"; do
        [[ ! -e $target && ! -L $target ]] || { echo "Refusing to replace an unmanaged path: $target" >&2; exit 1; }
    done
fi
[[ ! -e "$app/current" || -L "$app/current" ]] || { echo 'current must be a symlink.' >&2; exit 1; }
nginx -t
exec 9>/var/lock/sgz-agent-deploy.lock
flock -n 9 || { echo 'Another deployment is in progress.' >&2; exit 1; }

previous_active=false
previous_enabled=false
systemctl is-active --quiet sgz-agent.service && previous_active=true
systemctl is-enabled --quiet sgz-agent.service && previous_enabled=true
if ! $previous_active && (echo >/dev/tcp/127.0.0.1/3080) 2>/dev/null; then
    echo 'Port 3080 is already occupied by another process; no service was changed.' >&2
    exit 1
fi
previous_release=$(readlink "$app/current" 2>/dev/null || true)
install -d -m 700 "$backup"
for pair in "nginx:$nginx_config" "service:$unit" "env:$environment"; do
    name=${pair%%:*}; path=${pair#*:}
    if [[ -f $path ]]; then cp -- "$path" "$backup/$name"; fi
done

rollback() {
    status=$?
    trap - ERR
    set +e
    echo 'Deployment failed; restoring previous application configuration.' >&2
    for pair in "nginx:$nginx_config" "service:$unit" "env:$environment"; do
        name=${pair%%:*}; path=${pair#*:}
        if [[ -f "$backup/$name" ]]; then cp -- "$backup/$name" "$path"; else rm -f -- "$path"; fi
    done
    if [[ -n $previous_release ]]; then
        ln -sfn -- "$previous_release" "$app/current.rollback"
        mv -Tf -- "$app/current.rollback" "$app/current"
    elif [[ -L "$app/current" ]]; then
        rm -f -- "$app/current"
    fi
    systemctl stop sgz-agent.service
    systemctl daemon-reload
    if $previous_enabled; then systemctl enable sgz-agent.service; else systemctl disable sgz-agent.service; fi
    if $previous_active; then systemctl start sgz-agent.service; fi
    if nginx -t; then systemctl reload nginx; fi
    echo "Backup retained at $backup; failed release retained at $release" >&2
    exit "$status"
}

if ! id sgz-agent >/dev/null 2>&1; then
    useradd --system --user-group --home-dir /var/lib/sgz-agent --shell /usr/sbin/nologin sgz-agent
fi
install -d -o sgz-agent -g sgz-agent -m 700 /var/lib/sgz-agent /var/lib/sgz-agent/workspace
install -d -m 755 "$runtime" "$release/frontend" /etc/sgz-agent
install -m 644 /dev/null /etc/sgz-agent/managed-by-deploy
if ! /usr/bin/node -e 'const p=require(process.argv[1]); if(p.version!==process.argv[2]) process.exit(1)' \
    "$runtime/node_modules/@deepseek-ai/dsh/package.json" "$dsh_version" 2>/dev/null; then
    npm install --prefix "$runtime" --omit=dev --no-audit --no-fund --save-exact "@deepseek-ai/dsh@$dsh_version"
fi
cp -a -- "$stage/frontend/." "$release/frontend/"
find "$release" -type d -exec chmod 755 {} +
find "$release" -type f -exec chmod 644 {} +

# From here on any failure restores the previous service, API key and site config.
trap rollback ERR
if [[ -f "$stage/dsh.env" ]]; then install -m 600 "$stage/dsh.env" "$environment"; fi
install -m 644 "$stage/sgz-agent.service" "$unit"
install -m 644 "$stage/nginx.conf" "$nginx_config"
ln -sfn -- "$release" "$app/current.next"
mv -Tf -- "$app/current.next" "$app/current"
nginx -t
systemctl daemon-reload
systemctl enable sgz-agent.service
systemctl restart sgz-agent.service
ready=false
for attempt in {1..30}; do
    if systemctl is-active --quiet sgz-agent.service; then
        code=$(curl --max-time 2 -s -o /dev/null -w '%{http_code}' http://127.0.0.1:3080/ || true)
        if [[ $code == 200 || $code == 302 || $code == 401 ]]; then ready=true; break; fi
    fi
    sleep 1
done
$ready || { echo 'DSH did not become ready; inspect its private systemd journal.' >&2; false; }
systemctl reload nginx
curl --fail --silent --show-error --max-time 10 --resolve "$domain:443:127.0.0.1" "https://$domain/healthz"
curl --fail --silent --show-error --max-time 10 --resolve "$domain:443:127.0.0.1" "https://$domain/" | cmp - "$release/frontend/index.html"
trap - ERR
echo "Installed release $release_id. Previous release and backups retained."
echo 'Finish DSH browser authentication and model verification using the deployment README.'
