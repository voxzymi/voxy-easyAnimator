#include "supervisor.h"

#include <chrono>
#include <thread>

#include "net.h"

#ifdef _WIN32
#include <windows.h>
#else
#include <signal.h>
#include <sys/wait.h>
#include <unistd.h>
#endif

namespace voxy {

bool ServiceSupervisor::healthy(const json& info) const {
  if (!info.is_object() || !info.contains("port")) return false;
  auto r = net::http_request(info["port"], "GET", "/v1/health", "", "", 2000);
  return r.status == 200;
}

std::vector<std::string> ServiceSupervisor::default_command() const {
  json j = read_json(data_dir_ / "install.json", json::object());
  std::vector<std::string> cmd;
  if (j.contains("service") && j["service"].is_array())
    for (auto& s : j["service"]) cmd.push_back(s.get<std::string>());
  return cmd;
}

json ServiceSupervisor::ensure(const std::vector<std::string>& command, int parent_pid, int timeout_ms) {
  const fs::path info_path = data_dir_ / "service.json";
  json info = read_json(info_path, json::object());
  if (info.is_object() && info.contains("pid") && pid_alive(info["pid"]) && healthy(info)) {
    info["started"] = false;
    return info;
  }
  if (command.empty()) throw make_error("SERVICE_START_FAILED", "no service command configured");
  std::error_code ec;
  fs::create_directories(data_dir_, ec);
  fs::remove(info_path, ec);
  std::vector<std::string> args = command;
  args.push_back("--parent-pid");
  args.push_back(std::to_string(parent_pid));
  args.push_back("--data-dir");
  args.push_back(path_str(data_dir_));
  args.push_back("--info-file");
  args.push_back(path_str(info_path));
#ifdef _WIN32
  std::wstring cmdline;
  for (auto& a : args) {
    std::wstring w(a.begin(), a.end());
    cmdline += L"\"" + w + L"\" ";
  }
  STARTUPINFOW si{};
  si.cb = sizeof si;
  PROCESS_INFORMATION pi{};
  if (!CreateProcessW(nullptr, cmdline.data(), nullptr, nullptr, FALSE, CREATE_NO_WINDOW, nullptr, nullptr, &si, &pi))
    throw make_error("SERVICE_START_FAILED", "CreateProcess failed");
  CloseHandle(pi.hThread);
  child_ = reinterpret_cast<long long>(pi.hProcess);
#else
  pid_t pid = fork();
  if (pid < 0) throw make_error("SERVICE_START_FAILED", "fork failed");
  if (pid == 0) {
    std::vector<char*> argv;
    for (auto& a : args) argv.push_back(const_cast<char*>(a.c_str()));
    argv.push_back(nullptr);
    setsid();
    execvp(argv[0], argv.data());
    _exit(127);
  }
  child_ = pid;
#endif
  auto deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(timeout_ms);
  while (std::chrono::steady_clock::now() < deadline) {
    info = read_json(info_path, json::object());
    if (healthy(info)) {
      info["started"] = true;
      return info;
    }
#ifndef _WIN32
    int st = 0;
    if (waitpid(static_cast<pid_t>(child_), &st, WNOHANG) != 0) { child_ = 0; throw make_error("SERVICE_START_FAILED", "service exited"); }
#endif
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
  }
  stop();
  throw make_error("SERVICE_START_FAILED", "service did not become healthy in time");
}

void ServiceSupervisor::stop() {
  if (!child_) return;
#ifdef _WIN32
  TerminateProcess(reinterpret_cast<HANDLE>(child_), 0);
  CloseHandle(reinterpret_cast<HANDLE>(child_));
#else
  kill(static_cast<pid_t>(child_), SIGTERM);
  for (int i = 0; i < 30; ++i) {
    int st;
    if (waitpid(static_cast<pid_t>(child_), &st, WNOHANG) != 0) { child_ = 0; return; }
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
  }
  kill(static_cast<pid_t>(child_), SIGKILL);
  waitpid(static_cast<pid_t>(child_), nullptr, 0);
#endif
  child_ = 0;
}

}  // namespace voxy
