/* Bounded chunk coalescing for pre-materialized BLAKE3 streams.
 *
 * The installed BLAKE3 backend still owns every cryptographic operation. Small
 * exact-byte chunks are copied into at most 64 KiB of temporary storage so its
 * SIMD tree walker sees whole batches instead of one leaf at a time. Mutable
 * buffers, subclasses and other objects reach the backend unchanged, after all
 * preceding bytes have been absorbed. Generic iterators stay with the Python
 * eager loop: their next() may inspect the stream before producing a chunk.
 *
 * Real list/tuple iterators and owned chunk references survive allocation,
 * callbacks and a backend update releasing the GIL. No message, key, context,
 * hasher or bound method survives this call.
 */
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <string.h>

#define BATCH_BYTES 65536
#define SMALL_CHUNK_BYTES 2048
#define SIGNAL_INTERVAL 4096

static int
update_chunk(PyObject *update, PyObject *chunk)
{
    PyObject *result = PyObject_CallOneArg(update, chunk);
    if (result == NULL) {
        return -1;
    }
    Py_DECREF(result);
    return 0;
}

static int
flush(PyObject *update, const char *buffer, Py_ssize_t *used)
{
    if (*used == 0) {
        return 0;
    }
    PyObject *chunk = PyBytes_FromStringAndSize(buffer, *used);
    if (chunk == NULL) {
        return -1;
    }
    int status = update_chunk(update, chunk);
    Py_DECREF(chunk);
    if (status == 0) {
        *used = 0;
    }
    return status;
}

static PyObject *
absorb(PyObject *module, PyObject *const *args, Py_ssize_t nargs)
{
    (void)module;
    if (nargs != 2) {
        PyErr_SetString(PyExc_TypeError, "absorb() takes exactly two arguments");
        return NULL;
    }
    PyObject *chunks = args[1];
    if (!PyTuple_CheckExact(chunks) && !PyList_CheckExact(chunks)) {
        Py_RETURN_NOTIMPLEMENTED;
    }
    PyObject *update = PyObject_GetAttrString(args[0], "update");
    if (update == NULL) {
        return NULL;
    }
    PyObject *iterator = PyObject_GetIter(chunks);
    if (iterator == NULL) {
        Py_DECREF(update);
        return NULL;
    }

    char *buffer = NULL;
    Py_ssize_t used = 0;
    unsigned int since_signal = 0;
    int status = 0;
    PyObject *previous = NULL;
    PyObject *chunk;
    while ((chunk = PyIter_Next(iterator)) != NULL) {
        /* Python's for-loop keeps its previous target alive until next()
         * returns the replacement. A mutable chunk can remove itself from
         * the list during update and run a source-mutating finalizer here. */
        Py_XSETREF(previous, chunk);
        if (PyBytes_CheckExact(chunk)
            && PyBytes_GET_SIZE(chunk) <= SMALL_CHUNK_BYTES) {
            Py_ssize_t size = PyBytes_GET_SIZE(chunk);
            if (size != 0) {
                if (buffer == NULL) {
                    buffer = PyMem_Malloc(BATCH_BYTES);
                    if (buffer == NULL) {
                        PyErr_NoMemory();
                        status = -1;
                    }
                }
                if (status == 0 && size > BATCH_BYTES - used) {
                    status = flush(update, buffer, &used);
                }
                if (status == 0) {
                    memcpy(buffer + used, PyBytes_AS_STRING(chunk), (size_t)size);
                    used += size;
                    if (used == BATCH_BYTES) {
                        status = flush(update, buffer, &used);
                    }
                }
            }
        }
        else {
            status = flush(update, buffer, &used);
            if (status == 0) {
                status = update_chunk(update, chunk);
            }
        }
        if (status < 0) {
            break;
        }
        if (++since_signal == SIGNAL_INTERVAL) {
            /* Also stay interruptible for arbitrarily many empty chunks.
             * Flush before a signal handler can inspect or update the stream. */
            since_signal = 0;
            status = flush(update, buffer, &used);
            if (status == 0) {
                status = PyErr_CheckSignals();
            }
            if (status < 0) {
                break;
            }
        }
    }
    if (status == 0 && PyErr_Occurred()) {
        status = -1;
    }
    if (status == 0) {
        status = flush(update, buffer, &used);
    }
    PyMem_Free(buffer);
    Py_XDECREF(previous);
    Py_DECREF(iterator);
    Py_DECREF(update);
    if (status < 0) {
        return NULL;
    }
    Py_RETURN_NONE;
}

static PyMethodDef methods[] = {
    {"absorb", (PyCFunction)(void (*)(void))absorb, METH_FASTCALL,
     "Absorb an exact list or tuple with bounded immutable-byte batching."},
    {NULL, NULL, 0, NULL}
};

static struct PyModuleDef definition = {
    PyModuleDef_HEAD_INIT,
    "_stream_native",
    "Optional bounded BLAKE3 stream chunk batching.",
    -1,
    methods
};

PyMODINIT_FUNC
PyInit__stream_native(void)
{
    return PyModule_Create(&definition);
}
