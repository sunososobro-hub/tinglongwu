# Builds llama.cpp (pinned commit used by llama_cpp_dart 23dae0b) from source for
# Android arm64 and packages native/llama-cpp-dart-selfbuilt.aar. The upstream
# prebuilt AAR is NOT used (pubspec hooks.user_defines.llama_cpp_dart.android_aar).
#
#   powershell -File tool\build_llama_android.ps1
$ErrorActionPreference = 'Stop'
$sdk = "$env:LOCALAPPDATA\Android\Sdk"
$ndk = "$sdk\ndk\28.2.13676358"
$env:PATH = "$sdk\cmake\3.22.1\bin;$env:PATH"   # cmake + ninja from the SDK
$commit = 'afeebe103bd99cda8f5dfaefcabadf890db7fda7'
$root = if ($env:TLWU_BUILD_ROOT) { Join-Path $env:TLWU_BUILD_ROOT 'build' } else { Join-Path $env:USERPROFILE 'tlwu-eval' | Join-Path -ChildPath 'build' }
$src = "$root\llama.cpp"; $b = "$root\llama-android\build"; $inst = "$root\llama-android\install"
$stage = "$root\llama-android\aar"
$mobile = Split-Path -Parent $PSScriptRoot

if (-not (Test-Path "$src\.git")) {
  New-Item -ItemType Directory -Force $src | Out-Null
  git -C $src init -q; git -C $src remote add origin https://github.com/ggml-org/llama.cpp
}
git -C $src fetch --depth 1 origin $commit; git -C $src checkout -q FETCH_HEAD

# GGML_CPU_ARM_ARCH enables dotprod/i8mm kernels (Cortex-X2/A710 on 8+ Gen 1); upstream script left them off.
cmake -G Ninja -B $b -S $src "-DCMAKE_TOOLCHAIN_FILE=$ndk\build\cmake\android.toolchain.cmake" `
  -DANDROID_ABI=arm64-v8a -DANDROID_PLATFORM=android-26 -DCMAKE_BUILD_TYPE=Release "-DCMAKE_INSTALL_PREFIX=$inst" `
  -DBUILD_SHARED_LIBS=ON -DLLAMA_BUILD_EXAMPLES=OFF -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_SERVER=OFF `
  -DLLAMA_BUILD_APP=OFF -DLLAMA_CURL=OFF -DLLAMA_BUILD_TOOLS=ON -DMTMD_VIDEO=OFF -DGGML_OPENMP=OFF `
  -DGGML_LLAMAFILE=OFF -DGGML_NATIVE=OFF "-DGGML_CPU_ARM_ARCH=armv8.6-a+dotprod+i8mm+fp16" "-DCMAKE_CXX_FLAGS=-O3 -DNDEBUG"
cmake --build $b --parallel
cmake --install $b

Remove-Item -Recurse -Force $stage -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force "$stage\jni\arm64-v8a" | Out-Null
$strip = "$ndk\toolchains\llvm\prebuilt\windows-x86_64\bin\llvm-strip.exe"
foreach ($n in 'libllama', 'libggml', 'libggml-base', 'libggml-cpu', 'libmtmd') {
  Copy-Item "$inst\lib\$n.so" "$stage\jni\arm64-v8a\"; & $strip --strip-unneeded "$stage\jni\arm64-v8a\$n.so"
}
'<?xml version="1.0" encoding="utf-8"?><manifest xmlns:android="http://schemas.android.com/apk/res/android" package="dev.netdur.llama_cpp_dart"><uses-sdk android:minSdkVersion="26" /></manifest>' |
  Set-Content "$stage\AndroidManifest.xml" -Encoding ascii
New-Item "$stage\R.txt" -ItemType File | Out-Null
New-Item -ItemType Directory -Force "$mobile\native" | Out-Null
$out = "$mobile\native\llama-cpp-dart-selfbuilt.aar"
python -I -c "
import zipfile, os
st = r'$stage'; out = r'$out'
with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
    z.writestr('classes.jar', b'PK\x05\x06' + b'\x00' * 18)  # empty jar
    z.write(st + '/AndroidManifest.xml', 'AndroidManifest.xml'); z.write(st + '/R.txt', 'R.txt')
    for f in sorted(os.listdir(st + '/jni/arm64-v8a')): z.write(st + '/jni/arm64-v8a/' + f, 'jni/arm64-v8a/' + f)
"
Get-FileHash $out -Algorithm SHA256
