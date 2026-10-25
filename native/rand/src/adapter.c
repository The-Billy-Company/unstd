/* Optional seed-faithful Fisher-Yates loop. Python owns the Random engine and
 * selects this kernel only for its ordinary getrandbits implementation. */
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <stddef.h>
#include "keyed.h"

static PyObject *
shuffle(PyObject *module, PyObject *args)
{
    (void)module;
    PyObject *getrandbits, *items;
    if (!PyArg_ParseTuple(args, "OO:shuffle", &getrandbits, &items)) return NULL;
    if (!PyList_CheckExact(items)) {
        PyErr_SetString(PyExc_TypeError, "native shuffle requires an exact list");
        return NULL;
    }
    if (!PyCallable_Check(getrandbits)) {
        PyErr_SetString(PyExc_TypeError, "getrandbits must be callable");
        return NULL;
    }
    Py_ssize_t length = PyList_GET_SIZE(items);
    unsigned int checkpoints = 0;
    for (Py_ssize_t i = length - 1; i > 0; i--) {
        size_t bound = (size_t)i + 1, remaining = bound;
        long bits = 0;
        while (remaining != 0) {
            remaining >>= 1;
            bits++;
        }
        PyObject *width = PyLong_FromLong(bits);
        if (width == NULL) return NULL;
        size_t selected;
        do {
            /* Both the loop and adversarial rejection callbacks remain
             * interruptible without releasing the GIL or retaining buffers. */
            if ((checkpoints++ & 1023U) == 0 && PyErr_CheckSignals() < 0) {
                Py_DECREF(width);
                return NULL;
            }
            PyObject *result = PyObject_CallOneArg(getrandbits, width);
            if (result == NULL) {
                Py_DECREF(width);
                return NULL;
            }
            selected = PyLong_AsSize_t(result);
            Py_DECREF(result);
            if (selected == (size_t)-1 && PyErr_Occurred()) {
                Py_DECREF(width);
                return NULL;
            }
            /* A callback, its result finalizer, or a profiler
             * can mutate the list. Never retain borrowed items across them. */
            if (PyList_GET_SIZE(items) != length) {
                Py_DECREF(width);
                PyErr_SetString(PyExc_RuntimeError,
                                "list size changed during native shuffle");
                return NULL;
            }
        } while (selected >= bound);
        Py_DECREF(width);

        PyObject *left = PyList_GetItem(items, i);
        if (left == NULL) return NULL;
        PyObject *right = PyList_GetItem(items, (Py_ssize_t)selected);
        if (right == NULL) return NULL;
        /* Own both existing values so replacing a slot cannot run an item
         * finalizer between the two assignments. SetItem steals these refs. */
        Py_INCREF(left);
        Py_INCREF(right);
        if (PyList_SetItem(items, i, right) < 0) {
            Py_DECREF(left);
            return NULL;
        }
        if (PyList_SetItem(items, (Py_ssize_t)selected, left) < 0) return NULL;
    }
    Py_RETURN_NONE;
}

static PyMethodDef methods[] = {
    {"shuffle", shuffle, METH_VARARGS,
     "Shuffle an exact list with a live getrandbits callable and unbiased rejection sampling."},
    {"seeded_ints", rand_seeded_ints, METH_VARARGS,
     "Stateless SplitMix64-v1 integer draws from independent unsigned 64-bit seeds."},
    {NULL, NULL, 0, NULL}
};

static struct PyModuleDef definition = {
    PyModuleDef_HEAD_INIT,
    "_rand_native",
    "Seed-faithful native list shuffling without a private PRNG implementation.",
    0,
    methods,
    NULL, NULL, NULL, NULL
};

PyMODINIT_FUNC
PyInit__rand_native(void)
{
    PyObject *module = PyModule_Create(&definition);
    if (module == NULL) return NULL;
    if (PyModule_AddIntConstant(module, "KERNEL_VERSION", 1) < 0) {
        Py_DECREF(module);
        return NULL;
    }
    return module;
}
