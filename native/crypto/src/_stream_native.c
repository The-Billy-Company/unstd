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
    /* A C backend can return without reaching Python's evaluation loop.
     * Observe pending interrupts between updates, even for large chunks. */
    return PyErr_CheckSignals();
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

/* Bounded bearer-envelope parsing. Every cryptographic operation remains
 * with the current Python module functions and its installed BLAKE3 backend.
 * The bound helper retains only fallback/globals and static keyword/name
 * objects: no signer keys, token messages or cryptographic state persist.
 */
#define MIN_TOKEN_CHARS 46
#define TAG_CHARS 43

/* Exact ASCII text alone reaches this table. 0xff marks invalid bytes; the
 * lower six bits of valid entries also establish canonical terminal bits.
 * The table is immutable, contains no secret data and has no initialization.
 */
static const unsigned char sextets[128] = {
    255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,
    255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,
    255,255,255,255,255,255,255,255,255,255,255,255,255, 62,255,255,
     52, 53, 54, 55, 56, 57, 58, 59, 60, 61,255,255,255,255,255,255,
    255,  0,  1,  2,  3,  4,  5,  6,  7,  8,  9, 10, 11, 12, 13, 14,
     15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25,255,255,255,255, 63,
    255, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40,
     41, 42, 43, 44, 45, 46, 47, 48, 49, 50, 51,255,255,255,255,255
};

/* Return a borrowed global reference. Reusing bound name objects avoids the
 * temporary Unicode allocation in PyDict_GetItemString on every accepted call.
 * A real dictionary lookup error must survive, including MemoryError.
 */
static PyObject *
lookup_global(PyObject *context, Py_ssize_t name_index)
{
    PyObject *name = PyTuple_GET_ITEM(context, name_index);
    PyObject *value = PyDict_GetItemWithError(PyTuple_GET_ITEM(context, 0), name);
    if (value == NULL && !PyErr_Occurred())
        PyErr_Format(PyExc_NameError, "name '%U' is not defined", name);
    return value;
}

/* Separate payload and fixed tag loops avoid a separator branch per byte.
 * Signal handling occurs once per block, outside the classification loop.
 */
static int
canonical_alphabet(const unsigned char *raw, Py_ssize_t length)
{
    Py_ssize_t offset = 0;
    while (length - offset >= 65536) {
        unsigned int invalid = 0;
        for (Py_ssize_t index = 0; index < 65536; ++index)
            invalid |= sextets[raw[offset + index]];
        if (invalid & 128) return 0;
        if (PyErr_CheckSignals() < 0) return -1;
        offset += 65536;
    }
    unsigned int invalid = 0;
    for (; offset < length; ++offset)
        invalid |= sextets[raw[offset]];
    return (invalid & 128) == 0;
}

static PyObject *
fallback_call(PyObject *context, PyObject *const *args)
{
    PyObject *values[] = {args[0], args[1], args[2]};
    return PyObject_Vectorcall(PyTuple_GET_ITEM(context, 1), values, 2,
                               PyTuple_GET_ITEM(context, 2));
}

static PyObject *
verify_fast(PyObject *context, PyObject *const *args, Py_ssize_t nargs)
{
    if (nargs != 3) {
        PyErr_SetString(PyExc_TypeError, "verify_fast() takes exactly three arguments");
        return NULL;
    }
    PyObject *limit = args[2];
    Py_ssize_t maximum = PY_SSIZE_T_MAX;
    if (limit != Py_None) {
        if (!PyLong_CheckExact(limit)) {
            PyErr_SetString(PyExc_TypeError,
                            "max_token_chars must be a positive integer or None");
            return NULL;
        }
        maximum = PyLong_AsSsize_t(limit);
        if (maximum == -1 && PyErr_Occurred()) {
            if (!PyErr_ExceptionMatches(PyExc_OverflowError)) return NULL;
            PyErr_Clear();
            PyObject *zero = PyLong_FromLong(0);
            if (zero == NULL) return NULL;
            int positive = PyObject_RichCompareBool(limit, zero, Py_GT);
            Py_DECREF(zero);
            if (positive < 0) return NULL;
            maximum = positive ? PY_SSIZE_T_MAX : 0;
        }
        if (maximum <= 0) {
            PyErr_SetString(PyExc_ValueError,
                            "max_token_chars must be a positive integer or None");
            return NULL;
        }
    }

    PyObject *text = args[1];
    if (!PyUnicode_CheckExact(text)) return fallback_call(context, args);
    Py_ssize_t total = PyUnicode_GET_LENGTH(text);
    if (total < MIN_TOKEN_CHARS || total > maximum) Py_RETURN_NONE;
    if (!PyUnicode_IS_ASCII(text)) Py_RETURN_NONE;
    /* The fixed-width suffix makes an unbounded separator search unnecessary.
     * Reject every other dot in the single alphabet pass below. */
    const unsigned char *raw = PyUnicode_1BYTE_DATA(text);
    Py_ssize_t payload_size = total - TAG_CHARS - 1;
    if (raw[payload_size] != '.') Py_RETURN_NONE;
    Py_ssize_t remainder = payload_size % 4;
    if (remainder == 1) Py_RETURN_NONE;
    unsigned int final = sextets[raw[payload_size - 1]];
    if (final == 255 || (remainder == 2 && (final & 15))
                  || (remainder == 3 && (final & 3))) Py_RETURN_NONE;
    unsigned int tag_final = sextets[raw[total - 1]];
    if (tag_final == 255 || (tag_final & 3)) Py_RETURN_NONE;
    int alphabet = canonical_alphabet(raw, payload_size);
    if (alphabet < 0) return NULL;
    if (!alphabet) Py_RETURN_NONE;
    alphabet = canonical_alphabet(raw + payload_size + 1, TAG_CHARS);
    if (!alphabet) Py_RETURN_NONE;

    PyObject *b64 = lookup_global(context, 4);
    if (b64 == NULL) return NULL;
    Py_INCREF(b64);
    PyObject *decoder = PyObject_GetAttrString(b64, "decode");
    Py_DECREF(b64);
    if (decoder == NULL) return NULL;
    PyObject *tag = PyUnicode_FromStringAndSize((const char *)raw + payload_size + 1,
                                              TAG_CHARS);
    if (tag == NULL) { Py_DECREF(decoder); return NULL; }
    PyObject *decode_values[] = {tag, Py_True};
    PyObject *got = PyObject_Vectorcall(decoder, decode_values, 1,
                                       PyTuple_GET_ITEM(context, 3));
    Py_DECREF(tag);
    Py_DECREF(decoder);
    if (got == NULL) {
        if (PyErr_ExceptionMatches(PyExc_ValueError)) {
            PyErr_Clear();
            Py_RETURN_NONE;
        }
        return NULL;
    }
    PyObject *payload = PyBytes_FromStringAndSize((const char *)raw, payload_size);
    if (payload == NULL) { Py_DECREF(got); return NULL; }
    /* Lookups occur at their Python expression boundary: hooks invoked by the
     * decoder may replace the module functions. Strong references survive calls.
     */
    PyObject *equal = lookup_global(context, 6);
    if (equal == NULL) {
        Py_DECREF(payload); Py_DECREF(got); return NULL;
    }
    Py_INCREF(equal);
    PyObject *mac_raw = lookup_global(context, 5);
    if (mac_raw == NULL) {
        Py_DECREF(equal); Py_DECREF(payload); Py_DECREF(got);
        return NULL;
    }
    Py_INCREF(mac_raw);
    PyObject *mac_values[] = {args[0], payload};
    PyObject *expected = PyObject_Vectorcall(mac_raw, mac_values, 2, NULL);
    Py_DECREF(mac_raw);
    if (expected == NULL) {
        Py_DECREF(equal); Py_DECREF(payload); Py_DECREF(got);
        return NULL;
    }
    PyObject *equal_values[] = {got, expected};
    PyObject *matches = PyObject_Vectorcall(equal, equal_values, 2, NULL);
    Py_DECREF(equal); Py_DECREF(expected); Py_DECREF(got);
    if (matches == NULL) { Py_DECREF(payload); return NULL; }
    int authentic = PyObject_IsTrue(matches);
    Py_DECREF(matches);
    if (authentic <= 0) {
        Py_DECREF(payload);
        if (authentic < 0) return NULL;
        Py_RETURN_NONE;
    }
    /* The Python version rereads b64.decode after MAC/compare callbacks. */
    b64 = lookup_global(context, 4);
    if (b64 == NULL) {
        Py_DECREF(payload); return NULL;
    }
    Py_INCREF(b64);
    decoder = PyObject_GetAttrString(b64, "decode");
    Py_DECREF(b64);
    if (decoder == NULL) { Py_DECREF(payload); return NULL; }
    decode_values[0] = payload;
    PyObject *claims = PyObject_Vectorcall(decoder, decode_values, 1,
                                          PyTuple_GET_ITEM(context, 3));
    Py_DECREF(payload); Py_DECREF(decoder);
    return claims;
}

static PyMethodDef verify_definition = {
    "verify_fast", (PyCFunction)(void (*)(void))verify_fast, METH_FASTCALL,
    "Bounded envelope verification with existing MAC/compare."
};

static PyObject *
bind_token_verifier(PyObject *module, PyObject *fallback)
{
    (void)module;
    if (!PyFunction_Check(fallback)) {
        PyErr_SetString(PyExc_TypeError, "bind_token_verifier() needs a Python fallback function");
        return NULL;
    }
    PyObject *globals = PyFunction_GetGlobals(fallback);
    PyObject *fallback_keywords = Py_BuildValue("(s)", "max_token_chars");
    if (fallback_keywords == NULL) return NULL;
    PyObject *decode_keywords = Py_BuildValue("(s)", "url");
    if (decode_keywords == NULL) { Py_DECREF(fallback_keywords); return NULL; }
    PyObject *b64_name = PyUnicode_FromString("b64");
    PyObject *mac_name = PyUnicode_FromString("mac_raw");
    PyObject *equal_name = PyUnicode_FromString("equal");
    if (b64_name == NULL || mac_name == NULL || equal_name == NULL) {
        Py_XDECREF(b64_name); Py_XDECREF(mac_name); Py_XDECREF(equal_name);
        Py_DECREF(fallback_keywords); Py_DECREF(decode_keywords); return NULL;
    }
    PyObject *context = PyTuple_Pack(7, globals, fallback, fallback_keywords,
                                     decode_keywords, b64_name, mac_name,
                                     equal_name);
    Py_DECREF(fallback_keywords); Py_DECREF(decode_keywords);
    Py_DECREF(b64_name); Py_DECREF(mac_name); Py_DECREF(equal_name);
    if (context == NULL) return NULL;
    PyObject *result = PyCFunction_New(&verify_definition, context);
    Py_DECREF(context);
    return result;
}

static PyMethodDef methods[] = {
    {"absorb", (PyCFunction)(void (*)(void))absorb, METH_FASTCALL,
     "Absorb an exact list or tuple with bounded immutable-byte batching."},
    {"bind_token_verifier", bind_token_verifier, METH_O,
     "Bind bounded token verification to a Python fallback and its globals."},
    {NULL, NULL, 0, NULL}
};

static struct PyModuleDef definition = {
    PyModuleDef_HEAD_INIT,
    "_stream_native",
    "Optional bounded BLAKE3 stream batching and bearer-envelope parsing.",
    -1,
    methods,
    NULL,
    NULL,
    NULL,
    NULL
};

PyMODINIT_FUNC
PyInit__stream_native(void)
{
    return PyModule_Create(&definition);
}
