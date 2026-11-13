#!/usr/bin/env bash
# Linux/macOS twin of tool/build_llama_android.ps1 (used by .github/workflows/mobile-apk.yml).
# Builds llama.cpp at the commit pinned by llama_cpp_dart 23dae0b for Android arm64 and packages
# native/llama-cpp-dart-selfbuilt.aar. The upstream prebuilt AAR is NOT used.
#
#   ANDROID_HOME=~/Android/Sdk bash tool/build_llama_android.sh
# Needs: NDK 28.2.13676358 and SDK cmake 3.22.1 (cmake + ninja) under $ANDROID_HOME, git, python3.
set -euo pipefail
SDK="${ANDROID_HOME:-${ANDROID_SDK_ROOT:-$HOME/Android/Sdk}}"
NDK="$SDK/ndk/28.2.13676358"
export PATH="$SDK/cmake/3.22.1/bin:$PATH"   # cmake + ninja from the SDK
COMMIT=afeebe103bd99cda8f5dfaefcabadf890db7fda7
ROOT="${TLWU_BUILD_ROOT:-$HOME/tlwu-eval}/build"
SRC="$ROOT/llama.cpp"; B="$ROOT/llama-android/build"; INST="$ROOT/llama-android/install"
STAGE="$ROOT/llama-android/aar"
MOBILE="$(cd "$(dirname "$0")/.." && pwd)"
case "$(uname -s)" in Darwin) HOST=darwin-x86_64 ;; *) HOST=linux-x86_64 ;; esac

[ -d "$NDK" ] || { echo "NDK not found: $NDK" >&2; exit 1; }
if [ ! -d "$SRC/.git" ]; then
  mkdir -p "$SRC"
  git -C "$SRC" init -q; git -C "$SRC" remote add origin https://github.com/ggml-org/llama.cpp
fi
git -C "$SRC" fetch -q --depth 1 origin "$COMMIT"; git -C "$SRC" checkout -q FETCH_HEAD

# GGML_CPU_ARM_ARCH enables dotprod/i8mm kernels (Cortex-X2/A710 on 8+ Gen 1); upstream script left them off.
cmake -G Ninja -B "$B" -S "$SRC" "-DCMAKE_TOOLCHAIN_FILE=$NDK/build/cmake/android.toolchain.cmake" \
  -DANDROID_ABI=arm64-v8a -DANDROID_PLATFORM=android-26 -DCMAKE_BUILD_TYPE=Release "-DCMAKE_INSTALL_PREFIX=$INST" \
  -DBUILD_SHARED_LIBS=ON -DLLAMA_BUILD_EXAMPLES=OFF -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_SERVER=OFF \
  -DLLAMA_BUILD_APP=OFF -DLLAMA_CURL=OFF -DLLAMA_BUILD_TOOLS=ON -DMTMD_VIDEO=OFF -DGGML_OPENMP=OFF \
  -DGGML_LLAMAFILE=OFF -DGGML_NATIVE=OFF "-DGGML_CPU_ARM_ARCH=armv8.6-a+dotprod+i8mm+fp16" "-DCMAKE_CXX_FLAGS=-O3 -DNDEBUG"
cmake --build "$B" --parallel
cmake --install "$B"

rm -rf "$STAGE"; mkdir -p "$STAGE/jni/arm64-v8a"
STRIP="$NDK/toolchains/llvm/prebuilt/$HOST/bin/llvm-strip"
for n in libllama libggml libggml-base libggml-cpu libmtmd; do
  cp "$INST/lib/$n.so" "$STAGE/jni/arm64-v8a/"; "$STRIP" --strip-unneeded "$STAGE/jni/arm64-v8a/$n.so"
done
printf '%s' '<?xml version="1.0" encoding="utf-8"?><manifest xmlns:android="http://schemas.android.com/apk/res/android" package="dev.netdur.llama_cpp_dart"><uses-sdk android:minSdkVersion="26" /></manifest>' > "$STAGE/AndroidManifest.xml"
: > "$STAGE/R.txt"
mkdir -p "$MOBILE/native"
OUT="$MOBILE/native/llama-cpp-dart-selfbuilt.aar"
python3 -I - "$STAGE" "$OUT" <<'PY'
import os, sys, zipfile
st, out = sys.argv[1], sys.argv[2]
with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
    z.writestr('classes.jar', b'PK\x05\x06' + b'\x00' * 18)  # empty jar
    z.write(st + '/AndroidManifest.xml', 'AndroidManifest.xml'); z.write(st + '/R.txt', 'R.txt')
    for f in sorted(os.listdir(st + '/jni/arm64-v8a')):
        z.write(st + '/jni/arm64-v8a/' + f, 'jni/arm64-v8a/' + f)
PY
sha256sum "$OUT" 2>/dev/null || shasum -a 256 "$OUT"
