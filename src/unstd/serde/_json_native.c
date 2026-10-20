/* Exact builtin JSON proofs and serialization in one uninterrupted GIL span.
 *
 * No allocation or Python callback occurs while validating an eligible tree.
 * The cached orjson builtin retains the GIL, so another thread, trace hook or
 * signal callback cannot insert an unvalidated value before serialization.
 * Other values return NotImplemented for the stdlib-compatible Python path.
 */
#ifndef Py_LIMITED_API
#define Py_LIMITED_API 0x030C0000
#endif
#define PY_SSIZE_T_CLEAN
#include <Python.h>

#define MAX_DEPTH 16
#define MAX_VALUES 65536

typedef struct {
    PyObject *encoder;
} NativeState;

static int
safe_string(PyObject *value, int spaced)
{
    if (!spaced) {
        return 1;
    }
    Py_ssize_t end = PyUnicode_GetLength(value);
    if (end < 0) {
        return -1;
    }
    Py_ssize_t found = PyUnicode_FindChar(value, ',', 0, end, 1);
    if (found != -1) {
        return found == -2 ? -1 : 0;
    }
    found = PyUnicode_FindChar(value, ':', 0, end, 1);
    return found == -2 ? -1 : found == -1;
}

static int
eligible(PyObject *value, Py_ssize_t *remaining, int spaced, int depth)
{
    if (--*remaining < 0) {
        return 0;
    }
    if (PyUnicode_CheckExact(value)) {
        return safe_string(value, spaced);
    }
    if (value == Py_None || PyBool_Check(value) || PyLong_CheckExact(value)) {
        /* orjson rejects oversized integers without exposing a lossy result;
         * its error sends the original tree through the stdlib fallback. */
        return 1;
    }
    if (depth >= MAX_DEPTH) {
        return 0;
    }
    if (Py_EnterRecursiveCall(" while proving JSON encoding")) {
        return -1;
    }

    int result = 0;
    if (PyList_CheckExact(value) || PyTuple_CheckExact(value)) {
        int list = PyList_CheckExact(value);
        Py_ssize_t size = list ? PyList_Size(value) : PyTuple_Size(value);
        if (size <= *remaining) {
            result = 1;
            for (Py_ssize_t index = 0; index < size; index++) {
                PyObject *item = list ? PyList_GetItem(value, index)
                                      : PyTuple_GetItem(value, index);
                result = eligible(item, remaining, spaced, depth + 1);
                if (result != 1) {
                    break;
                }
            }
        }
    } else if (PyDict_CheckExact(value)) {
        if (PyDict_Size(value) <= *remaining) {
            Py_ssize_t position = 0;
            PyObject *key, *item;
            result = 1;
            while (PyDict_Next(value, &position, &key, &item)) {
                if (!PyUnicode_CheckExact(key)) {
                    result = 0;
                    break;
                }
                result = safe_string(key, spaced);
                if (result == 1) {
                    result = eligible(item, remaining, spaced, depth + 1);
                }
                if (result != 1) {
                    break;
                }
            }
        }
    }
    Py_LeaveRecursiveCall();
    return result;
}

static PyObject *
encode(PyObject *module, PyObject *const *args, Py_ssize_t count,
       PyObject *keywords)
{
    PyObject *value = NULL, *option = NULL, *spaced = Py_False;
    if (count > 3) {
        PyErr_SetString(PyExc_TypeError, "encode() takes at most 3 arguments");
        return NULL;
    }
    if (count > 0) {
        value = args[0];
    }
    if (count > 1) {
        option = args[1];
    }
    if (count > 2) {
        spaced = args[2];
    }
    Py_ssize_t named = keywords == NULL ? 0 : PyTuple_Size(keywords);
    for (Py_ssize_t index = 0; index < named; index++) {
        PyObject *key = PyTuple_GetItem(keywords, index);
        PyObject *argument = args[count + index];
        if (PyUnicode_CompareWithASCIIString(key, "obj") == 0 && value == NULL) {
            value = argument;
        } else if (PyUnicode_CompareWithASCIIString(key, "option") == 0
                   && option == NULL) {
            option = argument;
        } else if (PyUnicode_CompareWithASCIIString(key, "spaced") == 0
                   && count < 3) {
            spaced = argument;
        } else {
            PyErr_SetString(PyExc_TypeError, "invalid encode() keyword arguments");
            return NULL;
        }
    }
    if (value == NULL) {
        PyErr_SetString(PyExc_TypeError, "encode() requires obj");
        return NULL;
    }
    if ((option != NULL && !PyLong_CheckExact(option))
        || !PyBool_Check(spaced)) {
        Py_RETURN_NOTIMPLEMENTED;
    }

    /* Reserve the native caller's recursion entry as well as each container. */
    if (Py_EnterRecursiveCall(" while proving JSON encoding")) {
        return NULL;
    }
    Py_ssize_t remaining = MAX_VALUES;
    int result = eligible(value, &remaining, spaced == Py_True, 0);
    if (result != 1) {
        Py_LeaveRecursiveCall();
        if (result < 0) {
            return NULL;
        }
        Py_RETURN_NOTIMPLEMENTED;
    }

    NativeState *state = PyModule_GetState(module);
    PyObject *backend_args[] = {value, Py_None, option};
    PyObject *encoded = PyObject_Vectorcall(
        state->encoder, backend_args, option == NULL ? 1 : 3, NULL);
    Py_LeaveRecursiveCall();
    return encoded;
}

static PyMethodDef methods[] = {
    {"encode", (PyCFunction)(void (*)(void))encode,
     METH_FASTCALL | METH_KEYWORDS,
     "encode(obj, option=0, spaced=False)\n--\n\n"
     "Serialize an exact builtin tree, or return NotImplemented."},
    {NULL, NULL, 0, NULL}
};

static int
traverse(PyObject *module, visitproc visit, void *arg)
{
    NativeState *state = PyModule_GetState(module);
    Py_VISIT(state->encoder);
    return 0;
}

static int
clear(PyObject *module)
{
    NativeState *state = PyModule_GetState(module);
    Py_CLEAR(state->encoder);
    return 0;
}

static struct PyModuleDef definition = {
    PyModuleDef_HEAD_INIT,
    "_json_native",
    "Optional atomic proofs for stdlib-compatible orjson encoding.",
    sizeof(NativeState),
    methods,
    NULL,
    traverse,
    clear,
    NULL
};

PyMODINIT_FUNC
PyInit__json_native(void)
{
    /* ABI3 builds may be loaded by a free-threaded interpreter. A forced
     * no-GIL runtime cannot provide the atomic proof this module requires. */
    PyObject *system = PyImport_ImportModule("sys");
    if (system == NULL) {
        return NULL;
    }
    PyObject *check = PyObject_GetAttrString(system, "_is_gil_enabled");
    Py_DECREF(system);
    if (check == NULL) {
        if (!PyErr_ExceptionMatches(PyExc_AttributeError)) {
            return NULL;
        }
        PyErr_Clear();
    } else {
        PyObject *enabled = PyObject_CallNoArgs(check);
        Py_DECREF(check);
        if (enabled == NULL) {
            return NULL;
        }
        int locked = enabled == Py_True;
        Py_DECREF(enabled);
        if (!locked) {
            PyErr_SetString(PyExc_ImportError,
                            "native JSON proofs require the GIL");
            return NULL;
        }
    }
    PyObject *backend = PyImport_ImportModule("orjson");
    if (backend == NULL) {
        return NULL;
    }
    PyObject *encoder = PyObject_GetAttrString(backend, "dumps");
    Py_DECREF(backend);
    if (encoder == NULL) {
        return NULL;
    }
    if (!PyCFunction_Check(encoder)) {
        Py_DECREF(encoder);
        PyErr_SetString(PyExc_ImportError, "orjson.dumps must be a native builtin");
        return NULL;
    }
    PyObject *module = PyModule_Create(&definition);
    if (module == NULL) {
        Py_DECREF(encoder);
        return NULL;
    }
    NativeState *state = PyModule_GetState(module);
    state->encoder = encoder;
    return module;
}
