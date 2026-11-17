// The commands the panel can send. Each one that changes the project is exactly one undo step.
#pragma once
#include <string>
#include <vector>

#include "ae_api.h"
#include "autosave.h"
#include "journal.h"
#include "plan.h"

namespace voxy {

class Commands {
 public:
  Commands(AeApi& api, Journal& journal, AutosaveManager& autosave, fs::path data_dir);

  json selection();
  json export_frame(const json& args);          // {path, [layer_id], [time]}
  json add_to_project(const json& req);         // {manifest, output_dir, visible_ids[], options{}}
  json restore();
  json apply_upscale(const json& req);          // {layer_id, image_path, [factor]}
  json save_project();
  json autosave();
  json session_begin();
  json session_end();
  json recovery_find();
  json recovery_open(const json& req);          // {backup}: only backups Voxy itself wrote
  json backups(const json& req);

  // Only touch files inside these folders (Voxy's data folder in the real plug-in). Empty = no limit (tests).
  void set_allowed_roots(std::vector<fs::path> roots) { roots_ = std::move(roots); }

  // Entry point used by the bridge. Returns {ok:true,result} or {ok:false,error}.
  json dispatch(const std::string& cmd, const json& args);

 private:
  AeApi& api_;
  Journal& journal_;
  AutosaveManager& autosave_;
  fs::path data_dir_;
  std::vector<fs::path> roots_;
  void require_allowed(const std::string& path) const;
};

}  // namespace voxy
