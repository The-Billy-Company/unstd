/* Test-only ELF seam: real fsync receipts plus bounded injected kernel errors. */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <unistd.h>

static void
record(const char *event)
{
    const char *path = getenv("FS_PROOF_LOG");
    if (path == NULL) return;
    int fd = open(path, O_WRONLY | O_APPEND | O_CREAT | O_CLOEXEC, 0600);
    if (fd < 0) return;
    (void)syscall(SYS_write, fd, event, strlen(event));
    (void)syscall(SYS_write, fd, "\n", 1);
    (void)syscall(SYS_close, fd);
}

int
fsync(int fd)
{
    static unsigned directory_syncs = 0;
    int (*real_fsync)(int) = dlsym(RTLD_NEXT, "fsync");
    struct stat info;
    int directory = fstat(fd, &info) == 0 && S_ISDIR(info.st_mode);
    if (directory) {
        directory_syncs++;
        const char *fail = getenv("FS_PROOF_FAIL_DIR_SYNC_AT");
        if (fail != NULL && directory_syncs == strtoul(fail, NULL, 10)) {
            record("directory-sync-failed");
            errno = EIO;
            return -1;
        }
    } else if (getenv("FS_PROOF_FAIL_FILE_SYNC") != NULL) {
        record("file-sync-failed");
        errno = EIO;
        return -1;
    }
    int result = real_fsync(fd);
    int error = errno;
    record(directory ? "directory-sync" : "file-sync");
    errno = error;
    return result;
}

ssize_t
write(int fd, const void *buffer, size_t size)
{
    ssize_t (*real_write)(int, const void *, size_t) = dlsym(RTLD_NEXT, "write");
    if (getenv("FS_PROOF_SHORT_WRITE") != NULL) {
        char link[64];
        char path[4096];
        snprintf(link, sizeof(link), "/proc/self/fd/%d", fd);
        ssize_t length = readlink(link, path, sizeof(path) - 1);
        if (length > 4) {
            path[length] = '\0';
            if (strcmp(path + length - 4, ".tmp") == 0) {
                static int interrupted = 0;
                if (!interrupted) {
                    interrupted = 1;
                    record("write-interrupted");
                    errno = EINTR;
                    return -1;
                }
                if (size > 7) size = 7;
                record("short-write");
            }
        }
    }
    return real_write(fd, buffer, size);
}

int
rename(const char *source, const char *destination)
{
    int (*real_rename)(const char *, const char *) = dlsym(RTLD_NEXT, "rename");
    int result = real_rename(source, destination);
    int error = errno;
    record(result == 0 ? "rename" : "rename-failed");
    errno = error;
    return result;
}
