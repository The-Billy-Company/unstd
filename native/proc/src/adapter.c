/* Optional POSIX spawn and captured pipe kernels for the bounded proc runner.
 * Python owns Popen descriptors, timeout exceptions and final child reaping.
 */
#define PY_SSIZE_T_CLEAN
#define _GNU_SOURCE
#include <Python.h>
#include "pipes.h"

#if defined(__APPLE__) || defined(__linux__)
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <spawn.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>
#ifdef __linux__
#include <dlfcn.h>
#endif

extern char **environ;

typedef int (*CloseFrom)(posix_spawn_file_actions_t *, int);
typedef int (*ChdirFd)(posix_spawn_file_actions_t *, int);

static int
spawn_functions(CloseFrom *closefrom, ChdirFd *chdirfd)
{
    *closefrom = NULL;
    *chdirfd = NULL;
#ifdef __APPLE__
    if (__builtin_available(macOS 10.15, *)) {
        *chdirfd = posix_spawn_file_actions_addfchdir_np;
    }
    return 1;
#else
    *closefrom = (CloseFrom)dlsym(RTLD_DEFAULT, "posix_spawn_file_actions_addclosefrom_np");
    *chdirfd = (ChdirFd)dlsym(RTLD_DEFAULT, "posix_spawn_file_actions_addfchdir_np");
    return *closefrom != NULL;
#endif
}

/* Own encoded arguments while C pointer arrays are live. Exact input admission
 * permits fallback before audit instead of invoking foreign conversion hooks. */
static int
encode_sequence(PyObject *values, PyObject ***owners, Py_ssize_t *owner_count, char ***array)
{
    PyObject *snapshot = PySequence_Tuple(values);
    if (snapshot == NULL) return -1;
    Py_ssize_t count = PyTuple_GET_SIZE(snapshot);
    if ((size_t)count >= SIZE_MAX / sizeof(char *) - 1) {
        Py_DECREF(snapshot);
        PyErr_NoMemory();
        return -1;
    }
    *owner_count = count;
    *owners = count ? PyMem_Calloc((size_t)count, sizeof(PyObject *)) : NULL;
    *array = PyMem_Calloc((size_t)count + 1, sizeof(char *));
    if ((count && *owners == NULL) || *array == NULL) {
        Py_DECREF(snapshot);
        if (!PyErr_Occurred()) PyErr_NoMemory();
        return -1;
    }
    for (Py_ssize_t i = 0; i < count; i++) {
        PyObject *encoded = NULL;
        if (!PyUnicode_FSConverter(PyTuple_GET_ITEM(snapshot, i), &encoded)) {
            Py_DECREF(snapshot);
            return -1;
        }
        (*owners)[i] = encoded;
        (*array)[i] = PyBytes_AS_STRING(encoded);
    }
    Py_DECREF(snapshot);
    return 0;
}

static PyObject *
executable_paths(PyObject *executable, PyObject *environment)
{
    PyObject *encoded = PyUnicode_EncodeFSDefault(executable);
    if (encoded == NULL) return NULL;
    if (strchr(PyBytes_AS_STRING(encoded), '/') != NULL) {
        PyObject *result = PyTuple_Pack(1, encoded);
        Py_DECREF(encoded);
        return result;
    }
    PyObject *os = PyImport_ImportModule("os");
    if (os == NULL) {
        Py_DECREF(encoded);
        return NULL;
    }
    PyObject *paths = PyObject_CallMethod(os, "get_exec_path", "O", environment);
    Py_DECREF(os);
    if (paths == NULL) {
        Py_DECREF(encoded);
        return NULL;
    }
    PyObject *sequence = PySequence_Tuple(paths);
    Py_DECREF(paths);
    if (sequence == NULL) {
        Py_DECREF(encoded);
        return NULL;
    }
    Py_ssize_t count = PyTuple_GET_SIZE(sequence);
    if (count == 0) {
        Py_DECREF(encoded);
        Py_DECREF(sequence);
        PyErr_SetString(PyExc_ValueError, "get_exec_path returned no executable candidates");
        return NULL;
    }
    PyObject *result = PyTuple_New(count);
    if (result == NULL) goto done;
    /* A private bytes-only tuple must stay invisible to gc.get_objects while
     * foreign directory conversions can inspect Python's tracked objects. */
    PyObject_GC_UnTrack(result);
    for (Py_ssize_t i = 0; i < count; i++) {
        PyObject *path = NULL;
        if (!PyUnicode_FSConverter(PyTuple_GET_ITEM(sequence, i), &path)) {
            Py_CLEAR(result);
            goto done;
        }
        Py_ssize_t prefix = PyBytes_GET_SIZE(path), suffix = PyBytes_GET_SIZE(encoded);
        int slash = prefix > 0 && PyBytes_AS_STRING(path)[prefix - 1] != '/';
        if (prefix > PY_SSIZE_T_MAX - suffix - slash) {
            Py_DECREF(path);
            Py_CLEAR(result);
            PyErr_NoMemory();
            goto done;
        }
        PyObject *joined = PyBytes_FromStringAndSize(NULL, prefix + slash + suffix);
        if (joined == NULL) {
            Py_DECREF(path);
            Py_CLEAR(result);
            goto done;
        }
        char *target = PyBytes_AS_STRING(joined);
        memcpy(target, PyBytes_AS_STRING(path), (size_t)prefix);
        if (slash) target[prefix] = '/';
        memcpy(target + prefix + slash, PyBytes_AS_STRING(encoded), (size_t)suffix);
        PyTuple_SET_ITEM(result, i, joined);
        Py_DECREF(path);
    }
done:
    Py_DECREF(encoded);
    Py_DECREF(sequence);
    return result;
}

static int
encode_environment(PyObject *environment, PyObject ***owners, Py_ssize_t *owner_count,
                   char ***array, char **raw_storage)
{
    if (environment == Py_None) {
        /* os.putenv does not update os.environ. Copy libc's actual environment
         * while holding the GIL, before releasing it around posix_spawn. */
        size_t count = 0;
        while (environ != NULL && environ[count] != NULL) count++;
        if (count >= SIZE_MAX / sizeof(char *) - 1) {
            PyErr_NoMemory();
            return -1;
        }
        *array = PyMem_Calloc(count + 1, sizeof(char *));
        if (*array == NULL) {
            PyErr_NoMemory();
            return -1;
        }
        size_t total = 0;
        for (size_t i = 0; i < count; i++) {
            size_t length = strlen(environ[i]) + 1;
            if (length > SIZE_MAX - total) {
                PyErr_NoMemory();
                return -1;
            }
            total += length;
        }
        *raw_storage = PyMem_Malloc(total ? total : 1);
        if (*raw_storage == NULL) {
            PyErr_NoMemory();
            return -1;
        }
        char *target = *raw_storage;
        for (size_t i = 0; i < count; i++) {
            size_t length = strlen(environ[i]) + 1;
            (*array)[i] = target;
            memcpy(target, environ[i], length);
            target += length;
        }
        return 0;
    }
    /* The audit hook may have changed dictionary values after admission.
     * Snapshot strong key/value references before any filesystem conversion,
     * allocation, or foreign __fspath__ callback can mutate the dictionary. */
    PyObject *dictionary_items = PyDict_Items(environment);
    if (dictionary_items == NULL) return -1;
    PyObject *items = PySequence_Tuple(dictionary_items);
    Py_DECREF(dictionary_items);
    if (items == NULL) return -1;
    Py_ssize_t count = PyTuple_GET_SIZE(items);
    if ((size_t)count >= SIZE_MAX / sizeof(char *) - 1) {
        Py_DECREF(items);
        PyErr_NoMemory();
        return -1;
    }
    *owner_count = count;
    *owners = count ? PyMem_Calloc((size_t)count, sizeof(PyObject *)) : NULL;
    *array = PyMem_Calloc((size_t)count + 1, sizeof(char *));
    if ((count && *owners == NULL) || *array == NULL) {
        Py_DECREF(items);
        if (!PyErr_Occurred()) PyErr_NoMemory();
        return -1;
    }
    int status = -1;
    for (Py_ssize_t i = 0; i < count; i++) {
        PyObject *pair = PyTuple_GET_ITEM(items, i);
        PyObject *key = NULL, *value = NULL;
        if (!PyUnicode_FSConverter(PyTuple_GET_ITEM(pair, 0), &key)
            || !PyUnicode_FSConverter(PyTuple_GET_ITEM(pair, 1), &value)) {
            Py_XDECREF(key);
            Py_XDECREF(value);
            goto done;
        }
        Py_ssize_t key_size = PyBytes_GET_SIZE(key), value_size = PyBytes_GET_SIZE(value);
        if (memchr(PyBytes_AS_STRING(key), '=', (size_t)key_size) != NULL) {
            Py_DECREF(key);
            Py_DECREF(value);
            PyErr_SetString(PyExc_ValueError, "illegal environment variable name");
            goto done;
        }
        if (key_size > PY_SSIZE_T_MAX - value_size - 1) {
            Py_DECREF(key);
            Py_DECREF(value);
            PyErr_NoMemory();
            goto done;
        }
        PyObject *encoded = PyBytes_FromStringAndSize(NULL, key_size + value_size + 1);
        if (encoded == NULL) {
            Py_DECREF(key);
            Py_DECREF(value);
            goto done;
        }
        char *target = PyBytes_AS_STRING(encoded);
        memcpy(target, PyBytes_AS_STRING(key), (size_t)key_size);
        target[key_size] = '=';
        memcpy(target + key_size + 1, PyBytes_AS_STRING(value), (size_t)value_size);
        Py_DECREF(key);
        Py_DECREF(value);
        (*owners)[i] = encoded;
        (*array)[i] = PyBytes_AS_STRING(encoded);
    }
    status = 0;
done:
    Py_DECREF(items);
    return status;
}

static int
string_admitted(PyObject *value, int environment_key)
{
    if (!PyUnicode_CheckExact(value)) return 0;
    Py_ssize_t length;
    const char *text = PyUnicode_AsUTF8AndSize(value, &length);
    if (text == NULL) {
        /* Surrogate-escaped filesystem strings stay with the stdlib path. */
        PyErr_Clear();
        return 0;
    }
    return memchr(text, '\0', (size_t)length) == NULL
        && (!environment_key || memchr(text, '=', (size_t)length) == NULL);
}

/* Python's os.posix_spawn rejects an empty argv even though fork/exec can
 * pass it to the kernel. Preserve the running subprocess route's exception
 * when an audit hook clears an originally admitted argument list. */
static int
empty_argv_uses_stdlib_spawn(PyObject *executable, PyObject *cwd)
{
    if (cwd != Py_None) return 0;
    Py_ssize_t slash = PyUnicode_FindChar(executable, '/', 0,
                                         PyUnicode_GET_LENGTH(executable), 1);
    if (slash == -2) return -1;
    if (slash < 0) return 0;
    PyObject *subprocess = PyImport_ImportModule("subprocess");
    if (subprocess == NULL) return -1;
    const char *names[] = {"_USE_POSIX_SPAWN", "_HAVE_POSIX_SPAWN_CLOSEFROM"};
    int eligible = 1;
    for (size_t i = 0; i < sizeof(names) / sizeof(names[0]); i++) {
        PyObject *value = PyObject_GetAttrString(subprocess, names[i]);
        if (value == NULL) {
            if (PyErr_ExceptionMatches(PyExc_AttributeError)) {
                PyErr_Clear();
                eligible = 0;
            }
            else eligible = -1;
            break;
        }
        eligible = PyObject_IsTrue(value);
        Py_DECREF(value);
        if (eligible <= 0) break;
    }
    Py_DECREF(subprocess);
    return eligible;
}

static PyObject *
spawn_process(PyObject *module, PyObject *args)
{
    (void)module;
    PyObject *child, *argv, *environment, *cwd, *executable;
    int p2cread, p2cwrite, c2pread, c2pwrite, errread, errwrite, restore;
    if (!PyArg_ParseTuple(args, "OOOOOiiiiiip:spawn", &child, &argv, &environment,
                          &cwd, &executable, &p2cread, &p2cwrite,
                          &c2pread, &c2pwrite, &errread, &errwrite, &restore)) {
        return NULL;
    }
    CloseFrom closefrom;
    ChdirFd chdirfd;
    if (!spawn_functions(&closefrom, &chdirfd)
        || (cwd != Py_None && chdirfd == NULL)
        || !PyTuple_CheckExact(argv) || PyTuple_GET_SIZE(argv) == 0
        || !string_admitted(executable, 0)
        || (environment != Py_None && !PyDict_CheckExact(environment))) {
        Py_RETURN_NOTIMPLEMENTED;
    }
    int descriptors[] = {p2cread, p2cwrite, c2pread, c2pwrite, errread, errwrite};
    for (size_t i = 0; i < sizeof(descriptors) / sizeof(descriptors[0]); i++) {
        if (descriptors[i] != -1 && descriptors[i] < 3) Py_RETURN_NOTIMPLEMENTED;
    }
    for (Py_ssize_t i = 0; i < PyTuple_GET_SIZE(argv); i++) {
        if (!string_admitted(PyTuple_GET_ITEM(argv, i), 0)) Py_RETURN_NOTIMPLEMENTED;
    }
    if (environment != Py_None) {
        Py_ssize_t position = 0;
        PyObject *key, *value;
        while (PyDict_Next(environment, &position, &key, &value)) {
            if (!string_admitted(key, 1) || !string_admitted(value, 0)) {
                Py_RETURN_NOTIMPLEMENTED;
            }
        }
    }

    /* Match subprocess's event payload (argv list, original cwd and environment)
     * before either directory access or process creation can have side effects. */
    PyObject *audit_argv = PySequence_List(argv);
    if (audit_argv == NULL) return NULL;
    int audited = PySys_Audit("subprocess.Popen", "OOOO", executable, audit_argv,
                             cwd, environment);
    if (audited < 0) {
        Py_DECREF(audit_argv);
        return NULL;
    }
    if (PyList_GET_SIZE(audit_argv) == 0) {
        int standard_spawn = empty_argv_uses_stdlib_spawn(executable, cwd);
        if (standard_spawn != 0) {
            Py_DECREF(audit_argv);
            if (standard_spawn > 0) {
                PyErr_SetString(PyExc_ValueError, "posix_spawn: argv must not be empty");
            }
            return NULL;
        }
    }

    PyObject **argv_owners = NULL, **env_owners = NULL;
    Py_ssize_t argv_count = 0, env_count = 0;
    PyObject *cwd_bytes = NULL, *paths = NULL;
    char **argv_array = NULL, **env_array = NULL;
    char *raw_environment = NULL;
    int directory = -1, failure = 0, actions_live = 0, attr_live = 0;
    PyObject *error_filename = executable;
    posix_spawn_file_actions_t actions;
    posix_spawnattr_t attributes;
    pid_t pid = -1;
    PyObject *result = NULL;
    paths = executable_paths(executable, environment);
    if (paths == NULL
        || encode_environment(environment, &env_owners, &env_count, &env_array, &raw_environment) < 0
        || encode_sequence(audit_argv, &argv_owners, &argv_count, &argv_array) < 0) goto done;
    if (cwd != Py_None) {
        if (!PyUnicode_FSConverter(cwd, &cwd_bytes)) goto done;
        const char *path = PyBytes_AS_STRING(cwd_bytes);
        if (memchr(path, '\0', (size_t)PyBytes_GET_SIZE(cwd_bytes)) != NULL) {
            PyErr_SetString(PyExc_ValueError, "embedded null byte");
            goto done;
        }
#ifdef __APPLE__
        directory = open(path, O_SEARCH | O_CLOEXEC);
#else
        directory = open(path, O_PATH | O_DIRECTORY | O_CLOEXEC);
#endif
        if (directory < 0) {
            failure = errno;
            error_filename = cwd;
            goto done;
        }
#ifndef __APPLE__
        /* O_PATH itself does not require search permission on the final
         * directory. Check fchdir's effective-id permission before spawning so
         * failures retain cwd as their filename, like subprocess's chdir. */
        if (faccessat(directory, ".", X_OK, AT_EACCESS) < 0) {
            failure = errno;
            error_filename = cwd;
            goto done;
        }
#endif
    }
    failure = posix_spawn_file_actions_init(&actions);
    if (failure) goto done;
    actions_live = 1;
    if (directory >= 0 && (failure = chdirfd(&actions, directory))) goto done;
    int child_fds[] = {p2cread, c2pwrite, errwrite};
    for (int target = 0; target < 3; target++) {
        if (child_fds[target] >= 0) {
            failure = posix_spawn_file_actions_adddup2(&actions, child_fds[target], target);
            if (failure) goto done;
        }
#ifdef __APPLE__
        else {
            int flags = fcntl(target, F_GETFD);
            if (flags >= 0 && !(flags & FD_CLOEXEC)) {
                failure = posix_spawn_file_actions_addinherit_np(&actions, target);
                if (failure) goto done;
            }
        }
#endif
    }
#ifndef __APPLE__
    failure = closefrom(&actions, 3);
    if (failure) goto done;
#endif
    failure = posix_spawnattr_init(&attributes);
    if (failure) goto done;
    attr_live = 1;
    short flags = 0;
#ifdef __APPLE__
    flags |= POSIX_SPAWN_CLOEXEC_DEFAULT;
#endif
    if (restore) {
        sigset_t signals;
        sigemptyset(&signals);
        sigaddset(&signals, SIGPIPE);
#ifdef SIGXFZ
        sigaddset(&signals, SIGXFZ);
#endif
#ifdef SIGXFSZ
        sigaddset(&signals, SIGXFSZ);
#endif
        failure = posix_spawnattr_setsigdefault(&attributes, &signals);
        if (failure) goto done;
        flags |= POSIX_SPAWN_SETSIGDEF;
    }
    failure = posix_spawnattr_setflags(&attributes, flags);
    if (failure) goto done;
    int first_failure = 0;
    Py_ssize_t path_count = PyTuple_GET_SIZE(paths);
    for (Py_ssize_t i = 0; i < path_count; i++) {
        const char *path = PyBytes_AS_STRING(PyTuple_GET_ITEM(paths, i));
        Py_BEGIN_ALLOW_THREADS
        int missing = 0;
        if (path_count > 1) {
            struct stat status;
            if (fstatat(directory >= 0 ? directory : AT_FDCWD, path, &status, 0) < 0) {
                int lookup_error = errno;
                if (lookup_error == ENOENT || lookup_error == ENOTDIR) {
                    failure = lookup_error;
                    missing = 1;
                }
            }
        }
        if (!missing) {
            failure = posix_spawn(&pid, path, &actions, &attributes, argv_array, env_array);
        }
        Py_END_ALLOW_THREADS
        if (failure == 0) break;
        if (!first_failure && failure != ENOENT && failure != ENOTDIR) first_failure = failure;
    }
    if (failure) {
        if (first_failure) failure = first_failure;
        goto done;
    }
    if (pid <= 0) {
        PyErr_SetString(PyExc_RuntimeError, "posix_spawn returned an invalid child pid");
        goto done;
    }
    result = PyLong_FromLong((long)pid);
    if (result != NULL
        && (PyObject_SetAttrString(child, "pid", result) < 0
            || PyObject_SetAttrString(child, "_child_created", Py_True) < 0)) {
        Py_CLEAR(result);
    }
    /* Publish Popen ownership under the GIL before returning through a signal
     * checkpoint. On allocation/attribute failure, C retains cleanup ownership. */
    if (result == NULL) {
        int saved_errno = errno;
        kill(pid, SIGKILL);
        Py_BEGIN_ALLOW_THREADS
        while (waitpid(pid, NULL, 0) < 0 && errno == EINTR) {}
        Py_END_ALLOW_THREADS
        errno = saved_errno;
    }
done:
    if (attr_live) posix_spawnattr_destroy(&attributes);
    if (actions_live) posix_spawn_file_actions_destroy(&actions);
    if (directory >= 0) close(directory);
    Py_DECREF(audit_argv);
    Py_XDECREF(paths);
    if (argv_owners != NULL) {
        for (Py_ssize_t i = 0; i < argv_count; i++) Py_XDECREF(argv_owners[i]);
    }
    if (env_owners != NULL) {
        for (Py_ssize_t i = 0; i < env_count; i++) Py_XDECREF(env_owners[i]);
    }
    PyMem_Free(argv_owners);
    PyMem_Free(env_owners);
    Py_XDECREF(cwd_bytes);
    PyMem_Free(argv_array);
    PyMem_Free(raw_environment);
    PyMem_Free(env_array);
    if (failure && !PyErr_Occurred()) {
        errno = failure;
        PyErr_SetFromErrnoWithFilenameObject(PyExc_OSError, error_filename);
    }
    return result;
}
#else
static PyObject *
spawn_process(PyObject *module, PyObject *args)
{
    (void)module;
    (void)args;
    Py_RETURN_NOTIMPLEMENTED;
}
#endif

static PyMethodDef methods[] = {
    {"spawn", spawn_process, METH_VARARGS,
     "Spawn admitted proc argv with descriptor isolation; return NotImplemented before audit otherwise."},
    {"communicate", proc_communicate, METH_VARARGS,
     "Drain admitted captured output pipes to an absolute monotonic deadline."},
    {NULL, NULL, 0, NULL}
};

static struct PyModuleDef definition = {
    PyModuleDef_HEAD_INIT,
    "_proc_native",
    "Optional proc process and captured pipe kernels.",
    0,
    methods,
    NULL, NULL, NULL, NULL
};

PyMODINIT_FUNC
PyInit__proc_native(void)
{
    return PyModule_Create(&definition);
}
