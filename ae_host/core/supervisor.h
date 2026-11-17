// Starts the local ML service (a separate program) and finds out where it is listening.
#pragma once
#include <string>
#include <vector>

#include "types.h"
#include "util.h"

namespace voxy {

class ServiceSupervisor {
 public:
  explicit ServiceSupervisor(fs::path data_dir) : data_dir_(std::move(data_dir)) {}
  ~ServiceSupervisor() { stop(); }
  // Reuse a running service, or start ``command`` (program + arguments) and wait until it is healthy.
  // Returns {port, token, pid, started}. Throws SERVICE_START_FAILED.
  json ensure(const std::vector<std::string>& command, int parent_pid, int timeout_ms = 30000);
  // The default command from <data>/install.json written by the installer: {"service": ["path\\to\\voxy-service.exe"]}.
  std::vector<std::string> default_command() const;
  void stop();

 private:
  bool healthy(const json& info) const;
  fs::path data_dir_;
  long long child_ = 0;   // process id (POSIX) or handle (Windows) of a service we started
};

}  // namespace voxy
