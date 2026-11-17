#include "journal.h"

namespace voxy {

static json layers_json(const std::vector<LayerRef>& v) {
  json a = json::array();
  for (auto& l : v) a.push_back({{"layer_id", l.layer_id}, {"comp_id", l.comp_id}, {"name", l.name}, {"part", l.part}});
  return a;
}
static std::vector<LayerRef> layers_from(const json& a) {
  std::vector<LayerRef> v;
  for (auto& j : a) v.push_back({j.value("layer_id", int64_t(0)), j.value("comp_id", int64_t(0)), j.value("name", ""), j.value("part", false)});
  return v;
}

json AddRecord::to_json() const {
  json items_j = json::array();
  for (auto& i : items) items_j.push_back({{"id", i.id}, {"name", i.name}, {"kind", i.kind}});
  return {{"id", id}, {"created_at", created_at}, {"project_id", project_id}, {"project_path", project_path},
          {"main_comp_id", main_comp_id}, {"container", container}, {"layers", layers_json(layers)},
          {"items", items_j}, {"disabled", layers_json(disabled)}, {"restored", restored}};
}

AddRecord AddRecord::from_json(const json& j) {
  AddRecord r;
  r.id = j.value("id", "");
  r.created_at = j.value("created_at", "");
  r.project_id = j.value("project_id", int64_t(0));
  r.project_path = j.value("project_path", "");
  r.main_comp_id = j.value("main_comp_id", int64_t(0));
  r.container = j.value("container", "");
  r.layers = layers_from(j.value("layers", json::array()));
  for (auto& i : j.value("items", json::array())) r.items.push_back({i.value("id", int64_t(0)), i.value("name", ""), i.value("kind", "")});
  r.disabled = layers_from(j.value("disabled", json::array()));
  r.restored = j.value("restored", false);
  return r;
}

Journal::Journal(fs::path file) : file_(std::move(file)) { load(); }

void Journal::load() {
  json j = read_json(file_, json::array());
  if (!j.is_array()) return;
  for (auto& r : j) records_.push_back(AddRecord::from_json(r));
}

void Journal::save() const {
  json a = json::array();
  size_t start = records_.size() > 50 ? records_.size() - 50 : 0;     // keep the file small
  for (size_t i = start; i < records_.size(); ++i) a.push_back(records_[i].to_json());
  write_json_atomic(file_, a);
}

void Journal::append(const AddRecord& r) {
  records_.push_back(r);
  save();
}

static bool same_project(const AddRecord& r, const std::string& path, int64_t id) {
  if (!path.empty() && !r.project_path.empty()) return r.project_path == path;
  return r.project_id == id;                                           // untitled project: same session only
}

std::optional<AddRecord> Journal::last_active(const std::string& project_path, int64_t project_id) const {
  for (auto it = records_.rbegin(); it != records_.rend(); ++it)
    if (!it->restored && same_project(*it, project_path, project_id)) return *it;
  return std::nullopt;
}

std::vector<LayerRef> Journal::hair_layers(const std::string& project_path, int64_t project_id) const {
  std::vector<LayerRef> out;
  for (auto& r : records_) {
    if (r.restored || !same_project(r, project_path, project_id)) continue;
    for (auto& l : r.layers)
      if (l.part && l.name == "Hair") out.push_back(l);   // the single Hair part, not the group wrapper
  }
  return out;
}

void Journal::mark_restored(const std::string& id) {
  for (auto& r : records_)
    if (r.id == id) r.restored = true;
  save();
}

}  // namespace voxy
