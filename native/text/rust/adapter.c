/* CPython adapter for the bounded, safe Rust text kernels.
 *
 * Exact strings and their storage remain strongly held by the call, and the GIL
 * remains held throughout. Rust borrows their immutable 1/2/4-byte code-point
 * storage synchronously; no UTF-8 conversion or Python callback occurs.
 */
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <stddef.h>

/* Keep these layouts synchronized with native/text/rust/src/lib.rs. */
typedef struct {
    const void *data;
    size_t length;
    int kind;
} UnstdText;

typedef struct {
    int status;
    size_t value;
} UnstdTextDistanceResult;

typedef struct {
    int status;
    double value;
} UnstdTextJaroResult;

extern UnstdTextDistanceResult unstd_text_distance(UnstdText, UnstdText);
extern UnstdTextJaroResult unstd_text_jaro(UnstdText, UnstdText);

static UnstdText
text_view(PyObject *value)
{
    UnstdText result = {PyUnicode_DATA(value),
                        (size_t)PyUnicode_GET_LENGTH(value),
                        PyUnicode_KIND(value)};
    return result;
}

static PyObject *
kernel_failure(int status)
{
    if (status == 0) {
        Py_RETURN_NOTIMPLEMENTED;
    }
    if (status == -1) {
        return PyErr_NoMemory();
    }
    PyErr_SetString(PyExc_SystemError, "Rust text kernel panicked");
    return NULL;
}

static PyObject *
distance(PyObject *module, PyObject *const *args, Py_ssize_t nargs)
{
    (void)module;
    if (nargs != 2) {
        PyErr_SetString(PyExc_TypeError, "distance() takes exactly two arguments");
        return NULL;
    }
    if (!PyUnicode_CheckExact(args[0]) || !PyUnicode_CheckExact(args[1])) {
        Py_RETURN_NOTIMPLEMENTED;
    }
    UnstdTextDistanceResult result = unstd_text_distance(text_view(args[0]),
                                                       text_view(args[1]));
    if (result.status != 1) {
        return kernel_failure(result.status);
    }
    return PyLong_FromSize_t(result.value);
}

static PyObject *
jaro(PyObject *module, PyObject *const *args, Py_ssize_t nargs)
{
    (void)module;
    if (nargs != 2) {
        PyErr_SetString(PyExc_TypeError, "jaro() takes exactly two arguments");
        return NULL;
    }
    if (!PyUnicode_CheckExact(args[0]) || !PyUnicode_CheckExact(args[1])) {
        Py_RETURN_NOTIMPLEMENTED;
    }
    UnstdTextJaroResult result = unstd_text_jaro(text_view(args[0]), text_view(args[1]));
    if (result.status != 1) {
        return kernel_failure(result.status);
    }
    return PyFloat_FromDouble(result.value);
}

static PyMethodDef methods[] = {
    {"distance", (PyCFunction)(void(*)(void))distance, METH_FASTCALL,
     PyDoc_STR("Bounded exact-string Levenshtein distance, or NotImplemented.")},
    {"jaro", (PyCFunction)(void(*)(void))jaro, METH_FASTCALL,
     PyDoc_STR("Bounded exact-string Jaro similarity, or NotImplemented.")},
    {NULL, NULL, 0, NULL}
};

static PyModuleDef_Slot module_slots[] = {
    {Py_mod_multiple_interpreters, Py_MOD_PER_INTERPRETER_GIL_SUPPORTED},
#if PY_VERSION_HEX >= 0x030D0000
    {Py_mod_gil, Py_MOD_GIL_USED},
#endif
    {0, NULL}
};

static struct PyModuleDef definition = {
    PyModuleDef_HEAD_INIT,
    "_text_native",
    "Bounded Rust text kernels with portable Python fallbacks.",
    0,
    methods,
    module_slots,
    NULL,
    NULL,
    NULL
};

PyMODINIT_FUNC
PyInit__text_native(void)
{
#ifdef Py_GIL_DISABLED
    PyErr_SetString(PyExc_ImportError, "native text kernels require a GIL build");
    return NULL;
#else
    return PyModuleDef_Init(&definition);
#endif
}
