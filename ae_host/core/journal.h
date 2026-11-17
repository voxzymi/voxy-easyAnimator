// Remembers what each Add to Project created, so Restore can remove exactly that and nothing else.
#pragma once
#include <optional>
#include <string>
#include <vector>

#include "types.h"
#include "util.h"

namespace voxy {

struct LayerRef {
  int64_t layer_id = 0;
  int64_t comp_id = 0;
  std::string name;
  bool part = false;       // a part layer (as opposed to a group's wrapper layer or the background)
};
struct ItemRef {
  int64_t id = 0;
  std::string name;
  std::string kind;  // folder | comp | footage
};

struct AddRecord {
  std::string id;
  std::string created_at;
  int64_t project_id = 0;
  std::string project_path;
  int64_t main_comp_id = 0;
  std::string container;           // precomp | folder
  std::vector<LayerRef> layers;    // every layer we created, in any comp
  std::vector<ItemRef> items;      // folders, comps and footage we created, in creation order
  std::vector<LayerRef> disabled;  // layers we turned off (a replaced single Hair layer)
  bool restored = false;

  json to_json() const;
  static AddRecord from_json(const json& j);
};

class Journal {
 public:
  explicit Journal(fs::path file);
  void append(const AddRecord& r);
  // The last Add to Project in this project that has not been restored.
  std::optional<AddRecord> last_active(const std::string& project_path, int64_t project_id) const;
  // The single "Hair" layer added by an earlier Add in this project (for "replace").
  std::vector<LayerRef> hair_layers(const std::string& project_path, int64_t project_id) const;
  void mark_restored(const std::string& id);
  const std::vector<AddRecord>& all() const { return records_; }

 private:
  void load();
  void save() const;
  fs::path file_;
  std::vector<AddRecord> records_;
};

}  // namespace voxy
