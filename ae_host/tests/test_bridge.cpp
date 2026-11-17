#include <thread>

#include "../core/bridge.h"
#include "../core/net.h"
#include "../core/supervisor.h"
#include "env.h"

namespace {
struct Pump {          // plays the part of After Effects' main thread: runs queued commands
  MainThreadQueue& q;
  std::atomic<bool> stop{false};
  std::thread t;
  explicit Pump(MainThreadQueue& queue) : q(queue), t([this] { while (!stop) { q.pump(); std::this_thread::sleep_for(std::chrono::milliseconds(2)); } }) {}
  ~Pump() { stop = true; t.join(); }
};
json cmd(int port, const std::string& token, json body, int* status = nullptr) {
  auto r = net::http_request(port, "POST", "/v1/cmd", body.dump(), token, 8000);
  if (status) *status = r.status;
  return json::parse(r.body, nullptr, false);
}
}  // namespace

TEST(bridge_needs_the_session_token) {
  Env e;
  MainThreadQueue q;
  Bridge b(e.cmds, q, e.data / "bridge.json", "secret");
  CHECK(b.start());
  Pump pump(q);
  int st = 0;
  json r = cmd(b.port(), "", {{"cmd", "ping"}}, &st);
  CHECK_EQ(st, 401); CHECK_EQ(r["error"]["code"].get<std::string>(), std::string("UNAUTHORIZED"));
  cmd(b.port(), "wrong", {{"cmd", "ping"}}, &st);
  CHECK_EQ(st, 401);
  r = cmd(b.port(), "secret", {{"cmd", "ping"}}, &st);
  CHECK_EQ(st, 200); CHECK(r["ok"].get<bool>() && r["result"]["pong"].get<bool>());
  CHECK_EQ(net::http_request(b.port(), "GET", "/v1/ping").status, 200);           // ping needs no token, reveals nothing
}

TEST(bridge_only_talks_to_localhost_names) {
  Env e;
  MainThreadQueue q;
  Bridge b(e.cmds, q, "", "secret");
  CHECK(b.start());
  auto r = net::http_request(b.port(), "POST", "/v1/cmd", "{\"cmd\":\"ping\"}", "secret", 3000, "evil.example.com");
  CHECK_EQ(r.status, 401);
}

TEST(bridge_writes_and_removes_the_info_file) {
  Env e;
  MainThreadQueue q;
  fs::path info = e.data / "bridge.json";
  {
    Bridge b(e.cmds, q, info, "tok");
    CHECK(b.start());
    json j = read_json(info, json());
    CHECK_EQ((int)j["port"], b.port()); CHECK_EQ(j["token"].get<std::string>(), std::string("tok"));
  }
  CHECK(!fs::exists(info));
}

TEST(bridge_runs_every_command_on_the_main_thread) {
  Env e;
  MainThreadQueue q;
  Bridge b(e.cmds, q, "", "t");
  CHECK(b.start());
  std::thread::id main_id;
  std::atomic<bool> stop{false};
  std::thread ae_main([&] { main_id = std::this_thread::get_id(); while (!stop) { q.pump(); std::this_thread::sleep_for(std::chrono::milliseconds(2)); } });
  json r = cmd(b.port(), "t", {{"cmd", "selection"}});
  stop = true; ae_main.join();
  CHECK(r["ok"].get<bool>());
  CHECK_EQ(r["result"]["layer_name"].get<std::string>(), std::string("Hero"));
  CHECK(e.ae.last_thread == main_id);                                  // the AE API was only ever touched on the main thread
  CHECK(e.ae.last_thread != std::this_thread::get_id());
}

TEST(bridge_add_to_project_end_to_end) {
  Env e;
  MainThreadQueue q;
  Bridge b(e.cmds, q, "", "t");
  CHECK(b.start());
  Pump pump(q);
  json r = cmd(b.port(), "t", {{"cmd", "add_to_project"}, {"manifest", e.make}, {"output_dir", path_str(e.make_dir)},
                               {"visible_ids", e.all_ids(e.make, false)}, {"options", {{"container", "precomp"}}}});
  CHECK(r["ok"].get<bool>());
  CHECK_EQ(e.ae.undo_groups.size(), (size_t)1);
  json r2 = cmd(b.port(), "t", {{"cmd", "restore"}});
  CHECK(r2["ok"].get<bool>() && r2["result"]["removed_layers"].get<int>() > 0);
}

TEST(bridge_gives_up_politely_when_the_main_thread_is_busy) {
  Env e;
  MainThreadQueue q;                                                   // nobody pumps: After Effects is busy
  Bridge b(e.cmds, q, "", "t");
  b.set_command_timeout(std::chrono::milliseconds(300));
  CHECK(b.start());
  int st = 0;
  json r = cmd(b.port(), "t", {{"cmd", "selection"}}, &st);
  CHECK(!r["ok"].get<bool>() && r["error"]["code"].get<std::string>() == "TIMEOUT");
  CHECK(r["error"]["what_to_do"].is_string());
  int ran = q.pump();                                                  // when AE is free again the abandoned command is NOT run
  CHECK_EQ(ran, 1);
  CHECK(e.ae.last_thread == std::thread::id());
}

TEST(bridge_rejects_bad_requests_without_crashing) {
  Env e;
  MainThreadQueue q;
  Bridge b(e.cmds, q, "", "t");
  CHECK(b.start());
  Pump pump(q);
  auto r = net::http_request(b.port(), "POST", "/v1/cmd", "{not json", "t");
  CHECK_EQ(r.status, 400);
  r = net::http_request(b.port(), "POST", "/v1/cmd", "{\"nocmd\":1}", "t");
  CHECK_EQ(r.status, 400);
  r = net::http_request(b.port(), "GET", "/v1/other", "", "t");
  CHECK_EQ(r.status, 404);
  CHECK(cmd(b.port(), "t", {{"cmd", "ping"}})["ok"].get<bool>());       // still alive
}

// ------------------------------------------------------------------ service supervisor
static std::vector<std::string> fake_service(const std::string& extra = "") {
  const char* py = std::getenv("VOXY_PYTHON");
  std::vector<std::string> c = {py ? py : "python3", std::string(std::getenv("VOXY_TESTS_DIR")) + "/fake_service.py"};
  if (!extra.empty()) c.push_back(extra);
  return c;
}

TEST(supervisor_starts_the_service_reuses_it_and_stops_it) {
  Env e;
  ServiceSupervisor sup(e.data);
  json a = sup.ensure(fake_service(), current_pid(), 15000);
  CHECK(a["started"].get<bool>() && a["port"].get<int>() > 0 && a["token"] == "tok");
  CHECK_EQ(net::http_request(a["port"], "GET", "/v1/health").status, 200);
  json b = sup.ensure(fake_service(), current_pid(), 15000);
  CHECK(!b["started"].get<bool>() && b["port"] == a["port"]);          // second call reuses the running service
  sup.stop();
  std::this_thread::sleep_for(std::chrono::milliseconds(300));
  CHECK_EQ(net::http_request(a["port"], "GET", "/v1/health", "", "", 500).status, 0);
}

TEST(supervisor_reports_a_service_that_will_not_start) {
  Env e;
  ServiceSupervisor sup(e.data);
  CHECK_CODE(sup.ensure({}, current_pid(), 1000), "SERVICE_START_FAILED");
  CHECK_CODE(sup.ensure(fake_service("--die"), current_pid(), 5000), "SERVICE_START_FAILED");
  CHECK_CODE(sup.ensure({"definitely-not-a-program-xyz"}, current_pid(), 3000), "SERVICE_START_FAILED");
}
