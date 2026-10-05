#pragma once

// Minimal socket/file portability shim used by Backburner's split-prefill
// protocol. Kept intentionally small so iOS/macOS/Linux behavior stays
// identical while Windows uses Winsock2.

#include <algorithm>
#include <chrono>
#include <climits>
#include <cstdio>
#include <cstdint>
#include <string>
#include <stdexcept>
#include <thread>

#ifdef _WIN32
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>
#include <io.h>
#else
#include <arpa/inet.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <sys/socket.h>
#include <unistd.h>
#include <cerrno>
#include <cstring>
#endif

namespace spt {

#ifdef _WIN32
using socket_handle_t = SOCKET;
using socket_len_t = int;
constexpr socket_handle_t invalid_socket = INVALID_SOCKET;

inline void ensure_socket_runtime() {
    struct winsock_runtime {
        winsock_runtime() {
            WSADATA data{};
            const int rc = WSAStartup(MAKEWORD(2, 2), &data);
            if (rc != 0) {
                throw std::runtime_error("WSAStartup failed: " + std::to_string(rc));
            }
        }
        ~winsock_runtime() { WSACleanup(); }
    };
    static winsock_runtime runtime;
    (void) runtime;
}

inline bool socket_is_invalid(socket_handle_t s) { return s == invalid_socket; }
inline int socket_last_error() { return WSAGetLastError(); }
inline bool socket_interrupted(int e) { return e == WSAEINTR; }

inline std::string socket_error_string(int e) {
    char * raw = nullptr;
    const DWORD n = FormatMessageA(
        FORMAT_MESSAGE_ALLOCATE_BUFFER | FORMAT_MESSAGE_FROM_SYSTEM | FORMAT_MESSAGE_IGNORE_INSERTS,
        nullptr, (DWORD) e, MAKELANGID(LANG_NEUTRAL, SUBLANG_DEFAULT),
        reinterpret_cast<char *>(&raw), 0, nullptr);
    std::string msg = n && raw ? std::string(raw, n) : ("Winsock error " + std::to_string(e));
    if (raw) LocalFree(raw);
    while (!msg.empty() && (msg.back() == '\r' || msg.back() == '\n' || msg.back() == ' ')) msg.pop_back();
    return msg;
}

inline void socket_close(socket_handle_t s) {
    if (!socket_is_invalid(s)) closesocket(s);
}
inline void socket_shutdown(socket_handle_t s) {
    if (!socket_is_invalid(s)) ::shutdown(s, SD_BOTH);
}
inline int socket_set_opt(socket_handle_t s, int level, int name, const void * value, int len) {
    return ::setsockopt(s, level, name, reinterpret_cast<const char *>(value), len);
}
inline void socket_set_recv_timeout(socket_handle_t s, int seconds) {
    const DWORD ms = seconds > 0 ? (DWORD) seconds * 1000u : 0u;
    socket_set_opt(s, SOL_SOCKET, SO_RCVTIMEO, &ms, (int) sizeof(ms));
}
inline int64_t socket_send(socket_handle_t s, const char * data, size_t n, int flags) {
    const int chunk = (int) std::min<size_t>(n, INT_MAX);
    return (int64_t) ::send(s, data, chunk, flags);
}
inline int64_t socket_recv(socket_handle_t s, char * data, size_t n, int flags) {
    const int chunk = (int) std::min<size_t>(n, INT_MAX);
    return (int64_t) ::recv(s, data, chunk, flags);
}
inline int file_seek64(FILE * f, int64_t off, int whence) { return _fseeki64(f, off, whence); }
inline int64_t file_tell64(FILE * f) { return _ftelli64(f); }
inline int file_unlink(const char * p) { return _unlink(p); }
inline void sleep_us(uint64_t us) { std::this_thread::sleep_for(std::chrono::microseconds(us)); }

#else
using socket_handle_t = int;
using socket_len_t = socklen_t;
constexpr socket_handle_t invalid_socket = -1;
inline void ensure_socket_runtime() {}
inline bool socket_is_invalid(socket_handle_t s) { return s < 0; }
inline int socket_last_error() { return errno; }
inline bool socket_interrupted(int e) { return e == EINTR; }
inline std::string socket_error_string(int e) { return strerror(e); }
inline void socket_close(socket_handle_t s) { if (!socket_is_invalid(s)) close(s); }
inline void socket_shutdown(socket_handle_t s) { if (!socket_is_invalid(s)) ::shutdown(s, SHUT_RDWR); }
inline int socket_set_opt(socket_handle_t s, int level, int name, const void * value, int len) {
    return ::setsockopt(s, level, name, value, (socklen_t) len);
}
inline void socket_set_recv_timeout(socket_handle_t s, int seconds) {
    timeval tv = { seconds, 0 };
    socket_set_opt(s, SOL_SOCKET, SO_RCVTIMEO, &tv, (int) sizeof(tv));
}
inline int64_t socket_send(socket_handle_t s, const char * data, size_t n, int flags) {
    return (int64_t) ::send(s, data, n, flags);
}
inline int64_t socket_recv(socket_handle_t s, char * data, size_t n, int flags) {
    return (int64_t) ::recv(s, data, n, flags);
}
inline int file_seek64(FILE * f, int64_t off, int whence) { return fseeko(f, (off_t) off, whence); }
inline int64_t file_tell64(FILE * f) { return (int64_t) ftello(f); }
inline int file_unlink(const char * p) { return unlink(p); }
inline void sleep_us(uint64_t us) { std::this_thread::sleep_for(std::chrono::microseconds(us)); }
#endif

} // namespace spt
