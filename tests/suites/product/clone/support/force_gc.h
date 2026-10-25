/* Validation-only allocation boundaries for the clone kernel.
 * Compile with -include this file; never include it in production builds.
 * A nonnegative sys._unstd_clone_fault_after injects one MemoryError after that
 * many wrapped calls. Otherwise callbacks run at allocation/API boundaries.
 */
#include <Python.h>

static inline int
clone_fault_before(int phase, PyObject *source)
{
    PyObject *countdown = PySys_GetObject("_unstd_clone_fault_after");
    if (countdown != NULL && PyLong_CheckExact(countdown)) {
        Py_ssize_t remaining = PyLong_AsSsize_t(countdown);
        if (remaining == -1 && PyErr_Occurred()) {
            return -1;
        }
        PyObject *selected = PySys_GetObject("_unstd_clone_fault_phase");
        int matches = selected == NULL || !PyLong_CheckExact(selected)
            || PyLong_AsLong(selected) == phase;
        if (remaining >= 0 && matches) {
            PyObject *next = PyLong_FromSsize_t(remaining - 1);
            if (next == NULL) {
                return -1;
            }
            int updated = PySys_SetObject("_unstd_clone_fault_after", next);
            Py_DECREF(next);
            if (updated < 0) {
                return -1;
            }
            if (remaining == 0) {
                PyErr_NoMemory();
                return -1;
            }
        }
        /* OOM campaigns run separately from reentrant-GC campaigns. */
        return 0;
    }
    PyObject *previous = PySys_GetObject("_unstd_clone_fault_allocation");
    PyObject *previous_source = PySys_GetObject("_unstd_clone_fault_source");
    Py_XINCREF(previous);
    Py_XINCREF(previous_source);
    PyObject *marker = PyLong_FromLong(phase);
    PyObject *identity = PyLong_FromVoidPtr(source);
    int status = -1;
    if (marker != NULL && identity != NULL
        && PySys_SetObject("_unstd_clone_fault_allocation", marker) == 0
        && PySys_SetObject("_unstd_clone_fault_source", identity) == 0) {
        PyGC_Collect();
        status = 0;
    }
    /* Preserve a failure while restoring the previous diagnostic markers. */
    PyObject *error = PyErr_GetRaisedException();
    PySys_SetObject("_unstd_clone_fault_allocation", previous);
    PySys_SetObject("_unstd_clone_fault_source", previous_source);
    if (error != NULL) {
        PyErr_SetRaisedException(error);
    }
    Py_XDECREF(identity);
    Py_XDECREF(marker);
    Py_XDECREF(previous_source);
    Py_XDECREF(previous);
    return status;
}

static inline PyObject *
clone_fault_list_new(Py_ssize_t size, PyObject *source)
{
    if (clone_fault_before(1, source) < 0) return NULL;
    return PyList_New(size);
}
static inline PyObject *
clone_fault_tuple_new(Py_ssize_t size, PyObject *source)
{
    if (clone_fault_before(2, source) < 0) return NULL;
    return PyTuple_New(size);
}
static inline PyObject *
clone_fault_dict_new(Py_ssize_t size, PyObject *source)
{
    if (clone_fault_before(3, source) < 0) return NULL;
    return _PyDict_NewPresized(size);
}
static inline PyObject *
clone_fault_dict_copy(PyObject *source)
{
    if (clone_fault_before(4, source) < 0) return NULL;
    return PyDict_Copy(source);
}
static inline PyObject *
clone_fault_list_slice(PyObject *source, Py_ssize_t low, Py_ssize_t high)
{
    if (clone_fault_before(5, source) < 0) return NULL;
    return PyList_GetSlice(source, low, high);
}
static inline PyObject *
clone_fault_generic_alloc(PyTypeObject *type, Py_ssize_t size, PyObject *source)
{
    if (clone_fault_before(6, source) < 0) return NULL;
    return PyType_GenericAlloc(type, size);
}
static inline PyObject *
clone_fault_identity(void *source)
{
    if (clone_fault_before(7, (PyObject *)source) < 0) return NULL;
    return PyLong_FromVoidPtr(source);
}
static inline PyObject *
clone_fault_empty_dict(PyObject *source)
{
    if (clone_fault_before(8, source) < 0) return NULL;
    return PyDict_New();
}
static inline int
clone_fault_dict_set(PyObject *mapping, PyObject *key, PyObject *value)
{
    if (clone_fault_before(9, mapping) < 0) return -1;
    return PyDict_SetItem(mapping, key, value);
}
static inline int
clone_fault_list_append(PyObject *list, PyObject *value)
{
    if (clone_fault_before(10, list) < 0) return -1;
    return PyList_Append(list, value);
}
static inline void *
clone_fault_realloc(void *memory, size_t size, PyObject *source)
{
    if (clone_fault_before(11, source) < 0) return NULL;
    return PyMem_Realloc(memory, size);
}
static inline void *
clone_fault_calloc(size_t count, size_t size, PyObject *source)
{
    if (clone_fault_before(12, source) < 0) return NULL;
    return PyMem_Calloc(count, size);
}

/* Function-like macros preserve allocation-slot pointer comparisons. Object
 * allocation sites have a source parameter; raw memo allocation sites do not. */
#define PyType_GenericAlloc(type, size) clone_fault_generic_alloc(type, size, source)
#define PyList_New(size) clone_fault_list_new(size, NULL)
#define PyTuple_New(size) clone_fault_tuple_new(size, NULL)
#define _PyDict_NewPresized(size) clone_fault_dict_new(size, NULL)
#define PyDict_Copy(source) clone_fault_dict_copy(source)
#define PyList_GetSlice(source, low, high) clone_fault_list_slice(source, low, high)
#define PyLong_FromVoidPtr(value) clone_fault_identity(value)
#define PyDict_New() clone_fault_empty_dict(NULL)
#define PyDict_SetItem(mapping, key, value) clone_fault_dict_set(mapping, key, value)
#define PyList_Append(list, value) clone_fault_list_append(list, value)
#define PyMem_Realloc(memory, size) clone_fault_realloc(memory, size, NULL)
#define PyMem_Calloc(count, size) clone_fault_calloc(count, size, NULL)
