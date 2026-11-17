// Turns a Make Layers result plus the user's eye choices into a list of things to add.
#pragma once
#include <map>
#include <string>
#include <vector>

#include "types.h"

namespace voxy {

struct Options {
  std::string container = "precomp";  // precomp | folder
  bool replace_hair = false;
  bool include_background = true;
  static Options from_json(const json& j);
};

struct PlanPart {
  std::string id, name, group, cls, kind;
  int z = 0;
  int bbox_x = 0, bbox_y = 0, bbox_w = 0, bbox_h = 0;
  std::string image_path;          // absolute
  Point pivot;                     // comp space
  std::vector<Polygon> outline;    // layer space (relative to the part picture's top-left)
  std::string parent_id;           // another part in the same plan ("" = none)
};

struct PlanGroup {
  std::string name;
  int label = 0;
  std::vector<PlanPart> parts;     // bottom to top
  std::string parent_group;        // precomp mode: the group whose layer this group's layer is parented to
};

struct PlanBackground {
  std::string image_path;
  int w = 0, h = 0;
};

struct AddPlan {
  std::vector<PlanGroup> groups;   // bottom to top: Legs, Torso, Arms, Head, Hidden, Face, Hair (only those with parts)
  bool has_background = false;
  PlanBackground background;
  int part_count = 0;
  bool has_hair = false;
};

const std::vector<std::string>& group_stack_order();
int group_label(const std::string& group);
// Parent class of a part class in the rig hierarchy ("" = none).
std::string parent_class(const std::string& cls);

// ``visible_ids`` are the parts whose eye is open (plus "background" if its eye is open). Hidden parts are simply
// not in the list, so they are never added. Throws NOTHING_TO_ADD / UNKNOWN_PART / PART_FILE_MISSING.
AddPlan build_add_plan(const json& manifest, const std::string& output_dir, const std::vector<std::string>& visible_ids,
                       const Options& opt, bool check_files = true);

}  // namespace voxy
