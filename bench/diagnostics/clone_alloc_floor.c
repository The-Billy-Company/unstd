/* Favorable empty-node allocation diagnostic, never a production copier.
 *
 * Build as _clone_alloc_floor for the selected interpreter. The Python driver
 * supplies prepared node counts and an ordinary dataclass type; no input tree,
 * protocol discovery, payload references or nonempty tables are copied here.
 */
#define PY_SSIZE_T_CLEAN
#include <Python.h>

static PyObject *
nodes(PyObject *module, PyObject *args)
{
    (void)module;
    Py_ssize_t lists, dicts, records;
    PyObject *record_type;
    if (!PyArg_ParseTuple(args, "nnnO", &lists, &dicts, &records, &record_type)) {
        return NULL;
    }
    if (lists < 0 || dicts < 0 || records < 0
        || lists > PY_SSIZE_T_MAX - dicts
        || lists + dicts > PY_SSIZE_T_MAX - records
        || !PyType_Check(record_type)) {
        PyErr_SetString(PyExc_ValueError,
                        "nonnegative node counts and record type required");
        return NULL;
    }
    Py_ssize_t size = lists + dicts + records;
    PyObject *output = PyList_New(size);
    if (output == NULL) {
        return NULL;
    }
    PyObject_GC_UnTrack(output);
    for (Py_ssize_t i = 0; i < size; i++) {
        PyObject *node = i < lists ? PyList_New(0)
            : i < lists + dicts ? PyDict_New()
            : PyType_GenericAlloc((PyTypeObject *)record_type, 0);
        if (node == NULL) {
            Py_DECREF(output);
            return NULL;
        }
        PyList_SET_ITEM(output, i, node);
    }
    PyObject_GC_Track(output);
    return output;
}

static PyMethodDef methods[] = {
    {"nodes", nodes, METH_VARARGS,
     "Allocate empty output nodes without copying payloads."},
    {NULL, NULL, 0, NULL}
};

static struct PyModuleDef definition = {
    PyModuleDef_HEAD_INIT,
    "_clone_alloc_floor",
    "Favorable empty-node allocation diagnostic, not a copying algorithm.",
    -1,
    methods,
    NULL,
    NULL,
    NULL,
    NULL
};

PyMODINIT_FUNC
PyInit__clone_alloc_floor(void)
{
    return PyModule_Create(&definition);
}
