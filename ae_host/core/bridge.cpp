#include "bridge.h"

#include <algorithm>
#include <cctype>
#include <map>
#include <sstream>

namespace voxy {

json MainThreadQueue::run(Task t, std::chrono::milliseconds timeout) {
  auto it = std::make_shared<Item>();
  it->task = std::move(t);
  {
    std::lock_guard<std::mutex> g(m_);
    q_.push_back(it);
  }
  std::unique_lock<std::mutex> lk(it->m);
  if (!it->cv.wait_for(lk, timeout, [&] { return it->done; })) {
    it->cancelled = true;                       // never run it later: the caller gave up
    return {{"ok", false}, {"error", make_error("TIMEOUT").to_json()}};
  }
  return it->result;
}

int MainThreadQueue::pump(int max_tasks) {
  int n = 0;
  while (n < max_tasks) {
    std::shared_ptr<Item> it;
    {
      std::lock_guard<std::mutex> g(m_);
      if (q_.empty()) break;
      it = q_.front();
      q_.pop_front();
    }
    std::unique_lock<std::mutex> lk(it->m);
    if (!it->cancelled) {
      lk.unlock();
      json r;
      try {
        r = it->task();
      } catch (const std::exception& e) {
        r = {{"ok", false}, {"error", make_error("INTERNAL", e.what()).to_json()}};
      }
      lk.lock();
      it->result = std::move(r);
    }
    it->done = true;
    it->cv.notify_all();
    ++n;
  }
  return n;
}

Bridge::Bridge(Commands& cmds, MainThreadQueue& queue, fs::path info_file, std::string token)
    : cmds_(cmds), queue_(queue), info_file_(std::move(info_file)), token_(token.empty() ? random_token(24) : std::move(token)) {}

Bridge::~Bridge() { stop(); }

bool Bridge::start(int port) {
  listener_ = net::listen_local(port, &port_);
  if (listener_ == net::kInvalid) return false;
  running_ = true;
  thread_ = std::thread([this] { accept_loop(); });
  if (!info_file_.empty()) write_json_atomic(info_file_, {{"port", port_}, {"token", token_}, {"pid", current_pid()}});
  return true;
}

void Bridge::stop() {
  if (!running_.exchange(false)) return;
  if (thread_.joinable()) thread_.join();
  net::close_socket(listener_);
  listener_ = net::kInvalid;
  for (int i = 0; i < 200 && active_ > 0; ++i) std::this_thread::sleep_for(std::chrono::milliseconds(10));
  std::error_code ec;
  if (!info_file_.empty()) fs::remove(info_file_, ec);
}

void Bridge::accept_loop() {
  while (running_) {
    net::Socket c = net::accept_with_timeout(listener_, 100);
    if (c == net::kInvalid) continue;
    ++active_;
    std::thread([this, c] {
      handle(c);
      net::close_socket(c);
      --active_;
    }).detach();
  }
}

static std::string lower(std::string s) {
  std::transform(s.begin(), s.end(), s.begin(), [](unsigned char c) { return std::tolower(c); });
  return s;
}

static std::string reply(int status, const std::string& reason, const std::string& body) {
  std::ostringstream o;
  o << "HTTP/1.1 " << status << " " << reason << "\r\nContent-Type: application/json\r\nContent-Length: " << body.size()
    << "\r\nAccess-Control-Allow-Origin: *\r\nAccess-Control-Allow-Headers: Content-Type, X-Voxy-Token\r\n"
       "Access-Control-Allow-Methods: GET, POST, OPTIONS\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n" << body;
  return o.str();
}

void Bridge::handle(net::Socket s) {
  net::set_recv_timeout(s, 5000);
  std::string data;
  char buf[8192];
  size_t header_end = std::string::npos;
  while (header_end == std::string::npos && data.size() < 65536) {
    long n = net::recv_some(s, buf, sizeof buf);
    if (n <= 0) return;
    data.append(buf, static_cast<size_t>(n));
    header_end = data.find("\r\n\r\n");
  }
  if (header_end == std::string::npos) return;
  std::istringstream hs(data.substr(0, header_end));
  std::string method, path, ver;
  hs >> method >> path >> ver;
  std::map<std::string, std::string> h;
  std::string line;
  std::getline(hs, line);
  while (std::getline(hs, line)) {
    if (!line.empty() && line.back() == '\r') line.pop_back();
    size_t c = line.find(':');
    if (c == std::string::npos) continue;
    std::string v = line.substr(c + 1);
    v.erase(0, v.find_first_not_of(" \t"));
    h[lower(line.substr(0, c))] = v;
  }
  size_t want = h.count("content-length") ? static_cast<size_t>(std::strtoull(h["content-length"].c_str(), nullptr, 10)) : 0;
  if (want > 32u * 1024 * 1024) { net::send_all(s, reply(413, "Too Large", "{}")); return; }
  std::string body = data.substr(header_end + 4);
  while (body.size() < want) {
    long n = net::recv_some(s, buf, sizeof buf);
    if (n <= 0) return;
    body.append(buf, static_cast<size_t>(n));
  }
  body.resize(want);

  std::string host = lower(h["host"]);
  host = host.substr(0, host.find(':'));
  if (!host.empty() && host != "127.0.0.1" && host != "localhost" && host != "[::1]") {      // DNS-rebinding guard
    net::send_all(s, reply(401, "Unauthorized", json{{"ok", false}, {"error", make_error("UNAUTHORIZED", "host").to_json()}}.dump()));
    return;
  }
  if (method == "OPTIONS") { net::send_all(s, reply(204, "No Content", "")); return; }
  if (method == "GET" && path == "/v1/ping") { net::send_all(s, reply(200, "OK", json{{"pong", true}}.dump())); return; }
  if (h["x-voxy-token"] != token_) {
    net::send_all(s, reply(401, "Unauthorized", json{{"ok", false}, {"error", make_error("UNAUTHORIZED").to_json()}}.dump()));
    return;
  }
  if (method == "POST" && path == "/v1/cmd") {
    json req = json::parse(body, nullptr, false);
    if (req.is_discarded() || !req.is_object() || !req.contains("cmd") || !req["cmd"].is_string()) {
      net::send_all(s, reply(400, "Bad Request", json{{"ok", false}, {"error", make_error("BAD_COMMAND", "invalid request").to_json()}}.dump()));
      return;
    }
    std::string cmd = req["cmd"];
    req.erase("cmd");
    json out = queue_.run([this, cmd, req] { return cmds_.dispatch(cmd, req); }, timeout_);
    net::send_all(s, reply(200, "OK", out.dump()));
    return;
  }
  net::send_all(s, reply(404, "Not Found", json{{"ok", false}, {"error", make_error("BAD_COMMAND", "no such route").to_json()}}.dump()));
}

}  // namespace voxy
