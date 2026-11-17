// An in-memory After Effects, good enough to test every command. It models the parts of AE that matter for the rules:
// project tree, layer stacking, parenting that keeps the child in place, masks, replace-source scaling, undo groups,
// and the ways saving can go wrong.
#pragma once
#include <algorithm>
#include <fstream>
#include <map>
#include <set>
#include <thread>

#include "../core/ae_api.h"
#include "../core/util.h"

namespace voxy {

struct FakeItem {
  int64_t id = 0;
  std::string name, kind;  // folder | comp | footage
  int64_t parent = 0;
  std::string path;
  int w = 0, h = 0;
  double fps = 24, duration = 5;
};

struct FakeLayer {
  int64_t id = 0, comp = 0, item = 0, parent = 0;
  std::string name;
  Point anchor{0, 0}, pos{0, 0};
  int label = 0;
  bool enabled = true;
  std::vector<Polygon> masks;
  double scale = 100;
  std::vector<std::pair<double, double>> scale_keys;  // (time, scale)
  std::string kind = "still";
};

class FakeAe : public AeApi {
 public:
  // ---- the "project"
  std::string path;
  bool dirty = true;
  int64_t pid = 1;
  std::map<int64_t, FakeItem> items;
  std::map<int64_t, FakeLayer> layers;
  std::map<int64_t, std::vector<int64_t>> order;   // comp item id -> layer ids, top first
  int64_t cur_comp = 0, sel_layer = 0;
  int sel_count = 1;
  double time = 0;
  int64_t next_item = 100, next_layer = 1000;
  // ---- bookkeeping for the tests
  std::vector<std::string> undo_groups;            // labels of completed undo groups
  int undo_depth = 0, mutations_outside_undo = 0;
  std::thread::id last_thread;
  // ---- failure injection
  int fail_add_layer_on_call = -1, add_layer_calls = 0;
  bool copy_fail = false, copy_corrupt = false, copy_empty = false, copy_changes_path = false, copy_touches_project = false;
  bool project_save_fail = false, dialog_cancels = false;
  std::string dialog_path;

  // ---- setup helpers
  int64_t add_user_comp(const std::string& name, int w, int h) {
    int64_t id = next_item++;
    items[id] = {id, name, "comp", 0, "", w, h, 24, 5};
    order[id] = {};
    if (!cur_comp) cur_comp = id;
    return id;
  }
  int64_t add_user_layer(int64_t comp, const std::string& name, const std::string& kind = "still") {
    int64_t it = next_item++;
    items[it] = {it, name + ".png", "footage", 0, "", items[comp].w, items[comp].h, 24, 5};
    int64_t l = next_layer++;
    FakeLayer L;
    L.id = l; L.comp = comp; L.item = it; L.name = name; L.kind = kind;
    L.anchor = {items[comp].w / 2.0, items[comp].h / 2.0}; L.pos = L.anchor;
    layers[l] = L;
    order[comp].insert(order[comp].begin(), l);
    sel_layer = l;
    return l;
  }

  // ---- reads
  Selection get_selection() override {
    last_thread = std::this_thread::get_id();
    Selection s;
    s.has_project = true;
    s.project_id = pid;
    if (cur_comp && items.count(cur_comp)) {
      auto& c = items[cur_comp];
      s.has_comp = true; s.comp_id = cur_comp; s.comp_name = c.name; s.comp_w = c.w; s.comp_h = c.h; s.fps = c.fps; s.duration = c.duration; s.time = time;
    }
    s.selected_layers = sel_layer ? sel_count : 0;
    if (sel_layer && layers.count(sel_layer) && sel_count == 1) {
      s.has_layer = true; s.layer_id = sel_layer; s.layer_name = layers[sel_layer].name; s.source_kind = layers[sel_layer].kind;
    }
    s.signature = std::to_string(cur_comp) + ":" + std::to_string(sel_layer) + ":" + std::to_string(time);
    return s;
  }
  void export_layer_frame_png(int64_t layer_id, double, const std::string& p) override {
    if (!layers.count(layer_id)) throw make_error("FRAME_EXPORT_FAILED", "no such layer");
    write_file_atomic(p, std::string("\x89PNG\r\n\x1a\n", 8) + "fake-frame");
  }
  std::string project_path() override { return path; }
  std::string project_name() override { return path.empty() ? "Untitled Project.aep" : fs::path(path).filename().string(); }
  bool project_dirty() override { return dirty; }
  int64_t project_id() override { return pid; }
  bool layer_exists(int64_t id) override { return layers.count(id) > 0; }
  std::string layer_name(int64_t id) override { return layers.at(id).name; }
  std::string layer_source_kind(int64_t id) override { return layers.at(id).kind; }
  bool item_exists(int64_t id) override { return items.count(id) > 0; }
  std::string item_name(int64_t id) override { return items.at(id).name; }
  int item_use_count(int64_t id) override {
    int n = 0;
    for (auto& [lid, l] : layers) n += l.item == id;
    return n;
  }
  int comp_layer_count(int64_t comp) override { return static_cast<int>(order[comp].size()); }
  int folder_child_count(int64_t folder) override {
    int n = 0;
    for (auto& [id, it] : items) n += it.parent == folder;
    return n;
  }

  // ---- save / open
  static std::string aep_bytes(const std::string& tag = "") { return std::string("RIFX\0\0\0\0Egg!", 12) + std::string(200, 'x') + tag; }
  void save_project() override {
    guard();
    if (project_save_fail) throw std::runtime_error("disk full");
    write_file_atomic(path, aep_bytes("saved"));
    dirty = false;
  }
  void save_with_dialog() override {
    if (dialog_cancels) return;
    path = dialog_path;
    write_file_atomic(path, aep_bytes("dialog"));
    dirty = false;
  }
  void save_copy(const std::string& p) override {
    if (copy_fail) throw std::runtime_error("cannot write copy");
    if (copy_empty) { std::ofstream(p).close(); return; }
    if (copy_corrupt) { write_file_atomic(p, "garbage"); return; }
    write_file_atomic(p, aep_bytes("copy"));
    if (copy_changes_path) path = p;
    if (copy_touches_project && !path.empty()) write_file_atomic(path, aep_bytes("touched-by-copy"));
  }
  void open_project(const std::string& p) override { opened.push_back(p); }
  std::vector<std::string> opened;

  // ---- undo
  void begin_undo(const std::string& label) override { cur_label = label; ++undo_depth; }
  void end_undo() override { if (--undo_depth == 0) undo_groups.push_back(cur_label); }
  std::string cur_label;

  // ---- create
  int64_t create_folder(const std::string& name, int64_t parent) override {
    guard(); int64_t id = next_item++; items[id] = {id, name, "folder", parent}; return id;
  }
  int64_t import_image(const std::string& png, int64_t folder) override {
    guard();
    int64_t id = next_item++;
    FakeItem it{id, fs::path(png).filename().string(), "footage", folder, png};
    png_size(png, it.w, it.h);
    items[id] = it;
    return id;
  }
  int64_t create_comp(const std::string& name, int w, int h, double fps, double dur, int64_t folder) override {
    guard(); int64_t id = next_item++; items[id] = {id, name, "comp", folder, "", w, h, fps, dur}; order[id] = {}; return id;
  }
  int64_t add_layer(int64_t comp, int64_t src) override {
    guard();
    if (++add_layer_calls == fail_add_layer_on_call) throw std::runtime_error("injected add_layer failure");
    int64_t l = next_layer++;
    FakeLayer L;
    L.id = l; L.comp = comp; L.item = src; L.name = items.at(src).name;
    L.kind = items.at(src).kind == "comp" ? "precomp" : "still";
    L.anchor = {items[src].w / 2.0, items[src].h / 2.0};
    L.pos = {items[comp].w / 2.0, items[comp].h / 2.0};
    layers[l] = L;
    order[comp].insert(order[comp].begin(), l);                      // on top
    return l;
  }
  int64_t comp_of_layer(int64_t l) override { return layers.at(l).comp; }
  void set_layer_name(int64_t l, const std::string& n) override { guard(); layers.at(l).name = n; }
  void set_layer_anchor(int64_t l, double x, double y) override { guard(); layers.at(l).anchor = {x, y}; }
  void set_layer_position(int64_t l, double x, double y) override { guard(); layers.at(l).pos = {x, y}; }
  void set_layer_label(int64_t l, int v) override { guard(); layers.at(l).label = v; }
  void set_layer_enabled(int64_t l, bool on) override { guard(); layers.at(l).enabled = on; }
  void set_layer_parent(int64_t child, int64_t parent) override {
    guard();
    auto& c = layers.at(child);
    auto& p = layers.at(parent);
    // keep the child where it is: its local position is now relative to the parent's layer space
    Point w = world_pos(child);
    c.parent = parent;
    Point po = world_origin(parent);
    c.pos = {w.x - po.x, w.y - po.y};
    (void)p;
  }
  void add_masks(int64_t l, const std::vector<Polygon>& polys) override {
    guard(); for (auto& p : polys) layers.at(l).masks.push_back(p);
  }

  // ---- replace / delete
  void replace_layer_source(int64_t l, const std::string& png, double f) override {
    guard();
    auto& L = layers.at(l);
    int64_t id = next_item++;
    FakeItem it{id, fs::path(png).filename().string(), "footage", 0, png};
    png_size(png, it.w, it.h);
    items[id] = it;
    L.item = id;
    L.anchor = {L.anchor.x * f, L.anchor.y * f};
    for (auto& m : L.masks) for (auto& p : m.points) { p.x *= f; p.y *= f; }
    L.scale /= f;
    for (auto& k : L.scale_keys) k.second /= f;
  }
  void delete_layer(int64_t l) override {
    guard();
    auto& v = order[layers.at(l).comp];
    v.erase(std::remove(v.begin(), v.end(), l), v.end());
    for (auto& [id, x] : layers) if (x.parent == l) x.parent = 0;
    layers.erase(l);
  }
  void delete_item(int64_t id) override {
    guard();
    std::vector<int64_t> dead;
    for (auto& [lid, l] : layers) if (l.item == id || l.comp == id) dead.push_back(lid);
    for (auto lid : dead) if (layers.count(lid)) delete_layer(lid);
    order.erase(id);
    items.erase(id);
  }

  // ---- geometry helpers for assertions
  Point world_origin(int64_t l) {         // where the layer's own (0,0) is in comp space (identity scale/rotation)
    auto& L = layers.at(l);
    Point po{0, 0};
    if (L.parent) po = world_origin(L.parent);
    return {po.x + L.pos.x - L.anchor.x, po.y + L.pos.y - L.anchor.y};
  }
  Point world_pos(int64_t l) {            // where the anchor point is in comp space
    auto& L = layers.at(l);
    Point po{0, 0};
    if (L.parent) po = world_origin(L.parent);
    return {po.x + L.pos.x, po.y + L.pos.y};
  }
  Point to_comp(int64_t l, Point p) {     // a point in layer space -> comp space, honouring layer scale
    auto& L = layers.at(l);
    Point o = world_origin(l);
    double s = L.scale / 100.0;
    return {o.x + L.anchor.x + (p.x - L.anchor.x) * s - 0 + 0, o.y + L.anchor.y + (p.y - L.anchor.y) * s};
  }
  std::vector<int64_t> layers_in(int64_t comp) { return order[comp]; }
  std::vector<int64_t> items_named(const std::string& name, const std::string& kind = "") {
    std::vector<int64_t> r;
    for (auto& [id, it] : items) if (it.name == name && (kind.empty() || it.kind == kind)) r.push_back(id);
    return r;
  }
  int64_t layer_named(int64_t comp, const std::string& name) {
    for (auto l : order[comp]) if (layers[l].name == name) return l;
    return 0;
  }

 private:
  void guard() { if (undo_depth == 0) ++mutations_outside_undo; }
  static void png_size(const std::string& p, int& w, int& h) {
    std::string b = read_file(p).substr(0, 32);
    if (b.size() >= 24 && b.compare(1, 3, "PNG") == 0) {
      auto be = [&](int o) { return (unsigned char)b[o] << 24 | (unsigned char)b[o + 1] << 16 | (unsigned char)b[o + 2] << 8 | (unsigned char)b[o + 3]; };
      w = be(16); h = be(20);
    }
  }
};

}  // namespace voxy
