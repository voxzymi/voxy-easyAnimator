#include "env.h"

// ------------------------------------------------------------------ autosave
static std::vector<fs::path> backups_in(const fs::path& dir) {
  std::vector<fs::path> v;
  if (fs::exists(dir)) for (auto& e : fs::directory_iterator(dir)) v.push_back(e.path());
  std::sort(v.begin(), v.end());
  return v;
}

TEST(autosave_writes_a_backup_copy_next_to_the_project_and_never_the_project) {
  Env e;
  std::string before = read_file(e.ae.path);
  json r = e.cmds.autosave();
  CHECK_EQ(r["status"].get<std::string>(), std::string("saved"));
  fs::path dir = fs::path(e.ae.path).parent_path() / "Voxy Autosave";
  auto files = backups_in(dir);
  CHECK_EQ(files.size(), (size_t)1);
  CHECK(files[0].filename().string().rfind("MyProject-autosave-", 0) == 0 && files[0].extension() == ".aep");
  CHECK(AutosaveManager::looks_like_aep(read_file(files[0]).substr(0, 16), (size_t)fs::file_size(files[0]), AutosaveConfig{}));
  CHECK_EQ(read_file(e.ae.path), before);                              // the user's last manual save is byte-for-byte unchanged
  CHECK(e.ae.dirty);                                                   // and the project is still "unsaved": autosave is not a Save
  CHECK(!r["time"].get<std::string>().empty());                        // the panel can say when it ran
}

TEST(a_failed_autosave_never_overwrites_the_last_manual_save_or_earlier_backups) {
  Env e;
  CHECK_EQ(e.cmds.autosave()["status"].get<std::string>(), std::string("saved"));
  fs::path dir = fs::path(e.ae.path).parent_path() / "Voxy Autosave";
  auto good = backups_in(dir);
  std::string good_bytes = read_file(good[0]), manual = read_file(e.ae.path);
  std::this_thread::sleep_for(std::chrono::milliseconds(1100));      // a new timestamp
  e.ae.copy_fail = true;
  json r = e.cmds.autosave();
  CHECK_EQ(r["status"].get<std::string>(), std::string("failed"));
  CHECK_EQ(r["error"]["code"].get<std::string>(), std::string("AUTOSAVE_FAILED"));
  CHECK(r["error"]["what_to_do"].is_string());
  CHECK_EQ(read_file(e.ae.path), manual);                              // manual save untouched
  auto after = backups_in(dir);
  CHECK_EQ(after.size(), (size_t)1);                                   // no half-written file left behind
  CHECK_EQ(read_file(after[0]), good_bytes);                           // the earlier backup is untouched
  CHECK(!e.autosave.unsafe());                                         // a plain failure does not disable autosave
}

TEST(a_broken_copy_is_thrown_away_and_never_becomes_a_backup) {
  for (int mode = 0; mode < 2; ++mode) {
    Env e;
    std::string manual = read_file(e.ae.path);
    e.ae.copy_corrupt = mode == 0;
    e.ae.copy_empty = mode == 1;
    json r = e.cmds.autosave();
    CHECK_EQ(r["status"].get<std::string>(), std::string("failed"));
    CHECK(backups_in(fs::path(e.ae.path).parent_path() / "Voxy Autosave").empty());
    CHECK_EQ(read_file(e.ae.path), manual);
  }
}

TEST(autosave_turns_itself_off_if_saving_a_copy_moves_the_project) {
  Env e;
  e.ae.copy_changes_path = true;
  std::string manual = read_file(e.ae.path);
  json r = e.cmds.autosave();
  CHECK_EQ(r["status"].get<std::string>(), std::string("disabled"));
  CHECK_EQ(r["error"]["code"].get<std::string>(), std::string("AUTOSAVE_UNSAFE"));
  CHECK(e.autosave.unsafe());
  e.ae.path = path_str(e.tmp / "MyProject.aep");                       // user re-points the project
  CHECK_EQ(e.cmds.autosave()["status"].get<std::string>(), std::string("disabled"));   // stays off until reset
  CHECK_EQ(read_file(e.ae.path), manual);
  e.cmds.dispatch("autosave_reset", {});
  e.ae.copy_changes_path = false;
  CHECK_EQ(e.cmds.autosave()["status"].get<std::string>(), std::string("saved"));
}

TEST(autosave_turns_itself_off_if_it_touches_the_project_file) {
  Env e;
  e.ae.copy_touches_project = true;
  std::this_thread::sleep_for(std::chrono::milliseconds(50));
  json r = e.cmds.autosave();
  CHECK_EQ(r["status"].get<std::string>(), std::string("disabled"));
  CHECK(e.autosave.unsafe());
}

TEST(autosave_skips_when_nothing_changed) {
  Env e;
  e.ae.dirty = false;
  CHECK_EQ(e.cmds.autosave()["status"].get<std::string>(), std::string("skipped"));
  CHECK(backups_in(fs::path(e.ae.path).parent_path() / "Voxy Autosave").empty());
}

TEST(autosave_keeps_only_the_newest_backups) {
  Env e;
  AutosaveConfig cfg;
  cfg.keep = 3;
  AutosaveManager am(e.data, cfg);
  for (int i = 0; i < 5; ++i) {
    CHECK_EQ(am.run(e.ae)["status"].get<std::string>(), std::string("saved"));
    std::this_thread::sleep_for(std::chrono::milliseconds(1050));
  }
  auto files = backups_in(fs::path(e.ae.path).parent_path() / "Voxy Autosave");
  CHECK_EQ(files.size(), (size_t)3);
}

TEST(an_unsaved_project_is_backed_up_in_the_app_data_folder) {
  Env e;
  e.ae.path.clear();
  json r = e.cmds.autosave();
  CHECK_EQ(r["status"].get<std::string>(), std::string("saved"));
  CHECK(r["backup"].get<std::string>().find("untitled") != std::string::npos);
  CHECK(r["backup"].get<std::string>().find("Untitled-autosave-") != std::string::npos);
}

// ------------------------------------------------------------------ crash recovery
TEST(after_a_crash_the_newest_backup_newer_than_the_last_save_is_offered) {
  Env e;
  e.cmds.session_begin();                                              // panel opens: lock written
  std::this_thread::sleep_for(std::chrono::milliseconds(50));
  e.cmds.autosave();                                                   // a backup newer than the manual save
  write_file_atomic(e.data / "session.lock", "999999");                // simulate: that session's process is gone
  AutosaveManager next(e.data);                                        // AE restarts, panel opens again
  json r = next.begin_session(e.ae);
  CHECK(r["recovery"].is_object());
  CHECK(r["recovery"]["backup"].get<std::string>().find("MyProject-autosave-") != std::string::npos);
  std::string text = r["recovery"]["explanation"];
  CHECK(text.find("closed unexpectedly") != std::string::npos && text.find("has not been changed") != std::string::npos);   // plain language
  CHECK(text.find("RIFX") == std::string::npos && text.find("aep") == std::string::npos);
}

TEST(a_clean_exit_leaves_no_recovery_offer) {
  Env e;
  e.cmds.session_begin();
  e.cmds.autosave();
  e.cmds.session_end();
  AutosaveManager next(e.data);
  CHECK(next.begin_session(e.ae)["recovery"].is_null());
}

TEST(no_recovery_when_the_manual_save_is_newer_than_every_backup) {
  Env e;
  e.cmds.autosave();
  std::this_thread::sleep_for(std::chrono::milliseconds(1100));
  write_file_atomic(e.ae.path, FakeAe::aep_bytes("saved later"));      // the user saved after the backup
  write_file_atomic(e.data / "session.lock", "999999");
  AutosaveManager next(e.data);
  CHECK(next.begin_session(e.ae)["recovery"].is_null());               // nothing was lost
}

TEST(a_running_other_session_is_not_mistaken_for_a_crash) {
  Env e;
  e.cmds.autosave();
  write_file_atomic(e.data / "session.lock", std::to_string(current_pid()));   // a live process owns the lock
  AutosaveManager next(e.data);
  CHECK(next.begin_session(e.ae)["recovery"].is_null());
}

TEST(only_backups_voxy_wrote_can_be_opened) {
  Env e;
  json r = e.cmds.autosave();
  std::string backup = r["backup"];
  json ok = e.cmds.dispatch("recovery_open", {{"backup", backup}});
  CHECK(ok["ok"].get<bool>());
  CHECK_EQ(e.ae.opened.size(), (size_t)1);
  json bad = e.cmds.dispatch("recovery_open", {{"backup", path_str(e.tmp / "evil.aep")}});
  CHECK(!bad["ok"].get<bool>() && bad["error"]["code"] == "BACKUP_NOT_FOUND");
  json bad2 = e.cmds.dispatch("recovery_open", {{"backup", "/etc/passwd"}});
  CHECK(!bad2["ok"].get<bool>());
  CHECK_EQ(e.ae.opened.size(), (size_t)1);
  CHECK_EQ(e.cmds.backups({{"project", e.ae.path}})["backups"].size(), (size_t)1);
}
