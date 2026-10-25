/* Test-only ELF syscall boundaries. Never load this library for benchmarks. */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/random.h>
#include <sys/syscall.h>
#include <unistd.h>

static int fired;
static int interrupted;
static unsigned directory_syncs;

static int
armed(void)
{
    const char *value = getenv("FS_NATIVE_ARMED");
    return value != NULL && strcmp(value, "1") == 0;
}

static int
directory_path(const char *path)
{
    if (!armed()) return 0;
    const char *target = getenv("FS_NATIVE_TARGET");
    const char *base = target == NULL ? NULL : strrchr(target, '/');
    return base != NULL && strlen(path) == (size_t)(base - target) &&
           strncmp(path, target, (size_t)(base - target)) == 0;
}

static void
record(const char *event)
{
    int error = errno;
    const char *path = getenv("FS_NATIVE_LOG");
    if (path != NULL) {
        int fd = (int)syscall(SYS_openat, AT_FDCWD, path,
                             O_WRONLY | O_APPEND | O_CREAT | O_CLOEXEC, 0600);
        if (fd >= 0) {
            (void)syscall(SYS_write, fd, event, strlen(event));
            (void)syscall(SYS_write, fd, "\n", 1);
            (void)syscall(SYS_close, fd);
        }
    }
    errno = error;
}

static int
scratch_path(const char *path)
{
    if (!armed()) return 0;
    const char *target = getenv("FS_NATIVE_TARGET");
    if (target == NULL) return 0;
    const char *base = strrchr(target, '/');
    if (base == NULL) return 0;
    size_t parent = (size_t)(base - target);
    size_t name = strlen(base + 1);
    size_t length = strlen(path);
    return length > parent + name + 7 &&
           strncmp(path, target, parent) == 0 &&
           path[parent] == '/' && path[parent + 1] == '.' &&
           strncmp(path + parent + 2, base + 1, name) == 0 &&
           path[parent + 2 + name] == '.' &&
           strcmp(path + length - 4, ".tmp") == 0;
}

static int
scratch_fd(int fd)
{
    if (!armed()) return 0;
    char link[64], path[8192];
    snprintf(link, sizeof(link), "/proc/self/fd/%d", fd);
    ssize_t size = readlink(link, path, sizeof(path) - 1);
    if (size < 0) return 0;
    path[size] = '\0';
    return scratch_path(path);
}

static int
directory_fd(int fd)
{
    if (!armed()) return 0;
    char link[64], path[8192];
    snprintf(link, sizeof(link), "/proc/self/fd/%d", fd);
    ssize_t size = readlink(link, path, sizeof(path) - 1);
    if (size < 0) return 0;
    path[size] = '\0';
    return directory_path(path);
}

static int
selected(const char *stage)
{
    const char *at = getenv("FS_NATIVE_AT");
    return !fired && at != NULL && strcmp(at, stage) == 0;
}

static int
failure(const char *stage, int error)
{
    const char *action = getenv("FS_NATIVE_ACTION");
    if (!selected(stage) || action == NULL ||
        (strcmp(action, "error") != 0 && strcmp(action, "eintr") != 0)) return 0;
    fired = 1;
    char event[96];
    snprintf(event, sizeof(event), "%s:%s", stage, action);
    record(event);
    errno = strcmp(action, "eintr") == 0 ? EINTR : error;
    return 1;
}

static void
completed(const char *stage)
{
    record(stage);
    const char *action = getenv("FS_NATIVE_ACTION");
    if (selected(stage) && action != NULL && strcmp(action, "signal") == 0) {
        fired = 1;
        const char *number = getenv("FS_NATIVE_SIGNAL");
        record("signal-delivered");
        /* raise targets the calling thread before the wrapped syscall returns. */
        (void)raise(number == NULL ? SIGUSR1 : atoi(number));
    }
}

static int
opened(const char *path, int fd)
{
    if (fd >= 0 && scratch_path(path)) {
        struct stat info;
        char mode[64];
        if (fstat(fd, &info) == 0) {
            snprintf(mode, sizeof(mode), "scratch-mode:%o", info.st_mode & 07777);
            record(mode);
        }
        completed("scratch-open");
    }
    return fd;
}

static mode_t
open_mode(int flags, va_list args)
{
    return (flags & O_CREAT) || (flags & O_TMPFILE) == O_TMPFILE
               ? (mode_t)va_arg(args, int) : 0;
}

int
open(const char *path, int flags, ...)
{
    va_list args;
    va_start(args, flags);
    mode_t mode = open_mode(flags, args);
    va_end(args);
    int (*real_open)(const char *, int, ...) = dlsym(RTLD_NEXT, "open");
    if (scratch_path(path) && failure("scratch-open", EACCES)) return -1;
    return opened(path, real_open(path, flags, mode));
}

int
open64(const char *path, int flags, ...)
{
    va_list args;
    va_start(args, flags);
    mode_t mode = open_mode(flags, args);
    va_end(args);
    int (*real_open)(const char *, int, ...) = dlsym(RTLD_NEXT, "open64");
    if (scratch_path(path) && failure("scratch-open", EACCES)) return -1;
    return opened(path, real_open(path, flags, mode));
}

ssize_t
getrandom(void *buffer, size_t size, unsigned flags)
{
    ssize_t (*real_random)(void *, size_t, unsigned) = dlsym(RTLD_NEXT, "getrandom");
    const char *failure_code = getenv("FS_NATIVE_ENTROPY_FAILURE");
    if (armed() && !fired && failure_code != NULL) {
        fired = 1;
        record("getrandom:unavailable");
        errno = atoi(failure_code);
        return -1;
    }
    return real_random(buffer, size, flags);
}

ssize_t
write(int fd, const void *data, size_t size)
{
    ssize_t (*real_write)(int, const void *, size_t) = dlsym(RTLD_NEXT, "write");
    if (!scratch_fd(fd)) return real_write(fd, data, size);
    if (failure("write", ENOSPC)) return -1;
    if (getenv("FS_NATIVE_SHORT_WRITE") != NULL) {
        if (!interrupted) {
            interrupted = 1;
            record("short-write:eintr");
            errno = EINTR;
            return -1;
        }
        if (size > 17) size = 17;
        record("short-write");
    }
    ssize_t result = real_write(fd, data, size);
    int error = errno;
    if (result >= 0) completed("write");
    errno = error;
    return result;
}

int
fsync(int fd)
{
    int (*real_sync)(int) = dlsym(RTLD_NEXT, "fsync");
    if (!armed()) return real_sync(fd);
    struct stat info;
    char stage[64];
    if (fstat(fd, &info) == 0 && S_ISDIR(info.st_mode)) {
        snprintf(stage, sizeof(stage), "directory-sync-%u", ++directory_syncs);
    } else if (scratch_fd(fd)) {
        strcpy(stage, "file-sync");
    } else return real_sync(fd);
    if (failure(stage, EIO)) return -1;
    int result = real_sync(fd);
    int error = errno;
    if (result == 0) completed(stage);
    errno = error;
    return result;
}

int
close(int fd)
{
    int (*real_close)(int) = dlsym(RTLD_NEXT, "close");
    int scratch = scratch_fd(fd);
    int directory = directory_fd(fd);
    int result = real_close(fd);
    int error = errno;
    if (scratch) {
        if (failure("scratch-close", EIO)) return -1;
        if (fired && getenv("FS_NATIVE_CLEANUP_CLOSE") != NULL) {
            record("cleanup-close:error");
            errno = EIO;
            return -1;
        }
        if (result == 0) completed("scratch-close");
    } else if (directory && fired &&
               getenv("FS_NATIVE_DIRECTORY_CLEANUP_CLOSE") != NULL) {
        record("directory-cleanup-close:error");
        errno = EIO;
        return -1;
    }
    errno = error;
    return result;
}

int
rename(const char *source, const char *target)
{
    int (*real_rename)(const char *, const char *) = dlsym(RTLD_NEXT, "rename");
    const char *selected_target = getenv("FS_NATIVE_TARGET");
    int relevant = scratch_path(source) && selected_target != NULL &&
                   strcmp(target, selected_target) == 0;
    if (relevant && failure("rename", EACCES)) return -1;
    int result = real_rename(source, target);
    int error = errno;
    if (relevant && result == 0) completed("rename");
    errno = error;
    return result;
}

int
unlink(const char *path)
{
    int (*real_unlink)(const char *) = dlsym(RTLD_NEXT, "unlink");
    if (scratch_path(path) && fired && getenv("FS_NATIVE_CLEANUP_UNLINK") != NULL) {
        record("cleanup-unlink:error");
        errno = EACCES;
        return -1;
    }
    return real_unlink(path);
}
