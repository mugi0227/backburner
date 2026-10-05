#!/usr/bin/env python3
"""Apply the native-Windows split-prefill portability changes to backburner-llama.cpp.

This is a deterministic patch helper for the upstream submodule revision used by
Backburner (1839b781). The parent repository retains the upstream gitlink;
Windows builds apply this checked patch without maintaining a second fork.
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import tempfile
from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise RuntimeError(f"{label}: expected one match, found {n}")
    return text.replace(old, new, 1)


def patch_tail_server(path: Path) -> None:
    s = path.read_text(encoding="utf-8")
    s = replace_once(s,
'''#include <arpa/inet.h>\n#include <netinet/in.h>\n#include <netinet/tcp.h>\n#include <sys/socket.h>\n#include <unistd.h>\n''',
'''#include "socket-compat.h"\n''', "tail-server platform includes")

    for old, new in [
        ("inline void send_all(int fd, const void * p, size_t n)", "inline void send_all(socket_handle_t fd, const void * p, size_t n)"),
        ("inline void recv_all(int fd, void * p, size_t n)", "inline void recv_all(socket_handle_t fd, void * p, size_t n)"),
        ("inline void send_msg(int fd, uint32_t type", "inline void send_msg(socket_handle_t fd, uint32_t type"),
        ("inline msg_hdr recv_hdr(int fd)", "inline msg_hdr recv_hdr(socket_handle_t fd)"),
        ("inline void tune_socket(int fd)", "inline void tune_socket(socket_handle_t fd)"),
        ("using accept_filter_fn = std::function<bool(int fd, std::string & why)>;", "using accept_filter_fn = std::function<bool(socket_handle_t fd, std::string & why)>;"),
        ("inline bool accept_loopback_only(int fd, std::string & why)", "inline bool accept_loopback_only(socket_handle_t fd, std::string & why)"),
        ("socklen_t pl = sizeof p;", "socket_len_t pl = (socket_len_t) sizeof p;"),
        ("static void send_err(int fd, const std::string & e)", "static void send_err(socket_handle_t fd, const std::string & e)"),
        ("void handle(int fd)", "void handle(socket_handle_t fd)"),
        ("void send_state(int fd)", "void send_state(socket_handle_t fd)"),
    ]:
        s = replace_once(s, old, new, old)

    s = replace_once(s,
'''#ifdef MSG_NOSIGNAL\n        ssize_t w = ::send(fd, c, n, MSG_NOSIGNAL);   // a dead peer is an error, not a SIGPIPE that kills the process\n#else\n        ssize_t w = ::send(fd, c, n, 0);              // SO_NOSIGPIPE is set in tune_socket (macOS/iOS)\n#endif\n        if (w < 0 && errno == EINTR) continue;\n        if (w <= 0) throw std::runtime_error(std::string("send: ") + strerror(errno));\n''',
'''#ifdef MSG_NOSIGNAL\n        const int64_t w = socket_send(fd, c, n, MSG_NOSIGNAL);   // a dead peer is an error, not a SIGPIPE that kills the process\n#else\n        const int64_t w = socket_send(fd, c, n, 0);              // SO_NOSIGPIPE is set in tune_socket (macOS/iOS)\n#endif\n        const int se = w < 0 ? socket_last_error() : 0;\n        if (w < 0 && socket_interrupted(se)) continue;\n        if (w <= 0) throw std::runtime_error(std::string("send: ") + socket_error_string(se));\n''', "send_all body")

    s = replace_once(s,
'''        ssize_t r = ::recv(fd, c, n, 0);\n        if (r < 0 && errno == EINTR) continue;\n        if (r == 0) throw std::runtime_error("connection closed");\n        if (r < 0) throw std::runtime_error(std::string("recv: ") + strerror(errno));\n''',
'''        const int64_t r = socket_recv(fd, c, n, 0);\n        const int se = r < 0 ? socket_last_error() : 0;\n        if (r < 0 && socket_interrupted(se)) continue;\n        if (r == 0) throw std::runtime_error("connection closed");\n        if (r < 0) throw std::runtime_error(std::string("recv: ") + socket_error_string(se));\n''', "recv_all body")

    # The option wrapper handles Winsock's const-char* signature while preserving POSIX behavior.
    s = s.replace("setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof(one));",
                  "socket_set_opt(fd, IPPROTO_TCP, TCP_NODELAY, &one, (int) sizeof(one));")
    s = s.replace("setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &one, sizeof(one));",
                  "socket_set_opt(fd, SOL_SOCKET, SO_NOSIGPIPE, &one, (int) sizeof(one));")
    s = s.replace("setsockopt(fd, SOL_SOCKET, SO_SNDBUF, &sz, sizeof(sz))",
                  "socket_set_opt(fd, SOL_SOCKET, SO_SNDBUF, &sz, (int) sizeof(sz))")
    s = s.replace("setsockopt(fd, SOL_SOCKET, SO_RCVBUF, &sz, sizeof(sz))",
                  "socket_set_opt(fd, SOL_SOCKET, SO_RCVBUF, &sz, (int) sizeof(sz))")

    s = replace_once(s,
'''        fseeko(f, 0, SEEK_END); file_bytes_ = (uint64_t) ftello(f); fclose(f);''',
'''        file_seek64(f, 0, SEEK_END); file_bytes_ = (uint64_t) file_tell64(f); fclose(f);''', "file size")

    old_serve = '''    std::string serve(int port) {\n        int srv = socket(AF_INET, SOCK_STREAM, 0);\n        if (srv < 0) return std::string("socket: ") + strerror(errno);\n        int one = 1;\n        setsockopt(srv, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));\n        sockaddr_in addr = {};\n        addr.sin_family = AF_INET;\n        addr.sin_port = htons((uint16_t) port);\n        addr.sin_addr.s_addr = htonl(INADDR_ANY);\n        if (bind(srv, (sockaddr *) &addr, sizeof(addr)) != 0 || listen(srv, 2) != 0) {\n            std::string e = std::string("tail listen :") + std::to_string(port) + ": " + strerror(errno);\n            close(srv);\n            return e;\n        }\n        log_("tail worker listening on :" + std::to_string(port));\n        while (true) {\n            int fd = accept(srv, nullptr, nullptr);\n            if (fd < 0) {   // a USB replug fails accept: keep listening (breaking here left the port dead until relaunch)\n                if (errno != EINTR) { log_(std::string("tail accept: ") + strerror(errno) + " (retrying)"); usleep(100000); }\n                continue;\n            }\n            {\n                std::string why;\n                if (!(accept_ok_ ? accept_ok_(fd, why) : accept_loopback_only(fd, why))) {\n                    log_("refused a connection from " + why);\n                    close(fd);\n                    continue;\n                }\n            }\n            tune_socket(fd);\n            {\n                std::lock_guard<std::mutex> lk(st_->mu);\n                st_->sessions++;\n            }\n            status("connected");\n            try {\n                handle(fd);\n            } catch (const std::exception & e) {\n                log_(std::string("tail session ended: ") + e.what());\n            }\n            close(fd);\n            status(model_ ? "ready" : "no model", model_ ? desc_ : path_);\n        }\n        close(srv);\n        return "tail accept failed";\n    }'''
    new_serve = '''    std::string serve(int port) {\n        ensure_socket_runtime();\n        socket_handle_t srv = socket(AF_INET, SOCK_STREAM, 0);\n        if (socket_is_invalid(srv)) return std::string("socket: ") + socket_error_string(socket_last_error());\n        int one = 1;\n        socket_set_opt(srv, SOL_SOCKET, SO_REUSEADDR, &one, (int) sizeof(one));\n        sockaddr_in addr = {};\n        addr.sin_family = AF_INET;\n        addr.sin_port = htons((uint16_t) port);\n        addr.sin_addr.s_addr = htonl(INADDR_ANY);\n        if (bind(srv, (sockaddr *) &addr, sizeof(addr)) != 0 || listen(srv, 2) != 0) {\n            std::string e = std::string("tail listen :") + std::to_string(port) + ": " + socket_error_string(socket_last_error());\n            socket_close(srv);\n            return e;\n        }\n        log_("tail worker listening on :" + std::to_string(port));\n        while (true) {\n            socket_handle_t fd = accept(srv, nullptr, nullptr);\n            if (socket_is_invalid(fd)) {   // a USB replug fails accept: keep listening (breaking here left the port dead until relaunch)\n                const int se = socket_last_error();\n                if (!socket_interrupted(se)) { log_(std::string("tail accept: ") + socket_error_string(se) + " (retrying)"); sleep_us(100000); }\n                continue;\n            }\n            {\n                std::string why;\n                if (!(accept_ok_ ? accept_ok_(fd, why) : accept_loopback_only(fd, why))) {\n                    log_("refused a connection from " + why);\n                    socket_close(fd);\n                    continue;\n                }\n            }\n            tune_socket(fd);\n            {\n                std::lock_guard<std::mutex> lk(st_->mu);\n                st_->sessions++;\n            }\n            status("connected");\n            try {\n                handle(fd);\n            } catch (const std::exception & e) {\n                log_(std::string("tail session ended: ") + e.what());\n            }\n            socket_close(fd);\n            status(model_ ? "ready" : "no model", model_ ? desc_ : path_);\n        }\n        socket_close(srv);\n        return "tail accept failed";\n    }'''
    s = replace_once(s, old_serve, new_serve, "tail_server::serve")

    s = s.replace("unlink(tmp.c_str())", "file_unlink(tmp.c_str())")
    s = replace_once(s, "fseeko(f, 12, SEEK_SET);", "file_seek64(f, 12, SEEK_SET);", "state seek")

    path.write_text(s, encoding="utf-8")


def patch_tail_client(path: Path) -> None:
    s = path.read_text(encoding="utf-8")
    s = replace_once(s,
'''        try {\n            fd_ = socket(AF_INET, SOCK_STREAM, 0);''',
'''        try {\n            ensure_socket_runtime();\n            fd_ = socket(AF_INET, SOCK_STREAM, 0);\n            if (socket_is_invalid(fd_)) throw std::runtime_error("socket: " + socket_error_string(socket_last_error()));''', "tail-client socket create")
    s = replace_once(s,
'''            if (recv_timeout_s > 0) {\n                timeval tv = { recv_timeout_s, 0 };\n                setsockopt(fd_, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));\n            }''',
'''            if (recv_timeout_s > 0) {\n                socket_set_recv_timeout(fd_, recv_timeout_s);\n            }''', "tail-client recv timeout")
    s = replace_once(s,
'''                throw std::runtime_error("cannot connect to tail worker at " + host + ":" + std::to_string(port) + ": " + strerror(errno));''',
'''                throw std::runtime_error("cannot connect to tail worker at " + host + ":" + std::to_string(port) + ": " + socket_error_string(socket_last_error()));''', "tail-client connect error")
    s = replace_once(s,
'''        if (fd_ >= 0) shutdown(fd_, SHUT_RDWR);''',
'''        if (!socket_is_invalid(fd_)) socket_shutdown(fd_);''', "tail-client abort")
    s = replace_once(s, "    int fd_ = -1;", "    socket_handle_t fd_ = invalid_socket;", "tail-client fd type")
    s = replace_once(s,
'''        if (fd_ >= 0) {\n            try { send_msg(fd_, MSG_BYE, nullptr, 0); } catch (...) {}\n            close(fd_);\n            fd_ = -1;\n        }''',
'''        if (!socket_is_invalid(fd_)) {\n            try { send_msg(fd_, MSG_BYE, nullptr, 0); } catch (...) {}\n            socket_close(fd_);\n            fd_ = invalid_socket;\n        }''', "tail-client close")
    path.write_text(s, encoding="utf-8")


def patch_llama_split(path: Path) -> None:
    s = path.read_text(encoding="utf-8")
    s = replace_once(s,
"#if defined(__APPLE__) || defined(__linux__)\n#define LLAMA_SPLIT_HAVE_SOCKETS 1",
"#if defined(__APPLE__) || defined(__linux__) || defined(_WIN32)\n#define LLAMA_SPLIT_HAVE_SOCKETS 1",
"llama-split Windows socket gate")
    path.write_text(s, encoding="utf-8")


def patch_cmake(path: Path) -> None:
    s = path.read_text(encoding="utf-8")
    needle = "target_link_libraries(llama PUBLIC ggml)"
    replacement = '''target_link_libraries(llama PUBLIC ggml)\n\n# Backburner split-prefill uses Winsock on native Windows. PUBLIC is intentional:\n# static consumers such as llama-server must inherit ws2_32 as well.\nif (WIN32)\n    target_link_libraries(llama PUBLIC ws2_32)\nendif()'''
    s = replace_once(s, needle, replacement, "llama ws2_32 link")
    path.write_text(s, encoding="utf-8")


def patch_toggles(path: Path) -> None:
    s = path.read_text(encoding="utf-8")
    s = replace_once(s, "#include <sys/stat.h>", "#include <filesystem>", "toggle timestamp includes")
    s = replace_once(s,
        "    struct stat st;\n    const long long mtime = stat(path, &st) == 0 ? (long long) st.st_mtimespec.tv_sec * 1000000000LL + st.st_mtimespec.tv_nsec : 0;",
        "    std::error_code ec;\n    const auto stamp = std::filesystem::last_write_time(path, ec);\n    const long long mtime = ec ? 0 : stamp.time_since_epoch().count();",
        "portable toggle timestamp")
    path.write_text(s, encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", nargs="?", type=Path, default=Path("llama.cpp"), help="backburner-llama.cpp checkout")
    args = ap.parse_args()
    root = args.root.resolve()
    files = {
        "server": root / "tools/split-prefill/tail-server.h",
        "client": root / "tools/split-prefill/tail-client.h",
        "split": root / "src/llama-split.cpp",
        "cmake": root / "src/CMakeLists.txt",
        "toggles": root / "common/infernet-toggles.cpp",
    }
    for p in files.values():
        if not p.is_file():
            raise SystemExit(f"missing expected upstream file: {p}")

    expected = {
        "toggles": ("24f52baac32df304be9acfba803efd744dbcc3ea39f5e282060074bd59aec641", "f668f245bf3160ade99f54879890eb025b27c13a1d663d077235b0eeab25b199"),
        "server": ("abd5ca2e8c9f1ca38bdccbeade7c3510f7c871bcaca26041954b2836ab130e53", "88e5be815a652faabe1090e94022a2a49b24b0f17e5208d50718c9e2e160b827"),
        "client": ("58cd0c8932de0418a060fbacb85992a4ff028be7c1e5e9296d95bbb30080d6a4", "3e1e262f1dc0bc893f34283f2758a418784454b98da889e957cf8700753b34ad"),
        "split": ("b9e917dc9b08ec2957a915596de2c95787603ce75d6d984b508a49851ef3fa09", "2454136771cd16745dc4e78767d559a86a6df16f3d64a2275092219b035c0bdf"),
        "cmake": ("b665842646c27418c1bc495bdaaac791cde1c62b26dbd58e57596596dc5fe230", "4c478561d00e8dc6236e9851052a26fab44d562d66ef00782de3b1d4040a644a"),
    }
    transforms = {"server": patch_tail_server, "client": patch_tail_client,
                  "split": patch_llama_split, "cmake": patch_cmake, "toggles": patch_toggles}
    updates = {}
    # Validate every input before changing the checkout. Normalize checkout CRLF.
    with tempfile.TemporaryDirectory() as tmp:
        for name, path in files.items():
            text = path.read_text(encoding="utf-8")
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
            original, patched = expected[name]
            if digest == patched:
                continue
            if digest != original:
                raise SystemExit(f"unsupported or locally edited source: {path}; expected engine 1839b781")
            staged = Path(tmp) / name
            staged.write_text(text, encoding="utf-8", newline="\n")
            transforms[name](staged)
            output = staged.read_text(encoding="utf-8")
            if hashlib.sha256(output.encode("utf-8")).hexdigest() != patched:
                raise SystemExit(f"unexpected patch output: {path}")
            updates[path] = output
    for path, output in updates.items():
        path.write_text(output, encoding="utf-8", newline="\n")
    compat_src = Path(__file__).with_name("socket-compat.h")
    shutil.copyfile(compat_src, root / "tools/split-prefill/socket-compat.h")
    print("Applied native Windows split-prefill socket port to", root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
