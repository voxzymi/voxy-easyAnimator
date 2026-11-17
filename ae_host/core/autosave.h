// Autosave and crash recovery.
//
// Rules (tested in tests/test_autosave.cpp):
//  * An autosave writes a backup COPY next to the project (in "Voxy Autosave/"); it never writes the project file.
//  * The copy is written to a temporary name, checked, and only then renamed into place. A failed or broken write
//    leaves every earlier backup, and the user's last manual save, exactly as they were.
//  * If saving a copy changes which file the project points to, or touches the project file, autosave switches itself
//    off and says why instead of carrying on.
//  * A session lock file tells the next start whether the last session ended cleanly; after a crash the newest
//    backup that is newer than the last manual save is offered for recovery.
#pragma once
#include <string>
#include <vector>

#include "ae_api.h"
#include "util.h"

namespace voxy {

struct AutosaveConfig {
  int keep = 10;                 // backups kept per project
  size_t min_bytes = 64;         // smaller than this is not a project file
  bool check_magic = true;       // After Effects .aep files start with "RIFX" ... "Egg!"
};

class AutosaveManager {
 public:
  AutosaveManager(fs::path data_dir, AutosaveConfig cfg = {});

  json run(AeApi& api);                       // one autosave attempt
  json begin_session(AeApi& api);             // call when the panel opens; returns {recovery: {...} | null}
  void end_session();                         // call on a clean close
  json find_recovery(AeApi& api);             // the backup to offer, or null
  json list_backups(const std::string& project_path);
  bool is_known_backup(const std::string& path) const;
  bool unsafe() const { return unsafe_; }
  void reset_unsafe() { unsafe_ = false; }
  void note_manual_save(const std::string& project_path);

  static fs::path backup_dir_for(const std::string& project_path, const fs::path& fallback);
  static bool looks_like_aep(const std::string& head, size_t file_size, const AutosaveConfig& cfg);

 private:
  void add_index(const std::string& project, const fs::path& backup, size_t size);
  void prune(const fs::path& dir, const std::string& stem);
  fs::path data_dir_;
  AutosaveConfig cfg_;
  bool unsafe_ = false;
};

}  // namespace voxy
