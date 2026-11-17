# #783: one self-contained tlwu-whisper library (whisper.cpp + ggml linked in statically), so its ggml never clashes
# with the llama.cpp ggml DLLs that sit next to it in vulkan-pack/. Build (WHISPER_SRC = whisper.cpp at the pin):
#   cmake -S scripts/whisper_vulkan -B build -DWHISPER_SRC=<dir> -DGGML_VULKAN=ON -DCMAKE_BUILD_TYPE=Release
#   cmake --build build --config Release --target tlwu-whisper
cmake_minimum_required(VERSION 3.14)
project(tlwu_whisper C CXX)
set(WHISPER_SRC "" CACHE PATH "whisper.cpp source tree (scripts/build_whisper_vulkan.py checks out the pin)")
set(BUILD_SHARED_LIBS OFF CACHE BOOL "" FORCE)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)
set(WHISPER_BUILD_TESTS OFF CACHE BOOL "" FORCE)
set(WHISPER_BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
set(WHISPER_BUILD_SERVER OFF CACHE BOOL "" FORCE)
set(GGML_NATIVE OFF CACHE BOOL "" FORCE)   # AVX2 baseline, not the build machine's CPU (#74)
add_subdirectory(${WHISPER_SRC} whisper EXCLUDE_FROM_ALL)
add_library(tlwu-whisper SHARED tlwu_whisper.cpp)
target_link_libraries(tlwu-whisper PRIVATE whisper)
if(NOT WIN32)
  set_target_properties(tlwu-whisper PROPERTIES CXX_VISIBILITY_PRESET hidden)
  target_link_options(tlwu-whisper PRIVATE -Wl,--exclude-libs,ALL -Wl,-Bsymbolic)
endif()
