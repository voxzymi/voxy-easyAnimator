#include "commands.h"

#include <algorithm>
#include <set>

namespace voxy {

Commands::Commands(AeApi& api, Journal& journal, AutosaveManager& autosave, fs::path data_dir)
    : api_(api), journal_(journal), autosave_(autosave), data_dir_(std::move(data_dir)) {}

static json selection_json(const Selection& s) {
  return {{"has_project", s.has_project}, {"project_id", s.project_id}, {"has_comp", s.has_comp}, {"comp_id", s.comp_id},
          {"comp_name", s.comp_name}, {"width", s.comp_w}, {"height", s.comp_h}, {"fps", s.fps}, {"duration", s.duration},
          {"time", s.time}, {"selected_layers", s.selected_layers}, {"has_layer", s.has_layer}, {"layer_id", s.layer_id},
          {"layer_name", s.layer_name}, {"source_kind", s.source_kind}, {"signature", s.signature}};
}

json Commands::selection() { return selection_json(api_.get_selection()); }

json Commands::export_frame(const json& args) {
  Selection s = api_.get_selection();
  if (!s.has_project) throw make_error("NO_PROJECT");
  if (!s.has_comp) throw make_error("NO_COMP");
  int64_t layer = args.value("layer_id", int64_t(0));
  if (layer == 0) {
    if (s.selected_layers > 1) throw make_error("MANY_LAYERS");
    if (!s.has_layer) throw make_error("NO_LAYER");
    layer = s.layer_id;
  }
  const std::string path = args.at("path");
  const double t = args.value("time", s.time);
  try {
    api_.export_layer_frame_png(layer, t, path);
  } catch (const HostError&) {
    throw;
  } catch (const std::exception& e) {
    throw make_error("FRAME_EXPORT_FAILED", e.what());
  }
  if (!fs::exists(path) || fs::file_size(path) == 0) throw make_error("FRAME_EXPORT_FAILED", "no file written");
  return {{"path", path}, {"width", s.comp_w}, {"height", s.comp_h}, {"layer_id", layer}, {"layer_name", s.layer_name},
          {"time", t}, {"signature", s.signature}};
}

// ------------------------------------------------------------------ Add to Project
json Commands::add_to_project(const json& req) {
  Selection sel = api_.get_selection();
  if (!sel.has_project) throw make_error("NO_PROJECT");
  if (!sel.has_comp) throw make_error("NO_COMP");
  const json& manifest = req.at("manifest");
  if (manifest.contains("source") && !manifest["source"].is_null()) {
    if (manifest["source"].value("width", sel.comp_w) != sel.comp_w || manifest["source"].value("height", sel.comp_h) != sel.comp_h)
      throw make_error("SIZE_MISMATCH");
  }
  std::vector<std::string> visible = req.at("visible_ids").get<std::vector<std::string>>();
  Options opt = Options::from_json(req.value("options", json::object()));
  AddPlan plan = build_add_plan(manifest, req.at("output_dir"), visible, opt);   // validates everything before we touch AE

  AddRecord rec;
  rec.id = random_token(9);
  rec.created_at = iso_now();
  rec.project_id = api_.project_id();
  rec.project_path = api_.project_path();
  rec.main_comp_id = sel.comp_id;
  rec.container = opt.container;
  json result_groups = json::array();
  std::vector<int64_t> disabled;

  auto made_layer = [&](int64_t layer, int64_t comp, const std::string& name, bool part = false) { rec.layers.push_back({layer, comp, name, part}); };
  auto made_item = [&](int64_t id, const std::string& name, const std::string& kind) { rec.items.push_back({id, name, kind}); };

  // if anything fails half way, remove what this command made so the project is left as it was
  auto rollback = [&]() {
    for (auto it = rec.layers.rbegin(); it != rec.layers.rend(); ++it)
      try { if (api_.layer_exists(it->layer_id)) api_.delete_layer(it->layer_id); } catch (...) {}
    for (auto it = rec.items.rbegin(); it != rec.items.rend(); ++it)
      try { if (api_.item_exists(it->id)) api_.delete_item(it->id); } catch (...) {}
  };

  UndoGroup undo(api_, "Voxy: Add to Project");                      // one undo step for everything below
  try {
    const std::string root_name = "Voxy - " + sel.layer_name;
    int64_t root = api_.create_folder(root_name, 0);
    made_item(root, root_name, "folder");
    int64_t parts_folder = 0;
    if (opt.container == "precomp") {
      parts_folder = api_.create_folder("Parts", root);
      made_item(parts_folder, "Parts", "folder");
    }

    if (plan.has_background) {
      int64_t it = api_.import_image(plan.background.image_path, root);
      made_item(it, "Background", "footage");
      int64_t l = api_.add_layer(sel.comp_id, it);
      api_.set_layer_name(l, "Background");
      made_layer(l, sel.comp_id, "Background");
    }

    std::map<std::string, int64_t> layer_of_part;            // part id -> layer id
    std::map<std::string, int64_t> group_layer;              // group name -> its precomp layer in the main comp
    std::map<std::string, int64_t> comp_item_of_group;
    for (auto& g : plan.groups) {
      int64_t target_comp = sel.comp_id, footage_folder = 0;
      if (opt.container == "precomp") {
        target_comp = api_.create_comp(g.name, sel.comp_w, sel.comp_h, sel.fps, sel.duration, root);
        made_item(target_comp, g.name, "comp");
        comp_item_of_group[g.name] = target_comp;
        footage_folder = parts_folder;
      } else {
        footage_folder = api_.create_folder(g.name, root);
        made_item(footage_folder, g.name, "folder");
      }
      json parts_json = json::array();
      for (auto& p : g.parts) {
        int64_t fi = api_.import_image(p.image_path, footage_folder);
        made_item(fi, fs::path(p.image_path).filename().string(), "footage");
        int64_t l = api_.add_layer(target_comp, fi);
        made_layer(l, target_comp, p.name, true);
        api_.set_layer_name(l, p.name);
        api_.set_layer_anchor(l, p.pivot.x - p.bbox_x, p.pivot.y - p.bbox_y);
        api_.set_layer_position(l, p.pivot.x, p.pivot.y);
        api_.set_layer_label(l, g.label);
        if (!p.outline.empty()) api_.add_masks(l, p.outline);
        layer_of_part[p.id] = l;
        parts_json.push_back({{"id", p.id}, {"name", p.name}, {"layer_id", l}});
      }
      int64_t gl = 0;
      if (opt.container == "precomp") {
        gl = api_.add_layer(sel.comp_id, target_comp);
        api_.set_layer_name(gl, g.name);
        api_.set_layer_label(gl, g.label);
        made_layer(gl, sel.comp_id, g.name);
        group_layer[g.name] = gl;
      }
      result_groups.push_back({{"name", g.name}, {"item_id", opt.container == "precomp" ? target_comp : footage_folder},
                               {"layer_id", gl}, {"parts", parts_json}});
    }

    // parenting, once everything exists (a parent can be created after its child because groups stack bottom to top)
    for (auto& g : plan.groups)
      for (auto& p : g.parts)
        if (!p.parent_id.empty() && layer_of_part.count(p.parent_id)) api_.set_layer_parent(layer_of_part[p.id], layer_of_part[p.parent_id]);
    if (opt.container == "precomp")
      for (auto& g : plan.groups)
        if (!g.parent_group.empty() && group_layer.count(g.parent_group)) api_.set_layer_parent(group_layer[g.name], group_layer[g.parent_group]);

    // "replace": turn off the earlier single Hair layer instead of deleting it, so Restore can bring it back
    if (opt.replace_hair && plan.has_hair) {
      std::set<int64_t> ours;
      for (auto& l : rec.layers) ours.insert(l.layer_id);
      for (auto& old : journal_.hair_layers(rec.project_path, rec.project_id)) {
        if (ours.count(old.layer_id) || !api_.layer_exists(old.layer_id) || api_.layer_name(old.layer_id) != old.name) continue;
        api_.set_layer_enabled(old.layer_id, false);
        rec.disabled.push_back(old);
        disabled.push_back(old.layer_id);
      }
    }
  } catch (const HostError&) {
    rollback();
    throw;
  } catch (const std::exception& e) {
    rollback();
    throw make_error("ADD_FAILED", e.what());
  }
  journal_.append(rec);
  int total_visible = static_cast<int>(visible.size());
  int total_parts = static_cast<int>(manifest.value("parts", json::array()).size());
  return {{"add_id", rec.id}, {"container", opt.container}, {"groups", result_groups}, {"parts_added", plan.part_count},
          {"hidden_skipped", std::max(0, total_parts - (total_visible - (plan.has_background ? 1 : 0)))},
          {"background", plan.has_background}, {"disabled_layers", disabled}, {"comp_id", sel.comp_id}};
}

// ------------------------------------------------------------------ Restore
json Commands::restore() {
  auto rec = journal_.last_active(api_.project_path(), api_.project_id());
  if (!rec) throw make_error("NOTHING_TO_RESTORE");
  json kept = json::array();
  int removed_layers = 0, removed_items = 0, reenabled = 0;
  UndoGroup undo(api_, "Voxy: Restore");
  for (auto& d : rec->disabled)
    if (api_.layer_exists(d.layer_id) && api_.layer_name(d.layer_id) == d.name) {
      api_.set_layer_enabled(d.layer_id, true);
      ++reenabled;
    }
  for (auto it = rec->layers.rbegin(); it != rec->layers.rend(); ++it) {
    if (!api_.layer_exists(it->layer_id)) continue;                    // the user already deleted it
    if (api_.layer_name(it->layer_id) != it->name) {                   // renamed by hand: it is theirs now
      kept.push_back({{"kind", "layer"}, {"name", it->name}, {"reason", "edited"}});
      continue;
    }
    api_.delete_layer(it->layer_id);
    ++removed_layers;
  }
  for (auto it = rec->items.rbegin(); it != rec->items.rend(); ++it) {
    if (!api_.item_exists(it->id) || api_.item_name(it->id) != it->name) continue;
    bool empty = true;
    if (it->kind == "comp") empty = api_.comp_layer_count(it->id) == 0;
    else if (it->kind == "folder") empty = api_.folder_child_count(it->id) == 0;
    else if (it->kind == "footage") empty = api_.item_use_count(it->id) == 0;
    if (!empty) {
      kept.push_back({{"kind", it->kind}, {"name", it->name}, {"reason", "in_use"}});
      continue;
    }
    api_.delete_item(it->id);
    ++removed_items;
  }
  journal_.mark_restored(rec->id);
  return {{"restored_add_id", rec->id}, {"removed_layers", removed_layers}, {"removed_items", removed_items},
          {"re_enabled_layers", reenabled}, {"kept", kept}};
}

// ------------------------------------------------------------------ Apply upscale
json Commands::apply_upscale(const json& req) {
  int64_t layer = req.at("layer_id");
  const std::string path = req.at("image_path");
  double factor = req.value("factor", 2.0);
  if (!api_.layer_exists(layer)) throw make_error("NO_LAYER");
  if (api_.layer_source_kind(layer) != "still") throw make_error("NOT_A_PICTURE_LAYER");
  if (!fs::exists(path)) throw make_error("PART_FILE_MISSING", path);
  std::string name = api_.layer_name(layer);
  {
    UndoGroup undo(api_, "Voxy: Apply Upscale");
    api_.replace_layer_source(layer, path, factor);
  }
  return {{"layer_id", layer}, {"layer_name", name}, {"factor", factor}};
}

// ------------------------------------------------------------------ save / autosave
json Commands::save_project() {
  Selection s = api_.get_selection();
  if (!s.has_project) throw make_error("NO_PROJECT");
  std::string path = api_.project_path();
  try {
    if (path.empty()) {
      api_.save_with_dialog();
      path = api_.project_path();
      if (path.empty()) return {{"saved", false}, {"cancelled", true}};
    } else {
      api_.save_project();
    }
  } catch (const HostError&) {
    throw;
  } catch (const std::exception& e) {
    throw make_error("SAVE_FAILED", e.what());
  }
  std::error_code ec;
  if (!fs::exists(path, ec) || fs::file_size(path, ec) == 0) throw make_error("SAVE_FAILED", "file missing after save");
  autosave_.note_manual_save(path);
  return {{"saved", true}, {"path", path}, {"time", iso_now()}, {"name", api_.project_name()}};
}

json Commands::autosave() { return autosave_.run(api_); }
json Commands::session_begin() { return autosave_.begin_session(api_); }
json Commands::session_end() { autosave_.end_session(); return {{"ok", true}}; }
json Commands::recovery_find() { return {{"recovery", autosave_.find_recovery(api_)}}; }
json Commands::backups(const json& req) { return {{"backups", autosave_.list_backups(req.value("project", api_.project_path()))}}; }

json Commands::recovery_open(const json& req) {
  std::string b = req.at("backup");
  if (!autosave_.is_known_backup(b)) throw make_error("BACKUP_NOT_FOUND", b);   // never open arbitrary paths
  api_.open_project(b);
  return {{"opened", b}};
}

json Commands::dispatch(const std::string& cmd, const json& a) {
  try {
    json r;
    if (cmd == "ping") r = {{"pong", true}};
    else if (cmd == "selection") r = selection();
    else if (cmd == "export_frame") r = export_frame(a);
    else if (cmd == "add_to_project") r = add_to_project(a);
    else if (cmd == "restore") r = restore();
    else if (cmd == "apply_upscale") r = apply_upscale(a);
    else if (cmd == "save_project") r = save_project();
    else if (cmd == "autosave") r = autosave();
    else if (cmd == "session_begin") r = session_begin();
    else if (cmd == "session_end") r = session_end();
    else if (cmd == "recovery_find") r = recovery_find();
    else if (cmd == "recovery_open") r = recovery_open(a);
    else if (cmd == "backups") r = backups(a);
    else if (cmd == "autosave_reset") { autosave_.reset_unsafe(); r = {{"ok", true}}; }
    else throw make_error("BAD_COMMAND", cmd);
    return {{"ok", true}, {"result", r}};
  } catch (const HostError& e) {
    return {{"ok", false}, {"error", e.to_json()}};
  } catch (const json::exception& e) {
    return {{"ok", false}, {"error", make_error("BAD_COMMAND", e.what()).to_json()}};
  } catch (const std::exception& e) {
    return {{"ok", false}, {"error", make_error("INTERNAL", e.what()).to_json()}};
  }
}

}  // namespace voxy
