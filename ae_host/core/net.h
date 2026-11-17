// Tiny portable socket helpers (POSIX / Winsock) shared by the bridge and the service supervisor.
#pragma once
#include <string>

namespace voxy {
namespace net {

using Socket = long long;
constexpr Socket kInvalid = -1;

bool init();                                     // WSAStartup on Windows, no-op elsewhere
void close_socket(Socket s);
Socket listen_local(int port, int* bound_port);  // 127.0.0.1 only
Socket accept_with_timeout(Socket listener, int timeout_ms);
bool set_recv_timeout(Socket s, int ms);
long send_all(Socket s, const std::string& data);
long recv_some(Socket s, char* buf, size_t n);

struct HttpResponse {
  int status = 0;
  std::string body;
};
// Blocking HTTP/1.1 request to 127.0.0.1. Returns status 0 if the connection failed.
HttpResponse http_request(int port, const std::string& method, const std::string& path, const std::string& body = "",
                          const std::string& token = "", int timeout_ms = 5000, const std::string& host_header = "");

}  // namespace net
}  // namespace voxy
