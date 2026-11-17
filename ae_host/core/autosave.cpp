#include "autosave.h"

#include <algorithm>

namespace voxy {

AutosaveManager::AutosaveManager(fs::path data_dir, AutosaveConfig cfg) : data_dir_(std::move(data_dir)), cfg_(cfg) {}

fs::path AutosaveManager::backup_dir_for(const std::string& project_path, const fs::path& fallback) {
  if (project_path.empty()) return fallback / "autosave" / "untitled";
  return fs::path(project_path).parent_path() / "Voxy Autosave";
}

bool AutosaveManager::looks_like_aep(const std::string& head, size_t file_size, const AutosaveConfig& cfg) {
  if (file_size < cfg.min_bytes) return false;
  if (!cfg.check_magic) return true;
  return head.size() >= 12 && head.compare(0, 4, "RIFX") == 0 && head.compare(8, 4, "Egg!") == 0;
}

static std::string stem_of(const std::string& project_path) {
  std::string s = project_path.empty() ? "Untitled" : fs::path(project_path).stem().string();
  return s.empty() ? "Untitled" : s;
}

static bool is_aep(const std::string& path) {
  std::string e = fs::path(path).extension().string();
  std::transform(e.begin(), e.end(), e.begin(), ::tolower);
  return e == ".aep";
}

static std::string head_of(const fs::path& p, size_t n = 16) {
  std::string s = read_file(p);
  return s.substr(0, n);
}

json AutosaveManager::run(AeApi& api) {
  json out = {{"status", "failed"}};
  if (unsafe_) {
    out["status"] = "disabled";
    out["error"] = make_error("AUTOSAVE_UNSAFE").to_json();
    return out;
  }
  if (!api.project_dirty()) return {{"status", "skipped"}, {"reason", "nothing_changed"}};

  const std::string project = api.project_path();
  const fs::path dir = backup_dir_for(project, data_dir_);
  std::error_code ec;
  fs::create_directories(dir, ec);
  if (ec) {
    out["error"] = make_error("AUTOSAVE_FAILED", "cannot create " + path_str(dir)).to_json();
    return out;
  }
  const std::string stem = stem_of(project), stamp = now_stamp();
  const fs::path final_path = dir / (stem + "-autosave-" + stamp + ".aep");
  const fs::path temp = dir / ("~voxy-" + stamp + ".aep");          // a valid After Effects name, never listed as a backup
  if (!project.empty() && (fs::equivalent(final_path.parent_path(), fs::path(project).parent_path(), ec) && final_path.filename() == fs::path(project).filename())) {
    out["error"] = make_error("AUTOSAVE_FAILED", "backup name equals project name").to_json();
    return out;
  }

  // snapshot of the user's saved project, to prove we never touch it
  bool had_file = !project.empty() && fs::exists(project, ec);
  std::uintmax_t size_before = had_file ? fs::file_size(project, ec) : 0;
  auto mtime_before = had_file ? fs::last_write_time(project, ec) : fs::file_time_type{};

  try {
    api.save_copy(path_str(temp));
  } catch (const std::exception& e) {
    fs::remove(temp, ec);
    out["error"] = make_error("AUTOSAVE_FAILED", e.what()).to_json();
    return out;
  }

  bool touched = false;
  if (had_file) {
    std::uintmax_t sz = fs::file_size(project, ec);
    touched = (sz != size_before) || (fs::last_write_time(project, ec) != mtime_before);
  }
  const bool moved = api.project_path() != project;
  if (touched || moved) unsafe_ = true;

  std::error_code ec2;
  std::uintmax_t bytes = fs::exists(temp, ec2) ? fs::file_size(temp, ec2) : 0;
  if (!looks_like_aep(head_of(temp), static_cast<size_t>(bytes), cfg_) && is_aep(path_str(temp))) {
    fs::remove(temp, ec2);                                           // a broken copy is thrown away, never promoted
    out["error"] = make_error(unsafe_ ? "AUTOSAVE_UNSAFE" : "AUTOSAVE_FAILED", "backup copy was empty or damaged").to_json();
    out["status"] = unsafe_ ? "disabled" : "failed";
    return out;
  }
  fs::rename(temp, final_path, ec2);
  if (ec2) {
    fs::remove(temp, ec2);
    out["error"] = make_error("AUTOSAVE_FAILED", "cannot move the backup into place").to_json();
    return out;
  }
  add_index(project, final_path, static_cast<size_t>(bytes));
  prune(dir, stem);
  out = {{"status", unsafe_ ? "disabled" : "saved"}, {"backup", path_str(final_path)}, {"time", iso_now()}, {"size", bytes}};
  if (unsafe_) out["error"] = make_error("AUTOSAVE_UNSAFE", moved ? "project path changed" : "project file touched").to_json();
  return out;
}

void AutosaveManager::prune(const fs::path& dir, const std::string& stem) {
  std::vector<fs::path> mine;
  std::error_code ec;
  const std::string prefix = stem + "-autosave-";
  for (auto& e : fs::directory_iterator(dir, ec))
    if (e.is_regular_file() && e.path().filename().string().rfind(prefix, 0) == 0 && e.path().extension() == ".aep") mine.push_back(e.path());
  std::sort(mine.begin(), mine.end());                               // names carry the timestamp
  while (static_cast<int>(mine.size()) > cfg_.keep) {
    fs::remove(mine.front(), ec);
    mine.erase(mine.begin());
  }
}

void AutosaveManager::add_index(const std::string& project, const fs::path& backup, size_t size) {
  fs::path f = data_dir_ / "autosave_index.json";
  json idx = read_json(f, json::array());
  if (!idx.is_array()) idx = json::array();
  idx.push_back({{"project", project}, {"backup", path_str(backup)}, {"time", iso_now()}, {"size", size}});
  while (idx.size() > 100) idx.erase(idx.begin());
  write_json_atomic(f, idx);
}

bool AutosaveManager::is_known_backup(const std::string& path) const {
  json idx = read_json(data_dir_ / "autosave_index.json", json::array());
  for (auto& e : idx)
    if (e.value("backup", "") == path && fs::exists(path)) return true;
  return false;
}

json AutosaveManager::list_backups(const std::string& project_path) {
  json out = json::array();
  json idx = read_json(data_dir_ / "autosave_index.json", json::array());
  for (auto it = idx.rbegin(); it != idx.rend(); ++it)
    if ((project_path.empty() || it->value("project", "") == project_path) && fs::exists(it->value("backup", ""))) out.push_back(*it);
  return out;
}

void AutosaveManager::note_manual_save(const std::string&) { /* the project file's own modified time is the record */ }

json AutosaveManager::find_recovery(AeApi&) {
  json idx = read_json(data_dir_ / "autosave_index.json", json::array());
  for (auto it = idx.rbegin(); it != idx.rend(); ++it) {
    std::string backup = it->value("backup", ""), project = it->value("project", "");
    std::error_code ec;
    if (!fs::exists(backup, ec)) continue;
    if (!project.empty() && fs::exists(project, ec) && fs::last_write_time(project, ec) >= fs::last_write_time(backup, ec))
      return nullptr;                                                // the manual save is newer: nothing was lost
    return {{"backup", backup}, {"project", project}, {"time", it->value("time", "")}, {"size", it->value("size", 0)},
            {"explanation", "After Effects closed unexpectedly last time. Voxy had saved a backup copy of your project at " + it->value("time", "") +
                            ". It may contain work your last Save does not. Opening it gives you that work back. Your own project file has not been changed."}};
  }
  return nullptr;
}

json AutosaveManager::begin_session(AeApi& api) {
  fs::path lock = data_dir_ / "session.lock";
  json recovery = nullptr;
  std::string prev = read_file(lock);
  if (!prev.empty()) {
    int pid = 0;
    try { pid = std::stoi(prev); } catch (...) {}
    if (!pid_alive(pid)) recovery = find_recovery(api);   // the last session did not end cleanly
  }
  write_file_atomic(lock, std::to_string(current_pid()));
  return {{"recovery", recovery}};
}

void AutosaveManager::end_session() {
  std::error_code ec;
  fs::remove(data_dir_ / "session.lock", ec);
}

}  // namespace voxy
