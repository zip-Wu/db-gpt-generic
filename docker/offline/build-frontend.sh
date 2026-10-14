#!/usr/bin/env bash
# DB-GPT 前端构建脚本
# 用途：在本机构建前端静态产物，并投放进 static/web
# 前置：node >= 18、web/node_modules 已安装
# 用法：cd services/dbgpt/src && bash docker/offline/build-frontend.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
WEB_DIR="$REPO_ROOT/web"
STATIC_WEB="$REPO_ROOT/packages/dbgpt-app/src/dbgpt_app/static/web"

echo "源码根目录: $REPO_ROOT"
echo "前端目录  : $WEB_DIR"
echo "投放目标  : $STATIC_WEB"
echo

cd "$WEB_DIR"

# 记录构建前状态
echo "===== 构建前 ====="
echo "源码基线: $(cd "$REPO_ROOT" && git rev-parse --short HEAD 2>/dev/null || echo unknown)"
echo "web/out 存在: $([ -d out ] && echo yes || echo no)"
echo "static/web buildId: $(ls -d "$STATIC_WEB"/_next/static/*/ 2>/dev/null | grep -v chunks | xargs -n1 basename 2>/dev/null | head -1)"
echo

# 1) 清理旧产物（避免上次构建残留混入）
echo "===== 1. 清理旧产物 ====="
rm -rf out
rm -rf .next
echo "已清理 web/out 与 web/.next"
echo

# 1.5) 行尾归一化
# 背景：仓库 core.autocrlf=true，Windows 工作区检出为 CRLF。
# 而 web/.eslintrc.js 启用了 plugin:prettier/recommended（prettier/prettier=error），
# 会因 "Delete ␍" 直接让 next build 编译失败。
# 这里把 web/ 下源码统一成 LF，使构建结果不依赖 checkout 时的 autocrlf 配置。
echo "===== 1.5. 行尾归一化（CRLF -> LF）====="
CRLF_FIXED=$(python - <<'PYEOF'
import os, sys
fixed = 0
skip = {'node_modules', '.next', 'out', '.git'}
for root, dirs, files in os.walk('.'):
    dirs[:] = [d for d in dirs if d not in skip]
    for f in files:
        if not f.endswith(('.ts', '.tsx', '.js', '.jsx', '.json', '.css', '.scss')):
            continue
        p = os.path.join(root, f)
        try:
            d = open(p, 'rb').read()
        except OSError:
            continue
        if b'\r\n' not in d:
            continue
        # 归一化：CRLF -> LF，同时清掉孤立 CR
        n = d.replace(b'\r\n', b'\n').replace(b'\r', b'\n')
        open(p, 'wb').write(n)
        fixed += 1
print(fixed)
PYEOF
)
echo "已归一化 $CRLF_FIXED 个文件的行尾"
echo

# 2) 构建
# --no-lint：生产构建不应被 lint 规则阻塞（lint 由 npm run lint 单独负责）。
# 同时上面的行尾归一化已消除 prettier/prettier 的 CRLF 报错源，双重保险。
echo "===== 2. next build ====="
echo "（约 3-10 分钟，内存峰值约 8GB）"
NODE_OPTIONS=--max_old_space_size=8192 npx next build --no-lint
echo "build 完成"
echo

# 3) 导出静态
echo "===== 3. next export ====="
npx next export
echo "export 完成"
echo

# 4) 校验产物
echo "===== 4. 校验产物 ====="
if [ ! -f out/index.html ]; then
  echo "错误：out/index.html 不存在，构建失败"
  exit 1
fi
echo "out 文件数: $(find out -type f | wc -l)"
echo "out 大小  : $(du -sh out | cut -f1)"
echo

# 关键页面存在性断言
for p in index.html; do
  if [ -f "out/$p" ]; then
    echo "OK   $p"
  else
    echo "MISS $p"
  fi
done
echo

# 5) 投放（先投临时目录，校验通过再原子替换；失败自动回滚）
echo "===== 5. 投放进 static/web ====="
BAK="$STATIC_WEB.bak-$(date +%Y%m%d-%H%M%S)"
STAGE="$STATIC_WEB.staging-$$"

# 5.1 先把产物拷到 staging，校验通过才动正式目录
rm -rf "$STAGE"
cp -r out "$STAGE"

STAGE_BAD=0
for p in index.html; do
  if [ ! -f "$STAGE/$p" ]; then
    echo "错误：staging 缺少 $p，放弃投放"
    STAGE_BAD=1
  fi
done
if [ "$STAGE_BAD" -ne 0 ]; then
  rm -rf "$STAGE"
  exit 1
fi
echo "staging 校验通过（$STAGE，$(find "$STAGE" -type f | wc -l) 文件）"

# 5.2 备份旧版 -> 原子替换
if [ -d "$STATIC_WEB" ]; then
  mv "$STATIC_WEB" "$BAK"
  echo "旧版已备份到: $BAK"
fi
if ! mv "$STAGE" "$STATIC_WEB"; then
  echo "错误：投放失败，正在回滚"
  [ -d "$BAK" ] && mv "$BAK" "$STATIC_WEB"
  exit 1
fi
echo "已投放: out -> static/web"
echo

# 6) 投放后校验
echo "===== 6. 投放后校验 ====="
echo "static/web 文件数: $(find "$STATIC_WEB" -type f | wc -l)"
echo "static/web 大小  : $(du -sh "$STATIC_WEB" | cut -f1)"
echo "新 buildId: $(ls -d "$STATIC_WEB"/_next/static/*/ 2>/dev/null | grep -v chunks | xargs -n1 basename 2>/dev/null | head -1)"
echo
echo "构建完成。旧版备份: $BAK"
