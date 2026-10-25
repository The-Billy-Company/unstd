/* Linux-only one-shot filesystem proof. Not connected to unstd's build or API. */
#define PY_SSIZE_T_CLEAN
#define _GNU_SOURCE
#include <Python.h>
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/random.h>
#include <sys/stat.h>
#include <unistd.h>

#ifndef __linux__
#error "This qualification prototype requires Linux"
#endif

typedef struct {
    int error;
    int cleanup_error;
    int committed;
    const char *stage;
} Outcome;

static char *
parent_name(const char *path)
{
    const char *slash = strrchr(path, '/');
    if (slash == NULL) return strdup(".");
    if (slash == path) return strdup("/");
    return strndup(path, (size_t)(slash - path));
}

static int
sync_descriptor(int fd)
{
    int result;
    do result = fsync(fd); while (result < 0 && errno == EINTR);
    return result;
}

static int
sync_directory(const char *path, int strict, Outcome *outcome)
{
    int fd = open(path, O_RDONLY | O_CLOEXEC | (strict ? O_DIRECTORY : 0));
    if (fd < 0) return strict ? -1 : 0;
    int result = 0;
    if (strict) {
        struct stat info;
        result = fstat(fd, &info);
        if (result == 0 && !S_ISDIR(info.st_mode)) {
            errno = ENOTDIR;
            result = -1;
        }
    }
    if (result == 0) result = sync_descriptor(fd);
    int error = result < 0 ? errno : 0;
    if (close(fd) < 0) {
        if (error == 0) error = errno;
        else if (strict) outcome->cleanup_error = errno;
    }
    if (strict && error != 0) {
        errno = error;
        return -1;
    }
    return 0;
}

static int
ensure_directory(const char *path, int strict, Outcome *outcome)
{
    struct stat info;
    if (stat(path, &info) == 0) {
        if (S_ISDIR(info.st_mode)) return 0;
        errno = ENOTDIR;
        return -1;
    }
    if (errno != ENOENT) return -1;
    char *parent = parent_name(path);
    if (parent == NULL) {
        errno = ENOMEM;
        return -1;
    }
    if (strcmp(parent, path) == 0) {
        free(parent);
        errno = ENOENT;
        return -1;
    }
    int result = ensure_directory(parent, strict, outcome);
    if (result == 0) {
        result = mkdir(path, 0777);
        if (result < 0 && errno == EEXIST && stat(path, &info) == 0 &&
            S_ISDIR(info.st_mode)) result = 0;
        if (result == 0 && strict) result = sync_directory(parent, 1, outcome);
    }
    int error = errno;
    free(parent);
    errno = error;
    return result;
}

static int
entropy(unsigned char *bytes, size_t size)
{
    while (size != 0) {
        ssize_t count = getrandom(bytes, size, 0);
        if (count < 0) {
            if (errno == EINTR) continue;
            return -1;
        }
        if (count == 0) {
            errno = EIO;
            return -1;
        }
        bytes += count;
        size -= (size_t)count;
    }
    return 0;
}

static Outcome
commit(const char *path, const char *data, size_t size, int durability)
{
    Outcome outcome = {0, 0, 0, "parent preparation"};
    char *parent = parent_name(path);
    char *scratch = NULL;
    int fd = -1;
    int scratch_exists = 0;
    if (parent == NULL) {
        errno = ENOMEM;
        goto failed;
    }
    if (ensure_directory(parent, durability == 2, &outcome) < 0) goto failed;
    outcome.stage = "directory-sync preflight";
    if (durability == 2 && sync_directory(parent, 1, &outcome) < 0) goto failed;

    struct stat prior;
    mode_t mode = 0600;
    int preserve = 0;
    outcome.stage = "destination stat";
    if (stat(path, &prior) == 0) {
        mode = prior.st_mode & 07777;
        preserve = 1;
    } else if (errno != ENOENT) goto failed;

    const char *base = strrchr(path, '/');
    base = base == NULL ? path : base + 1;
    size_t capacity = strlen(parent) + strlen(base) + 16;
    scratch = malloc(capacity);
    if (scratch == NULL) {
        errno = ENOMEM;
        goto failed;
    }
    outcome.stage = "scratch creation";
    for (;;) {
        unsigned char random[4];
        static const char hex[] = "0123456789abcdef";
        char token[9];
        if (entropy(random, sizeof(random)) < 0) goto failed;
        for (int i = 0; i < 4; i++) {
            token[i * 2] = hex[random[i] >> 4];
            token[i * 2 + 1] = hex[random[i] & 15];
        }
        token[8] = '\0';
        snprintf(scratch, capacity, "%s/.%s.%s.tmp", parent, base, token);
        fd = open(scratch, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, mode);
        if (fd >= 0) break;
        if (errno != EEXIST) goto failed;
    }
    scratch_exists = 1;
    outcome.stage = "scratch permissions";
    if (preserve && fchmod(fd, mode) < 0) goto failed;
    outcome.stage = "payload write";
    while (size != 0) {
        ssize_t count = write(fd, data, size);
        if (count < 0) {
            if (errno == EINTR) continue;
            goto failed;
        }
        if (count == 0) {
            errno = EIO;
            goto failed;
        }
        data += count;
        size -= (size_t)count;
    }
    outcome.stage = "file sync";
    if (sync_descriptor(fd) < 0) goto failed;
    outcome.stage = "scratch close";
    int owned_fd = fd;
    fd = -1;  /* Linux close errors never license retrying a reused descriptor. */
    if (close(owned_fd) < 0) goto failed;
    outcome.stage = "atomic replacement";
    if (rename(scratch, path) < 0) goto failed;
    scratch_exists = 0;
    outcome.committed = 1;
    outcome.stage = "post-commit directory sync";
    if (durability && sync_directory(parent, durability == 2, &outcome) < 0)
        goto failed;
    goto done;

failed:
    outcome.error = errno;
    if (fd >= 0 && close(fd) < 0) outcome.cleanup_error = errno;
    if (scratch_exists && unlink(scratch) < 0 && errno != ENOENT)
        outcome.cleanup_error = errno;
done:
    free(parent);
    free(scratch);
    return outcome;
}

static void
note(const char *message)
{
    PyObject *error = PyErr_GetRaisedException();
    if (error == NULL) return;
    PyObject *result = PyObject_CallMethod(error, "add_note", "s", message);
    if (result == NULL) PyErr_Clear();
    Py_XDECREF(result);
    PyErr_SetRaisedException(error);
}

static PyObject *
atomic_write(PyObject *self, PyObject *args, PyObject *kwargs)
{
    (void)self;
    static char *keywords[] = {"path", "payload", "durable", NULL};
    PyObject *path;
    PyObject *payload;
    PyObject *durable = Py_False;
    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "OO!|$O:atomic_write", keywords,
                                    &path, &PyBytes_Type, &payload, &durable))
        return NULL;
    int durability = 0;
    if (durable == Py_True) durability = 1;
    else if (durable != Py_False) {
        if (PyUnicode_Check(durable) &&
            PyUnicode_CompareWithASCIIString(durable, "strict") == 0)
            durability = 2;
        else {
            PyErr_SetString(PyExc_ValueError, "durable must be False, True, or 'strict'");
            return NULL;
        }
    }
    PyObject *encoded = NULL;
    if (!PyUnicode_FSConverter(path, &encoded)) return NULL;
    const char *filename = PyBytes_AS_STRING(encoded);
    Py_ssize_t length = PyBytes_GET_SIZE(encoded);
    if (memchr(filename, '\0', (size_t)length) != NULL || length == 0 ||
        filename[length - 1] == '/') {
        Py_DECREF(encoded);
        PyErr_SetString(PyExc_ValueError,
                        "path must be a normalized nonempty filename without NUL");
        return NULL;
    }
    Outcome outcome;
    const char *data = PyBytes_AS_STRING(payload);
    size_t size = (size_t)PyBytes_GET_SIZE(payload);
    Py_BEGIN_ALLOW_THREADS
    outcome = commit(filename, data, size, durability);
    Py_END_ALLOW_THREADS
    Py_DECREF(encoded);
    if (outcome.error != 0) {
        errno = outcome.error;
        PyErr_SetFromErrnoWithFilenameObject(PyExc_OSError, path);
        note(outcome.stage);
        if (outcome.cleanup_error != 0) {
            char message[160];
            snprintf(message, sizeof(message), "scratch/directory cleanup failed: %s",
                     strerror(outcome.cleanup_error));
            note(message);
        }
        if (outcome.committed)
            note("replacement already committed; directory sync failed");
        return NULL;
    }
    if (PyErr_CheckSignals() < 0) {
        if (outcome.committed) note("replacement already committed before cancellation");
        return NULL;
    }
    Py_RETURN_NONE;
}

static PyMethodDef methods[] = {
    {"atomic_write", (PyCFunction)(void(*)(void))atomic_write,
     METH_VARARGS | METH_KEYWORDS,
     "atomic_write(path, payload: bytes, *, durable=False) -> None\n"
     "Experimental Linux atomic commit with real fsync; not a public backend."},
    {NULL, NULL, 0, NULL}
};

static struct PyModuleDef module = {
    PyModuleDef_HEAD_INIT, "_fs_experiment", NULL, -1, methods
};

PyMODINIT_FUNC
PyInit__fs_experiment(void)
{
    return PyModule_Create(&module);
}
