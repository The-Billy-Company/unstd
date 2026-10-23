/* Optional exact-builtin tree clone. No user copy/hash/equality hooks run here.
 *
 * Unsupported trees return NotImplemented, leaving the Python walker in charge
 * of classes, reducers, sets, foreign keys and cycle behavior. The native path
 * never explicitly invokes copy, reducer, hash or equality hooks.
 *
 * Container allocation can run cyclic GC and arbitrary Python finalizers even
 * while the GIL is held. Real iterators and owned item references are deliberate:
 * no cached list size, borrowed dict value or raw container span survives an
 * allocation. Dict item iterators also retain Python's mutation checks.
 */
#define PY_SSIZE_T_CLEAN
#include <Python.h>

#define MAX_DEPTH 128
#define MAX_VALUES 65536

static int clone_value(PyObject *, PyObject **, unsigned int, Py_ssize_t *);
static int atom(PyObject *);
static int eligible(PyObject *, unsigned int, Py_ssize_t *);

static int
shallow_atoms(PyObject *source)
{
    if (PyList_CheckExact(source)) {
        Py_ssize_t size = PyList_GET_SIZE(source);
        if (size >= MAX_VALUES) {
            return 0;
        }
        for (Py_ssize_t i = 0; i < size; i++) {
            if (!atom(PyList_GET_ITEM(source, i))) {
                return 0;
            }
        }
        return 1;
    }
    if (PyDict_CheckExact(source)) {
        if (PyDict_Size(source) >= MAX_VALUES) {
            return 0;
        }
        Py_ssize_t position = 0;
        PyObject *key, *value;
        while (PyDict_Next(source, &position, &key, &value)) {
            if (!PyUnicode_CheckExact(key) || !atom(value)) {
                return 0;
            }
        }
        return 1;
    }
    return 0;
}

/* An allocation-free, bounded eligibility pass avoids constructing partial
 * clones of ordinary foreign trees. The GIL and absence of callbacks make
 * borrowed references safe in this pass only. The copying pass revalidates
 * everything because its allocations can trigger source-mutating finalizers. */
static int
eligible_contents(PyObject *source, unsigned int depth, Py_ssize_t *remaining)
{
    if (PyList_CheckExact(source) || PyTuple_CheckExact(source)) {
        Py_ssize_t size = Py_SIZE(source);
        if (size > *remaining) {
            return 0;
        }
        for (Py_ssize_t i = 0; i < size; i++) {
            PyObject *item = PyList_CheckExact(source)
                ? PyList_GET_ITEM(source, i) : PyTuple_GET_ITEM(source, i);
            int status = eligible(item, depth - 1, remaining);
            if (status != 1) {
                return status;
            }
        }
        return 1;
    }
    if (PyDict_CheckExact(source)) {
        if (PyDict_Size(source) > *remaining) {
            return 0;
        }
        Py_ssize_t position = 0;
        PyObject *key, *value;
        while (PyDict_Next(source, &position, &key, &value)) {
            if (!PyUnicode_CheckExact(key)) {
                return 0;
            }
            int status = eligible(value, depth - 1, remaining);
            if (status != 1) {
                return status;
            }
        }
        return 1;
    }
    return 0;
}

static int
eligible(PyObject *source, unsigned int depth, Py_ssize_t *remaining)
{
    if (--*remaining < 0) {
        return 0;
    }
    if (atom(source)) {
        return 1;
    }
    if (depth == 0 || (!PyList_CheckExact(source) && !PyTuple_CheckExact(source)
                       && !PyDict_CheckExact(source))) {
        return 0;
    }
    /* Also guard the proof itself against a nearly exhausted native C stack.
     * No allocation/callback occurs unless entering fails; errors unwind the
     * proof immediately without touching another borrowed source reference. */
    if (Py_EnterRecursiveCall(" while checking a builtin clone tree")) {
        /* Native stack pressure must defer to the Python walker. Raising this
         * RecursionError would make deep() select whole-graph deepcopy, which
         * preserves aliases that an acyclic structural clone must duplicate. */
        if (PyErr_ExceptionMatches(PyExc_RecursionError)) {
            PyErr_Clear();
            return 0;
        }
        return -1;
    }
    int status = eligible_contents(source, depth, remaining);
    Py_LeaveRecursiveCall();
    return status;
}

static int
atom(PyObject *value)
{
    return value == Py_None || value == Py_Ellipsis
        || value == Py_NotImplemented || PyBool_Check(value)
        || PyLong_CheckExact(value) || PyFloat_CheckExact(value)
        || PyComplex_CheckExact(value) || PyBytes_CheckExact(value)
        || PyUnicode_CheckExact(value);
}

/* 1: owned result; 0: unsupported (no exception); -1: real failure. */
static int
clone_sequence(PyObject *source, PyObject **result, unsigned int depth,
               Py_ssize_t *remaining)
{
    PyObject *iterator = PyObject_GetIter(source);
    if (iterator == NULL) {
        return -1;
    }
    PyObject *output = PyList_New(0);
    if (output == NULL) {
        Py_DECREF(iterator);
        return -1;
    }
    PyObject *item;
    int status = 1;
    while ((item = PyIter_Next(iterator)) != NULL) {
        PyObject *copied = NULL;
        status = clone_value(item, &copied, depth - 1, remaining);
        Py_DECREF(item);
        if (status != 1) {
            break;
        }
        int appended = PyList_Append(output, copied);
        Py_DECREF(copied);
        if (appended < 0) {
            status = -1;
            break;
        }
    }
    if (status == 1 && PyErr_Occurred()) {
        status = -1;
    }
    Py_DECREF(iterator);
    if (status != 1) {
        Py_DECREF(output);
        return status;
    }
    if (PyTuple_CheckExact(source)) {
        *result = PyList_AsTuple(output);
        Py_DECREF(output);
        return *result == NULL ? -1 : 1;
    }
    *result = output;
    return 1;
}

static int
clone_dict(PyObject *source, PyObject **result, unsigned int depth,
           Py_ssize_t *remaining)
{
    PyObject *output = PyDict_New();
    if (output == NULL) {
        return -1;
    }
    /* Do not snapshot: the Python comprehension iterates the live item view. */
    PyObject *view = PyObject_CallMethod(source, "items", NULL);
    if (view == NULL) {
        Py_DECREF(output);
        return -1;
    }
    PyObject *iterator = PyObject_GetIter(view);
    Py_DECREF(view);
    if (iterator == NULL) {
        Py_DECREF(output);
        return -1;
    }
    PyObject *item;
    int status = 1;
    while ((item = PyIter_Next(iterator)) != NULL) {
        /* The owned item tuple pins both key and value through recursive
         * allocations, even if a finalizer clears the source dictionary. */
        PyObject *key = PyTuple_GET_ITEM(item, 0);
        if (!PyUnicode_CheckExact(key)) {
            Py_DECREF(item);
            status = 0;
            break;
        }
        PyObject *copied = NULL;
        status = clone_value(PyTuple_GET_ITEM(item, 1), &copied, depth - 1,
                             remaining);
        if (status != 1) {
            Py_DECREF(item);
            break;
        }
        int inserted = PyDict_SetItem(output, key, copied);
        Py_DECREF(copied);
        Py_DECREF(item);
        if (inserted < 0) {
            status = -1;
            break;
        }
    }
    if (status == 1 && PyErr_Occurred()) {
        status = -1;
    }
    Py_DECREF(iterator);
    if (status != 1) {
        Py_DECREF(output);
        return status;
    }
    *result = output;
    return 1;
}

static int
clone_value(PyObject *source, PyObject **result, unsigned int depth,
            Py_ssize_t *remaining)
{
    if (--*remaining < 0) {
        return 0;
    }
    if (atom(source)) {
        *result = Py_NewRef(source);
        return 1;
    }
    if (depth == 0) {
        return 0;
    }
    if (!PyList_CheckExact(source) && !PyTuple_CheckExact(source)
        && !PyDict_CheckExact(source)) {
        return 0;
    }
    if (Py_EnterRecursiveCall(" while cloning a builtin tree")) {
        if (PyErr_ExceptionMatches(PyExc_RecursionError)) {
            PyErr_Clear();
            return 0;
        }
        return -1;
    }
    int status;
    if (shallow_atoms(source)) {
        PyObject *copied = PyDict_CheckExact(source)
            ? PyDict_Copy(source) : PyList_GetSlice(source, 0, PY_SSIZE_T_MAX);
        if (copied == NULL) {
            status = -1;
        } else {
            Py_ssize_t size = PyDict_CheckExact(copied)
                ? PyDict_Size(copied) : PyList_GET_SIZE(copied);
            if (size <= *remaining && shallow_atoms(copied)) {
                *remaining -= size;
                *result = copied;
                status = 1;
            } else {
                Py_DECREF(copied);
                status = 0;
            }
        }
    } else {
        status = PyDict_CheckExact(source)
            ? clone_dict(source, result, depth, remaining)
            : clone_sequence(source, result, depth, remaining);
    }
    Py_LeaveRecursiveCall();
    return status;
}

static PyObject *
clone_builtin(PyObject *module, PyObject *source)
{
    (void)module;
    /* The C stack guard alone cannot model Python's frame budget on 3.12+.
     * These versioned CPython wheels use the same Python-frame budget field
     * as the JSON guard on our tested interpreter range. Future interpreters
     * use the conservative public frame walk until their ABI is validated. */
    int recursion_limit = Py_GetRecursionLimit();
#if PY_VERSION_HEX >= 0x030C0000 && PY_VERSION_HEX < 0x030F0000
    int remaining = PyThreadState_Get()->py_recursion_remaining - 8;
#else
    int remaining = recursion_limit - 8;
    PyFrameObject *frame = PyThreadState_GetFrame(PyThreadState_Get());
    while (frame != NULL) {
        --remaining;
        PyFrameObject *previous = PyFrame_GetBack(frame);
        Py_DECREF(frame);
        frame = previous;
    }
    if (PyErr_Occurred()) {
        return NULL;
    }
#endif
    if (remaining <= 0) {
        Py_RETURN_NOTIMPLEMENTED;
    }
    unsigned int budget = remaining < MAX_DEPTH
        ? (unsigned int)remaining : MAX_DEPTH;
    if (shallow_atoms(source)) {
        /* Bulk copies remove per-item iterator/append overhead on flat data.
         * Revalidate the owned snapshot: allocation may have run a finalizer
         * which inserted a foreign value into the source after the proof. */
        PyObject *copied = PyDict_CheckExact(source)
            ? PyDict_Copy(source) : PyList_GetSlice(source, 0, PY_SSIZE_T_MAX);
        if (copied == NULL) {
            return NULL;
        }
        if (shallow_atoms(copied) && Py_GetRecursionLimit() == recursion_limit) {
            return copied;
        }
        Py_DECREF(copied);
        Py_RETURN_NOTIMPLEMENTED;
    }
    Py_ssize_t values = MAX_VALUES;
    int accepted = eligible(source, budget, &values);
    if (accepted < 0) {
        return NULL;
    }
    if (accepted == 0) {
        Py_RETURN_NOTIMPLEMENTED;
    }
    values = MAX_VALUES;
    PyObject *result = NULL;
    int status = clone_value(source, &result, budget, &values);
    if (status < 0) {
        return NULL;
    }
    if (status == 0) {
        Py_RETURN_NOTIMPLEMENTED;
    }
    if (Py_GetRecursionLimit() != recursion_limit) {
        Py_DECREF(result);
        Py_RETURN_NOTIMPLEMENTED;
    }
    return result;
}

static PyMethodDef methods[] = {
    {"clone", clone_builtin, METH_O,
     "Clone exact builtin data or return NotImplemented without copy hooks."},
    {NULL, NULL, 0, NULL}
};

static PyModuleDef_Slot slots[] = {
    {Py_mod_multiple_interpreters, Py_MOD_PER_INTERPRETER_GIL_SUPPORTED},
#if PY_VERSION_HEX >= 0x030D0000
    {Py_mod_gil, Py_MOD_GIL_USED},
#endif
    {0, NULL}
};

static struct PyModuleDef definition = {
    PyModuleDef_HEAD_INIT,
    "_clone_native",
    "Optional exact-builtin structural cloning; Python owns all other shapes.",
    0,
    methods,
    slots,
    NULL,
    NULL,
    NULL
};

PyMODINIT_FUNC
PyInit__clone_native(void)
{
#ifdef Py_GIL_DISABLED
    PyErr_SetString(PyExc_ImportError, "native cloning requires a GIL build");
    return NULL;
#else
    return PyModuleDef_Init(&definition);
#endif
}
