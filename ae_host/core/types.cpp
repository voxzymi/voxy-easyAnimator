#include "types.h"

#include <map>
#include <tuple>

namespace voxy {

namespace {
struct Text {
  const char* what;
  const char* todo;
  const char* topic;
};
const std::map<std::string, Text>& catalog() {
  static const std::map<std::string, Text> c = {
      {"NO_PROJECT", {"No project is open in After Effects.", "Open or create a project, then try again.", "troubleshooting"}},
      {"NO_COMP", {"There is no composition open.", "Open a composition and select your character layer.", "no-layer"}},
      {"NO_LAYER", {"No layer is selected.", "Click your character layer in the timeline.", "no-layer"}},
      {"MANY_LAYERS", {"More than one layer is selected.", "Select just one layer.", "no-layer"}},
      {"FRAME_EXPORT_FAILED", {"Voxy could not get the picture from After Effects.", "Make sure the layer is visible at the current time, then try again.", "blank-preview"}},
      {"SIZE_MISMATCH", {"The composition changed size since Make Layers.", "Run Make Layers again on this composition.", "part-missing"}},
      {"NOT_A_PICTURE_LAYER", {"Upscale 2x works on picture layers.", "Select a layer that is a still picture, such as a PNG or PSD layer.", "upscale"}},
      {"PART_FILE_MISSING", {"One of the part pictures is missing.", "Run Make Layers again, then press Add to Project.", "part-missing"}},
      {"ADD_FAILED", {"After Effects could not add the parts.", "Nothing was changed. Try again. If it keeps happening, restart After Effects.", "part-missing"}},
      {"NOTHING_TO_ADD", {"Every part is hidden, so there is nothing to add.", "Turn on the eye of at least one part.", "eye-buttons"}},
      {"NOTHING_TO_RESTORE", {"There is nothing to restore.", "Restore only undoes the last Add to Project.", "restore"}},
      {"UNKNOWN_PART", {"A part in the request is not in the Make Layers result.", "Run Make Layers again.", "part-missing"}},
      {"SAVE_FAILED", {"The project could not be saved.", "Check that the folder is writable and the disk is not full, then use File > Save As.", "project-did-not-save"}},
      {"SAVE_NO_PATH", {"This project has not been saved yet.", "Choose a name and place for the project when After Effects asks.", "project-did-not-save"}},
      {"AUTOSAVE_FAILED", {"The automatic backup did not work. Your last saved project is untouched.", "Press Save Project to save now.", "project-did-not-save"}},
      {"AUTOSAVE_UNSAFE", {"Automatic backups were turned off because After Effects changed which file the project points to.", "Use File > Save As to pick your project file again, then press Save Project.", "project-did-not-save"}},
      {"BACKUP_NOT_FOUND", {"That backup could not be found.", "Choose another backup, or open the folder next to your project.", "crash-recovery"}},
      {"SERVICE_START_FAILED", {"Voxy's helper program did not start.", "Close and reopen the panel. If it keeps happening, run the installer again.", "service-wont-start"}},
      {"BAD_COMMAND", {"Voxy sent After Effects a request it did not understand.", "Close and reopen the panel.", "troubleshooting"}},
      {"UNAUTHORIZED", {"Voxy could not connect to After Effects.", "Close and reopen the panel. If it keeps happening, restart After Effects.", "panel-does-not-appear"}},
      {"TIMEOUT", {"After Effects took too long to answer.", "Wait for After Effects to finish what it is doing, then try again.", "troubleshooting"}},
      {"INTERNAL", {"Something went wrong inside Voxy.", "Try again. If it keeps happening, open the log file from the help page.", "troubleshooting"}},
  };
  return c;
}
}  // namespace

HostError::HostError(std::string c, std::string d) : std::runtime_error(c + (d.empty() ? "" : ": " + d)), code(std::move(c)), detail(std::move(d)) {}

json HostError::to_json() const {
  auto it = catalog().find(code);
  const Text& t = it != catalog().end() ? it->second : catalog().at("INTERNAL");
  return json{{"code", it != catalog().end() ? code : "INTERNAL"}, {"user_message", t.what}, {"what_to_do", t.todo}, {"help_topic", t.topic}, {"detail", detail}};
}

HostError make_error(const std::string& code, const std::string& detail) { return HostError(code, detail); }

}  // namespace voxy
