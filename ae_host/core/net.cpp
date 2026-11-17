#include <cstdlib>
#include "net.h"

#include <cstring>

#ifdef _WIN32
#include <winsock2.h>
#include <ws2tcpip.h>
#pragma comment(lib, "ws2_32.lib")
#else
#include <arpa/inet.h>
#include <netinet/in.h>
#include <poll.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <unistd.h>
#endif

namespace voxy {
namespace net {

bool init() {
#ifdef _WIN32
  static bool done = false;
  if (!done) {
    WSADATA d;
    if (WSAStartup(MAKEWORD(2, 2), &d) != 0) return false;
    done = true;
  }
#endif
  return true;
}

void close_socket(Socket s) {
  if (s == kInvalid) return;
#ifdef _WIN32
  closesocket(static_cast<SOCKET>(s));
#else
  ::close(static_cast<int>(s));
#endif
}

Socket listen_local(int port, int* bound_port) {
  init();
  Socket s = static_cast<Socket>(::socket(AF_INET, SOCK_STREAM, 0));
  if (s == kInvalid) return kInvalid;
  int one = 1;
  ::setsockopt(static_cast<int>(s), SOL_SOCKET, SO_REUSEADDR, reinterpret_cast<const char*>(&one), sizeof one);
  sockaddr_in a{};
  a.sin_family = AF_INET;
  a.sin_port = htons(static_cast<uint16_t>(port));
  a.sin_addr.s_addr = htonl(INADDR_LOOPBACK);                        // never reachable from another computer
  if (::bind(static_cast<int>(s), reinterpret_cast<sockaddr*>(&a), sizeof a) != 0 || ::listen(static_cast<int>(s), 16) != 0) {
    close_socket(s);
    return kInvalid;
  }
  socklen_t len = sizeof a;
  ::getsockname(static_cast<int>(s), reinterpret_cast<sockaddr*>(&a), &len);
  if (bound_port) *bound_port = ntohs(a.sin_port);
  return s;
}

Socket accept_with_timeout(Socket listener, int timeout_ms) {
#ifdef _WIN32
  fd_set fds;
  FD_ZERO(&fds);
  FD_SET(static_cast<SOCKET>(listener), &fds);
  timeval tv{timeout_ms / 1000, (timeout_ms % 1000) * 1000};
  if (::select(0, &fds, nullptr, nullptr, &tv) <= 0) return kInvalid;
  return static_cast<Socket>(::accept(static_cast<SOCKET>(listener), nullptr, nullptr));
#else
  pollfd p{static_cast<int>(listener), POLLIN, 0};
  if (::poll(&p, 1, timeout_ms) <= 0) return kInvalid;
  return static_cast<Socket>(::accept(static_cast<int>(listener), nullptr, nullptr));
#endif
}

bool set_recv_timeout(Socket s, int ms) {
#ifdef _WIN32
  DWORD t = static_cast<DWORD>(ms);
  return ::setsockopt(static_cast<SOCKET>(s), SOL_SOCKET, SO_RCVTIMEO, reinterpret_cast<const char*>(&t), sizeof t) == 0;
#else
  timeval tv{ms / 1000, (ms % 1000) * 1000};
  return ::setsockopt(static_cast<int>(s), SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof tv) == 0;
#endif
}

long send_all(Socket s, const std::string& data) {
  size_t off = 0;
  while (off < data.size()) {
#ifdef _WIN32
    int n = ::send(static_cast<SOCKET>(s), data.data() + off, static_cast<int>(data.size() - off), 0);
#else
    ssize_t n = ::send(static_cast<int>(s), data.data() + off, data.size() - off, MSG_NOSIGNAL);
#endif
    if (n <= 0) return -1;
    off += static_cast<size_t>(n);
  }
  return static_cast<long>(off);
}

long recv_some(Socket s, char* buf, size_t n) {
#ifdef _WIN32
  return ::recv(static_cast<SOCKET>(s), buf, static_cast<int>(n), 0);
#else
  return static_cast<long>(::recv(static_cast<int>(s), buf, n, 0));
#endif
}

HttpResponse http_request(int port, const std::string& method, const std::string& path, const std::string& body,
                          const std::string& token, int timeout_ms, const std::string& host_header) {
  init();
  HttpResponse r;
  Socket s = static_cast<Socket>(::socket(AF_INET, SOCK_STREAM, 0));
  if (s == kInvalid) return r;
  sockaddr_in a{};
  a.sin_family = AF_INET;
  a.sin_port = htons(static_cast<uint16_t>(port));
  a.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
  set_recv_timeout(s, timeout_ms);
  if (::connect(static_cast<int>(s), reinterpret_cast<sockaddr*>(&a), sizeof a) != 0) {
    close_socket(s);
    return r;
  }
  std::string req = method + " " + path + " HTTP/1.1\r\nHost: " + (host_header.empty() ? "127.0.0.1:" + std::to_string(port) : host_header) +
                    "\r\nConnection: close\r\nContent-Length: " + std::to_string(body.size()) + "\r\n";
  if (!token.empty()) req += "X-Voxy-Token: " + token + "\r\n";
  if (!body.empty()) req += "Content-Type: application/json\r\n";
  req += "\r\n" + body;
  if (send_all(s, req) < 0) {
    close_socket(s);
    return r;
  }
  std::string resp;
  char buf[4096];
  long n;
  while ((n = recv_some(s, buf, sizeof buf)) > 0) resp.append(buf, static_cast<size_t>(n));
  close_socket(s);
  size_t sp = resp.find(' ');
  if (sp == std::string::npos) return r;
  r.status = std::atoi(resp.c_str() + sp + 1);
  size_t hb = resp.find("\r\n\r\n");
  if (hb != std::string::npos) r.body = resp.substr(hb + 4);
  return r;
}

}  // namespace net
}  // namespace voxy
