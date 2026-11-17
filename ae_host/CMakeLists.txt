cmake_minimum_required(VERSION 3.16)
project(VoxyEasyAnimatorHost LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
find_package(Threads REQUIRED)

# ---- portable core: everything except the After Effects SDK calls
add_library(voxy_core STATIC
  core/types.cpp core/util.cpp core/journal.cpp core/plan.cpp core/commands.cpp core/autosave.cpp
  core/net.cpp core/bridge.cpp core/supervisor.cpp)
target_include_directories(voxy_core PUBLIC core)
target_link_libraries(voxy_core PUBLIC Threads::Threads)
if(WIN32)
  target_link_libraries(voxy_core PUBLIC ws2_32)
  target_compile_definitions(voxy_core PUBLIC NOMINMAX WIN32_LEAN_AND_MEAN)
endif()
if(NOT MSVC)
  target_compile_options(voxy_core PRIVATE -Wall -Wextra -Wno-unused-parameter)
else()
  target_compile_options(voxy_core PRIVATE /W4 /utf-8)
endif()

# ---- tests (run against an in-memory fake After Effects)
option(VOXY_BUILD_TESTS "Build the host tests" ON)
if(VOXY_BUILD_TESTS)
  enable_testing()
  add_executable(voxy_host_tests tests/test_main.cpp tests/test_core.cpp tests/test_autosave.cpp tests/test_bridge.cpp)
  target_link_libraries(voxy_host_tests PRIVATE voxy_core)
  find_package(Python3 COMPONENTS Interpreter)
  set(FIXTURE_DIR ${CMAKE_BINARY_DIR}/fixture)
  add_test(NAME make_fixture COMMAND ${Python3_EXECUTABLE} ${CMAKE_SOURCE_DIR}/tests/make_fixture.py ${FIXTURE_DIR})
  add_test(NAME host_tests COMMAND voxy_host_tests)
  set_tests_properties(host_tests PROPERTIES DEPENDS make_fixture
    ENVIRONMENT "VOXY_FIXTURE=${FIXTURE_DIR};VOXY_TESTS_DIR=${CMAKE_SOURCE_DIR}/tests;VOXY_PYTHON=${Python3_EXECUTABLE}")
endif()

# ---- the After Effects plug-in itself. Needs the Adobe After Effects SDK (not redistributable): see ae_host/README.md
option(VOXY_BUILD_AEGP "Build the AEGP plug-in (needs AE_SDK_DIR)" OFF)
if(VOXY_BUILD_AEGP)
  add_subdirectory(aegp)
endif()
