#include "util.h"

#include <chrono>
#include <cstdio>
#include <ctime>
#include <fstream>
#include <random>
#include <sstream>

#ifdef _WIN32
#include <windows.h>
#else
#include <signal.h>
#include <unistd.h>
#endif

namespace voxy {

static std::string fmt_time(const char* f) {
  std::time_t t = std::time(nullptr);
  std::tm tm{};
#ifdef _WIN32
  localtime_s(&tm, &t);
#else
  localtime_r(&t, &tm);
#endif
  char buf[64];
  std::strftime(buf, sizeof buf, f, &tm);
  return buf;
}
std::string now_stamp() { return fmt_time("%Y%m%d-%H%M%S"); }
std::string iso_now() { return fmt_time("%Y-%m-%dT%H:%M:%S"); }

std::string read_file(const fs::path& p) {
  std::ifstream f(p, std::ios::binary);
  if (!f) return "";
  std::ostringstream ss;
  ss << f.rdbuf();
  return ss.str();
}

bool write_file_atomic(const fs::path& p, const std::string& data) {
  std::error_code ec;
  fs::create_directories(p.parent_path(), ec);
  fs::path tmp = p;
  tmp += ".tmp";
  {
    std::ofstream f(tmp, std::ios::binary | std::ios::trunc);
    if (!f) return false;
    f.write(data.data(), static_cast<std::streamsize>(data.size()));
    f.flush();
    if (!f) return false;
  }
  fs::rename(tmp, p, ec);
  if (ec) {
    fs::remove(tmp, ec);
    return false;
  }
  return true;
}

json read_json(const fs::path& p, const json& fallback) {
  std::string s = read_file(p);
  if (s.empty()) return fallback;
  try {
    return json::parse(s);
  } catch (...) {
    return fallback;
  }
}

bool write_json_atomic(const fs::path& p, const json& j) { return write_file_atomic(p, j.dump(1)); }

std::string random_token(size_t bytes) {
  static const char* a = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
  std::random_device rd;
  std::string s;
  for (size_t i = 0; i < bytes * 4 / 3 + 1; ++i) s += a[rd() % 64];
  return s;
}

int current_pid() {
#ifdef _WIN32
  return static_cast<int>(GetCurrentProcessId());
#else
  return static_cast<int>(getpid());
#endif
}

bool pid_alive(int pid) {
  if (pid <= 0) return false;
#ifdef _WIN32
  HANDLE h = OpenProcess(SYNCHRONIZE, FALSE, static_cast<DWORD>(pid));
  if (!h) return false;
  bool alive = WaitForSingleObject(h, 0) == WAIT_TIMEOUT;
  CloseHandle(h);
  return alive;
#else
  return kill(pid, 0) == 0;
#endif
}

std::string path_str(const fs::path& p) {
#if defined(__cpp_lib_char8_t)
  auto u = p.u8string();
  return std::string(u.begin(), u.end());
#else
  return p.u8string();
#endif
}

}  // namespace voxy
