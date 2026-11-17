// Shared types for the Voxy EasyAnimator AE host. Portable C++17: no After Effects SDK here.
#pragma once
#include <cstdint>
#include <stdexcept>
#include <string>
#include <vector>

#include "../third_party/nlohmann/json.hpp"

namespace voxy {

using json = nlohmann::json;

struct Point {
  double x = 0, y = 0;
};

struct Polygon {
  std::vector<Point> points;
  bool hole = false;
};

// What the user has selected right now in After Effects.
struct Selection {
  bool has_project = false;
  int64_t project_id = 0;
  bool has_comp = false;
  int64_t comp_id = 0;
  std::string comp_name;
  int comp_w = 0, comp_h = 0;
  double fps = 24.0, duration = 0.0, time = 0.0;
  int selected_layers = 0;
  bool has_layer = false;  // exactly one layer selected
  int64_t layer_id = 0;
  std::string layer_name;
  std::string source_kind;  // still | video | precomp | solid | other
  std::string signature;    // changes when the comp, layer or time changes
};

// An error the panel can show: what happened, what to do, which help topic. No stack traces.
class HostError : public std::runtime_error {
 public:
  HostError(std::string code, std::string detail = "");
  std::string code;
  std::string detail;
  json to_json() const;
};

HostError make_error(const std::string& code, const std::string& detail = "");

}  // namespace voxy
