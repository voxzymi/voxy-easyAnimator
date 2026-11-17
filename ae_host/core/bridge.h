// The local door the panel talks to. Binds 127.0.0.1 only, needs a per-session token, and runs every command on the
// After Effects main thread (the AEGP idle hook calls MainThreadQueue::pump()). It exposes no way to run code or read
// arbitrary files: only the named commands in commands.h.
#pragma once
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <deque>
#include <functional>
#include <memory>
#include <mutex>
#include <thread>

#include "commands.h"
#include "net.h"

namespace voxy {

class MainThreadQueue {
 public:
  using Task = std::function<json()>;
  // Called from the bridge's threads: queue the task and wait for the main thread to run it.
  json run(Task t, std::chrono::milliseconds timeout);
  // Called from the main thread: run what is waiting. Returns how many tasks ran.
  int pump(int max_tasks = 8);

 private:
  struct Item {
    Task task;
    std::mutex m;
    std::condition_variable cv;
    bool done = false, cancelled = false;
    json result;
  };
  std::mutex m_;
  std::deque<std::shared_ptr<Item>> q_;
};

class Bridge {
 public:
  Bridge(Commands& cmds, MainThreadQueue& queue, fs::path info_file, std::string token = "");
  ~Bridge();
  bool start(int port = 0);
  void stop();
  int port() const { return port_; }
  const std::string& token() const { return token_; }
  void set_command_timeout(std::chrono::milliseconds t) { timeout_ = t; }
  // "ensure_service" does not touch the AE project, so it runs on the bridge's own thread (starting the ML service can
  // take seconds and must not block After Effects).
  void set_service_starter(std::function<json(const json&)> fn) { service_starter_ = std::move(fn); }

 private:
  void accept_loop();
  void handle(net::Socket s);
  Commands& cmds_;
  MainThreadQueue& queue_;
  fs::path info_file_;
  std::string token_;
  int port_ = 0;
  net::Socket listener_ = net::kInvalid;
  std::thread thread_;
  std::atomic<bool> running_{false};
  std::atomic<int> active_{0};
  std::chrono::milliseconds timeout_{60000};
  std::function<json(const json&)> service_starter_;
};

}  // namespace voxy
