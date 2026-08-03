#!/usr/bin/env bash
# 下载并校验 Pikafish native binary + NNUE,置于 engine/ 下供 build-time 工具(tools/)使用。
# 版本与校验值全部锁在 ENGINE_VERSION;本脚本不含任何硬编码版本号。
# binary / nnue / 压缩包皆 gitignore,靠本脚本按需重建。
#
# 用法:
#   engine/fetch.sh            自动依当前平台选 binary
#   FORCE=1 engine/fetch.sh    即使档案已存在且校验通过也重新下载
#
# 依赖:curl、shasum(或 sha256sum)、7z 抽取器(7zz / 7z / 7za 任一)。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

# shellcheck source=/dev/null
source ./ENGINE_VERSION

# --- 平台 → 压缩包内 binary 路径 + 对应 sha256 变量 -------------------------
os="$(uname -s)"; arch="$(uname -m)"
case "$os/$arch" in
  Darwin/arm64)          BIN_IN_ARCHIVE="MacOS/pikafish-apple-silicon"; BIN_SHA256="$BIN_MACOS_APPLE_SILICON_SHA256" ;;
  Linux/x86_64|Linux/amd64) BIN_IN_ARCHIVE="Linux/pikafish-avx2";        BIN_SHA256="$BIN_LINUX_AVX2_SHA256" ;;
  *)
    echo "错误:未支援的平台 $os/$arch。" >&2
    echo "请在 ENGINE_VERSION 与本脚本的 case 中补上对应 binary 后再试。" >&2
    exit 1 ;;
esac

# --- 挑选可用工具 ----------------------------------------------------------
sha256() {  # 输出 <file> 的 sha256(仅 hash 值)
  if command -v shasum   >/dev/null 2>&1; then shasum -a 256 "$1" | awk '{print $1}'
  elif command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}'
  else echo "错误:找不到 shasum 或 sha256sum" >&2; exit 1; fi
}
SEVENZ=""
for c in 7zz 7z 7za; do command -v "$c" >/dev/null 2>&1 && { SEVENZ="$c"; break; }; done
if [ -z "$SEVENZ" ]; then
  echo "错误:找不到 7z 抽取器(需要 7zz / 7z / 7za 之一)。" >&2
  echo "  macOS: brew install sevenzip    Debian/Ubuntu: apt-get install p7zip-full" >&2
  exit 1
fi

verify() {  # verify <file> <expected-sha256> → 0 表示相符
  [ -f "$1" ] && [ "$(sha256 "$1")" = "$2" ]
}

# --- 已就位且校验通过则跳过(除非 FORCE) ----------------------------------
if [ "${FORCE:-0}" != "1" ] && verify ./pikafish "$BIN_SHA256" && verify ./pikafish.nnue "$NNUE_SHA256"; then
  echo "binary 与 nnue 均已就位且校验通过,无需下载。(FORCE=1 可强制重下)"
  exit 0
fi

# --- 下载压缩包(已存在且校验通过则复用) ----------------------------------
if [ "${FORCE:-0}" = "1" ] || ! verify "./$ARCHIVE_NAME" "$ARCHIVE_SHA256"; then
  echo "下载 $ARCHIVE_NAME ..."
  curl -fL --retry 3 -o "./$ARCHIVE_NAME" "$ARCHIVE_URL"
fi

echo "校验压缩包 sha256 ..."
if ! verify "./$ARCHIVE_NAME" "$ARCHIVE_SHA256"; then
  echo "错误:$ARCHIVE_NAME sha256 不符,预期 $ARCHIVE_SHA256,实得 $(sha256 "./$ARCHIVE_NAME")" >&2
  exit 1
fi

# --- 抽取所需 binary + nnue -------------------------------------------------
echo "抽取 $BIN_IN_ARCHIVE 与 pikafish.nnue ..."
rm -rf .extract && mkdir .extract
"$SEVENZ" x -o.extract "./$ARCHIVE_NAME" "$BIN_IN_ARCHIVE" "pikafish.nnue" >/dev/null
mv -f ".extract/$BIN_IN_ARCHIVE" ./pikafish
mv -f ".extract/pikafish.nnue"   ./pikafish.nnue
chmod +x ./pikafish
rm -rf .extract
[ "$os" = "Darwin" ] && xattr -d com.apple.quarantine ./pikafish 2>/dev/null || true

# --- 逐档校验 ---------------------------------------------------------------
echo "校验 binary 与 nnue sha256 ..."
verify ./pikafish       "$BIN_SHA256"  || { echo "错误:pikafish binary sha256 不符" >&2; exit 1; }
verify ./pikafish.nnue  "$NNUE_SHA256" || { echo "错误:pikafish.nnue sha256 不符" >&2; exit 1; }

echo "完成:engine/pikafish ($BIN_IN_ARCHIVE) + engine/pikafish.nnue,$PIKAFISH_RELEASE"
