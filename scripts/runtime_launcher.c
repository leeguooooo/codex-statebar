/* POSIX release entrypoint: initialize one onedir runtime per payload, then exec.
 * The advisory lock survives in the tar child if this process is killed. A
 * subsequent launch repairs the single staging directory after that child exits.
 */
#define _XOPEN_SOURCE 700
#define _DARWIN_C_SOURCE 1
#define _DEFAULT_SOURCE 1
#include <errno.h>
#include <dirent.h>
#include <fcntl.h>
#include <limits.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>
#ifdef __APPLE__
#include <mach-o/dyld.h>
#endif
#include "runtime_bundle.h"

extern const unsigned char cxs_payload_start[];

static void fail(const char *what) {
    fprintf(stderr, "cxs runtime: %s: %s\n", what, strerror(errno));
    exit(1);
}

static void path_join(char *out, const char *parent, const char *name) {
    if (snprintf(out, PATH_MAX, "%s/%s", parent, name) >= PATH_MAX) {
        errno = ENAMETOOLONG;
        fail("runtime path");
    }
}

static void mkdirs(const char *path) {
    char copy[PATH_MAX];
    if (strlen(path) >= sizeof(copy)) {
        errno = ENAMETOOLONG;
        fail("runtime parent");
    }
    strcpy(copy, path);
    for (char *p = copy + 1; ; ++p) {
        if (*p != '/' && *p != '\0') continue;
        char saved = *p;
        *p = '\0';
        if (mkdir(copy, 0700) != 0 && errno != EEXIST) fail("mkdir");
        *p = saved;
        if (!saved) break;
    }
}

static int wait_child(pid_t child) {
    int status;
    while (waitpid(child, &status, 0) < 0) {
        if (errno != EINTR) fail("waitpid");
    }
    return WIFEXITED(status) && WEXITSTATUS(status) == 0;
}

static void remove_incomplete(const char *path) {
    pid_t child = fork();
    if (child < 0) fail("fork cleanup");
    if (child == 0) {
        execl("/bin/rm", "rm", "-rf", "--", path, (char *)NULL);
        _exit(127);
    }
    if (!wait_child(child)) {
        errno = EIO;
        fail("remove incomplete runtime");
    }
}

static void extract(const char *stage) {
    int stream[2];
    if (pipe(stream) != 0) fail("pipe");
    pid_t child = fork();
    if (child < 0) fail("fork tar");
    if (child == 0) {
        close(stream[1]);
        if (dup2(stream[0], STDIN_FILENO) < 0) _exit(127);
        close(stream[0]);
        /* Keep the inherited flock fd open until extraction finishes. */
        execl("/usr/bin/tar", "tar", "-xzf", "-", "-C", stage, (char *)NULL);
        _exit(127);
    }
    close(stream[0]);
    signal(SIGPIPE, SIG_IGN);
    size_t sent = 0;
    while (sent < CXS_PAYLOAD_SIZE) {
        ssize_t n = write(stream[1], cxs_payload_start + sent,
                          CXS_PAYLOAD_SIZE - sent);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) break;
        sent += (size_t)n;
    }
    close(stream[1]);
    if (!wait_child(child) || sent != CXS_PAYLOAD_SIZE) {
        errno = EIO;
        fail("extract runtime");
    }
    signal(SIGPIPE, SIG_DFL);
}

static int ready(const char *runtime) {
    char executable[PATH_MAX], marker[PATH_MAX];
    path_join(executable, runtime, "cxs");
    path_join(marker, runtime, ".complete");
    return access(executable, X_OK) == 0 && access(marker, F_OK) == 0;
}

static int hash_name(const char *name) {
    return strlen(name) == 64 && strspn(name, "0123456789abcdef") == 64;
}

/* A shared lease survives exec into the onedir runtime. Maintenance takes an
 * exclusive lease before removing a retired version, so a running watch or
 * daemon can continue importing its modules throughout an upgrade. */
static int unused_lock(const char *base, const char *hash) {
    char name[80], path[PATH_MAX];
    snprintf(name, sizeof(name), "%s.lock", hash);
    path_join(path, base, name);
    int fd = open(path, O_RDWR | O_CREAT | O_NOFOLLOW, 0600);
    if (fd < 0) return -1;
    if (flock(fd, LOCK_EX | LOCK_NB) != 0) {
        close(fd);
        return -1;
    }
    return fd;
}

static int managed_runtime(const char *base, const char *name, struct stat *st) {
    char path[PATH_MAX], marker[PATH_MAX];
    if (!hash_name(name)) return 0;
    path_join(path, base, name);
    if (lstat(path, st) != 0 || !S_ISDIR(st->st_mode)) return 0;
    path_join(marker, path, ".lease-v1");
    return access(marker, F_OK) == 0;
}

static void prune_runtimes(const char *base) {
    /* Retain the current version, one unused rollback version, and all live
     * leased versions. Legacy versions lack leases and are left untouched. */
    DIR *dir = opendir(base);
    if (!dir) return;
    char keep[65] = "";
    time_t latest = 0;
    struct dirent *entry;
    struct stat st;
    while ((entry = readdir(dir))) {
        const char *name = entry->d_name;
        if (strcmp(name, CXS_PAYLOAD_HASH) == 0 || !managed_runtime(base, name, &st)) continue;
        int fd = unused_lock(base, name);
        if (fd < 0) continue;
        close(fd);
        if (!*keep || st.st_mtime > latest ||
                (st.st_mtime == latest && strcmp(name, keep) > 0)) {
            strcpy(keep, name);
            latest = st.st_mtime;
        }
    }
    rewinddir(dir);
    while ((entry = readdir(dir))) {
        const char *name = entry->d_name;
        if (strcmp(name, CXS_PAYLOAD_HASH) == 0 || strcmp(name, keep) == 0 ||
                !managed_runtime(base, name, &st)) continue;
        int fd = unused_lock(base, name);
        if (fd < 0) continue;
        char path[PATH_MAX], lockname[80], lockpath[PATH_MAX];
        path_join(path, base, name);
        remove_incomplete(path);
        snprintf(lockname, sizeof(lockname), "%s.lock", name);
        path_join(lockpath, base, lockname);
        unlink(lockpath);
        close(fd);
    }
    /* A cold start can be killed before it publishes a runtime, then the
     * entrypoint can be upgraded. Reclaim abandoned stages from other hashes
     * too; a live extractor holds that hash's exclusive lock. */
    rewinddir(dir);
    while ((entry = readdir(dir))) {
        const char *name = entry->d_name;
        if (strlen(name) != 72 || strcmp(name + 64, ".staging") != 0) continue;
        char hash[65], path[PATH_MAX];
        memcpy(hash, name, 64);
        hash[64] = '\0';
        if (!hash_name(hash) || strcmp(hash, CXS_PAYLOAD_HASH) == 0) continue;
        path_join(path, base, name);
        if (lstat(path, &st) != 0 || !S_ISDIR(st.st_mode)) continue;
        int fd = unused_lock(base, hash);
        if (fd < 0) continue;
        remove_incomplete(path);
        close(fd);
    }
    rewinddir(dir);
    while ((entry = readdir(dir))) {
        const char *name = entry->d_name;
        if (strlen(name) != 69 || strcmp(name + 64, ".lock") != 0) continue;
        char hash[65], path[PATH_MAX], stage_name[80];
        memcpy(hash, name, 64);
        hash[64] = '\0';
        if (!hash_name(hash) || strcmp(hash, CXS_PAYLOAD_HASH) == 0) continue;
        path_join(path, base, hash);
        if (access(path, F_OK) == 0) continue;
        snprintf(stage_name, sizeof(stage_name), "%s.staging", hash);
        path_join(path, base, stage_name);
        if (access(path, F_OK) == 0) continue;
        int fd = unused_lock(base, hash);
        if (fd < 0) continue;
        path_join(path, base, name);
        unlink(path);
        close(fd);
    }
    closedir(dir);
}

int main(int argc, char **argv) {
    const char *probe = getenv("CODEX_STATEBAR_VERSION_PROBE");
    if (argc > 1 && strcmp(argv[1], "_launch-codex") == 0 && probe && *probe) {
        fprintf(stderr, "codex-statebar: refused recursive Codex version probe\n");
        return 126;
    }
    char self[PATH_MAX], resolved[PATH_MAX], base[PATH_MAX];
#ifdef __APPLE__
    uint32_t size = sizeof(self);
    if (_NSGetExecutablePath(self, &size) != 0) {
        errno = ENAMETOOLONG;
        fail("entrypoint path");
    }
#else
    ssize_t size = readlink("/proc/self/exe", self, sizeof(self) - 1);
    if (size < 0 || size >= (ssize_t)sizeof(self) - 1) fail("entrypoint path");
    self[size] = '\0';
#endif
    if (!realpath(self, resolved)) fail("resolve entrypoint");
    const char *configured = getenv("CODEX_STATEBAR_RUNTIME_DIR");
    if (configured && *configured) {
        if (strlen(configured) >= sizeof(base)) {
            errno = ENAMETOOLONG;
            fail("runtime directory");
        }
        strcpy(base, configured);
    } else {
        const char *home = getenv("HOME");
        if (!home || !*home) {
            errno = EINVAL;
            fail("HOME is not set");
        }
        path_join(base, home, ".local/lib/codex-statebar");
    }
    char runtime[PATH_MAX], stage[PATH_MAX], lock_path[PATH_MAX];
    char executable[PATH_MAX], marker[PATH_MAX], maintenance[PATH_MAX];
    path_join(runtime, base, CXS_PAYLOAD_HASH);
    path_join(stage, base, CXS_PAYLOAD_HASH ".staging");
    path_join(lock_path, base, CXS_PAYLOAD_HASH ".lock");
    mkdirs(base);
    path_join(maintenance, base, ".maintenance.lock");
    int maintenance_fd = open(maintenance, O_RDWR | O_CREAT | O_NOFOLLOW, 0600);
    if (maintenance_fd < 0) fail("open maintenance lock");
    while (flock(maintenance_fd, LOCK_EX) != 0) {
        if (errno != EINTR) fail("lock maintenance");
    }
    int lease = open(lock_path, O_RDWR | O_CREAT | O_NOFOLLOW, 0600);
    if (lease < 0) fail("open runtime lease");
    if (!ready(runtime)) {
        int lock = lease;
        if (lock < 0) fail("open runtime lock");
        while (flock(lock, LOCK_EX) != 0) {
            if (errno != EINTR) fail("lock runtime");
        }
        if (!ready(runtime)) {
            remove_incomplete(stage);
            if (mkdir(stage, 0700) != 0) fail("create staging directory");
            extract(stage);
            path_join(executable, stage, "cxs");
            if (access(executable, X_OK) != 0) fail("runtime executable missing");
            path_join(marker, stage, ".complete");
            int fd = open(marker, O_WRONLY | O_CREAT | O_EXCL, 0600);
            if (fd < 0) fail("complete runtime");
            close(fd);
            path_join(marker, stage, ".lease-v1");
            fd = open(marker, O_WRONLY | O_CREAT | O_EXCL, 0600);
            if (fd < 0) fail("mark leased runtime");
            close(fd);
            remove_incomplete(runtime);
            if (rename(stage, runtime) != 0) fail("publish runtime");
        }
    }
    if (flock(lease, LOCK_SH) != 0) fail("lease runtime");
    prune_runtimes(base);
    close(maintenance_fd);
    /* Upgrades must replace the installed entrypoint, not this cached runtime.
     * Leased runtimes remain available to already-running Codex sessions. */
    if (setenv("CODEX_STATEBAR_ENTRYPOINT", resolved, 1) != 0 ||
        setenv("PYINSTALLER_RESET_ENVIRONMENT", "1", 1) != 0) fail("environment");
    path_join(executable, runtime, "cxs");
    execv(executable, argv);
    fail("exec runtime");
}
