#define _DARWIN_C_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <stddef.h>
#include <sys/stat.h>
#include <unistd.h>
#include <stdio.h>

/* Diagnostic only: prevalidated names, preencoded payload, existing ancestors.
 * Not a drop-in API: this intentionally omits Python hooks and error ordering. */
int write_floor(const char *parent, const char *source, const char *destination,
                const char *data, size_t length, int fresh, int replace) {
    if (fresh && mkdir(parent, 0777) < 0) return errno;
    int fd = open(source, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0666);
    if (fd < 0) return errno;
    size_t offset = 0;
    while (offset < length) {
        ssize_t written = write(fd, data + offset, length - offset);
        if (written < 0) {
            int failure = errno;
            close(fd);
            return failure;
        }
        if (written == 0) {
            close(fd);
            return EIO;
        }
        offset += (size_t)written;
    }
    if (close(fd) < 0) return errno;
    if (replace && rename(source, destination) < 0) return errno;
    return 0;
}
