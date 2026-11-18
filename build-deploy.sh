#!/bin/bash
# 小知 Wizzy 本地启动 / 打包发布到云服务器
#
# 用法：
#   ./build-deploy.sh              # 交互选菜单
#
# 【本地开发】
#   ./build-deploy.sh 11           # 本地启动后端  (http://127.0.0.1:8000)
#   ./build-deploy.sh 15           # 本地启动 dispatcher
#   ./build-deploy.sh 12           # 本地启动平台 Web  (http://127.0.0.1:4175)
#   ./build-deploy.sh 16           # 本地启动家庭端 H5  (http://127.0.0.1:4176)
#
# 【打包构建】
#   ./build-deploy.sh 3            # 后端打包（源码 zip）
#   ./build-deploy.sh 4            # 平台 Web 打包（npm run build + zip）
#   ./build-deploy.sh 7            # 家庭端 H5 打包（npm run build:h5 + zip）
#
# 【服务管理】
#   ./build-deploy.sh 13           # 查看本机后端状态
#   ./build-deploy.sh 14           # 停止本机后端和 dispatcher
#
# 【部署发布 → 106.13.191.107】
#   ./build-deploy.sh 0            # 全部发布到 /www/wizzy
#   ./build-deploy.sh 1            # 仅发布后端  → /www/wizzy/api  (127.0.0.1:9062)
#   ./build-deploy.sh 2            # 仅发布平台 Web  → /www/wizzy/front  (http://106.13.191.107:9060/admin/)
#   ./build-deploy.sh 5            # 仅发布原型  → /www/wizzy/prototype  (http://106.13.191.107:9061)
#   ./build-deploy.sh 6            # 仅发布家庭端  → /www/wizzy/family  (http://106.13.191.107:9060/)
#
# SSH 密钥默认：/Users/Admin1/tools/ssh/server_107-k-IaKzr3Mx.txt
# 云服务器：root@106.13.191.107
#   目录：/www/wizzy
#     api/         FastAPI 源码、.venv、.env、私有文件
#     front/       平台 Web 构建结果
#     prototype/   静态原型
#     redisdata/   Redis AOF
#   平台后台：http://106.13.191.107:9060/admin/
#   静态原型：http://106.13.191.107:9061
#   家庭统一入口：http://106.13.191.107:9060/
#   旧 /family/ 地址保留跳转兼容
#   API 只监听 127.0.0.1:9062，由 nginx 反代 /api/ 与 /health/
#   应用 Postgres：175.24.197.8:9432 / wizzy（独立云库，不在本应用机起 Docker Postgres）
#   Redis 只监听 127.0.0.1:9064
#   发布只改服务器 /www/wizzy/api/.env 的数据库 URL；项目根目录 .env 始终指向本机 127.0.0.1:5432
#
# 9060–9064 在编写时未被服务器占用，避开了 9001–9004、9080–9095、9190、
# 9230、9300、9379、9672–9751、9882–9984 等已监听端口。
# 家庭端 H5 使用站点首页，平台后台挂在 /admin/。9063 已被其他服务占用，且云安全组未放行。
# 微信小程序不在本脚本内。
# 作业批改与清单识别 worker 随后端发布常驻，由 systemd 管理。dispatcher 与教材 worker 不随发布常驻：
# 后者必须另行配置 ClamAV。首位平台管理员用 api/platform-bootstrap.sh 交互初始化。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$SCRIPT_DIR"
BACKEND_DIR="$PROJECT_DIR/backend"
FRONTEND_DIR="$PROJECT_DIR/frontend/platform"
FAMILY_DIR="$PROJECT_DIR/frontend/family"
PROTOTYPE_DIR="$PROJECT_DIR/docs/prototype"
PROTOTYPE_UI_DIR="$PROTOTYPE_DIR/ui"
ARTIFACT_DIR="$PROJECT_DIR/.deploy"

REMOTE_HOST="106.13.191.107"
REMOTE_PORT="22"
REMOTE_USER="root"
REMOTE_ROOT="/www/wizzy"
REMOTE_API="${REMOTE_ROOT}/api"
REMOTE_FRONT="${REMOTE_ROOT}/front"
REMOTE_FAMILY="${REMOTE_ROOT}/family"
REMOTE_PROTOTYPE="${REMOTE_ROOT}/prototype"
NGINX_CONF="/etc/nginx/conf.d/wizzy.conf"
NGINX_PORT="9060"
PROTOTYPE_PORT="9061"
API_PORT="9062"
REDIS_PORT="9064"
LOCAL_API_PORT="8000"
LOCAL_WEB_PORT="4175"
LOCAL_FAMILY_PORT="4176"
SSH_KEY="${SSH_KEY:-/Users/Admin1/tools/ssh/server_107-k-IaKzr3Mx.txt}"
API_ZIP_NAME="wizzy-api.zip"
FRONT_ZIP_NAME="wizzy-front.zip"
FAMILY_ZIP_NAME="wizzy-family.zip"
PROTO_ZIP_NAME="wizzy-prototype.zip"
WHEEL_ZIP_NAME="wizzy-pydeps.zip"
WHEEL_DIR="$ARTIFACT_DIR/wheels"
REDIS_CONTAINER="wizzy-redis"
REDIS_IMAGE="redis:8.2.10-bookworm"
CLOUD_PG_HOST="175.24.197.8"
CLOUD_PG_PORT="9432"
CLOUD_PG_DATABASE="wizzy"
CLOUD_PG_USER="wizzy"

SSH_BASE_OPTS=(-o StrictHostKeyChecking=accept-new -o IdentitiesOnly=yes -o ConnectTimeout=15)

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info()  { echo -e "${BLUE}[INFO]${NC} $1"; }
log_ok()    { echo -e "${GREEN}[OK]${NC} $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# Copy local wizzy DB secrets into a temp file for the app host. Never write them into
# the project .env, and never print passwords.
write_cloud_db_env() {
  local dest="$1"
  python3 - "$PROJECT_DIR/.env" "$dest" "$CLOUD_PG_HOST" "$CLOUD_PG_PORT" "$CLOUD_PG_DATABASE" "$CLOUD_PG_USER" <<'PY'
import base64
import json
import sys
from pathlib import Path
from urllib.parse import quote
from uuid import UUID

src, dest, host, port, database, owner = sys.argv[1:]

def load(path):
    data = {}
    for raw in Path(path).read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        data[key] = value
    return data

def url(user, password):
    return (
        f"postgresql+psycopg://{quote(user, safe='')}:{quote(password, safe='')}"
        f"@{host}:{port}/{database}"
    )

local = load(src)
links = json.loads((Path(src).parent / '.local/android-release/.well-known/assetlinks.json').read_text())
fingerprint = links[0]['target']['sha256_cert_fingerprints'][0]
android_origin = 'android:apk-key-hash:' + base64.urlsafe_b64encode(bytes.fromhex(fingerprint.replace(':', ''))).decode().rstrip('=')
required = (
    "POSTGRES_PASSWORD",
    "WIZZY_APP_ROLE",
    "WIZZY_APP_PASSWORD",
    "WIZZY_DISPATCHER_ROLE",
    "WIZZY_DISPATCHER_PASSWORD",
    "WIZZY_WORKER_ROLE",
    "WIZZY_WORKER_PASSWORD",
)
missing = [key for key in required if not local.get(key)]
if missing:
    raise SystemExit("local .env missing: " + ", ".join(missing))
lines = [
    f"POSTGRES_PASSWORD={local['POSTGRES_PASSWORD']}",
    f"WIZZY_APP_ROLE={local['WIZZY_APP_ROLE']}",
    f"WIZZY_APP_PASSWORD={local['WIZZY_APP_PASSWORD']}",
    f"WIZZY_DISPATCHER_ROLE={local['WIZZY_DISPATCHER_ROLE']}",
    f"WIZZY_DISPATCHER_PASSWORD={local['WIZZY_DISPATCHER_PASSWORD']}",
    f"WIZZY_WORKER_ROLE={local['WIZZY_WORKER_ROLE']}",
    f"WIZZY_WORKER_PASSWORD={local['WIZZY_WORKER_PASSWORD']}",
    f"WIZZY_DATABASE_URL={url(local['WIZZY_APP_ROLE'], local['WIZZY_APP_PASSWORD'])}",
    f"WIZZY_DISPATCHER_DATABASE_URL={url(local['WIZZY_DISPATCHER_ROLE'], local['WIZZY_DISPATCHER_PASSWORD'])}",
    f"WIZZY_WORKER_DATABASE_URL={url(local['WIZZY_WORKER_ROLE'], local['WIZZY_WORKER_PASSWORD'])}",
    f"WIZZY_PASSKEY_ANDROID_ORIGIN={android_origin}",
    "",
]
if local.get('WIZZY_HOMEWORK_LIST_MODEL_ID'):
    try:
        model_id = str(UUID(local['WIZZY_HOMEWORK_LIST_MODEL_ID']))
    except ValueError:
        raise SystemExit('WIZZY_HOMEWORK_LIST_MODEL_ID must be a UUID') from None
    lines.insert(-1, f'WIZZY_HOMEWORK_LIST_MODEL_ID={model_id}')
out = Path(dest)
out.write_text("\n".join(lines))
out.chmod(0o600)
PY
}

check_command() {
  local cmd="$1"
  local hint="$2"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    log_error "未找到 $cmd，$hint"
    exit 1
  fi
}

ensure_ssh_key() {
  if [[ ! -f "$SSH_KEY" ]]; then
    log_error "未找到 SSH 密钥: $SSH_KEY"
    log_info "可通过 SSH_KEY=/path/to/key ./build-deploy.sh 覆盖"
    exit 1
  fi
  chmod 600 "$SSH_KEY" 2>/dev/null || true
}

ssh_ready() {
  ssh -i "$SSH_KEY" -o BatchMode=yes "${SSH_BASE_OPTS[@]}" \
    -p "$REMOTE_PORT" "${REMOTE_USER}@${REMOTE_HOST}" "true" >/dev/null 2>&1
}

ensure_ssh_login() {
  ensure_ssh_key
  if ssh_ready; then
    log_ok "SSH 已就绪: ${REMOTE_USER}@${REMOTE_HOST}"
    return 0
  fi
  log_error "无法通过密钥登录 ${REMOTE_USER}@${REMOTE_HOST}"
  log_info "请确认密钥可用: $SSH_KEY"
  exit 1
}

scp_cmd() {
  scp -i "$SSH_KEY" "${SSH_BASE_OPTS[@]}" -P "$REMOTE_PORT" "$@"
}

ssh_cmd() {
  ssh -i "$SSH_KEY" "${SSH_BASE_OPTS[@]}" -p "$REMOTE_PORT" "$@"
}

make_zip() {
  local archive="$1"
  shift
  if command -v zip >/dev/null 2>&1; then
    COPYFILE_DISABLE=1 zip -r -q "$archive" "$@"
  else
    log_error "未找到 zip，无法打包"
    exit 1
  fi
}

ensure_npm_deps() {
  local dir="$1"
  if [[ -d "$dir/node_modules" ]]; then
    return 0
  fi
  log_info "安装前端依赖: $dir"
  if [[ -f "$dir/package-lock.json" ]]; then
    npm --prefix "$dir" ci
  else
    npm --prefix "$dir" install
  fi
}

# ========== 本地启动 ==========
listeners() {
  lsof -nP -iTCP:"$1" -sTCP:LISTEN 2>/dev/null || true
}

stop_listeners() {
  local port="$1"
  local label="$2"
  local pids pid cmd
  pids="$(lsof -t -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -z "$pids" ]]; then
    log_warn "${label} 未监听 ${port}"
    return 0
  fi
  for pid in $pids; do
    cmd="$(ps -p "$pid" -o args= 2>/dev/null || true)"
    case "$cmd" in
      *wizzy*)
        log_info "停止 ${label}: $pid"
        kill "$pid" 2>/dev/null || true
        ;;
      *)
        log_error "端口 ${port} 被其他进程占用，未停止: $cmd"
        exit 1
        ;;
    esac
  done
}

start_local_backend() {
  check_command uv "请先安装: https://docs.astral.sh/uv/"
  if [[ ! -f "$PROJECT_DIR/.env" ]]; then
    log_error "未找到 $PROJECT_DIR/.env，请按 .env.example 填写并先执行 bootstrap"
    exit 1
  fi
  if ! nc -z -w 2 127.0.0.1 5432 2>/dev/null; then
    log_warn "Postgres (127.0.0.1:5432) 未监听。可先在项目根目录执行: docker compose up -d --wait postgres redis"
  fi
  if listeners "$LOCAL_API_PORT" | grep -q .; then
    log_warn "端口 ${LOCAL_API_PORT} 已被占用"
    read -r -p "是否先停止本项目在该端口上的进程？[y/N]: " confirm
    if [[ "$confirm" =~ ^[Yy]$ ]]; then
      stop_listeners "$LOCAL_API_PORT" "API"
    fi
  fi
  log_info "启动 API: http://127.0.0.1:${LOCAL_API_PORT}"
  log_info "按 Ctrl+C 停止。dispatcher 用 ./build-deploy.sh 15"
  uv run --directory "$BACKEND_DIR" uvicorn wizzy.main:create_app --factory --host 127.0.0.1 --port "$LOCAL_API_PORT"
}

start_local_dispatcher() {
  check_command uv "请先安装: https://docs.astral.sh/uv/"
  log_info "启动 outbox dispatcher。按 Ctrl+C 停止"
  log_warn "没有批改消费者时不要长时间挂着，队列会积累重复消息"
  uv run --directory "$BACKEND_DIR" python -m wizzy.dispatch
}

start_local_frontend() {
  check_command npm "请先安装 Node.js"
  ensure_npm_deps "$FRONTEND_DIR"
  log_info "启动平台 Web: http://127.0.0.1:${LOCAL_WEB_PORT}"
  log_info "开发服务器把 /api/v1/admin 代理到 127.0.0.1:${LOCAL_API_PORT}"
  npm --prefix "$FRONTEND_DIR" run dev
}

start_local_family() {
  check_command npm "请先安装 Node.js"
  ensure_npm_deps "$FAMILY_DIR"
  log_info "启动家庭端 H5: http://127.0.0.1:${LOCAL_FAMILY_PORT}"
  log_info "家庭统一入口: /，登录后通过家长中心管理"
  log_info "开发服务器把 /api/v1 代理到 127.0.0.1:${LOCAL_API_PORT}"
  npm --prefix "$FAMILY_DIR" run dev
}

status_local() {
  if listeners "$LOCAL_API_PORT" | grep -q .; then
    log_ok "API 正在监听 ${LOCAL_API_PORT}"
    listeners "$LOCAL_API_PORT" | head -5
  else
    log_warn "API 未监听 ${LOCAL_API_PORT}"
  fi
  if pgrep -f "wizzy.dispatch" >/dev/null 2>&1; then
    log_ok "dispatcher 正在运行"
    pgrep -fl "wizzy.dispatch" | head -5
  else
    log_warn "dispatcher 未运行"
  fi
}

stop_local() {
  stop_listeners "$LOCAL_API_PORT" "API"
  local pids
  pids="$(pgrep -f "wizzy.dispatch" || true)"
  if [[ -n "$pids" ]]; then
    log_info "停止 dispatcher: $pids"
    # shellcheck disable=SC2086
    kill $pids 2>/dev/null || true
  else
    log_warn "dispatcher 未运行"
  fi
  log_ok "本机服务已停止"
}

# ========== 打包 ==========
build_backend_zip() {
  if [[ ! -f "$BACKEND_DIR/pyproject.toml" || ! -f "$BACKEND_DIR/uv.lock" ]]; then
    log_error "后端目录不完整: $BACKEND_DIR"
    exit 1
  fi
  local stage
  stage="$(mktemp -d)"
  log_info "整理后端源码包 ..."
  mkdir -p "$stage/wizzy-api/backend"
  tar -C "$BACKEND_DIR" \
    --exclude '.venv' \
    --exclude '__pycache__' \
    --exclude '.pytest_cache' \
    --exclude '.mypy_cache' \
    --exclude '.ruff_cache' \
    --exclude 'tests' \
    --exclude '.env' \
    -cf - . | tar -C "$stage/wizzy-api/backend" -xf -
  mkdir -p "$ARTIFACT_DIR"
  rm -f "$ARTIFACT_DIR/$API_ZIP_NAME"
  (cd "$stage" && make_zip "$ARTIFACT_DIR/$API_ZIP_NAME" wizzy-api)
  rm -rf "$stage"
  log_ok "后端包: $ARTIFACT_DIR/$API_ZIP_NAME"
  du -sh "$ARTIFACT_DIR/$API_ZIP_NAME"
}

build_linux_wheels() {
  check_command uv "请先安装: https://docs.astral.sh/uv/"
  local req
  req="$(mktemp)"
  rm -rf "$WHEEL_DIR"
  mkdir -p "$WHEEL_DIR"
  log_info "在本机准备 Linux x86_64 / Python 3.12 依赖（服务器访问 PyPI 很慢）..."
  (
    cd "$BACKEND_DIR"
    uv export --frozen --no-dev --no-emit-project --no-hashes -o "$req" >/dev/null
    uv pip install --python 3.12 \
      --python-platform x86_64-manylinux_2_28 \
      --python-version 3.12 \
      --only-binary :all: \
      --target "$WHEEL_DIR" \
      -r "$req"
  )
  rm -f "$req"
  # 控制台脚本的 shebang 指向本机解释器，服务器改用 python -m。
  rm -rf "$WHEEL_DIR/bin" "$WHEEL_DIR/include"
  mkdir -p "$ARTIFACT_DIR"
  rm -f "$ARTIFACT_DIR/$WHEEL_ZIP_NAME"
  (cd "$WHEEL_DIR" && make_zip "$ARTIFACT_DIR/$WHEEL_ZIP_NAME" .)
  log_ok "依赖包: $ARTIFACT_DIR/$WHEEL_ZIP_NAME"
  du -sh "$ARTIFACT_DIR/$WHEEL_ZIP_NAME"
}

build_frontend() {
  check_command npm "请先安装 Node.js"
  ensure_npm_deps "$FRONTEND_DIR"
  log_info "构建平台 Web: VITE_PLATFORM_BASE=/admin/ npm run build"
  VITE_PLATFORM_BASE=/admin/ npm --prefix "$FRONTEND_DIR" run build
  if [[ ! -f "$FRONTEND_DIR/dist/index.html" ]]; then
    log_error "前端构建失败：dist/index.html 不存在"
    exit 1
  fi
  mkdir -p "$ARTIFACT_DIR"
  rm -f "$ARTIFACT_DIR/$FRONT_ZIP_NAME"
  (cd "$FRONTEND_DIR/dist" && make_zip "$ARTIFACT_DIR/$FRONT_ZIP_NAME" .)
  log_ok "前端包: $ARTIFACT_DIR/$FRONT_ZIP_NAME"
  du -sh "$FRONTEND_DIR/dist"
}

build_family() {
  check_command npm "请先安装 Node.js"
  ensure_npm_deps "$FAMILY_DIR"
  log_info "构建并验证安卓正式签名安装包"
  "$PROJECT_DIR/frontend/android/build-release.sh"
  log_info "构建家庭端 H5: VITE_FAMILY_BASE=/ npm run build:h5"
  VITE_FAMILY_BASE=/ npm --prefix "$FAMILY_DIR" run build:h5
  local dist="$FAMILY_DIR/dist/build/h5"
  if [[ ! -f "$dist/index.html" ]]; then
    log_error "家庭端构建失败：$dist/index.html 不存在"
    exit 1
  fi
  cp -R "$PROJECT_DIR/.local/android-release/downloads" "$dist/"
  mkdir -p "$dist/.well-known"
  cp "$PROJECT_DIR/.local/android-release/.well-known/assetlinks.json" "$dist/.well-known/assetlinks.json"
  mkdir -p "$ARTIFACT_DIR"
  rm -f "$ARTIFACT_DIR/$FAMILY_ZIP_NAME"
  (cd "$dist" && make_zip "$ARTIFACT_DIR/$FAMILY_ZIP_NAME" .)
  log_ok "家庭端包: $ARTIFACT_DIR/$FAMILY_ZIP_NAME"
  du -sh "$dist"
}

build_prototype_zip() {
  check_command npm "请先安装 Node.js"
  ensure_npm_deps "$PROTOTYPE_UI_DIR"
  log_info "构建静态原型并发布到 docs/prototype"
  npm --prefix "$PROTOTYPE_UI_DIR" run build
  local name stage
  for name in index child parent platform; do
    if [[ ! -f "$PROTOTYPE_DIR/$name.html" ]]; then
      log_error "未找到原型页面: $PROTOTYPE_DIR/$name.html"
      exit 1
    fi
  done
  if [[ ! -d "$PROTOTYPE_DIR/assets" ]]; then
    log_error "未找到原型资源: $PROTOTYPE_DIR/assets"
    exit 1
  fi
  stage="$(mktemp -d)"
  for name in index child parent platform; do
    cp "$PROTOTYPE_DIR/$name.html" "$stage/"
  done
  cp -a "$PROTOTYPE_DIR/assets" "$stage/assets"
  mkdir -p "$ARTIFACT_DIR"
  rm -f "$ARTIFACT_DIR/$PROTO_ZIP_NAME"
  (cd "$stage" && make_zip "$ARTIFACT_DIR/$PROTO_ZIP_NAME" .)
  rm -rf "$stage"
  log_ok "原型包: $ARTIFACT_DIR/$PROTO_ZIP_NAME"
}

write_nginx_conf() {
  local out_file="$1"
  cat > "$out_file" <<EOF
# 小知 Wizzy
map "\$remote_addr:\$http_x_forwarded_proto" \$wizzy_forwarded_proto {
    default \$scheme;
    "175.24.197.8:https" https;
}

server {
    listen       ${NGINX_PORT};
    listen       [::]:${NGINX_PORT};
    server_name  ${REMOTE_HOST} _;

    client_max_body_size 64m;
    client_body_timeout 180s;

    gzip on;
    gzip_min_length 100;
    gzip_types text/plain text/css application/xml application/javascript application/json;
    gzip_vary on;

    location = /family {
        return 302 /\$is_args\$args;
    }

    location /family/ {
        return 302 /\$is_args\$args;
    }

    location = /admin {
        return 301 /admin/;
    }

    location /admin/ {
        alias ${REMOTE_FRONT}/;
        index index.html;
    }

    location / {
        root ${REMOTE_FAMILY};
        index index.html index.htm;
        try_files \$uri \$uri/ /index.html;
    }

    location /downloads/android/ {
        root ${REMOTE_FAMILY};
        try_files \$uri =404;
        add_header Cache-Control "no-cache" always;
        types { application/vnd.android.package-archive apk; application/json json; }
        add_header X-Content-Type-Options "nosniff" always;
    }

    location = /.well-known/assetlinks.json {
        root ${REMOTE_FAMILY};
        default_type application/json;
        add_header Cache-Control "public, max-age=300";
        try_files \$uri =404;
    }

    location /api/ {
        add_header Cache-Control "no-store" always;
        proxy_set_header Host              \$http_host;
        proxy_set_header X-Real-IP         \$remote_addr;
        proxy_set_header X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$wizzy_forwarded_proto;
        proxy_pass http://127.0.0.1:${API_PORT};
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }

    location /health/ {
        add_header Cache-Control "no-store" always;
        proxy_pass http://127.0.0.1:${API_PORT};
    }
}

server {
    listen       ${PROTOTYPE_PORT};
    listen       [::]:${PROTOTYPE_PORT};
    server_name  ${REMOTE_HOST} _;

    location / {
        root ${REMOTE_PROTOTYPE};
        index index.html index.htm;
    }
}
EOF
}

write_remote_scripts() {
  local out_dir="$1"
  mkdir -p "$out_dir"

  cat > "$out_dir/stop.sh" <<EOF
#!/bin/bash
set -euo pipefail
ROOT="\$(cd "\$(dirname "\$0")" && pwd)"
PID_FILE="\$ROOT/temp/api.pid"
PORT=${API_PORT}

stop_pid() {
  local pid="\$1"
  [[ -n "\$pid" ]] || return 0
  kill -0 "\$pid" 2>/dev/null || return 0
  echo "[wizzy] stopping pid=\$pid"
  kill "\$pid" 2>/dev/null || true
  local i
  for i in \$(seq 1 20); do
    kill -0 "\$pid" 2>/dev/null || return 0
    sleep 0.2
  done
  kill -9 "\$pid" 2>/dev/null || true
}

if command -v systemctl >/dev/null 2>&1 && systemctl cat wizzy-homework.service >/dev/null 2>&1; then
  systemctl stop wizzy-homework.service
fi
if command -v systemctl >/dev/null 2>&1 && systemctl cat wizzy-homework-import.service >/dev/null 2>&1; then
  systemctl stop wizzy-homework-import.service
fi

if [[ -f "\$PID_FILE" ]]; then
  stop_pid "\$(cat "\$PID_FILE" 2>/dev/null || true)"
  rm -f "\$PID_FILE"
fi

if command -v ss >/dev/null 2>&1; then
  listen_pids=\$(ss -lntp 2>/dev/null | awk -v p=":\$PORT" '\$4 ~ p {
    while (match(\$0, /pid=[0-9]+/)) {
      print substr(\$0, RSTART+4, RLENGTH-4)
      \$0 = substr(\$0, RSTART+RLENGTH)
    }
  }' | sort -u)
  for pid in \$listen_pids; do
    cmd=\$(ps -p "\$pid" -o args= 2>/dev/null || true)
    case "\$cmd" in
      *wizzy.main:create_app*) stop_pid "\$pid" ;;
    esac
  done
fi
echo "[wizzy] stopped"
EOF

  cat > "$out_dir/start.sh" <<EOF
#!/bin/bash
set -euo pipefail
ROOT="\$(cd "\$(dirname "\$0")" && pwd)"
cd "\$ROOT"
PID_FILE="\$ROOT/temp/api.pid"
LOG_FILE="\$ROOT/temp/logs/api.out"
PORT=${API_PORT}
PYTHON="\$ROOT/.venv/bin/python"

mkdir -p "\$ROOT/temp/logs" "\$ROOT/.local/private-media"
if [[ ! -x "\$PYTHON" ]]; then
  echo "[wizzy] missing venv python: \$PYTHON" >&2
  exit 1
fi
if [[ ! -f "\$ROOT/.env" ]]; then
  echo "[wizzy] missing \$ROOT/.env" >&2
  exit 1
fi

bash "\$ROOT/stop.sh" || true
PYTHONPATH="\$ROOT/backend/src" nohup "\$PYTHON" -m uvicorn wizzy.main:create_app --factory --host 127.0.0.1 --port "\$PORT" >"\$LOG_FILE" 2>&1 &
echo \$! >"\$PID_FILE"
code=""
for _ in \$(seq 1 30); do
  if ! kill -0 "\$(cat "\$PID_FILE")" 2>/dev/null; then
    echo "[wizzy] api start failed" >&2
    tail -n 40 "\$LOG_FILE" 2>/dev/null || true
    exit 1
  fi
  code=\$(curl -sS -o /dev/null -w "%{http_code}" --connect-timeout 2 "http://127.0.0.1:\${PORT}/health/ready" || true)
  [[ "\$code" == "200" ]] && break
  sleep 1
done
echo "[wizzy] api pid=\$(cat "\$PID_FILE") ready=\${code:-n/a}"
echo "[wizzy] log: \$LOG_FILE"
[[ "\$code" == "200" ]]
cat > /etc/systemd/system/wizzy-homework.service <<SERVICE
[Unit]
Description=Wizzy homework grading worker
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=\$ROOT
Environment=PYTHONPATH=\$ROOT/backend/src
ExecStart=\$PYTHON -m wizzy.homework_grading
Restart=always
RestartSec=5
TimeoutStopSec=400
UMask=0077

[Install]
WantedBy=multi-user.target
SERVICE
systemctl daemon-reload
systemctl enable --now wizzy-homework.service
systemctl is-active --quiet wizzy-homework.service
echo "[wizzy] homework grading worker active"
cat > /etc/systemd/system/wizzy-homework-import.service <<SERVICE
[Unit]
Description=Wizzy daily homework import worker
After=network-online.target
Wants=network-online.target

[Service]
Type=notify
NotifyAccess=main
TimeoutStartSec=45
WorkingDirectory=\$ROOT
Environment=PYTHONPATH=\$ROOT/backend/src
ExecStart=\$PYTHON -m wizzy.homework_import_worker
Restart=always
RestartSec=5
TimeoutStopSec=400
UMask=0077

[Install]
WantedBy=multi-user.target
SERVICE
systemctl daemon-reload
systemctl enable --now wizzy-homework-import.service
systemctl is-active --quiet wizzy-homework-import.service
echo "[wizzy] daily homework import worker active"
EOF

  cat > "$out_dir/platform-bootstrap.sh" <<EOF
#!/bin/bash
set -euo pipefail
ROOT="\$(cd "\$(dirname "\$0")" && pwd)"
cd "\$ROOT"
if [[ ! -x "\$ROOT/.venv/bin/python" ]]; then
  echo "[wizzy] missing venv" >&2
  exit 1
fi
exec "\$ROOT/.venv/bin/python" - "\$ROOT" "${CLOUD_PG_HOST}" "${CLOUD_PG_PORT}" "\$@" <<'PY'
import os, sys
from pathlib import Path
from urllib.parse import quote

root = Path(sys.argv[1])
host = sys.argv[2]
port = sys.argv[3]
args = sys.argv[4:]

def load_env(path):
    data = {}
    if not path.exists():
        return data
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        data[key] = value
    return data

env = load_env(root / ".env")
for key, value in env.items():
    os.environ.setdefault(key, value)
os.environ["PYTHONPATH"] = str(root / "backend" / "src")
if not os.environ.get("WIZZY_MIGRATION_URL"):
    password = env.get("POSTGRES_PASSWORD") or ""
    if not password:
        print("Database initialization secret is missing.", file=sys.stderr)
        raise SystemExit(1)
    secret = quote(password, safe="")
    os.environ["WIZZY_MIGRATION_URL"] = f"postgresql+psycopg://wizzy:{secret}@{host}:{port}/wizzy"
sys.path.insert(0, os.environ["PYTHONPATH"])
from wizzy.platform_bootstrap import main
raise SystemExit(main(args))
PY
EOF
  chmod +x "$out_dir/start.sh" "$out_dir/stop.sh" "$out_dir/platform-bootstrap.sh"
}

deploy_backend() {
  ensure_ssh_login
  "$PROJECT_DIR/frontend/android/build-release.sh"
  build_backend_zip
  build_linux_wheels
  if [[ ! -f "$PROJECT_DIR/.env" ]]; then
    log_error "缺少项目根目录 .env，无法把本机 wizzy 凭据同步到云库连接"
    exit 1
  fi
  local scripts_dir db_env local_env_hash
  local_env_hash="$(shasum -a 256 "$PROJECT_DIR/.env" | awk '{print $1}')"
  scripts_dir="$(mktemp -d)"
  db_env="$(mktemp)"
  chmod 600 "$db_env"
  write_cloud_db_env "$db_env"
  write_remote_scripts "$scripts_dir"
  log_info "上传后端到 ${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_API}/ ..."
  log_info "应用数据库：${CLOUD_PG_USER}@${CLOUD_PG_HOST}:${CLOUD_PG_PORT}/${CLOUD_PG_DATABASE}"
  ssh_cmd "${REMOTE_USER}@${REMOTE_HOST}" "mkdir -p '${REMOTE_API}/backend' '${REMOTE_ROOT}/redisdata'"
  scp_cmd "$ARTIFACT_DIR/$API_ZIP_NAME" "${REMOTE_USER}@${REMOTE_HOST}:/tmp/${API_ZIP_NAME}"
  scp_cmd "$ARTIFACT_DIR/$WHEEL_ZIP_NAME" "${REMOTE_USER}@${REMOTE_HOST}:/tmp/${WHEEL_ZIP_NAME}"
  scp_cmd "$db_env" "${REMOTE_USER}@${REMOTE_HOST}:/tmp/wizzy-cloud-db.env"
  scp_cmd "$scripts_dir/start.sh" "$scripts_dir/stop.sh" "$scripts_dir/platform-bootstrap.sh" \
    "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_API}/"
  rm -rf "$scripts_dir"
  rm -f "$db_env"

  ssh_cmd "${REMOTE_USER}@${REMOTE_HOST}" bash -s <<REMOTE_SCRIPT
set -euo pipefail
API_DIR="${REMOTE_API}"
TMP_ZIP="/tmp/${API_ZIP_NAME}"
TMP_DIR="/tmp/wizzy-api-deploy"
REDIS_CONTAINER="${REDIS_CONTAINER}"
REDIS_PORT="${REDIS_PORT}"
REDIS_IMAGE="${REDIS_IMAGE}"
REDIS_DATA="${REMOTE_ROOT}/redisdata"
WHEEL_ZIP="/tmp/${WHEEL_ZIP_NAME}"
CLOUD_DB_ENV="/tmp/wizzy-cloud-db.env"
CLOUD_PG_HOST="${CLOUD_PG_HOST}"
CLOUD_PG_PORT="${CLOUD_PG_PORT}"
CLOUD_PG_DATABASE="${CLOUD_PG_DATABASE}"
CLOUD_PG_USER="${CLOUD_PG_USER}"

rm -rf "\$TMP_DIR"
mkdir -p "\$TMP_DIR"
if command -v unzip >/dev/null 2>&1; then
  unzip -o "\$TMP_ZIP" -d "\$TMP_DIR" >/dev/null
else
  python3 - "\$TMP_ZIP" "\$TMP_DIR" <<'PY'
import sys, zipfile
zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])
PY
fi
rm -rf "\$TMP_DIR/__MACOSX"
mkdir -p "\$API_DIR/backend"
if command -v rsync >/dev/null 2>&1; then
  rsync -a --delete "\$TMP_DIR/wizzy-api/backend/" "\$API_DIR/backend/"
else
  find "\$API_DIR/backend" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
  cp -a "\$TMP_DIR/wizzy-api/backend/." "\$API_DIR/backend/"
fi
rm -rf "\$TMP_DIR" "\$TMP_ZIP"
test -f "\$API_DIR/backend/src/wizzy/main.py"
test -f "\$API_DIR/backend/alembic.ini"
bash "\$API_DIR/stop.sh"

if [[ ! -f "\$CLOUD_DB_ENV" ]]; then
  echo "[wizzy] 缺少云库凭据文件" >&2
  exit 1
fi
python3 - "\$API_DIR/.env" "\$CLOUD_DB_ENV" "\$REDIS_PORT" "\$API_DIR/.local/private-media" <<'PY'
import base64, secrets, sys
from pathlib import Path

env_path, incoming_path, redis_port, storage = sys.argv[1:]

def load(path):
    data = {}
    p = Path(path)
    if not p.exists():
        return data
    for raw in p.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        data[key] = value
    return data

base = load(env_path)
incoming = load(incoming_path)
if not incoming.get("POSTGRES_PASSWORD") or not incoming.get("WIZZY_DATABASE_URL"):
    raise SystemExit("cloud db env is incomplete")
base.update(incoming)
if not base.get("WIZZY_PLATFORM_MFA_KEY"):
    base["WIZZY_PLATFORM_MFA_KEY"] = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
base["WIZZY_BROKER_URL"] = base.get("WIZZY_BROKER_URL") or f"redis://127.0.0.1:{redis_port}/0"
base["WIZZY_STORAGE_ROOT"] = storage
base["WIZZY_FAMILY_PUBLIC_ORIGIN"] = base.get("WIZZY_FAMILY_PUBLIC_ORIGIN") or "https://wizzy.refineview.com"
order = [
    "POSTGRES_PASSWORD",
    "WIZZY_APP_ROLE",
    "WIZZY_APP_PASSWORD",
    "WIZZY_DISPATCHER_ROLE",
    "WIZZY_DISPATCHER_PASSWORD",
    "WIZZY_WORKER_ROLE",
    "WIZZY_WORKER_PASSWORD",
    "WIZZY_DATABASE_URL",
    "WIZZY_DISPATCHER_DATABASE_URL",
    "WIZZY_WORKER_DATABASE_URL",
    "WIZZY_BROKER_URL",
    "WIZZY_STORAGE_ROOT",
    "WIZZY_PLATFORM_MFA_KEY",
]
lines = [f"{key}={base[key]}" for key in order if base.get(key)]
for key, value in base.items():
    if key not in order and value:
        lines.append(f"{key}={value}")
lines.append("")
path = Path(env_path)
path.write_text("\n".join(lines))
path.chmod(0o600)
print("[wizzy] merged cloud Postgres URLs into server .env")
PY
rm -f "\$CLOUD_DB_ENV"
chmod 600 "\$API_DIR/.env"

if ! command -v docker >/dev/null 2>&1; then
  echo "[wizzy] 服务器没有 docker，无法启动 Redis" >&2
  exit 1
fi
mkdir -p "\$REDIS_DATA"
if docker ps -a --format '{{.Names}}' | grep -qx "\$REDIS_CONTAINER"; then
  docker start "\$REDIS_CONTAINER" >/dev/null
else
  docker run -d --name "\$REDIS_CONTAINER" --restart unless-stopped \
    -p "127.0.0.1:\${REDIS_PORT}:6379" \
    -v "\$REDIS_DATA:/data" \
    "\$REDIS_IMAGE" \
    redis-server --appendonly yes --appendfsync everysec --maxmemory-policy noeviction
fi
ready=0
for _ in \$(seq 1 30); do
  if [[ "\$(docker exec "\$REDIS_CONTAINER" redis-cli ping 2>/dev/null || true)" == "PONG" ]]; then
    ready=1
    break
  fi
  sleep 1
done
if [[ "\$ready" != "1" ]]; then
  echo "[wizzy] Redis 未就绪" >&2
  docker logs --tail 40 "\$REDIS_CONTAINER" 2>&1 || true
  exit 1
fi

export PATH="\$HOME/.local/bin:\$PATH"
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="\$HOME/.local/bin:\$PATH"
fi
WHEEL_DIR="/tmp/wizzy-pydeps"
rm -rf "\$WHEEL_DIR" "\$API_DIR/.venv"
mkdir -p "\$WHEEL_DIR"
unzip -o "\$WHEEL_ZIP" -d "\$WHEEL_DIR" >/dev/null
rm -rf "\$WHEEL_DIR/__MACOSX" "\$WHEEL_DIR/.DS_Store"
uv venv --python 3.12 "\$API_DIR/.venv"
SITE="\$("\$API_DIR/.venv/bin/python" -c 'import sysconfig; print(sysconfig.get_path("purelib"))')"
mkdir -p "\$SITE"
if command -v rsync >/dev/null 2>&1; then
  rsync -a "\$WHEEL_DIR/" "\$SITE/"
else
  cp -a "\$WHEEL_DIR/." "\$SITE/"
fi
rm -rf "\$WHEEL_DIR" "\$WHEEL_ZIP"
"\$API_DIR/.venv/bin/python" -c "import fastapi, uvicorn, psycopg, numpy, scipy, cryptography"

export PYTHONPATH="\$API_DIR/backend/src"
export WIZZY_MIGRATION_URL="\$(python3 - "\$API_DIR/.env" "\$CLOUD_PG_HOST" "\$CLOUD_PG_PORT" "\$CLOUD_PG_DATABASE" "\$CLOUD_PG_USER" <<'PY'
import sys
from pathlib import Path
from urllib.parse import quote
path, host, port, database, user = sys.argv[1:]
password = ""
for raw in Path(path).read_text().splitlines():
    line = raw.strip()
    if line.startswith("POSTGRES_PASSWORD="):
        password = line.split("=", 1)[1]
        if len(password) >= 2 and password[0] == password[-1] and password[0] in ("'", '"'):
            password = password[1:-1]
        break
if not password:
    raise SystemExit(1)
print(f"postgresql+psycopg://{quote(user, safe='')}:{quote(password, safe='')}@{host}:{port}/{database}")
PY
)"
"\$API_DIR/.venv/bin/python" -m wizzy.bootstrap
unset WIZZY_MIGRATION_URL

chmod +x "\$API_DIR/start.sh" "\$API_DIR/stop.sh" "\$API_DIR/platform-bootstrap.sh"
bash "\$API_DIR/start.sh"
REMOTE_SCRIPT

  if [[ "$(shasum -a 256 "$PROJECT_DIR/.env" | awk '{print $1}')" != "$local_env_hash" ]]; then
    log_error "本地 .env 在发布过程中被改写了，请检查后还原为本机 127.0.0.1:5432"
    exit 1
  fi
  rm -f "$ARTIFACT_DIR/$API_ZIP_NAME" "$ARTIFACT_DIR/$WHEEL_ZIP_NAME"
  rm -rf "$WHEEL_DIR"
  log_ok "后端部署完成 → ${REMOTE_API}"
  log_ok "本地 .env 未改，仍连接本机 Postgres"
  log_info "API 只在服务器本机 ${API_PORT}，页面发布后由 nginx :${NGINX_PORT} 反代"
  log_info "日志: ssh -i ${SSH_KEY} ${REMOTE_USER}@${REMOTE_HOST} 'tail -f ${REMOTE_API}/temp/logs/api.out'"
  log_info "首位平台管理员（交互，不打印密码；已有账号时会拒绝）："
  log_info "  ssh -t -i ${SSH_KEY} ${REMOTE_USER}@${REMOTE_HOST} '${REMOTE_API}/platform-bootstrap.sh --email you@example.com'"
}

deploy_static() {
  local zip_path="$1"
  local remote_dir="$2"
  local label="$3"
  local remote_tmp="/tmp/$(basename "$zip_path")"
  log_info "上传 ${label} ..."
  ssh_cmd "${REMOTE_USER}@${REMOTE_HOST}" "mkdir -p '${remote_dir}'"
  scp_cmd "$zip_path" "${REMOTE_USER}@${REMOTE_HOST}:${remote_tmp}"
  ssh_cmd "${REMOTE_USER}@${REMOTE_HOST}" bash -s <<REMOTE_SCRIPT
set -euo pipefail
DEPLOY_DIR="${remote_dir}"
TMP_ZIP="${remote_tmp}"
TMP_DIR="/tmp/wizzy-static-${label}"
rm -rf "\$TMP_DIR"
mkdir -p "\$TMP_DIR"
if command -v unzip >/dev/null 2>&1; then
  unzip -o "\$TMP_ZIP" -d "\$TMP_DIR" >/dev/null
else
  python3 - "\$TMP_ZIP" "\$TMP_DIR" <<'PY'
import sys, zipfile
zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])
PY
fi
rm -rf "\$TMP_DIR/__MACOSX" "\$TMP_DIR/.DS_Store"
mkdir -p "\$DEPLOY_DIR"
find "\$DEPLOY_DIR" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
cp -a "\$TMP_DIR/." "\$DEPLOY_DIR/"
rm -rf "\$TMP_DIR" "\$TMP_ZIP"
chmod -R a+rX "\$DEPLOY_DIR"
test -f "\$DEPLOY_DIR/index.html"
REMOTE_SCRIPT
}

reload_nginx_remote() {
  ssh_cmd "${REMOTE_USER}@${REMOTE_HOST}" bash -s <<'REMOTE_SCRIPT'
set -euo pipefail
nginx -t
reload_nginx() {
  local pid_file master_pid
  pid_file="$(nginx -V 2>&1 | sed -n 's/.*--pid-path=\([^ ]*\).*/\1/p')"
  [[ -n "$pid_file" ]] || pid_file="/run/nginx.pid"
  master_pid=""
  if [[ -f "$pid_file" ]]; then
    master_pid="$(tr -d '[:space:]' < "$pid_file" || true)"
  fi
  if [[ -z "$master_pid" ]] || ! kill -0 "$master_pid" 2>/dev/null; then
    master_pid="$(ps -eo pid=,args= | awk '/nginx: master process/{print $1; exit}')"
  fi
  if [[ -n "$master_pid" ]] && kill -0 "$master_pid" 2>/dev/null; then
    mkdir -p "$(dirname "$pid_file")"
    echo "$master_pid" > "$pid_file"
    kill -HUP "$master_pid"
    return 0
  fi
  if systemctl is-active --quiet nginx 2>/dev/null; then
    systemctl reload nginx
    return 0
  fi
  nginx
}
reload_nginx
REMOTE_SCRIPT
}

open_firewall() {
  ssh_cmd "${REMOTE_USER}@${REMOTE_HOST}" bash -s <<REMOTE_SCRIPT
set -euo pipefail
if command -v firewall-cmd >/dev/null 2>&1 && systemctl is-active --quiet firewalld 2>/dev/null; then
  firewall-cmd --permanent --add-port=${NGINX_PORT}/tcp >/dev/null || true
  firewall-cmd --permanent --add-port=${PROTOTYPE_PORT}/tcp >/dev/null || true
  firewall-cmd --reload >/dev/null || true
  echo "[wizzy] firewalld opened ${NGINX_PORT} and ${PROTOTYPE_PORT}"
fi
REMOTE_SCRIPT
}

deploy_frontend() {
  ensure_ssh_login
  build_frontend
  local nginx_tmp
  nginx_tmp="$(mktemp)"
  write_nginx_conf "$nginx_tmp"
  deploy_static "$ARTIFACT_DIR/$FRONT_ZIP_NAME" "$REMOTE_FRONT" "front"
  scp_cmd "$nginx_tmp" "${REMOTE_USER}@${REMOTE_HOST}:${NGINX_CONF}"
  rm -f "$nginx_tmp" "$ARTIFACT_DIR/$FRONT_ZIP_NAME"
  reload_nginx_remote
  open_firewall
  log_ok "平台 Web 部署完成 → ${REMOTE_FRONT}"
  log_info "访问: http://${REMOTE_HOST}:${NGINX_PORT}/admin/"
  if curl -fsS --max-time 5 -o /dev/null "http://${REMOTE_HOST}:${NGINX_PORT}/admin/"; then
    log_ok "公网页面可访问"
  else
    log_warn "文件已在云主机就绪，但本机访问 ${REMOTE_HOST}:${NGINX_PORT} 失败"
    log_warn "请在云安全组放行入站 TCP ${NGINX_PORT} 和 ${PROTOTYPE_PORT}"
  fi
}

deploy_family() {
  ensure_ssh_login
  build_family
  local nginx_tmp
  nginx_tmp="$(mktemp)"
  write_nginx_conf "$nginx_tmp"
  deploy_static "$ARTIFACT_DIR/$FAMILY_ZIP_NAME" "$REMOTE_FAMILY" "family"
  scp_cmd "$nginx_tmp" "${REMOTE_USER}@${REMOTE_HOST}:${NGINX_CONF}"
  rm -f "$nginx_tmp" "$ARTIFACT_DIR/$FAMILY_ZIP_NAME"
  reload_nginx_remote
  open_firewall
  log_ok "家庭端部署完成 → ${REMOTE_FAMILY}"
  log_info "家庭统一入口: http://${REMOTE_HOST}:${NGINX_PORT}/"
  log_info "内部管理后台: http://${REMOTE_HOST}:${NGINX_PORT}/admin/"
  if curl -fsS --max-time 8 "http://${REMOTE_HOST}:${NGINX_PORT}/" | grep -q '/assets/'; then
    log_ok "公网家庭端可访问"
  else
    log_warn "家庭端页面未从 ${NGINX_PORT}/ 返回预期资源，请检查 nginx"
  fi
}

deploy_prototype() {
  ensure_ssh_login
  build_prototype_zip
  local nginx_tmp
  nginx_tmp="$(mktemp)"
  write_nginx_conf "$nginx_tmp"
  deploy_static "$ARTIFACT_DIR/$PROTO_ZIP_NAME" "$REMOTE_PROTOTYPE" "prototype"
  scp_cmd "$nginx_tmp" "${REMOTE_USER}@${REMOTE_HOST}:${NGINX_CONF}"
  rm -f "$nginx_tmp" "$ARTIFACT_DIR/$PROTO_ZIP_NAME"
  reload_nginx_remote
  open_firewall
  log_ok "原型部署完成 → ${REMOTE_PROTOTYPE}"
  log_info "访问: http://${REMOTE_HOST}:${PROTOTYPE_PORT}"
  log_info "入口: index.html、child.html、parent.html、platform.html"
}

show_menu() {
  echo ""
  echo "=========================================="
  echo "   小知 Wizzy 构建部署脚本"
  echo "=========================================="
  echo ""
  echo "【本地开发】"
  echo "  11  - 本地启动后端  (http://127.0.0.1:${LOCAL_API_PORT})"
  echo "  15  - 本地启动 dispatcher"
  echo "  12  - 本地启动平台 Web  (http://127.0.0.1:${LOCAL_WEB_PORT})"
  echo "  16  - 本地启动家庭端 H5  (http://127.0.0.1:${LOCAL_FAMILY_PORT})"
  echo ""
  echo "【打包构建】"
  echo "  3   - 后端打包（源码 zip）"
  echo "  4   - 平台 Web 打包（npm run build + zip）"
  echo "  7   - 家庭端 H5 打包（npm run build:h5 + zip）"
  echo ""
  echo "【服务管理】"
  echo "  13  - 查看本机后端状态"
  echo "  14  - 停止本机后端和 dispatcher"
  echo ""
  echo "【部署发布 → ${REMOTE_HOST}】"
  echo "  0   - 全部发布到 ${REMOTE_ROOT}"
  echo "  1   - 仅发布后端  → ${REMOTE_API}  (127.0.0.1:${API_PORT})"
  echo "  2   - 仅发布平台 Web  → ${REMOTE_FRONT}  (http://${REMOTE_HOST}:${NGINX_PORT}/admin/)"
  echo "  5   - 仅发布原型  → ${REMOTE_PROTOTYPE}  (http://${REMOTE_HOST}:${PROTOTYPE_PORT})"
  echo "  6   - 仅发布家庭端  → ${REMOTE_FAMILY}  (http://${REMOTE_HOST}:${NGINX_PORT}/)"
  echo ""
  echo "=========================================="
  echo ""
}

MODE="${1:-}"
if [[ -z "$MODE" ]]; then
  show_menu
  read -r -p "请输入编号: " MODE
fi

case "$MODE" in
  0)
    log_info "========== 1/4 发布后端 =========="
    deploy_backend
    echo ""
    log_info "========== 2/4 发布平台 Web =========="
    deploy_frontend
    echo ""
    log_info "========== 3/4 发布家庭端 =========="
    deploy_family
    echo ""
    log_info "========== 4/4 发布原型 =========="
    deploy_prototype
    echo ""
    log_ok "========== 小知 Wizzy 部署完成 =========="
    log_info "家庭统一入口: http://${REMOTE_HOST}:${NGINX_PORT}/"
    log_info "内部管理后台: http://${REMOTE_HOST}:${NGINX_PORT}/admin/"
    log_info "原型: http://${REMOTE_HOST}:${PROTOTYPE_PORT}"
    ;;
  1) deploy_backend ;;
  2) deploy_frontend ;;
  3) build_backend_zip ;;
  4) build_frontend ;;
  5) deploy_prototype ;;
  6) deploy_family ;;
  7) build_family ;;
  11) start_local_backend ;;
  12) start_local_frontend ;;
  16) start_local_family ;;
  13) status_local ;;
  14) stop_local ;;
  15) start_local_dispatcher ;;
  *)
    echo "无效编号: $MODE"
    show_menu
    exit 1
    ;;
esac
