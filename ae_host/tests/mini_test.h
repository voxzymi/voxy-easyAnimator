// A tiny test harness (no external dependency).
#pragma once
#include <cmath>
#include <functional>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

namespace mt {
struct Case { std::string name; std::function<void()> fn; };
inline std::vector<Case>& cases() { static std::vector<Case> c; return c; }
struct Reg { Reg(const std::string& n, std::function<void()> f) { cases().push_back({n, std::move(f)}); } };
struct Failure { std::string msg; };
inline int run_all(const std::string& filter = "") {
  int fails = 0, ran = 0;
  for (auto& c : cases()) {
    if (!filter.empty() && c.name.find(filter) == std::string::npos) continue;
    ++ran;
    try { c.fn(); std::cout << "  ok   " << c.name << "\n"; }
    catch (const Failure& f) { ++fails; std::cout << "  FAIL " << c.name << ": " << f.msg << "\n"; }
    catch (const std::exception& e) { ++fails; std::cout << "  FAIL " << c.name << ": exception " << e.what() << "\n"; }
  }
  std::cout << ran - fails << "/" << ran << " passed\n";
  return fails ? 1 : 0;
}
}  // namespace mt

#define TEST(name) static void test_##name(); static mt::Reg reg_##name(#name, test_##name); static void test_##name()
#define CHECK(c) do { if (!(c)) { std::ostringstream o; o << __FILE__ << ":" << __LINE__ << " CHECK(" #c ")"; throw mt::Failure{o.str()}; } } while (0)
#define CHECK_EQ(a, b) do { auto va = (a); auto vb = (b); if (!(va == vb)) { std::ostringstream o; o << __FILE__ << ":" << __LINE__ << " " #a " == " #b "  (" << va << " vs " << vb << ")"; throw mt::Failure{o.str()}; } } while (0)
#define CHECK_NEAR(a, b, eps) do { double va = (a), vb = (b); if (std::fabs(va - vb) > (eps)) { std::ostringstream o; o << __FILE__ << ":" << __LINE__ << " " #a " ~ " #b " (" << va << " vs " << vb << ")"; throw mt::Failure{o.str()}; } } while (0)
#define CHECK_CODE(expr, CODE) do { bool ok = false; std::string got = "(no error)"; try { expr; } catch (const voxy::HostError& e) { got = e.code; ok = (got == CODE); } if (!ok) { std::ostringstream o; o << __FILE__ << ":" << __LINE__ << " expected error " CODE ", got " << got; throw mt::Failure{o.str()}; } } while (0)
