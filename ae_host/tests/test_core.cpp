#include "env.h"


// ------------------------------------------------------------------ plan
TEST(plan_only_visible_parts_and_hidden_ones_are_skipped) {
  Env e;
  auto ids = e.all_ids(e.make, false);
  std::vector<std::string> shown;
  for (auto& id : ids) if (id != "eye_l" && id != "hand_r" && id != "hidden_eye_l") shown.push_back(id);
  AddPlan p = build_add_plan(e.make, path_str(e.make_dir), shown, Options{});
  CHECK_EQ(p.part_count, (int)shown.size());
  for (auto& g : p.groups) for (auto& part : g.parts) CHECK(part.id != "eye_l" && part.id != "hand_r" && part.id != "hidden_eye_l");
}

TEST(plan_groups_exist_only_when_a_part_is_added) {
  Env e;
  AddPlan p = build_add_plan(e.make, path_str(e.make_dir), {"head", "torso"}, Options{});
  CHECK_EQ(p.groups.size(), (size_t)2);
  CHECK_EQ(p.groups[0].name, std::string("Torso"));       // bottom to top
  CHECK_EQ(p.groups[1].name, std::string("Head"));
}

TEST(plan_stacks_groups_back_to_front_and_parts_by_z) {
  Env e;
  AddPlan p = build_add_plan(e.make, path_str(e.make_dir), e.all_ids(e.make, false), Options{});
  std::vector<std::string> order;
  for (auto& g : p.groups) order.push_back(g.name);
  std::vector<std::string> want = {"Legs", "Torso", "Arms", "Head", "Hidden", "Face", "Hair"};
  CHECK(order == want);
  for (auto& g : p.groups) for (size_t i = 1; i < g.parts.size(); ++i) CHECK(g.parts[i - 1].z <= g.parts[i].z);
  CHECK(p.groups.back().parts.size() == 1);               // the Hair group holds a single Hair part
  CHECK_EQ(p.groups.back().parts[0].name, std::string("Hair"));
}

TEST(plan_parent_hierarchy_precomp_vs_folder) {
  Env e;
  auto ids = e.all_ids(e.make, false);
  auto find = [](AddPlan& p, const std::string& id) -> PlanPart* { for (auto& g : p.groups) for (auto& x : g.parts) if (x.id == id) return &x; return nullptr; };
  AddPlan flat = build_add_plan(e.make, path_str(e.make_dir), ids, Options{"folder", false, true});
  CHECK_EQ(find(flat, "hand_l")->parent_id, std::string("arm_l"));
  CHECK_EQ(find(flat, "arm_l")->parent_id, std::string("torso"));
  CHECK_EQ(find(flat, "head")->parent_id, std::string("torso"));
  CHECK_EQ(find(flat, "iris_l")->parent_id, std::string("eye_l"));
  CHECK_EQ(find(flat, "hair")->parent_id, std::string("head"));
  CHECK_EQ(find(flat, "torso")->parent_id, std::string(""));
  AddPlan pre = build_add_plan(e.make, path_str(e.make_dir), ids, Options{"precomp", false, true});
  CHECK_EQ(find(pre, "hand_l")->parent_id, std::string("arm_l"));   // same group: parented inside the precomp
  CHECK_EQ(find(pre, "head")->parent_id, std::string(""));          // other group: the precomp layers are parented instead
  for (auto& g : pre.groups) {
    if (g.name == "Face") CHECK_EQ(g.parent_group, std::string("Head"));
    if (g.name == "Head" || g.name == "Arms" || g.name == "Legs") CHECK_EQ(g.parent_group, std::string("Torso"));
    if (g.name == "Torso") CHECK_EQ(g.parent_group, std::string(""));
  }
  // a hidden parent: the nearest part that is still added becomes the parent
  std::vector<std::string> no_arm;
  for (auto& id : ids) if (id != "arm_l") no_arm.push_back(id);
  AddPlan p3 = build_add_plan(e.make, path_str(e.make_dir), no_arm, Options{"folder", false, true});
  CHECK_EQ(find(p3, "hand_l")->parent_id, std::string("torso"));
}

TEST(plan_outlines_are_in_layer_space_and_pivot_is_kept) {
  Env e;
  AddPlan p = build_add_plan(e.make, path_str(e.make_dir), {"head"}, Options{});
  const PlanPart& h = p.groups[0].parts[0];
  CHECK(!h.outline.empty());
  const json* part = nullptr;
  for (auto& x : e.make["parts"]) if (x["id"] == "head") part = &x;
  CHECK_NEAR(h.outline[0].points[0].x, (*part)["outline"][0]["points"][0][0].get<double>() - (*part)["bbox"][0].get<double>(), 1e-9);
  CHECK_NEAR(h.pivot.x, (*part)["pivot"][0].get<double>(), 1e-9);
  for (auto& poly : h.outline) for (auto& pt : poly.points) CHECK(pt.x >= -1 && pt.x <= h.bbox_w + 1 && pt.y >= -1 && pt.y <= h.bbox_h + 1);
}

TEST(plan_errors) {
  Env e;
  CHECK_CODE(build_add_plan(e.make, path_str(e.make_dir), {"head", "nonsense"}, Options{}), "UNKNOWN_PART");
  CHECK_CODE(build_add_plan(e.make, path_str(e.make_dir), {}, Options{}), "NOTHING_TO_ADD");
  CHECK_CODE(build_add_plan(e.make, path_str(e.tmp / "empty"), {"head"}, Options{}), "PART_FILE_MISSING");
}

// ------------------------------------------------------------------ Add to Project
TEST(add_makes_one_group_per_group_name_with_masked_layers_inside) {
  Env e;
  json r = e.add(e.make, e.make_dir, e.all_ids(e.make, false));
  CHECK_EQ(e.ae.undo_groups.size(), (size_t)1);                       // one undo step
  CHECK_EQ(e.ae.undo_groups[0], std::string("Voxy: Add to Project"));
  CHECK_EQ(e.ae.mutations_outside_undo, 0);
  std::set<std::string> groups;
  for (auto& g : e.make["groups"]) groups.insert(g["name"]);
  std::set<std::string> made;
  for (auto& g : r["groups"]) made.insert(g["name"]);
  CHECK(made == groups);                                              // exactly one group per group name
  for (auto& g : r["groups"]) {
    auto comps = e.ae.items_named(g["name"], "comp");
    CHECK_EQ(comps.size(), (size_t)1);                                // each group is a precomp named after the group
    int64_t cid = comps[0];
    for (auto& p : g["parts"]) {
      int64_t lid = p["layer_id"];
      CHECK_EQ(e.ae.layers[lid].comp, cid);                           // the part layer is INSIDE its group
      CHECK(!e.ae.layers[lid].masks.empty());                         // and masked to its part
    }
    CHECK_EQ(e.ae.layer_named(e.comp, g["name"]) != 0, true);         // the group's layer sits in the main comp
  }
  int64_t hair_comp = e.ae.items_named("Hair", "comp")[0];
  CHECK_EQ(e.ae.comp_layer_count(hair_comp), 1);                      // a single Hair layer
  CHECK_EQ(e.ae.layers[e.ae.layer_named(hair_comp, "Hair")].name, std::string("Hair"));
  CHECK(e.ae.layers.count(e.user_layer) == 1 && e.ae.layers[e.user_layer].enabled);   // the original layer is untouched
}

TEST(add_sets_pivot_as_anchor_and_masks_hide_all_but_the_part) {
  Env e;
  json r = e.add(e.make, e.make_dir, {"arm_l", "head"});
  for (auto& g : r["groups"]) for (auto& p : g["parts"]) {
    const json* part = nullptr;
    for (auto& x : e.make["parts"]) if (x["id"] == p["id"]) part = &x;
    auto& L = e.ae.layers[p["layer_id"].get<int64_t>()];
    CHECK_NEAR(L.pos.x, (*part)["pivot"][0].get<double>(), 1e-6);       // position = the pivot, in comp space
    CHECK_NEAR(L.pos.y, (*part)["pivot"][1].get<double>(), 1e-6);
    CHECK_NEAR(L.anchor.x, (*part)["pivot"][0].get<double>() - (*part)["bbox"][0].get<double>(), 1e-6);   // anchor = pivot in layer space
    CHECK_NEAR(L.anchor.y, (*part)["pivot"][1].get<double>() - (*part)["bbox"][1].get<double>(), 1e-6);
    int holes = 0;
    for (auto& m : L.masks) holes += m.hole;
    CHECK_EQ(L.masks.size(), (*part)["outline"].size());
    (void)holes;
  }
}

TEST(add_folder_mode_puts_every_layer_in_the_main_comp_grouped_by_project_folder) {
  Env e;
  json r = e.add(e.make, e.make_dir, e.all_ids(e.make, false), {{"container", "folder"}});
  for (auto& g : r["groups"]) {
    CHECK_EQ(e.ae.items_named(g["name"], "folder").size(), (size_t)1);
    CHECK(e.ae.items_named(g["name"], "comp").empty());
    for (auto& p : g["parts"]) CHECK_EQ(e.ae.layers[p["layer_id"].get<int64_t>()].comp, e.comp);
  }
  auto names = names_in(e.ae, e.comp);
  CHECK_EQ(names.back(), std::string("Hero"));                         // the original stays at the bottom
  CHECK_EQ(names.front(), std::string("Hair"));                        // hair on top
}

TEST(hidden_parts_are_never_added_and_hidden_groups_are_not_created) {
  Env e;
  std::vector<std::string> shown;
  for (auto& p : e.make["parts"]) if (p["group"] != "Face" && p["group"] != "Hidden" && p["id"] != "hand_l") shown.push_back(p["id"]);
  json r = e.add(e.make, e.make_dir, shown);
  CHECK(e.ae.items_named("Face", "comp").empty() && e.ae.items_named("Hidden", "comp").empty());   // no empty groups
  CHECK(e.ae.layer_named(e.comp, "Face") == 0);
  for (auto& l : e.ae.layers) { CHECK(l.second.name != "Eye L" && l.second.name != "Hand L"); }
  CHECK_EQ((int)r["parts_added"], (int)shown.size());
  CHECK(r["hidden_skipped"].get<int>() > 0);
}

TEST(nothing_is_added_when_every_part_is_hidden) {
  Env e;
  size_t items_before = e.ae.items.size();
  CHECK_CODE(e.add(e.make, e.make_dir, {}), "NOTHING_TO_ADD");
  CHECK_EQ(e.ae.items.size(), items_before);
  CHECK(e.ae.undo_groups.empty());                                      // the project was never touched
}

TEST(parenting_keeps_every_part_where_it_looks) {
  Env e;
  json r = e.add(e.make, e.make_dir, e.all_ids(e.make, false), {{"container", "folder"}});
  int parented = 0;
  for (auto& g : r["groups"]) for (auto& p : g["parts"]) {
    int64_t lid = p["layer_id"];
    const json* part = nullptr;
    for (auto& x : e.make["parts"]) if (x["id"] == p["id"]) part = &x;
    Point w = e.ae.world_pos(lid);
    CHECK_NEAR(w.x, (*part)["pivot"][0].get<double>(), 1e-6);            // same place in the picture as before parenting
    CHECK_NEAR(w.y, (*part)["pivot"][1].get<double>(), 1e-6);
    parented += e.ae.layers[lid].parent != 0;
  }
  CHECK(parented >= 15);
  CHECK_EQ(e.ae.layers[e.ae.layer_named(e.comp, "Hand L")].parent, e.ae.layer_named(e.comp, "Arm L"));
  // precomp mode parents the group layers instead
  Env e2;
  json r2 = e2.add(e2.make, e2.make_dir, e2.all_ids(e2.make, false));
  CHECK_EQ(e2.ae.layers[e2.ae.layer_named(e2.comp, "Head")].parent, e2.ae.layer_named(e2.comp, "Torso"));
  CHECK_EQ(e2.ae.layers[e2.ae.layer_named(e2.comp, "Hair")].parent, e2.ae.layer_named(e2.comp, "Head"));
}

TEST(background_plate_goes_to_the_bottom_of_the_composition) {
  Env e;
  CHECK(e.make["background"].is_object());
  e.add(e.make, e.make_dir, e.all_ids(e.make, true));
  int64_t bg = e.ae.layer_named(e.comp, "Background");
  CHECK(bg != 0);
  auto order = e.ae.layers_in(e.comp);
  CHECK_EQ(order.back(), e.user_layer);                                 // original stays below everything
  CHECK_EQ(order[order.size() - 2], bg);                                // the plate is just above it, below all groups
  Env e2;                                                               // eye closed on the background: not added
  e2.add(e2.make, e2.make_dir, e2.all_ids(e2.make, false));
  CHECK(e2.ae.layer_named(e2.comp, "Background") == 0);
}

TEST(subdivisions_are_added_inside_the_hair_group_and_share_a_pivot) {
  Env e;
  json r = e.add(e.subdiv, e.subdiv_dir, e.all_ids(e.subdiv, false));
  CHECK_EQ(r["groups"].size(), (size_t)1);
  CHECK_EQ(r["groups"][0]["name"], std::string("Hair"));
  int64_t hc = e.ae.items_named("Hair", "comp")[0];
  CHECK_EQ(e.ae.comp_layer_count(hc), (int)e.subdiv["parts"].size());
  Point p0 = e.ae.layers[r["groups"][0]["parts"][0]["layer_id"].get<int64_t>()].pos;
  for (auto& p : r["groups"][0]["parts"]) {
    Point q = e.ae.layers[p["layer_id"].get<int64_t>()].pos;
    CHECK_NEAR(q.x, p0.x, 1e-6); CHECK_NEAR(q.y, p0.y, 1e-6);           // one shared root pivot
  }
}

TEST(replace_hair_turns_the_single_hair_layer_off_and_restore_turns_it_back_on) {
  Env e;
  json a = e.add(e.make, e.make_dir, {"head", "hair"});
  int64_t old_hair = e.ae.layer_named(e.ae.items_named("Hair", "comp")[0], "Hair");
  CHECK(e.ae.layers[old_hair].enabled);
  json b = e.add(e.subdiv, e.subdiv_dir, e.all_ids(e.subdiv, false), {{"replace_hair", true}});
  CHECK(!e.ae.layers[old_hair].enabled);                                // replaced: switched off, not deleted
  CHECK_EQ(b["disabled_layers"].size(), (size_t)1);
  e.cmds.restore();                                                     // undoes the subdivision add
  CHECK(e.ae.layers.count(old_hair) == 1 && e.ae.layers[old_hair].enabled);
  json c = e.add(e.subdiv, e.subdiv_dir, e.all_ids(e.subdiv, false));  // without "replace": old Hair stays on
  CHECK(e.ae.layers[old_hair].enabled && c["disabled_layers"].empty());
}

TEST(a_failure_half_way_leaves_the_project_as_it_was) {
  Env e;
  e.ae.fail_add_layer_on_call = 6;
  size_t items = e.ae.items.size(), layers = e.ae.layers.size();
  CHECK_CODE(e.add(e.make, e.make_dir, e.all_ids(e.make, false)), "ADD_FAILED");
  CHECK_EQ(e.ae.items.size(), items);
  CHECK_EQ(e.ae.layers.size(), layers);
  CHECK_EQ(e.ae.undo_groups.size(), (size_t)1);                         // still a single, closed undo group
  CHECK_EQ(e.ae.undo_depth, 0);
  CHECK(!e.journal.last_active(e.ae.path, e.ae.pid));                   // and nothing to "restore"
}

TEST(a_changed_composition_size_is_refused) {
  Env e;
  e.ae.items[e.comp].w = 800;
  CHECK_CODE(e.add(e.make, e.make_dir, {"head"}), "SIZE_MISMATCH");
  CHECK(e.ae.undo_groups.empty());
}

TEST(no_comp_or_no_layer_gives_friendly_errors) {
  Env e;
  e.ae.cur_comp = 0;
  CHECK_CODE(e.cmds.export_frame({{"path", path_str(e.tmp / "f.png")}}), "NO_COMP");
  e.ae.cur_comp = e.comp; e.ae.sel_layer = 0;
  CHECK_CODE(e.cmds.export_frame({{"path", path_str(e.tmp / "f.png")}}), "NO_LAYER");
  e.ae.sel_layer = e.user_layer; e.ae.sel_count = 3;
  CHECK_CODE(e.cmds.export_frame({{"path", path_str(e.tmp / "f.png")}}), "MANY_LAYERS");
  json err = e.cmds.dispatch("export_frame", {{"path", "x"}});
  CHECK(!err["ok"].get<bool>() && err["error"]["user_message"].is_string() && err["error"]["what_to_do"].is_string() && err["error"]["help_topic"].is_string());
}

// ------------------------------------------------------------------ Restore
TEST(restore_removes_only_the_layers_from_the_last_add) {
  Env e;
  int64_t mine = e.ae.add_user_layer(e.comp, "My own layer");          // a layer the user made by hand
  e.ae.sel_layer = e.user_layer;
  e.add(e.make, e.make_dir, {"head", "torso", "hair"});
  size_t after_first = e.ae.layers.size(), items_first = e.ae.items.size();
  json second = e.add(e.make, e.make_dir, {"arm_l", "eye_l"});
  CHECK(e.ae.layers.size() > after_first);
  json r = e.cmds.restore();
  CHECK_EQ(e.ae.layers.size(), after_first);                           // exactly the second add is gone
  CHECK_EQ(e.ae.items.size(), items_first);
  CHECK(e.ae.layers.count(mine) && e.ae.layers.count(e.user_layer));   // layers made by hand are never touched
  CHECK(e.ae.layer_named(e.comp, "Head") != 0 && e.ae.layer_named(e.comp, "Torso") != 0);   // the first add is intact
  CHECK_EQ(e.ae.undo_groups.back(), std::string("Voxy: Restore"));
  CHECK_EQ(e.ae.mutations_outside_undo, 0);
  CHECK_EQ((int)r["removed_layers"] > 0, true);
  CHECK(r["more"].get<bool>());                                        // the first add can still be restored
  json r2 = e.cmds.restore();                                          // second restore removes the first add
  CHECK(!r2["more"].get<bool>());
  CHECK(e.ae.layer_named(e.comp, "Head") == 0);
  CHECK_EQ(e.ae.layers.size(), (size_t)2);                             // only the user's two layers remain
  CHECK_CODE(e.cmds.restore(), "NOTHING_TO_RESTORE");
}

TEST(restore_keeps_what_the_user_added_or_renamed) {
  Env e;
  e.add(e.make, e.make_dir, {"head", "torso"});
  int64_t head_comp = e.ae.items_named("Head", "comp")[0];
  int64_t user_inside = e.ae.add_user_layer(head_comp, "User sticker");   // the user drew inside our Head group
  int64_t torso_layer = e.ae.layer_named(e.ae.items_named("Torso", "comp")[0], "Torso");
  e.ae.layers[torso_layer].name = "My renamed torso";                  // and renamed another one: edited by hand
  json r = e.cmds.restore();
  CHECK(e.ae.layers.count(user_inside) == 1);                          // user layer survives
  CHECK(e.ae.items.count(head_comp) == 1);                             // so does the group that holds it
  CHECK(e.ae.layers.count(torso_layer) == 1);                          // renamed layer is theirs
  CHECK(e.ae.layer_named(e.comp, "Head") == 0);                        // our wrapper layer in the main comp is gone
  CHECK(r["kept"].size() >= 2);
}

TEST(restore_keeps_footage_used_elsewhere) {
  Env e;
  json a = e.add(e.make, e.make_dir, {"head"});
  int64_t head_layer = a["groups"][0]["parts"][0]["layer_id"];
  int64_t footage = e.ae.layers[head_layer].item;
  int64_t reuse = e.ae.add_user_layer(e.comp, "Reuses head footage");  // user made another layer from our footage
  e.ae.layers[reuse].item = footage;
  e.cmds.restore();
  CHECK(e.ae.items.count(footage) == 1 && e.ae.layers.count(reuse) == 1);
}

TEST(restore_when_the_project_is_a_different_one_does_nothing) {
  Env e;
  e.add(e.make, e.make_dir, {"head"});
  e.ae.path = path_str(e.tmp / "Other.aep");
  e.ae.pid = 99;
  CHECK_CODE(e.cmds.restore(), "NOTHING_TO_RESTORE");
}

TEST(journal_survives_a_restart) {
  fs::path t = Env::fresh_tmp();
  {
    Env e(t);
    e.add(e.make, e.make_dir, {"head"});
  }
  Env e2(t);                                                           // same data folder, new objects
  CHECK(e2.journal.last_active(e2.ae.path, 0).has_value());
}

// ------------------------------------------------------------------ Apply in Project
TEST(apply_upscale_replaces_the_source_and_keeps_everything_else) {
  Env e;
  FakeLayer& L = e.ae.layers[e.user_layer];
  L.masks.push_back({{{10, 10}, {50, 10}, {50, 60}, {10, 60}}, false});
  L.scale_keys = {{0, 100}, {2, 140}};
  L.pos = {200, 300};
  Point before = e.ae.to_comp(e.user_layer, {30, 30});
  std::string up = path_str(e.tmp / "up.png");
  // a 2x picture: PNG header says 800x1280
  std::string png("\x89PNG\r\n\x1a\n\0\0\0\rIHDR", 16);
  png += std::string("\0\0\x03\x20\0\0\x05\0", 8) + std::string(40, 'x');
  write_file_atomic(up, png);
  json r = e.cmds.apply_upscale({{"layer_id", e.user_layer}, {"image_path", up}});
  CHECK_EQ(e.ae.undo_groups.size(), (size_t)1);                        // one undo step
  CHECK_EQ(e.ae.undo_groups[0], std::string("Voxy: Apply Upscale"));
  CHECK_EQ(e.ae.layers[e.user_layer].name, std::string("Hero"));       // name kept
  CHECK_NEAR(e.ae.layers[e.user_layer].pos.x, 200, 1e-9);              // position kept
  CHECK_EQ(e.ae.layers[e.user_layer].masks.size(), (size_t)1);         // masks kept, scaled to the new pixels
  CHECK_NEAR(e.ae.layers[e.user_layer].masks[0].points[1].x, 100, 1e-9);
  CHECK_EQ(e.ae.layers[e.user_layer].scale_keys.size(), (size_t)2);    // keyframes kept, halved to cancel the 2x
  CHECK_NEAR(e.ae.layers[e.user_layer].scale_keys[1].second, 70, 1e-9);
  Point after = e.ae.to_comp(e.user_layer, {60, 60});                  // the same spot on the picture, now at 2x pixel coords
  CHECK_NEAR(after.x, before.x, 1e-6);                                 // so the layer looks exactly the same
  CHECK_NEAR(after.y, before.y, 1e-6);
  CHECK_EQ(e.ae.items[e.ae.layers[e.user_layer].item].w, 800);
  // the project links to a durable copy next to it, never to the temporary file the panel will delete
  std::string linked = e.ae.items[e.ae.layers[e.user_layer].item].path;
  CHECK(linked != up);
  CHECK(linked.find("Voxy Upscaled") != std::string::npos);
  CHECK(fs::path(linked).parent_path().parent_path() == fs::path(e.ae.path).parent_path());
  fs::remove(up);                                                      // the panel discards the temporary file after Apply
  CHECK(fs::exists(linked));
  CHECK_EQ(r["file"].get<std::string>(), linked);
}

TEST(apply_upscale_for_an_unsaved_project_keeps_its_copy_in_the_app_folder) {
  Env e;
  e.ae.path.clear();
  std::string up = path_str(e.tmp / "t.png");
  write_file_atomic(up, std::string("\x89PNG\r\n\x1a\n", 8) + std::string(40, 'x'));
  json r = e.cmds.apply_upscale({{"layer_id", e.user_layer}, {"image_path", up}});
  CHECK(r["file"].get<std::string>().find("upscaled") != std::string::npos && fs::exists(r["file"].get<std::string>()));
}

TEST(apply_upscale_only_touches_the_named_layer) {
  Env e;
  int64_t other = e.ae.add_user_layer(e.comp, "Other");
  e.ae.layers[other].item = e.ae.layers[e.user_layer].item;            // shares the same footage
  int64_t old_item = e.ae.layers[other].item;
  write_file_atomic(path_str(e.tmp / "u.png"), std::string("\x89PNG\r\n\x1a\n", 8) + std::string(40, 'x'));
  e.cmds.apply_upscale({{"layer_id", e.user_layer}, {"image_path", path_str(e.tmp / "u.png")}});
  CHECK_EQ(e.ae.layers[other].item, old_item);                         // other layers keep the old footage
  CHECK(e.ae.layers[e.user_layer].item != old_item);
  CHECK_NEAR(e.ae.layers[other].scale, 100, 1e-9);
}

TEST(apply_upscale_refuses_non_picture_layers_and_missing_files) {
  Env e;
  int64_t pre = e.ae.add_user_layer(e.comp, "A precomp", "precomp");
  write_file_atomic(path_str(e.tmp / "u.png"), "x");
  CHECK_CODE(e.cmds.apply_upscale({{"layer_id", pre}, {"image_path", path_str(e.tmp / "u.png")}}), "NOT_A_PICTURE_LAYER");
  CHECK_CODE(e.cmds.apply_upscale({{"layer_id", e.user_layer}, {"image_path", path_str(e.tmp / "missing.png")}}), "PART_FILE_MISSING");
  CHECK_CODE(e.cmds.apply_upscale({{"layer_id", 424242}, {"image_path", "x"}}), "NO_LAYER");
  CHECK(e.ae.undo_groups.empty());                                     // refused before changing anything
}

// ------------------------------------------------------------------ Save
TEST(save_project_reports_the_time_and_checks_the_file) {
  Env e;
  json r = e.cmds.save_project();
  CHECK(r["saved"].get<bool>() && !r["time"].get<std::string>().empty() && r["name"] == "MyProject.aep");
  CHECK(!e.ae.dirty);
  e.ae.project_save_fail = true;
  CHECK_CODE(e.cmds.save_project(), "SAVE_FAILED");
}

TEST(saving_an_untitled_project_asks_where_and_handles_cancel) {
  Env e;
  e.ae.path.clear();
  e.ae.dialog_cancels = true;
  json r = e.cmds.save_project();
  CHECK(!r["saved"].get<bool>() && r["cancelled"].get<bool>());
  e.ae.dialog_cancels = false;
  e.ae.dialog_path = path_str(e.tmp / "New.aep");
  CHECK(e.cmds.save_project()["saved"].get<bool>());
}

TEST(unknown_commands_and_bad_arguments_are_friendly) {
  Env e;
  json r = e.cmds.dispatch("format_my_disk", {});
  CHECK(!r["ok"].get<bool>() && r["error"]["code"] == "BAD_COMMAND");
  r = e.cmds.dispatch("add_to_project", {{"manifest", 5}});
  CHECK(!r["ok"].get<bool>() && r["error"]["code"] == "BAD_COMMAND");
  CHECK(e.cmds.dispatch("ping", {})["ok"].get<bool>());
}
