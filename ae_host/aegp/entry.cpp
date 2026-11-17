// *** NOT COMPILED OR RUN IN DEVELOPMENT: see ae_api_sdk.h ***
// AEGP entry point. After Effects loads this at start-up. It:
//   - creates the portable host core (commands, journal, autosave) on top of the SDK adapter,
//   - starts the localhost bridge the panel talks to and the helper that starts the ML service,
//   - pumps queued commands on After Effects' main thread from the idle hook,
//   - shuts everything down (and stops the service) when After Effects quits.
#include <memory>

#include "AEConfig.h"
#include "AE_GeneralPlug.h"
#include "AE_Macros.h"
#include "AEGP_SuiteHandler.h"
#include "entry.h"

#include "../core/bridge.h"
#include "../core/supervisor.h"
#include "ae_api_sdk.h"

#ifdef _WIN32
#include <windows.h>
#include <shlobj.h>
#endif

using namespace voxy;

namespace {
struct Host {
  std::unique_ptr<AeApiSdk> api;
  std::unique_ptr<Journal> journal;
  std::unique_ptr<AutosaveManager> autosave;
  std::unique_ptr<Commands> commands;
  MainThreadQueue queue;
  std::unique_ptr<Bridge> bridge;
  std::unique_ptr<ServiceSupervisor> supervisor;
};
Host* g_host = nullptr;

fs::path data_dir() {
#ifdef _WIN32
  wchar_t buf[MAX_PATH];
  if (SUCCEEDED(SHGetFolderPathW(nullptr, CSIDL_LOCAL_APPDATA, nullptr, 0, buf))) return fs::path(buf) / "VoxyEasyAnimator";
  return fs::path("C:/Users/Public/VoxyEasyAnimator");
#else
  const char* h = std::getenv("HOME");
  return fs::path(h ? h : "/tmp") / "Library" / "Application Support" / "VoxyEasyAnimator";
#endif
}

A_Err IdleHook(AEGP_GlobalRefcon, AEGP_IdleRefcon, A_long* max_sleepPL) {
  if (g_host) g_host->queue.pump(8);                  // run what the panel asked for, here, on the main thread
  if (max_sleepPL) *max_sleepPL = 20;                 // come back soon so the panel feels responsive
  return A_Err_NONE;
}

A_Err DeathHook(AEGP_GlobalRefcon, AEGP_DeathRefcon) {
  if (g_host) {
    g_host->autosave->end_session();                  // a clean exit: no recovery offer next time
    g_host->bridge->stop();
    g_host->supervisor->stop();
    delete g_host;
    g_host = nullptr;
  }
  return A_Err_NONE;
}
}  // namespace

extern "C" DllExport A_Err EntryPointFunc(struct SPBasicSuite* pica_basicP, A_long major_versionL, A_long minor_versionL,
                                          AEGP_PluginID aegp_plugin_id, AEGP_GlobalRefcon* global_refconP) {
  A_Err err = A_Err_NONE;
  try {
    AEGP_SuiteHandler suites(pica_basicP);
    auto h = std::make_unique<Host>();
    fs::path dir = data_dir();
    std::error_code ec;
    fs::create_directories(dir, ec);
    h->api = std::make_unique<AeApiSdk>(pica_basicP, aegp_plugin_id);
    h->journal = std::make_unique<Journal>(dir / "journal.json");
    h->autosave = std::make_unique<AutosaveManager>(dir);
    h->commands = std::make_unique<Commands>(*h->api, *h->journal, *h->autosave, dir);
    h->supervisor = std::make_unique<ServiceSupervisor>(dir);
    h->bridge = std::make_unique<Bridge>(*h->commands, h->queue, dir / "bridge.json");
    ServiceSupervisor* sup = h->supervisor.get();
    h->bridge->set_service_starter([sup](const json&) {
#ifdef _WIN32
      int pid = static_cast<int>(GetCurrentProcessId());
#else
      int pid = static_cast<int>(getpid());
#endif
      return sup->ensure(sup->default_command(), pid, 40000);
    });
    if (!h->bridge->start()) return A_Err_GENERIC;
    g_host = h.release();
    ERR(suites.RegisterSuite5()->AEGP_RegisterIdleHook(aegp_plugin_id, IdleHook, nullptr));
    ERR(suites.RegisterSuite5()->AEGP_RegisterDeathHook(aegp_plugin_id, DeathHook, nullptr));
  } catch (...) {
    err = A_Err_GENERIC;
  }
  return err;
}
