// Shared fixture for the host tests: a fake After Effects with a composition, one user layer, a saved project file,
// and the Make Layers / subdivision results that the Python service produced (tests/make_fixture.py).
#pragma once
#include <fstream>

#include "../core/autosave.h"
#include "../core/commands.h"
#include "../core/journal.h"
#include "../core/plan.h"
#include "fake_ae_api.h"
#include "mini_test.h"

using namespace voxy;

namespace testenv {
struct Env {
  fs::path tmp, data, fixture;
  json make, subdiv;
  fs::path make_dir, subdiv_dir;
  FakeAe ae;
  Journal journal;
  AutosaveManager autosave;
  Commands cmds;
  int64_t comp = 0, user_layer = 0;

  static fs::path fixture_dir() {
    const char* e = std::getenv("VOXY_FIXTURE");
    if (!e) throw std::runtime_error("set VOXY_FIXTURE to the folder made by make_fixture.py");
    return e;
  }
  static fs::path fresh_tmp() {
    static int n = 0;
    fs::path p = fs::temp_directory_path() / ("voxy-test-" + std::to_string(current_pid()) + "-" + std::to_string(++n));
    fs::remove_all(p);
    fs::create_directories(p);
    return p;
  }
  Env(fs::path t = fresh_tmp())
      : tmp(t), data(t / "data"), fixture(fixture_dir()), make_dir(fixture / "make_layers"), subdiv_dir(fixture / "subdivide"),
        journal(data / "journal.json"), autosave(data), cmds(ae, journal, autosave, data) {
    make = read_json(make_dir / "manifest.json", json());
    subdiv = read_json(subdiv_dir / "manifest.json", json());
    comp = ae.add_user_comp("Main Comp", 400, 640);
    user_layer = ae.add_user_layer(comp, "Hero");
    ae.path = path_str(tmp / "MyProject.aep");
    write_file_atomic(ae.path, FakeAe::aep_bytes("manual"));
    ae.dirty = true;
  }
  std::vector<std::string> all_ids(const json& m, bool bg = true) {
    std::vector<std::string> v;
    for (auto& p : m["parts"]) v.push_back(p["id"]);
    if (bg && m.contains("background") && !m["background"].is_null()) v.push_back("background");
    return v;
  }
  json add(const json& m, const fs::path& dir, std::vector<std::string> ids, json options = json::object()) {
    return cmds.add_to_project({{"manifest", m}, {"output_dir", path_str(dir)}, {"visible_ids", ids}, {"options", options}});
  }
};
inline std::vector<std::string> names_in(FakeAe& ae, int64_t comp) {
  std::vector<std::string> v;
  for (auto l : ae.layers_in(comp)) v.push_back(ae.layers[l].name);
  return v;
}
}  // namespace testenv
using namespace testenv;
