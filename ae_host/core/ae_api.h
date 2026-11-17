// The only door to the After Effects project. The SDK adapter (aegp/ae_api_sdk.cpp) implements it for real; the
// tests implement it with an in-memory fake. Everything above this line (Add to Project, Restore, Apply, autosave)
// is plain C++ and is unit tested without After Effects.
//
// Contract notes (the SDK adapter must honour these):
//  * Every mutating call happens between begin_undo() and end_undo(); a command is ONE undo step.
//  * set_layer_parent() keeps the child exactly where it looks now (like scripting's setParentWithJump).
//  * replace_layer_source() swaps only THIS layer's source (other layers that share the old footage are untouched),
//    and keeps its name, masks, position and keyframes, while multiplying layer-space values (anchor point, mask
//    vertices) by `factor` and dividing Scale (and every Scale keyframe) by `factor`, so the layer looks identical.
//  * save_copy() must not change the project's own path and must not touch the project file.
#pragma once
#include <cstdint>
#include <string>
#include <vector>

#include "types.h"

namespace voxy {

class AeApi {
 public:
  virtual ~AeApi() = default;

  // ---- read
  virtual Selection get_selection() = 0;
  virtual void export_layer_frame_png(int64_t layer_id, double time, const std::string& path) = 0;
  virtual std::string project_path() = 0;  // empty if the project was never saved
  virtual std::string project_name() = 0;
  virtual bool project_dirty() = 0;
  virtual int64_t project_id() = 0;  // changes when a different project is opened or the project is closed

  virtual bool layer_exists(int64_t layer_id) = 0;
  virtual std::string layer_name(int64_t layer_id) = 0;
  virtual std::string layer_source_kind(int64_t layer_id) = 0;  // still | video | precomp | solid | other
  virtual bool item_exists(int64_t item_id) = 0;
  virtual std::string item_name(int64_t item_id) = 0;
  virtual int item_use_count(int64_t item_id) = 0;     // layers (anywhere) that use this footage/comp as their source
  virtual int comp_layer_count(int64_t comp_item_id) = 0;
  virtual int folder_child_count(int64_t folder_item_id) = 0;

  // ---- save / open
  virtual void save_project() = 0;       // to the current path
  virtual void save_with_dialog() = 0;   // for a project that has no path yet
  virtual void save_copy(const std::string& path) = 0;
  virtual void open_project(const std::string& path) = 0;

  // ---- undo
  virtual void begin_undo(const std::string& label) = 0;
  virtual void end_undo() = 0;

  // ---- create
  virtual int64_t create_folder(const std::string& name, int64_t parent_folder) = 0;  // parent 0 = project root
  virtual int64_t import_image(const std::string& png_path, int64_t folder) = 0;      // returns a footage item id
  virtual int64_t create_comp(const std::string& name, int w, int h, double fps, double duration, int64_t folder) = 0;
  virtual int64_t add_layer(int64_t comp_item_id, int64_t source_item_id) = 0;        // new layer on top; returns layer id
  virtual int64_t comp_of_layer(int64_t layer_id) = 0;
  virtual void set_layer_name(int64_t layer_id, const std::string& name) = 0;
  virtual void set_layer_anchor(int64_t layer_id, double x, double y) = 0;            // layer space (pixels of the source)
  virtual void set_layer_position(int64_t layer_id, double x, double y) = 0;          // comp space
  virtual void set_layer_label(int64_t layer_id, int label) = 0;
  virtual void set_layer_enabled(int64_t layer_id, bool on) = 0;
  virtual void set_layer_parent(int64_t layer_id, int64_t parent_layer_id) = 0;
  virtual void add_masks(int64_t layer_id, const std::vector<Polygon>& polygons) = 0; // layer-space vertices; holes are subtracted

  // ---- replace / delete
  virtual void replace_layer_source(int64_t layer_id, const std::string& png_path, double factor) = 0;
  virtual void delete_layer(int64_t layer_id) = 0;
  virtual void delete_item(int64_t item_id) = 0;
};

// RAII undo group: one command is one undo step, even if it throws half way.
class UndoGroup {
 public:
  UndoGroup(AeApi& api, const std::string& label) : api_(api) { api_.begin_undo(label); }
  ~UndoGroup() { api_.end_undo(); }
  UndoGroup(const UndoGroup&) = delete;
  UndoGroup& operator=(const UndoGroup&) = delete;

 private:
  AeApi& api_;
};

}  // namespace voxy
