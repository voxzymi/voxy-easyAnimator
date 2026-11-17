// *** NOT COMPILED OR RUN IN DEVELOPMENT ***
// This file implements voxy::AeApi with the Adobe After Effects SDK. It was written without access to the SDK headers
// (the SDK is not redistributable and could not be downloaded in the build environment), from knowledge of the AEGP
// suites. Every SDK call whose exact name or signature should be double-checked is marked  // [verify].
// First step on a machine with the SDK: build with -DVOXY_BUILD_AEGP=ON -DAE_SDK_DIR=... and fix compile errors here
// only; the logic above this layer (Add to Project, Restore, autosave, bridge...) is already tested against a fake AE.
// See aegp/SDK_NOTES.md and docs/ae_test_checklist.md.
#pragma once
#include "../core/ae_api.h"

struct SPBasicSuite;

namespace voxy {

class AeApiSdk : public AeApi {
 public:
  AeApiSdk(SPBasicSuite* sp, int plugin_id);
  ~AeApiSdk() override;

  Selection get_selection() override;
  void export_layer_frame_png(int64_t layer_id, double time, const std::string& path) override;
  std::string project_path() override;
  std::string project_name() override;
  bool project_dirty() override;
  int64_t project_id() override;
  bool layer_exists(int64_t layer_id) override;
  std::string layer_name(int64_t layer_id) override;
  std::string layer_source_kind(int64_t layer_id) override;
  bool item_exists(int64_t item_id) override;
  std::string item_name(int64_t item_id) override;
  int item_use_count(int64_t item_id) override;
  int comp_layer_count(int64_t comp_item_id) override;
  int folder_child_count(int64_t folder_item_id) override;
  void save_project() override;
  void save_with_dialog() override;
  void save_copy(const std::string& path) override;
  void open_project(const std::string& path) override;
  void begin_undo(const std::string& label) override;
  void end_undo() override;
  int64_t create_folder(const std::string& name, int64_t parent_folder) override;
  int64_t import_image(const std::string& png_path, int64_t folder) override;
  int64_t create_comp(const std::string& name, int w, int h, double fps, double duration, int64_t folder) override;
  int64_t add_layer(int64_t comp_item_id, int64_t source_item_id) override;
  int64_t comp_of_layer(int64_t layer_id) override;
  void set_layer_name(int64_t layer_id, const std::string& name) override;
  void set_layer_anchor(int64_t layer_id, double x, double y) override;
  void set_layer_position(int64_t layer_id, double x, double y) override;
  void set_layer_label(int64_t layer_id, int label) override;
  void set_layer_enabled(int64_t layer_id, bool on) override;
  void set_layer_parent(int64_t layer_id, int64_t parent_layer_id) override;
  void add_masks(int64_t layer_id, const std::vector<Polygon>& polygons) override;
  void replace_layer_source(int64_t layer_id, const std::string& png_path, double factor) override;
  void delete_layer(int64_t layer_id) override;
  void delete_item(int64_t item_id) override;

 private:
  struct Impl;
  Impl* impl_;
};

}  // namespace voxy
