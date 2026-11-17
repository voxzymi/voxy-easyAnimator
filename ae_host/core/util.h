#pragma once
#include <cstdint>
#include <filesystem>
#include <string>

#include "types.h"

namespace voxy {
namespace fs = std::filesystem;

std::string now_stamp();                          // 20260131-142501 (local time)
std::string iso_now();                            // 2026-01-31T14:25:01
std::string read_file(const fs::path& p);         // whole file, "" if missing
bool write_file_atomic(const fs::path& p, const std::string& data);  // temp file + rename
json read_json(const fs::path& p, const json& fallback);
bool write_json_atomic(const fs::path& p, const json& j);
std::string random_token(size_t bytes = 18);      // URL-safe
int current_pid();
bool pid_alive(int pid);
std::string path_str(const fs::path& p);          // UTF-8 string of a path
}  // namespace voxy
