/* Linux byte commits; Python retains recursive-parent and text/error fallbacks. */
#define PY_SSIZE_T_CLEAN
#define _GNU_SOURCE
#include <Python.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/random.h>
#include <sys/stat.h>
#include <unistd.h>

#ifndef __linux__
#error "The filesystem commit backend is Linux-only"
#endif

/* A syscall result of -2 denotes a Python signal exception, not errno. */
static int
checked_stat(const char *path, struct stat *info)
{
    int result, error;
    do {
        Py_BEGIN_ALLOW_THREADS
        result = stat(path, info);
        error = errno;
        Py_END_ALLOW_THREADS
        if (PyErr_CheckSignals() < 0) return -2;
    } while (result < 0 && error == EINTR);
    errno = error;
    return result;
}

static int
checked_open(const char *path, int flags, mode_t mode, int *owned)
{
    int result, error;
    do {
        Py_BEGIN_ALLOW_THREADS
        result = open(path, flags, mode);
        error = errno;
        Py_END_ALLOW_THREADS
        if (result >= 0) *owned = result;
        if (PyErr_CheckSignals() < 0) return -2;
    } while (result < 0 && error == EINTR);
    errno = error;
    return result;
}

static int
checked_sync(int fd)
{
    int result, error;
    do {
        Py_BEGIN_ALLOW_THREADS
        result = fsync(fd);
        error = errno;
        Py_END_ALLOW_THREADS
        if (PyErr_CheckSignals() < 0) return -2;
    } while (result < 0 && error == EINTR);
    errno = error;
    return result;
}

static void
os_error(int error, PyObject *filename, PyObject *destination)
{
    errno = error;
    if (destination != NULL)
        PyErr_SetFromErrnoWithFilenameObjects(PyExc_OSError, filename, destination);
    else if (filename != NULL)
        PyErr_SetFromErrnoWithFilenameObject(PyExc_OSError, filename);
    else
        PyErr_SetFromErrno(PyExc_OSError);
}

static void
add_note(const char *text)
{
    PyObject *primary = PyErr_GetRaisedException();
    if (primary == NULL) return;
    PyObject *result = PyObject_CallMethod(primary, "add_note", "s", text);
    Py_XDECREF(result);
    PyErr_Clear();
    PyErr_SetRaisedException(primary);
}

static void
cleanup_note(const char *prefix, int error, PyObject *filename)
{
    PyObject *primary = PyErr_GetRaisedException();
    if (primary == NULL) return;
    os_error(error, filename, NULL);
    PyObject *secondary = PyErr_GetRaisedException();
    PyObject *message = NULL;
    if (secondary != NULL) {
        if (filename == NULL)
            message = PyUnicode_FromFormat("%s%S", prefix, secondary);
        else
            message = PyUnicode_FromFormat("%s%U: %S", prefix, filename, secondary);
    }
    Py_XDECREF(secondary);
    PyErr_Clear();
    if (message != NULL) {
        PyObject *result = PyObject_CallMethod(primary, "add_note", "O", message);
        Py_XDECREF(result);
        Py_DECREF(message);
        PyErr_Clear();
    }
    PyErr_SetRaisedException(primary);
}

static int
close_owned(int *owned)
{
    int fd = *owned, result, error;
    *owned = -1;  /* Linux releases the descriptor even when close reports EINTR. */
    Py_BEGIN_ALLOW_THREADS
    result = close(fd);
    error = errno;
    Py_END_ALLOW_THREADS
    if (PyErr_CheckSignals() < 0) return -2;
    errno = error;
    return result;
}

static void
cleanup_close(int *owned, const char *prefix, int report)
{
    if (*owned < 0) return;
    int fd = *owned, result, error;
    *owned = -1;
    Py_BEGIN_ALLOW_THREADS
    result = close(fd);
    error = errno;
    Py_END_ALLOW_THREADS
    if (result < 0 && report) cleanup_note(prefix, error, NULL);
}

static void
cleanup_scratch(const char *scratch, PyObject *name)
{
    int result, error;
    Py_BEGIN_ALLOW_THREADS
    result = unlink(scratch);
    error = errno;
    Py_END_ALLOW_THREADS
    if (result < 0 && error != ENOENT)
        cleanup_note("scratch file cleanup failed for ", error, name);
}

static int
sync_directory(const char *path, PyObject *name, int strict)
{
    int fd = -1;
    int result = checked_open(path, O_RDONLY | O_CLOEXEC |
                              (strict ? O_DIRECTORY : 0), 0, &fd);
    if (result == -2) {
        cleanup_close(&fd, "directory close failed: ", strict);
        return -1;
    }
    if (result < 0) {
        if (!strict) return 0;
        os_error(errno, name, NULL);
        return -1;
    }
    if (strict) {
        struct stat info;
        int error;
        do {
            Py_BEGIN_ALLOW_THREADS
            result = fstat(fd, &info);
            error = errno;
            Py_END_ALLOW_THREADS
            if (PyErr_CheckSignals() < 0) {
                cleanup_close(&fd, "directory close failed: ", 1);
                return -1;
            }
        } while (result < 0 && error == EINTR);
        if (result < 0 || !S_ISDIR(info.st_mode)) {
            os_error(result < 0 ? error : ENOTDIR,
                     result < 0 ? NULL : name, NULL);
            cleanup_close(&fd, "directory close failed: ", 1);
            return -1;
        }
    }
    result = checked_sync(fd);
    if (result < 0) {
        if (result == -1 && !strict) {
            /* An OS failure is best effort; a Python cancellation is not. */
            result = close_owned(&fd);
            return result == -2 ? -1 : 0;
        }
        if (result == -1) os_error(errno, NULL, NULL);
        cleanup_close(&fd, "directory close failed: ", strict);
        return -1;
    }
    result = close_owned(&fd);
    if (result == -2) return -1;
    if (result < 0 && strict) {
        os_error(errno, NULL, NULL);
        return -1;
    }
    return 0;
}

static int
random_token(char token[9])
{
    /* 1 declines unavailable getrandom; Python retains its /dev/urandom path. */
    unsigned char bytes[4];
    static const char hex[] = "0123456789abcdef";
    size_t offset = 0;
    while (offset != sizeof(bytes)) {
        ssize_t count;
        int error;
        Py_BEGIN_ALLOW_THREADS
        count = getrandom(bytes + offset, sizeof(bytes) - offset, 0);
        error = errno;
        Py_END_ALLOW_THREADS
        if (PyErr_CheckSignals() < 0) return -1;
        if (count < 0) {
            if (error == EINTR) continue;
            if (error == ENOSYS || error == EPERM) return 1;
            os_error(error, NULL, NULL);
            return -1;
        }
        if (count == 0) {
            os_error(EIO, NULL, NULL);
            return -1;
        }
        offset += (size_t)count;
    }
    for (int index = 0; index < 4; index++) {
        token[index * 2] = hex[bytes[index] >> 4];
        token[index * 2 + 1] = hex[bytes[index] & 15];
    }
    token[8] = '\0';
    return 0;
}

static int
write_payload(int fd, const char *data, size_t size)
{
    while (size != 0) {
        ssize_t count;
        int error;
        size_t request = size > (size_t)SSIZE_MAX ? (size_t)SSIZE_MAX : size;
        Py_BEGIN_ALLOW_THREADS
        count = write(fd, data, request);
        error = errno;
        Py_END_ALLOW_THREADS
        if (PyErr_CheckSignals() < 0) return -1;
        if (count < 0) {
            if (error == EINTR) continue;
            os_error(error, NULL, NULL);
            return -1;
        }
        if (count == 0) {
            os_error(EIO, NULL, NULL);
            return -1;
        }
        data += count;
        size -= (size_t)count;
    }
    return 0;
}

static PyObject *
atomic_write(PyObject *self, PyObject *args, PyObject *kwargs)
{
    (void)self;
    static char *keywords[] = {"path", "payload", "durable", NULL};
    PyObject *path, *payload, *durable = Py_False;
    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "OO|$O:atomic_write", keywords,
                                    &path, &payload, &durable))
        return NULL;
    if (!PyBytes_CheckExact(payload) && !PyUnicode_CheckExact(payload))
        Py_RETURN_NOTIMPLEMENTED;
    int durability = 0;
    if (durable == Py_True) durability = 1;
    else if (durable != Py_False) {
        if (PyUnicode_CheckExact(durable) &&
            PyUnicode_CompareWithASCIIString(durable, "strict") == 0)
            durability = 2;
        else
            Py_RETURN_NOTIMPLEMENTED;
    }
    PyObject *encoded = NULL, *display = NULL, *parent_display = NULL;
    PyObject *scratch_display = NULL;
    char *parent = NULL, *scratch = NULL;
    int fd = -1, scratch_exists = 0, committed = 0, post_sync = 0;
    PyObject *answer = NULL;
    if (!PyUnicode_FSConverter(path, &encoded)) {
        if (PyErr_ExceptionMatches(PyExc_UnicodeError)) {
            PyErr_Clear();
            Py_RETURN_NOTIMPLEMENTED;
        }
        return NULL;
    }
    const char *filename = PyBytes_AS_STRING(encoded);
    size_t length = (size_t)PyBytes_GET_SIZE(encoded);
    const char *base = strrchr(filename, '/');
    base = base == NULL ? filename : base + 1;
    if (length == 0 || memchr(filename, '\0', length) != NULL || *base == '\0' ||
        strcmp(base, ".") == 0 || strcmp(base, "..") == 0) {
        answer = Py_NewRef(Py_NotImplemented);
        goto finished;
    }
    const char *slash = strrchr(filename, '/');
    parent = slash == NULL ? strdup(".") :
             slash == filename ? strdup("/") :
             slash == filename + 1 && filename[0] == '/' ? strdup("//") :
             strndup(filename, (size_t)(slash - filename));
    if (parent == NULL) {
        PyErr_NoMemory();
        goto finished;
    }
    if (PyErr_CheckSignals() < 0) goto finished;
    struct stat info;
    int result = checked_stat(parent, &info);
    if (result == -2) goto finished;
    if (result < 0 || !S_ISDIR(info.st_mode)) {
        /* pathlib owns recursive mkdir and its precise error/filename behavior. */
        answer = Py_NewRef(Py_NotImplemented);
        goto finished;
    }
    display = PyUnicode_DecodeFSDefaultAndSize(filename, (Py_ssize_t)length);
    if (display == NULL) goto finished;
    parent_display = PyUnicode_DecodeFSDefault(parent);
    if (parent_display == NULL) goto finished;
    if (durability == 2 && sync_directory(parent, parent_display, 1) < 0)
        goto finished;
    mode_t mode = 0600;
    int preserve = 0;
    result = checked_stat(filename, &info);
    if (result == -2) goto finished;
    if (result == 0) {
        mode = info.st_mode & 07777;
        preserve = 1;
    } else if (errno != ENOENT) {
        os_error(errno, display, NULL);
        goto finished;
    }
    size_t capacity = strlen(parent) + strlen(base) + 16;
    scratch = malloc(capacity);
    if (scratch == NULL) {
        PyErr_NoMemory();
        goto finished;
    }
    for (;;) {
        char token[9];
        result = random_token(token);
        if (result < 0) goto finished;
        if (result > 0) {
            answer = Py_NewRef(Py_NotImplemented);
            goto finished;
        }
        const char *prefix = strcmp(parent, ".") == 0 ? "" : parent;
        const char *separator = *prefix != '\0' &&
                                prefix[strlen(prefix) - 1] != '/' ? "/" : "";
        snprintf(scratch, capacity, "%s%s.%s.%s.tmp", prefix, separator, base, token);
        Py_CLEAR(scratch_display);
        scratch_display = PyUnicode_DecodeFSDefault(scratch);
        if (scratch_display == NULL) goto finished;
        result = checked_open(scratch, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC,
                              mode, &fd);
        if (fd >= 0) scratch_exists = 1;
        if (result == -2) goto finished;
        if (result >= 0) break;
        if (errno != EEXIST) {
            os_error(errno, scratch_display, NULL);
            goto finished;
        }
    }
    if (preserve) {
        int error;
        do {
            Py_BEGIN_ALLOW_THREADS
            result = fchmod(fd, mode);
            error = errno;
            Py_END_ALLOW_THREADS
            if (PyErr_CheckSignals() < 0) goto finished;
        } while (result < 0 && error == EINTR);
        if (result < 0) {
            os_error(error, NULL, NULL);
            goto finished;
        }
    }
    const char *data;
    Py_ssize_t payload_size;
    if (PyUnicode_CheckExact(payload)) {
        /* Match TextIO.write's encoding stage after scratch acquisition. */
        data = PyUnicode_AsUTF8AndSize(payload, &payload_size);
        if (data == NULL) goto finished;
    } else {
        data = PyBytes_AS_STRING(payload);
        payload_size = PyBytes_GET_SIZE(payload);
    }
    size_t size = (size_t)payload_size;
    if (write_payload(fd, data, size) < 0) goto finished;
    result = checked_sync(fd);
    if (result == -2) goto finished;
    if (result < 0) {
        os_error(errno, NULL, NULL);
        goto finished;
    }
    result = close_owned(&fd);
    if (result == -2) goto finished;
    if (result < 0) {
        os_error(errno, NULL, NULL);
        goto finished;
    }
    if (PyErr_CheckSignals() < 0) goto finished;
    int error;
    Py_BEGIN_ALLOW_THREADS
    result = rename(scratch, filename);
    error = errno;
    Py_END_ALLOW_THREADS
    if (result == 0) {
        committed = 1;
        scratch_exists = 0;
    }
    if (PyErr_CheckSignals() < 0) goto finished;
    if (result < 0) {
        os_error(error, scratch_display, display);
        goto finished;
    }
    if (durability) {
        post_sync = 1;
        if (sync_directory(parent, parent_display, durability == 2) < 0)
            goto finished;
    }
    answer = Py_NewRef(Py_None);

finished:
    if (answer == NULL) {
        cleanup_close(&fd, "scratch descriptor cleanup failed: ", 1);
        if (scratch_exists) cleanup_scratch(scratch, scratch_display);
        if (committed)
            add_note(post_sync ? "replacement already committed; directory sync failed"
                               : "replacement already committed before cancellation");
    }
    Py_XDECREF(scratch_display);
    Py_XDECREF(parent_display);
    Py_XDECREF(display);
    Py_XDECREF(encoded);
    free(scratch);
    free(parent);
    return answer;
}

static PyMethodDef methods[] = {
    {"atomic_write", (PyCFunction)(void(*)(void))atomic_write,
     METH_VARARGS | METH_KEYWORDS,
     "atomic_write(path, payload: str | bytes, *, durable=False) -> None | NotImplemented\n"
     "Commit admitted Linux bytes or UTF8 text; decline before mutation otherwise."},
    {NULL, NULL, 0, NULL}
};

static struct PyModuleDef module = {
    PyModuleDef_HEAD_INIT, "_fs_native", NULL, -1, methods
};

PyMODINIT_FUNC
PyInit__fs_native(void)
{
    return PyModule_Create(&module);
}
