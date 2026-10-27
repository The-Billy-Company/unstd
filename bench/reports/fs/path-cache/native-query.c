/* Diagnostic POSIX is_dir: creation stays in pathlib to preserve audits/hooks. */
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <errno.h>
#include <sys/stat.h>

static PyObject *
is_dir(PyObject *module, PyObject *path)
{
    (void)module;
    PyObject *filesystem = PyOS_FSPath(path);
    if (filesystem == NULL) return NULL;
    PyObject *encoded = NULL;
    if (!PyUnicode_FSConverter(filesystem, &encoded)) {
        Py_DECREF(filesystem);
        if (PyErr_ExceptionMatches(PyExc_ValueError)) {
            PyErr_Clear();
            Py_RETURN_FALSE;
        }
        return NULL;
    }
    struct stat info;
    int result, error;
    do {
        Py_BEGIN_ALLOW_THREADS
        result = stat(PyBytes_AS_STRING(encoded), &info);
        error = errno;
        Py_END_ALLOW_THREADS
        if (PyErr_CheckSignals() < 0) {
            Py_DECREF(encoded);
            Py_DECREF(filesystem);
            return NULL;
        }
    } while (result < 0 && error == EINTR);
    Py_DECREF(encoded);
    if (result == 0) {
        Py_DECREF(filesystem);
        return PyBool_FromLong(S_ISDIR(info.st_mode));
    }
#if PY_VERSION_HEX < 0x030E0000
    if (error != ENOENT && error != ENOTDIR && error != EBADF && error != ELOOP) {
        errno = error;
        PyErr_SetFromErrnoWithFilenameObject(PyExc_OSError, filesystem);
        Py_DECREF(filesystem);
        return NULL;
    }
#endif
    Py_DECREF(filesystem);
    Py_RETURN_FALSE;
}

static PyMethodDef methods[] = {
    {"is_dir", is_dir, METH_O, "Stat a normalized path without a stat_result."},
    {NULL, NULL, 0, NULL}
};

static struct PyModuleDef definition = {
    PyModuleDef_HEAD_INIT, "_fs_directory_probe", NULL, -1, methods
};

PyMODINIT_FUNC
PyInit__fs_directory_probe(void)
{
    return PyModule_Create(&definition);
}
