/* Stateless SplitMix64-v1 integer batches. Python ordinary PRNG state is never
 * inspected or advanced. Unsigned arithmetic defines every wrap portably. */
#ifndef UNSTD_RAND_KEYED_H
#define UNSTD_RAND_KEYED_H

#include <stdint.h>

static uint64_t
rand_splitmix64_next(uint64_t *state)
{
    *state += UINT64_C(0x9E3779B97F4A7C15);
    uint64_t word = *state;
    word = (word ^ (word >> 30)) * UINT64_C(0xBF58476D1CE4E5B9);
    word = (word ^ (word >> 27)) * UINT64_C(0x94D049BB133111EB);
    return word ^ (word >> 31);
}

static PyObject *
rand_seeded_ints(PyObject *module, PyObject *args)
{
    (void)module;
    PyObject *seeds, *low_object, *width_object;
    if (!PyArg_ParseTuple(args, "OOO:seeded_ints", &seeds, &low_object,
                          &width_object)) return NULL;
    if (!PyList_CheckExact(seeds)) {
        PyErr_SetString(PyExc_TypeError,
                        "native seeded_ints requires an exact list");
        return NULL;
    }

    /* Direct private calls receive the same checked numeric boundary. */
    PyObject *low_index = PyNumber_Index(low_object);
    if (low_index == NULL) return NULL;
    long long low = PyLong_AsLongLong(low_index);
    if (low == -1 && PyErr_Occurred()) {
        Py_DECREF(low_index);
        return NULL;
    }
    PyObject *width_index = PyNumber_Index(width_object);
    if (width_index == NULL) {
        Py_DECREF(low_index);
        return NULL;
    }
    PyObject *one = PyLong_FromLong(1);
    if (one == NULL) {
        Py_DECREF(low_index);
        Py_DECREF(width_index);
        return NULL;
    }
    PyObject *last_offset = PyNumber_Subtract(width_index, one);
    Py_DECREF(one);
    Py_DECREF(width_index);
    if (last_offset == NULL) {
        Py_DECREF(low_index);
        return NULL;
    }
    uint64_t maximum_offset = PyLong_AsUnsignedLongLong(last_offset);
    if (maximum_offset == UINT64_MAX && PyErr_Occurred()) {
        Py_DECREF(low_index);
        Py_DECREF(last_offset);
        if (PyErr_ExceptionMatches(PyExc_OverflowError)) {
            PyErr_Clear();
            PyErr_SetString(PyExc_ValueError,
                            "width must be between 1 and 2**64");
        }
        return NULL;
    }
    PyObject *top = PyNumber_Add(low_index, last_offset);
    Py_DECREF(low_index);
    Py_DECREF(last_offset);
    if (top == NULL) return NULL;
    long long checked_top = PyLong_AsLongLong(top);
    Py_DECREF(top);
    if (checked_top == -1 && PyErr_Occurred()) return NULL;

    /* Hold immutable references through arbitrary __index__ callbacks. Validate
     * the entire snapshot before producing any output or local random words. */
    PyObject *snapshot = PySequence_Tuple(seeds);
    if (snapshot == NULL) return NULL;
    Py_ssize_t count = PyTuple_GET_SIZE(snapshot);
    if ((size_t)count > SIZE_MAX / sizeof(uint64_t)) {
        Py_DECREF(snapshot);
        return PyErr_NoMemory();
    }
    uint64_t *values = count ? PyMem_Malloc((size_t)count * sizeof(uint64_t)) : NULL;
    if (count && values == NULL) {
        Py_DECREF(snapshot);
        return PyErr_NoMemory();
    }
    for (Py_ssize_t i = 0; i < count; i++) {
        if ((i & 1023) == 0 && PyErr_CheckSignals() < 0) goto fail;
        PyObject *value = PyNumber_Index(PyTuple_GET_ITEM(snapshot, i));
        if (value == NULL) goto fail;
        values[i] = PyLong_AsUnsignedLongLong(value);
        Py_DECREF(value);
        if (values[i] == UINT64_MAX && PyErr_Occurred()) {
            if (PyErr_ExceptionMatches(PyExc_OverflowError)) {
                PyErr_Clear();
                PyErr_SetString(PyExc_ValueError,
                                "seeds must contain unsigned 64-bit integers");
            }
            goto fail;
        }
    }
    Py_DECREF(snapshot);
    snapshot = NULL;

    PyObject *result = PyList_New(count);
    if (result == NULL) goto fail;
    /* A signal handler may walk gc.get_objects(). Keep the partially filled
     * list invisible until all slots hold their final exact integer objects. */
    PyObject_GC_UnTrack(result);
    uint64_t width = maximum_offset + UINT64_C(1);
    uint64_t threshold = width ? (UINT64_C(0) - width) % width : 0;
    int power_of_two = (width & maximum_offset) == 0;
    unsigned int checkpoints = 0;
    for (Py_ssize_t i = 0; i < count; i++) {
        uint64_t word;
        do {
            if ((checkpoints++ & 1023U) == 0 && PyErr_CheckSignals() < 0) {
                Py_DECREF(result);
                goto fail;
            }
            word = rand_splitmix64_next(&values[i]);
        } while (word < threshold);
        uint64_t offset = power_of_two ? word & maximum_offset : word % width;
        uint64_t bits = (uint64_t)low + offset;
        /* Avoid implementation-defined unsigned-to-signed conversion. */
        PyObject *value = bits <= INT64_MAX
            ? PyLong_FromUnsignedLongLong(bits)
            : PyLong_FromLongLong(-1 - (long long)(UINT64_MAX - bits));
        if (value == NULL) {
            Py_DECREF(result);
            goto fail;
        }
        PyList_SET_ITEM(result, i, value);
    }
    PyMem_Free(values);
    PyObject_GC_Track(result);
    return result;

fail:
    Py_XDECREF(snapshot);
    PyMem_Free(values);
    return NULL;
}

#endif
