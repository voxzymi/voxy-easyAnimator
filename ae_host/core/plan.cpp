#include "plan.h"

#include <algorithm>
#include <set>

#include "util.h"

namespace voxy {

Options Options::from_json(const json& j) {
  Options o;
  if (!j.is_object()) return o;
  o.container = j.value("container", "precomp");
  if (o.container != "precomp" && o.container != "folder") o.container = "precomp";
  o.replace_hair = j.value("replace_hair", false);
  o.include_background = j.value("include_background", true);
  return o;
}

const std::vector<std::string>& group_stack_order() {
  // Back to front. The parts of a picture are a partition of its visible pixels, so this order only matters
  // for hidden parts (they must sit above the Head and below whatever covers them: the Hair).
  static const std::vector<std::string> o = {"Legs", "Torso", "Arms", "Head", "Hidden", "Face", "Hair"};
  return o;
}

int group_label(const std::string& g) {  // After Effects label colours: 1 Red .. 16 Dark Green
  static const std::map<std::string, int> m = {{"Head", 4}, {"Face", 5}, {"Hair", 8}, {"Torso", 9}, {"Arms", 11}, {"Legs", 2}, {"Hidden", 12}};
  auto it = m.find(g);
  return it == m.end() ? 0 : it->second;
}

std::string parent_class(const std::string& cls) {
  static const std::map<std::string, std::string> m = {
      {"head", "torso"}, {"arm_l", "torso"}, {"arm_r", "torso"}, {"hand_l", "arm_l"}, {"hand_r", "arm_r"},
      {"leg_l", "torso"}, {"leg_r", "torso"}, {"foot_l", "leg_l"}, {"foot_r", "leg_r"},
      {"eye_l", "head"}, {"eye_r", "head"}, {"iris_l", "eye_l"}, {"iris_r", "eye_r"}, {"eyelash_l", "eye_l"},
      {"eyelash_r", "eye_r"}, {"eyebrow_l", "head"}, {"eyebrow_r", "head"}, {"nose", "head"}, {"mouth", "head"},
      {"hair", "head"}, {"hidden_face", "head"}, {"hidden_eye_l", "head"}, {"hidden_eye_r", "head"}, {"hidden_mouth", "head"}};
  auto it = m.find(cls);
  return it == m.end() ? "" : it->second;
}

static std::string group_parent(const std::string& g) {
  static const std::map<std::string, std::string> m = {{"Head", "Torso"}, {"Arms", "Torso"}, {"Legs", "Torso"}, {"Face", "Head"}, {"Hair", "Head"}, {"Hidden", "Head"}};
  auto it = m.find(g);
  return it == m.end() ? "" : it->second;
}

AddPlan build_add_plan(const json& manifest, const std::string& output_dir, const std::vector<std::string>& visible_ids,
                       const Options& opt, bool check_files) {
  AddPlan plan;
  std::set<std::string> visible(visible_ids.begin(), visible_ids.end());
  std::map<std::string, const json*> by_id;
  const json& manifest_parts = manifest.at("parts");               // a reference: by_id keeps pointers into it
  for (const json& p : manifest_parts) by_id[p.at("id").get<std::string>()] = &p;

  std::map<std::string, PlanGroup> groups;
  for (auto& id : visible_ids) {
    if (id == "background") continue;
    auto it = by_id.find(id);
    if (it == by_id.end()) throw make_error("UNKNOWN_PART", id);
    const json& p = *it->second;
    PlanPart pp;
    pp.id = id;
    pp.name = p.at("name");
    pp.group = p.at("group");
    pp.cls = p.value("class", "");
    pp.kind = p.value("kind", "visible");
    pp.z = p.value("z_order", 0);
    pp.bbox_x = p["bbox"][0]; pp.bbox_y = p["bbox"][1]; pp.bbox_w = p["bbox"][2]; pp.bbox_h = p["bbox"][3];
    pp.image_path = path_str(fs::path(output_dir) / fs::path(p.at("image").get<std::string>()));
    pp.pivot = {p["pivot"][0].get<double>(), p["pivot"][1].get<double>()};
    for (auto& o : p.value("outline", json::array())) {
      Polygon poly;
      poly.hole = o.value("hole", false);
      for (auto& pt : o.at("points")) poly.points.push_back({pt[0].get<double>() - pp.bbox_x, pt[1].get<double>() - pp.bbox_y});
      if (poly.points.size() >= 3) pp.outline.push_back(std::move(poly));
    }
    if (check_files && !fs::exists(pp.image_path)) throw make_error("PART_FILE_MISSING", pp.image_path);
    auto& g = groups[pp.group];
    g.name = pp.group;
    g.label = group_label(pp.group);
    g.parts.push_back(std::move(pp));
    plan.part_count++;
    if (g.parts.back().cls == "hair") plan.has_hair = true;
  }

  if (opt.include_background && visible.count("background") && manifest.contains("background") && !manifest["background"].is_null()) {
    auto& b = manifest["background"];
    plan.has_background = true;
    plan.background.image_path = path_str(fs::path(output_dir) / fs::path(b.at("image").get<std::string>()));
    plan.background.w = b["bbox"][2];
    plan.background.h = b["bbox"][3];
    if (check_files && !fs::exists(plan.background.image_path)) throw make_error("PART_FILE_MISSING", plan.background.image_path);
  }
  if (plan.part_count == 0 && !plan.has_background) throw make_error("NOTHING_TO_ADD");

  // stack: fixed group order, parts inside a group by z (stable: manifest order for ties)
  std::set<std::string> present_groups;
  for (auto& name : group_stack_order()) {
    auto it = groups.find(name);
    if (it == groups.end()) continue;
    PlanGroup g = std::move(it->second);
    std::stable_sort(g.parts.begin(), g.parts.end(), [](const PlanPart& a, const PlanPart& b) { return a.z < b.z; });
    present_groups.insert(name);
    plan.groups.push_back(std::move(g));
  }

  // part parents: climb the hierarchy to the nearest ancestor that is in the plan (same group only for precomps)
  std::map<std::string, const PlanPart*> by_class;
  for (auto& g : plan.groups)
    for (auto& p : g.parts)
      if (!by_class.count(p.cls) || p.kind != "subdivision") by_class[p.cls] = &p;
  for (auto& g : plan.groups) {
    for (auto& p : g.parts) {
      std::string c = parent_class(p.cls);
      while (!c.empty()) {
        auto it = by_class.find(c);
        if (it != by_class.end() && it->second->id != p.id && (opt.container == "folder" || it->second->group == p.group)) {
          p.parent_id = it->second->id;
          break;
        }
        c = parent_class(c);
      }
    }
    std::string pg = group_parent(g.name);
    while (!pg.empty() && !present_groups.count(pg)) pg = group_parent(pg);
    g.parent_group = pg;
  }
  return plan;
}

}  // namespace voxy
