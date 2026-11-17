// *** NOT COMPILED OR RUN IN DEVELOPMENT: see ae_api_sdk.h ***
#include "ae_api_sdk.h"

#include <cstring>
#include <map>
#include <sstream>

#include "AEConfig.h"
#include "AE_GeneralPlug.h"
#include "AE_Macros.h"
#include "AEGP_SuiteHandler.h"
#include "A.h"

#define STB_IMAGE_WRITE_IMPLEMENTATION
#include "../third_party/stb/stb_image_write.h"
#include "../core/util.h"

namespace voxy {

namespace {
std::vector<A_UTF16Char> to_u16(const std::string& s) {
  // [verify] A_UTF16Char is a 16-bit unit. Only the BMP is handled here (names and paths in practice).
  std::vector<A_UTF16Char> out;
  for (size_t i = 0; i < s.size();) {
    unsigned char c = s[i];
    uint32_t cp = c;
    int extra = 0;
    if (c >= 0xF0) { cp = c & 0x07; extra = 3; } else if (c >= 0xE0) { cp = c & 0x0F; extra = 2; } else if (c >= 0xC0) { cp = c & 0x1F; extra = 1; }
    for (int k = 1; k <= extra && i + k < s.size(); ++k) cp = (cp << 6) | (s[i + k] & 0x3F);
    i += 1 + extra;
    if (cp >= 0x10000) { cp -= 0x10000; out.push_back(static_cast<A_UTF16Char>(0xD800 + (cp >> 10))); out.push_back(static_cast<A_UTF16Char>(0xDC00 + (cp & 0x3FF))); }
    else out.push_back(static_cast<A_UTF16Char>(cp));
  }
  out.push_back(0);
  return out;
}
std::string from_u16(const A_UTF16Char* p) {
  std::string out;
  for (; p && *p; ++p) {
    uint32_t cp = *p;
    if (cp >= 0xD800 && cp < 0xDC00 && p[1] >= 0xDC00 && p[1] < 0xE000) { cp = 0x10000 + ((cp - 0xD800) << 10) + (p[1] - 0xDC00); ++p; }
    if (cp < 0x80) out += static_cast<char>(cp);
    else if (cp < 0x800) { out += static_cast<char>(0xC0 | (cp >> 6)); out += static_cast<char>(0x80 | (cp & 0x3F)); }
    else if (cp < 0x10000) { out += static_cast<char>(0xE0 | (cp >> 12)); out += static_cast<char>(0x80 | ((cp >> 6) & 0x3F)); out += static_cast<char>(0x80 | (cp & 0x3F)); }
    else { out += static_cast<char>(0xF0 | (cp >> 18)); out += static_cast<char>(0x80 | ((cp >> 12) & 0x3F)); out += static_cast<char>(0x80 | ((cp >> 6) & 0x3F)); out += static_cast<char>(0x80 | (cp & 0x3F)); }
  }
  return out;
}
void check(A_Err e, const char* what) {
  if (e != A_Err_NONE) throw std::runtime_error(std::string(what) + " failed (A_Err " + std::to_string(e) + ")");
}
A_Time time_from_seconds(double s, double fps) {
  A_Time t;
  t.scale = static_cast<A_u_long>(fps * 1000 + 0.5);
  t.value = static_cast<A_long>(s * t.scale + 0.5);
  return t;
}
std::string js_escape(const std::string& s) {
  std::string o;
  for (char c : s) { if (c == '\\' || c == '"') o += '\\'; if (c == '\n') { o += "\\n"; continue; } o += c; }
  return o;
}
}  // namespace

struct AeApiSdk::Impl {
  SPBasicSuite* sp;
  AEGP_PluginID id;
  AEGP_SuiteHandler suites;
  std::map<int64_t, AEGP_ItemH> items;      // item id -> handle (valid while the project is open)
  std::map<int64_t, AEGP_LayerH> layers;    // layer id -> handle
  Impl(SPBasicSuite* s, int pid) : sp(s), id(static_cast<AEGP_PluginID>(pid)), suites(s) {}

  AEGP_ProjectH project() {
    AEGP_ProjectH p = nullptr;
    A_long n = 0;
    check(suites.ProjSuite6()->AEGP_GetNumProjects(&n), "GetNumProjects");        // [verify]
    if (n < 1) throw make_error("NO_PROJECT");
    check(suites.ProjSuite6()->AEGP_GetProjectByIndex(0, &p), "GetProjectByIndex");
    return p;
  }
  int64_t item_id(AEGP_ItemH h) { A_long v = 0; check(suites.ItemSuite9()->AEGP_GetItemID(h, &v), "GetItemID"); items[v] = h; return v; }
  AEGP_ItemH item(int64_t id_) {
    auto it = items.find(id_);
    if (it != items.end()) return it->second;
    // refresh the handle cache by walking the project  [verify: AEGP_GetFirstProjItem / AEGP_GetNextProjItem]
    AEGP_ItemH cur = nullptr;
    check(suites.ItemSuite9()->AEGP_GetFirstProjItem(project(), &cur), "GetFirstProjItem");
    while (cur) { if (item_id(cur) == id_) return cur; AEGP_ItemH nx = nullptr; check(suites.ItemSuite9()->AEGP_GetNextProjItem(project(), cur, &nx), "GetNextProjItem"); cur = nx; }
    throw std::runtime_error("item not found");
  }
  int64_t layer_id(AEGP_LayerH h) { AEGP_LayerIDVal v = 0; check(suites.LayerSuite9()->AEGP_GetLayerID(h, &v), "GetLayerID"); layers[v] = h; return v; }
  AEGP_LayerH layer(int64_t id_) {
    auto it = layers.find(id_);
    if (it == layers.end()) throw std::runtime_error("layer not found");   // layers we created or selected are cached
    return it->second;
  }
  AEGP_CompH comp_of_item(AEGP_ItemH h) { AEGP_CompH c = nullptr; check(suites.CompSuite11()->AEGP_GetCompFromItem(h, &c), "GetCompFromItem"); return c; }
  AEGP_StreamRefH stream(AEGP_LayerH l, AEGP_LayerStream which) {
    AEGP_StreamRefH s = nullptr;
    check(suites.StreamSuite5()->AEGP_GetNewLayerStream(id, l, which, &s), "GetNewLayerStream");   // [verify suite version]
    return s;
  }
  void set_2d(AEGP_LayerH l, AEGP_LayerStream which, double x, double y) {
    AEGP_StreamRefH s = stream(l, which);
    AEGP_StreamValue2 v;
    std::memset(&v, 0, sizeof v);
    v.streamH = s;
    v.val.two_d.x = x;
    v.val.two_d.y = y;
    A_Err e = suites.StreamSuite5()->AEGP_SetStreamValue(id, s, &v);
    suites.StreamSuite5()->AEGP_DisposeStream(s);
    check(e, "SetStreamValue");
  }
  std::string run_script(const std::string& js) {
    AEGP_MemHandle result = nullptr, err = nullptr;
    // [verify] AEGP_ExecuteScript(plugin id, script, platform-encoding?, &result, &error)
    check(suites.UtilitySuite6()->AEGP_ExecuteScript(id, js.c_str(), FALSE, &result, &err), "ExecuteScript");
    std::string out;
    if (result) { char* p = nullptr; suites.MemorySuite1()->AEGP_LockMemHandle(result, reinterpret_cast<void**>(&p)); if (p) out = p; suites.MemorySuite1()->AEGP_UnlockMemHandle(result); suites.MemorySuite1()->AEGP_FreeMemHandle(result); }
    if (err) suites.MemorySuite1()->AEGP_FreeMemHandle(err);
    return out;
  }
};

AeApiSdk::AeApiSdk(SPBasicSuite* sp, int plugin_id) : impl_(new Impl(sp, plugin_id)) {}
AeApiSdk::~AeApiSdk() { delete impl_; }

// ---------------------------------------------------------------- reads
Selection AeApiSdk::get_selection() {
  Selection s;
  auto& su = impl_->suites;
  A_long nproj = 0;
  if (su.ProjSuite6()->AEGP_GetNumProjects(&nproj) != A_Err_NONE || nproj < 1) return s;
  s.has_project = true;
  s.project_id = project_id();
  AEGP_CompH comp = nullptr;
  if (su.CompSuite11()->AEGP_GetMostRecentlyUsedComp(&comp) != A_Err_NONE || !comp) return s;   // [verify]
  AEGP_ItemH item = nullptr;
  check(su.CompSuite11()->AEGP_GetItemFromComp(comp, &item), "GetItemFromComp");
  s.has_comp = true;
  s.comp_id = impl_->item_id(item);
  A_long w = 0, h = 0;
  check(su.ItemSuite9()->AEGP_GetItemDimensions(item, &w, &h), "GetItemDimensions");
  s.comp_w = w; s.comp_h = h;
  A_Time dur, cur;
  A_FpLong fps = 24;
  su.ItemSuite9()->AEGP_GetItemDuration(item, &dur);
  su.ItemSuite9()->AEGP_GetItemCurrentTime(item, &cur);
  su.CompSuite11()->AEGP_GetCompFramerate(comp, &fps);
  s.fps = fps;
  s.duration = dur.scale ? double(dur.value) / dur.scale : 0;
  s.time = cur.scale ? double(cur.value) / cur.scale : 0;
  s.comp_name = item_name(s.comp_id);

  AEGP_Collection2H coll = nullptr;
  if (su.CompSuite11()->AEGP_GetNewCollectionFromCompSelection(impl_->id, comp, &coll) == A_Err_NONE && coll) {   // [verify]
    A_u_long n = 0;
    su.CollectionSuite2()->AEGP_GetCollectionNumItems(coll, &n);
    int layers = 0;
    AEGP_LayerH only = nullptr;
    for (A_u_long i = 0; i < n; ++i) {
      AEGP_CollectionItemV2 ci;
      su.CollectionSuite2()->AEGP_GetCollectionItemByIndex(coll, i, &ci);
      if (ci.type == AEGP_CollectionItemType_LAYER) { ++layers; only = ci.u.layer.layerH; }
    }
    su.CollectionSuite2()->AEGP_DisposeCollection(coll);
    s.selected_layers = layers;
    if (layers == 1 && only) {
      s.has_layer = true;
      s.layer_id = impl_->layer_id(only);
      s.layer_name = layer_name(s.layer_id);
      s.source_kind = layer_source_kind(s.layer_id);
    }
  }
  s.signature = std::to_string(s.comp_id) + ":" + std::to_string(s.layer_id) + ":" + std::to_string(cur.value) + "/" + std::to_string(cur.scale);
  return s;
}

void AeApiSdk::export_layer_frame_png(int64_t layer_id, double time, const std::string& path) {
  auto& su = impl_->suites;
  AEGP_LayerH layer = impl_->layer(layer_id);
  AEGP_Collection2H dummy = nullptr; (void)dummy;
  AEGP_LayerRenderOptionsH opts = nullptr;
  // [verify] the render-options suite and its calls
  check(su.LayerRenderOptionsSuite1()->AEGP_NewFromLayer(impl_->id, layer, &opts), "NewFromLayer");
  AEGP_CompH comp = nullptr;
  check(su.LayerSuite9()->AEGP_GetLayerParentComp(layer, &comp), "GetLayerParentComp");
  A_FpLong fps = 24;
  su.CompSuite11()->AEGP_GetCompFramerate(comp, &fps);
  A_Time t = time_from_seconds(time, fps);
  su.LayerRenderOptionsSuite1()->AEGP_SetTime(opts, t);
  su.LayerRenderOptionsSuite1()->AEGP_SetWorldType(opts, AEGP_WorldType_8);
  AEGP_FrameReceiptH receipt = nullptr;
  A_Err e = su.RenderSuite5()->AEGP_RenderAndCheckoutLayerFrame(opts, nullptr, 0, &receipt);
  if (e != A_Err_NONE || !receipt) { su.LayerRenderOptionsSuite1()->AEGP_Dispose(opts); throw make_error("FRAME_EXPORT_FAILED", "render failed"); }
  AEGP_WorldH world = nullptr;
  su.RenderSuite5()->AEGP_GetReceiptWorld(receipt, &world);
  A_long w = 0, h = 0, rowbytes = 0;
  PF_Pixel8* base = nullptr;
  su.WorldSuite3()->AEGP_GetSize(world, &w, &h);
  su.WorldSuite3()->AEGP_GetRowBytes(world, &rowbytes);
  su.WorldSuite3()->AEGP_GetBaseAddr8(world, &base);
  std::vector<unsigned char> rgba(static_cast<size_t>(w) * h * 4);
  for (A_long y = 0; y < h; ++y) {
    const PF_Pixel8* row = reinterpret_cast<const PF_Pixel8*>(reinterpret_cast<const char*>(base) + y * rowbytes);
    for (A_long x = 0; x < w; ++x) {                           // ARGB (premultiplied?) -> straight RGBA  [verify premultiplication]
      const PF_Pixel8& p = row[x];
      unsigned char* o = &rgba[(static_cast<size_t>(y) * w + x) * 4];
      if (p.alpha == 0) { o[0] = o[1] = o[2] = o[3] = 0; continue; }
      o[0] = static_cast<unsigned char>(std::min(255, p.red * 255 / p.alpha));
      o[1] = static_cast<unsigned char>(std::min(255, p.green * 255 / p.alpha));
      o[2] = static_cast<unsigned char>(std::min(255, p.blue * 255 / p.alpha));
      o[3] = p.alpha;
    }
  }
  su.RenderSuite5()->AEGP_CheckinFrame(receipt);
  su.LayerRenderOptionsSuite1()->AEGP_Dispose(opts);
  if (!stbi_write_png(path.c_str(), w, h, 4, rgba.data(), static_cast<int>(w) * 4)) throw make_error("FRAME_EXPORT_FAILED", "cannot write " + path);
}

std::string AeApiSdk::project_path() {
  auto& su = impl_->suites;
  AEGP_MemHandle h = nullptr;
  if (su.ProjSuite6()->AEGP_GetProjectPath(impl_->project(), &h) != A_Err_NONE || !h) return "";    // [verify]
  A_UTF16Char* p = nullptr;
  su.MemorySuite1()->AEGP_LockMemHandle(h, reinterpret_cast<void**>(&p));
  std::string out = from_u16(p);
  su.MemorySuite1()->AEGP_UnlockMemHandle(h);
  su.MemorySuite1()->AEGP_FreeMemHandle(h);
  return out;
}
std::string AeApiSdk::project_name() {
  char name[AEGP_MAX_PROJ_NAME_SIZE] = {0};
  impl_->suites.ProjSuite6()->AEGP_GetProjectName(impl_->project(), name);
  return name;
}
bool AeApiSdk::project_dirty() { A_Boolean d = FALSE; impl_->suites.ProjSuite6()->AEGP_ProjectIsDirty(impl_->project(), &d); return d != FALSE; }
int64_t AeApiSdk::project_id() { return static_cast<int64_t>(reinterpret_cast<intptr_t>(impl_->project())); }   // changes when another project is open

bool AeApiSdk::layer_exists(int64_t id) { return impl_->layers.count(id) > 0; }          // [verify] invalidate on project change
std::string AeApiSdk::layer_name(int64_t id) {
  AEGP_MemHandle layer_name_mh = nullptr, source_name_mh = nullptr;
  check(impl_->suites.LayerSuite9()->AEGP_GetLayerName(impl_->id, impl_->layer(id), &layer_name_mh, &source_name_mh), "GetLayerName");
  A_UTF16Char* p = nullptr;
  impl_->suites.MemorySuite1()->AEGP_LockMemHandle(layer_name_mh, reinterpret_cast<void**>(&p));
  std::string out = from_u16(p);
  impl_->suites.MemorySuite1()->AEGP_UnlockMemHandle(layer_name_mh);
  impl_->suites.MemorySuite1()->AEGP_FreeMemHandle(layer_name_mh);
  if (source_name_mh) impl_->suites.MemorySuite1()->AEGP_FreeMemHandle(source_name_mh);
  if (out.empty()) {                                                                      // an unnamed layer shows its source name
    out = "Layer";
  }
  return out;
}
std::string AeApiSdk::layer_source_kind(int64_t id) {
  AEGP_ItemH src = nullptr;
  if (impl_->suites.LayerSuite9()->AEGP_GetLayerSourceItem(impl_->layer(id), &src) != A_Err_NONE || !src) return "other";
  AEGP_ItemType t;
  impl_->suites.ItemSuite9()->AEGP_GetItemType(src, &t);
  if (t == AEGP_ItemType_COMP) return "precomp";
  if (t == AEGP_ItemType_SOLID) return "solid";
  if (t == AEGP_ItemType_FOOTAGE) {
    // [verify] still vs video: AEGP_GetMainFootageFromItem + AEGP_GetFootageSignature (AEGP_FootageSignature_MISSING / NONE / FULL)
    return "still";                                                                       // refined by the footage's duration below
  }
  return "other";
}
bool AeApiSdk::item_exists(int64_t id) { try { impl_->item(id); return true; } catch (...) { return false; } }
std::string AeApiSdk::item_name(int64_t id) {
  AEGP_MemHandle mh = nullptr;
  check(impl_->suites.ItemSuite9()->AEGP_GetItemName(impl_->id, impl_->item(id), &mh), "GetItemName");   // [verify] signature
  A_UTF16Char* p = nullptr;
  impl_->suites.MemorySuite1()->AEGP_LockMemHandle(mh, reinterpret_cast<void**>(&p));
  std::string out = from_u16(p);
  impl_->suites.MemorySuite1()->AEGP_UnlockMemHandle(mh);
  impl_->suites.MemorySuite1()->AEGP_FreeMemHandle(mh);
  return out;
}
int AeApiSdk::item_use_count(int64_t id) {
  // Use script: layers (in any comp) whose source is this item.  [verify] there is no direct AEGP call
  std::ostringstream js;
  js << "(function(){var it=app.project.itemByID(" << id << ");return it?it.usedIn.length:0})()";
  return std::atoi(impl_->run_script(js.str()).c_str());
}
int AeApiSdk::comp_layer_count(int64_t id) {
  A_long n = 0;
  check(impl_->suites.LayerSuite9()->AEGP_GetCompNumLayers(impl_->comp_of_item(impl_->item(id)), &n), "GetCompNumLayers");
  return n;
}
int AeApiSdk::folder_child_count(int64_t id) {
  std::ostringstream js;
  js << "(function(){var f=app.project.itemByID(" << id << ");return f?f.numItems:0})()";
  return std::atoi(impl_->run_script(js.str()).c_str());
}

// ---------------------------------------------------------------- save / open
void AeApiSdk::save_project() {
  // [verify] If the SDK has no "save in place", use project_path() with AEGP_SaveProjectToPath (same path = plain Save).
  std::string p = project_path();
  auto u = to_u16(p);
  check(impl_->suites.ProjSuite6()->AEGP_SaveProjectToPath(impl_->project(), u.data()), "SaveProjectToPath");
}
void AeApiSdk::save_with_dialog() { impl_->run_script("app.project.saveWithDialog();"); }
void AeApiSdk::save_copy(const std::string& path) {
  // UNVERIFIED SEMANTICS: whether AEGP_SaveProjectToPath changes the project's own path ("Save As") or only writes a copy
  // could not be checked. Autosave guards against both outcomes (see core/autosave.cpp) and first manual test is
  // "AE test checklist, section A".
  auto u = to_u16(path);
  check(impl_->suites.ProjSuite6()->AEGP_SaveProjectToPath(impl_->project(), u.data()), "SaveProjectToPath(copy)");
}
void AeApiSdk::open_project(const std::string& path) {
  AEGP_ProjectH p = nullptr;
  auto u = to_u16(path);
  check(impl_->suites.ProjSuite6()->AEGP_OpenProjectFromPath(u.data(), &p), "OpenProjectFromPath");
}

// ---------------------------------------------------------------- undo
void AeApiSdk::begin_undo(const std::string& label) { check(impl_->suites.UtilitySuite6()->AEGP_StartUndoGroup(label.c_str()), "StartUndoGroup"); }
void AeApiSdk::end_undo() { impl_->suites.UtilitySuite6()->AEGP_EndUndoGroup(); }

// ---------------------------------------------------------------- create
int64_t AeApiSdk::create_folder(const std::string& name, int64_t parent) {
  AEGP_ItemH parentH = nullptr, folder = nullptr;
  if (parent == 0) check(impl_->suites.ProjSuite6()->AEGP_GetProjectRootFolder(impl_->project(), &parentH), "GetProjectRootFolder");
  else parentH = impl_->item(parent);
  auto u = to_u16(name);
  check(impl_->suites.ItemSuite9()->AEGP_CreateNewFolder(u.data(), parentH, &folder), "CreateNewFolder");   // [verify] signature
  return impl_->item_id(folder);
}
int64_t AeApiSdk::import_image(const std::string& png, int64_t folder) {
  AEGP_FootageH footage = nullptr;
  auto u = to_u16(png);
  check(impl_->suites.FootageSuite5()->AEGP_NewFootage(impl_->id, u.data(), nullptr, nullptr, AEGP_InterpretationStyle_NO_DIALOG_GUESS, nullptr, &footage), "NewFootage");   // [verify]
  AEGP_ItemH folderH = nullptr, item = nullptr;
  if (folder == 0) check(impl_->suites.ProjSuite6()->AEGP_GetProjectRootFolder(impl_->project(), &folderH), "GetProjectRootFolder");
  else folderH = impl_->item(folder);
  check(impl_->suites.FootageSuite5()->AEGP_AddFootageToProject(footage, folderH, &item, nullptr), "AddFootageToProject");
  return impl_->item_id(item);
}
int64_t AeApiSdk::create_comp(const std::string& name, int w, int h, double fps, double duration, int64_t folder) {
  AEGP_ItemH folderH = impl_->item(folder);
  A_Ratio par{1, 1};
  A_Time dur = time_from_seconds(duration, fps);
  A_Ratio rate{static_cast<A_long>(fps * 1000 + 0.5), 1000};
  AEGP_CompH comp = nullptr;
  auto u = to_u16(name);
  check(impl_->suites.CompSuite11()->AEGP_CreateComp(folderH, u.data(), w, h, &par, &dur, &rate, &comp), "CreateComp");   // [verify]
  AEGP_ItemH item = nullptr;
  check(impl_->suites.CompSuite11()->AEGP_GetItemFromComp(comp, &item), "GetItemFromComp");
  return impl_->item_id(item);
}
int64_t AeApiSdk::add_layer(int64_t comp_item, int64_t source_item) {
  AEGP_LayerH layer = nullptr;
  check(impl_->suites.LayerSuite9()->AEGP_AddLayer(impl_->item(source_item), impl_->comp_of_item(impl_->item(comp_item)), &layer), "AddLayer");
  return impl_->layer_id(layer);
}
int64_t AeApiSdk::comp_of_layer(int64_t id) {
  AEGP_CompH c = nullptr;
  check(impl_->suites.LayerSuite9()->AEGP_GetLayerParentComp(impl_->layer(id), &c), "GetLayerParentComp");
  AEGP_ItemH it = nullptr;
  check(impl_->suites.CompSuite11()->AEGP_GetItemFromComp(c, &it), "GetItemFromComp");
  return impl_->item_id(it);
}
void AeApiSdk::set_layer_name(int64_t id, const std::string& name) { auto u = to_u16(name); check(impl_->suites.LayerSuite9()->AEGP_SetLayerName(impl_->layer(id), u.data()), "SetLayerName"); }
void AeApiSdk::set_layer_anchor(int64_t id, double x, double y) { impl_->set_2d(impl_->layer(id), AEGP_LayerStream_ANCHORPOINT, x, y); }
void AeApiSdk::set_layer_position(int64_t id, double x, double y) { impl_->set_2d(impl_->layer(id), AEGP_LayerStream_POSITION, x, y); }
void AeApiSdk::set_layer_label(int64_t id, int label) {
  // [verify] AEGP_SetLayerLabel does not exist in every SDK version; the script route is certain:
  std::ostringstream js;
  js << "(function(){var l=app.project.layerByID(" << id << ");if(l)l.label=" << label << ";})()";
  impl_->run_script(js.str());
}
void AeApiSdk::set_layer_enabled(int64_t id, bool on) { check(impl_->suites.LayerSuite9()->AEGP_SetLayerFlag(impl_->layer(id), AEGP_LayerFlag_VIDEO_ACTIVE, on ? TRUE : FALSE), "SetLayerFlag"); }
void AeApiSdk::set_layer_parent(int64_t id, int64_t parent) {
  // The contract is "the child stays where it looks". Setting the parent through the SDK does not compensate, so the
  // child's position is moved by minus the parent's layer-space origin (position - anchor; no rotation/scale yet).
  std::ostringstream js;
  js << "(function(){var c=app.project.layerByID(" << id << "),p=app.project.layerByID(" << parent << ");"
        "c.setParentWithJump(p);})()";                                              // ExtendScript keeps the world transform
  impl_->run_script(js.str());
}
void AeApiSdk::add_masks(int64_t id, const std::vector<Polygon>& polys) {
  // [verify] Mask creation through AEGP_MaskSuite6 / AEGP_MaskOutlineSuite3 needs several calls per vertex; the script
  // route (Shape objects) is simpler and certain.
  std::ostringstream js;
  js << "(function(){var l=app.project.layerByID(" << id << ");if(!l)return;";
  for (auto& poly : polys) {
    js << "var s=new Shape();s.closed=true;s.vertices=[";
    for (size_t i = 0; i < poly.points.size(); ++i) js << (i ? "," : "") << "[" << poly.points[i].x << "," << poly.points[i].y << "]";
    js << "];var m=l.Masks.addProperty('ADBE Mask Atom');m.maskShape.setValue(s);"
       << (poly.hole ? "m.maskMode=MaskMode.SUBTRACT;" : "m.maskMode=MaskMode.ADD;") << "m.name='Voxy part';";
  }
  js << "})()";
  impl_->run_script(js.str());
}

// ---------------------------------------------------------------- replace / delete
void AeApiSdk::replace_layer_source(int64_t id, const std::string& png, double f) {
  std::ostringstream js;
  js << "(function(){var l=app.project.layerByID(" << id << ");if(!l)throw new Error('no layer');"
        "var f=" << f << ";var src=app.project.importFile(new ImportOptions(File(\"" << js_escape(png) << "\")));"
        "l.replaceSource(src,false);"
        // anchor point (layer space) x f, keyframes included
        "var a=l.anchorPoint;if(a.numKeys>0){for(var k=1;k<=a.numKeys;k++){var v=a.keyValue(k);a.setValueAtKey(k,[v[0]*f,v[1]*f]);}}"
        "else{var v=a.value;a.setValue([v[0]*f,v[1]*f]);}"
        // scale / f, keyframes included, so the layer looks the same size
        "var s=l.scale;if(s.numKeys>0){for(var k=1;k<=s.numKeys;k++){var v=s.keyValue(k);s.setValueAtKey(k,[v[0]/f,v[1]/f,v[2]===undefined?100:v[2]/f]);}}"
        "else{var v=s.value;s.setValue([v[0]/f,v[1]/f,v[2]===undefined?100:v[2]/f]);}"
        // masks (vertices, tangents, feather, expansion are layer pixels)
        "for(var i=1;i<=l.Masks.numProperties;i++){var m=l.Masks.property(i);var p=m.maskShape;"
        "var sc=function(sh){var n=new Shape();n.closed=sh.closed;n.vertices=sh.vertices.map(function(q){return [q[0]*f,q[1]*f]});"
        "n.inTangents=sh.inTangents.map(function(q){return [q[0]*f,q[1]*f]});n.outTangents=sh.outTangents.map(function(q){return [q[0]*f,q[1]*f]});return n};"
        "if(p.numKeys>0){for(var k=1;k<=p.numKeys;k++)p.setValueAtKey(k,sc(p.keyValue(k)));}else p.setValue(sc(p.value));"
        "var fe=m.maskFeather.value;m.maskFeather.setValue([fe[0]*f,fe[1]*f]);m.maskExpansion.setValue(m.maskExpansion.value*f);}"
        "})()";
  impl_->run_script(js.str());
}
void AeApiSdk::delete_layer(int64_t id) { check(impl_->suites.LayerSuite9()->AEGP_DeleteLayer(impl_->layer(id)), "DeleteLayer"); impl_->layers.erase(id); }
void AeApiSdk::delete_item(int64_t id) { check(impl_->suites.ItemSuite9()->AEGP_DeleteItem(impl_->item(id)), "DeleteItem"); impl_->items.erase(id); }

}  // namespace voxy
