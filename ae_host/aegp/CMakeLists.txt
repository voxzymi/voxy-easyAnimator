# Builds the AEGP plug-in (.aex on Windows). Needs the Adobe After Effects SDK, which cannot be redistributed:
#   download it from Adobe, then   cmake -S ae_host -B build -DVOXY_BUILD_AEGP=ON -DAE_SDK_DIR="C:/path/to/AfterEffectsSDK"
# NOT BUILT IN DEVELOPMENT (no SDK available there). See SDK_NOTES.md.
if(NOT AE_SDK_DIR)
  message(FATAL_ERROR "Set AE_SDK_DIR to the folder that contains the After Effects SDK 'Headers' and 'Resources'.")
endif()
set(AE_INCLUDES
  ${AE_SDK_DIR}/Headers ${AE_SDK_DIR}/Headers/SP ${AE_SDK_DIR}/Util ${AE_SDK_DIR}/Resources)
add_library(VoxyEasyAnimator MODULE
  entry.cpp ae_api_sdk.cpp
  ${AE_SDK_DIR}/Util/AEGP_SuiteHandler.cpp)
target_include_directories(VoxyEasyAnimator PRIVATE ${AE_INCLUDES})
target_link_libraries(VoxyEasyAnimator PRIVATE voxy_core)
if(WIN32)
  # The PiPL must be compiled with Adobe's PiPLtool + cl (see the SDK's sample build scripts) into a .rc resource.
  target_compile_definitions(VoxyEasyAnimator PRIVATE MSWindows WIN32 _WINDOWS)
  set_target_properties(VoxyEasyAnimator PROPERTIES SUFFIX ".aex" PREFIX "")
  target_sources(VoxyEasyAnimator PRIVATE ${CMAKE_CURRENT_BINARY_DIR}/VoxyEasyAnimator_PiPL.rc)
  add_custom_command(OUTPUT ${CMAKE_CURRENT_BINARY_DIR}/VoxyEasyAnimator_PiPL.rc
    COMMAND cl /I "${AE_SDK_DIR}/Headers" /EP /DMSWindows /DAE_PROC_INTELx64 /Tc "${CMAKE_CURRENT_SOURCE_DIR}/VoxyEasyAnimator_PiPL.r"
            > "${CMAKE_CURRENT_BINARY_DIR}/VoxyEasyAnimator_PiPL.rr"
    COMMAND "${AE_SDK_DIR}/Resources/PiPLtool.exe" "${CMAKE_CURRENT_BINARY_DIR}/VoxyEasyAnimator_PiPL.rr"
            "${CMAKE_CURRENT_BINARY_DIR}/VoxyEasyAnimator_PiPL.rrc"
    COMMAND cl /D "MSWindows" /EP "${CMAKE_CURRENT_BINARY_DIR}/VoxyEasyAnimator_PiPL.rrc" > "${CMAKE_CURRENT_BINARY_DIR}/VoxyEasyAnimator_PiPL.rc"
    DEPENDS ${CMAKE_CURRENT_SOURCE_DIR}/VoxyEasyAnimator_PiPL.r)
endif()
