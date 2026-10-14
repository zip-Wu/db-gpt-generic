#!/usr/bin/env bash
# DB-GPT 后端镜像构建脚本（本机构建版）
# 用途：在本机 Docker Desktop 构建 k-ics:r2（平台）与 k-ics-python:r2（沙箱）
# 前置：docker 可用；docker/offline/vendor/instantclient-basic-linux.x64-19.32.0.0.0dbru.zip 已在位
#       （该压缩包不入库，需自行放置，脚本会给出下载地址）
# 用法：cd services/dbgpt/src && bash docker/offline/build-backend.sh [TAG]
set -euo pipefail

TAG="${1:-r2}"
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"

echo "源码根目录: $REPO_ROOT"
echo "镜像标签  : k-ics:${TAG} / k-ics-python:${TAG}"
echo

# ---- 前置检查 ----
echo "===== 前置检查 ====="
OCI_ZIP="docker/offline/vendor/instantclient-basic-linux.x64-19.32.0.0.0dbru.zip"
if [ ! -f "$OCI_ZIP" ]; then
  echo "错误：缺少 Oracle Instant Client: $OCI_ZIP"
  echo "该压缩包约 85 MB，未随源码入库，需自行下载后放到上述路径："
  echo "  https://download.oracle.com/otn_software/linux/instantclient/1932000/instantclient-basic-linux.x64-19.32.0.0.0dbru.zip"
  echo "放好后重跑本脚本即可。"
  exit 1
fi
echo "OK   Oracle 客户端: $(stat -c %s "$OCI_ZIP" 2>/dev/null || stat -f %z "$OCI_ZIP") 字节"

if [ ! -f "packages/dbgpt-app/src/dbgpt_app/static/web/index.html" ]; then
  echo "错误：static/web 里没有 index.html，请先跑 build-frontend.sh"
  exit 1
fi
echo "OK   前端产物 index.html 就位"

docker info >/dev/null 2>&1 || { echo "错误：Docker 未运行"; exit 1; }
echo "OK   Docker: $(docker --version)"
echo

# ---- 构建 runtime ----
echo "===== 1. 构建运行时镜像 k-ics:${TAG} ====="
echo "（约 10-30 分钟，含依赖下载与 Oracle 客户端解压）"
docker build \
  --target runtime \
  --progress=plain \
  -t "k-ics:${TAG}" \
  -f docker/offline/Dockerfile \
  .
echo "runtime 构建完成"
echo

# ---- 构建 sandbox ----
echo "===== 2. 构建沙箱镜像 k-ics-python:${TAG} ====="
docker build \
  --target sandbox \
  --progress=plain \
  -t "k-ics-python:${TAG}" \
  -f docker/offline/Dockerfile \
  .
echo "sandbox 构建完成"
echo

# ---- 验证 ----
echo "===== 3. 镜像验证 ====="
docker images | grep -E "k-ics" || true
echo
echo "--- runtime 关键内容检查 ---"
docker run --rm --entrypoint sh "k-ics:${TAG}" -c '
  echo "Oracle 客户端: $(ls /opt/oracle/ 2>/dev/null)"
  echo "Python: $(python --version 2>&1)"
  echo "venv: $(ls /app/.venv/bin/python 2>/dev/null && echo exists)"
  echo "首页: $(ls /app/packages/dbgpt-app/src/dbgpt_app/static/web/index.html 2>/dev/null || echo MISSING)"
  echo "custom tools: $(ls /app/packages/dbgpt-app/src/dbgpt_app/openapi/api_v1/tools/*.py 2>/dev/null | wc -l) 个"
  /app/.venv/bin/python -c "import oracledb; oracledb.init_oracle_client(); print(\"thick OK:\", oracledb.clientversion())" 2>&1 | tail -1
'
echo
echo "构建完成。镜像: k-ics:${TAG} / k-ics-python:${TAG}"
