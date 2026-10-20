/* Exact native decoding without changing the public Python function object.
 * Small records use msgspec's exact integer/correctly-rounded float parser.
 * Larger string-heavy trees use orjson and a bounded native float proof.
 * Failed parses and interpreter limits remain owned by the Python facade.
 */
#include <string.h>

static vectorcallfunc ordinary_loads_vectorcall;

#if PY_VERSION_HEX >= 0x030E0000 && PY_VERSION_HEX < 0x030F0000
/* CPython 3.14 moved these three fields immediately after the public thread
 * state and its reference count. Its installed internal header also includes
 * unshipped mimalloc headers, so mirror only this versioned prefix. Never
 * apply this layout to another CPython minor version.
 * Source: CPython v3.14.6 Include/internal/pycore_tstate.h. */
typedef struct {
    PyThreadState base;
    Py_ssize_t refcount;
    uintptr_t c_stack_top, c_stack_soft_limit, c_stack_hard_limit;
} NativeStackPrefix;
#endif

static int
orjson_stack_room(void)
{
    PyThreadState *thread = PyThreadState_Get();
#if PY_VERSION_HEX < 0x030E0000
    return thread->c_recursion_remaining >= 4096;
#elif PY_VERSION_HEX < 0x030F0000
    char marker;
    uintptr_t here = (uintptr_t)&marker;
    NativeStackPrefix *stack = (NativeStackPrefix *)thread;
    uintptr_t top = stack->c_stack_top;
    uintptr_t soft = stack->c_stack_soft_limit;
    uintptr_t hard = stack->c_stack_hard_limit;
#if _Py_STACK_GROWS_DOWN
    if (!(top > soft && soft > hard && here < top && here > hard)) {
        return 0;
    }
#else
    if (!(top < soft && soft < hard && here > top && here < hard)) {
        return 0;
    }
#endif
    /* orjson admits at most 1024 source containers, including discarded
     * duplicate values. Reserve 4 KiB per level above CPython's soft boundary;
     * small stacks use msgspec's own recursive guards instead. */
#if _Py_STACK_GROWS_DOWN
    return here > soft && here - soft >= 4 * 1024 * 1024;
#else
    return soft > here && soft - here >= 4 * 1024 * 1024;
#endif
#else
    return 0;
#endif
}

#if PY_VERSION_HEX >= 0x030E0000 && PY_VERSION_HEX < 0x030F0000 \
    && !defined(Py_GIL_DISABLED)
/* Complete CPython's opaque keys type only for its verified 3.14 GIL layout.
 * Fields and entry layout match v3.14.0 and v3.14.6 pycore_dict.h; including
 * that internal header also requires unshipped allocator headers.
 * Source: https://github.com/python/cpython/blob/v3.14.0/Include/internal/pycore_dict.h
 */
struct _dictkeysobject {
    Py_ssize_t dk_refcnt;
    uint8_t dk_log2_size, dk_log2_index_bytes, dk_kind;
    uint32_t dk_version;
    Py_ssize_t dk_usable, dk_nentries;
    char dk_indices[];
};
#endif

typedef struct {PyObject *me_key, *me_value;} NativeDictEntry;

static NativeDictEntry *
dense_entries(PyObject *dict)
{
#if PY_VERSION_HEX >= 0x030E0000 && PY_VERSION_HEX < 0x030F0000 \
    && !defined(Py_GIL_DISABLED)
    PyDictObject *mapping = (PyDictObject *)dict;
    PyDictKeysObject *keys = mapping->ma_keys;
    if (mapping->ma_values != NULL || keys->dk_kind != 1
        || keys->dk_nentries != mapping->ma_used
        || keys->dk_log2_index_bytes >= sizeof(size_t) * 8
        || keys->dk_log2_size > keys->dk_log2_index_bytes) {
        return NULL;
    }
    return (NativeDictEntry *)(keys->dk_indices
                              + ((size_t)1 << keys->dk_log2_index_bytes));
#else
    (void)dict;
    return NULL;
#endif
}

static int
exact_decoded_tree(PyObject *value)
{
    PyObject *pending[64];
    Py_ssize_t queued = 1, remaining = MAX_VALUES;
    pending[0] = value;
    while (queued) {
        PyObject *container = pending[--queued];
        if (PyList_CheckExact(container)) {
            Py_ssize_t size = PyList_GET_SIZE(container);
            if ((remaining -= size) < 0) {
                return 0;
            }
            PyObject **items = ((PyListObject *)container)->ob_item;
            Py_ssize_t index = 0;
            while (index + 4 <= size
                   && (PyUnicode_CheckExact(items[index])
                       & PyUnicode_CheckExact(items[index + 1])
                       & PyUnicode_CheckExact(items[index + 2])
                       & PyUnicode_CheckExact(items[index + 3]))) {
                index += 4;
            }
            for (; index < size; index++) {
                PyObject *item = items[index];
                if (PyUnicode_CheckExact(item)) {
                    continue;
                }
                if (PyFloat_CheckExact(item)) {
                    return 0;
                }
                if (PyList_CheckExact(item) || PyDict_CheckExact(item)) {
                    if (queued == 64) {
                        return 0;
                    }
                    pending[queued++] = item;
                }
            }
        } else if (PyDict_CheckExact(container)) {
            if ((remaining -= PyDict_GET_SIZE(container)) < 0) {
                return 0;
            }
            Py_ssize_t position = 0;
            NativeDictEntry *entries = dense_entries(container);
            PyObject *item;
            for (Py_ssize_t index = 0; index < PyDict_GET_SIZE(container); index++) {
                if (entries != NULL) {
                    item = entries[index].me_value;
                } else if (!PyDict_Next(container, &position, NULL, &item)) {
                    return 0;
                }
                if (PyUnicode_CheckExact(item)) {
                    continue;
                }
                if (PyFloat_CheckExact(item)) {
                    return 0;
                }
                if (PyList_CheckExact(item) || PyDict_CheckExact(item)) {
                    if (queued == 64) {
                        return 0;
                    }
                    pending[queued++] = item;
                }
            }
        } else if (PyFloat_CheckExact(container)) {
            return 0;
        }
    }
    return 1;
}

static int
array_float_prefix(PyObject *document)
{
    /* Object documents dominate larger string payloads. Avoid even the
     * cached UTF-8 conversion when the first character rules out an array. */
    if (PyBytes_CheckExact(document)) {
        if (PyBytes_GET_SIZE(document) != 0
            && PyBytes_AS_STRING(document)[0] == '{') {
            return 0;
        }
    } else if (PyUnicode_GET_LENGTH(document) != 0
               && PyUnicode_READ_CHAR(document, 0) == '{') {
        return 0;
    }
    Py_ssize_t size;
    const char *source;
    if (PyBytes_CheckExact(document)) {
        source = PyBytes_AS_STRING(document);
        size = PyBytes_GET_SIZE(document);
    } else {
        source = PyUnicode_AsUTF8AndSize(document, &size);
        if (source == NULL) {
            return -1;
        }
    }
    const char *end = source + (size < 128 ? size : 128);
    while (source < end && (*source == ' ' || *source == '\n'
                            || *source == '\r' || *source == '\t')) {
        source++;
    }
    if (source == end || *source++ != '[') {
        return 0;
    }
    while (source < end) {
        char current = *source++;
        if (current == '"') {
            while (source < end) {
                const char *quote = memchr(source, '"', end - source);
                if (quote == NULL) {
                    return 0;
                }
                const char *escape = quote;
                while (escape > source && escape[-1] == '\\') {
                    escape--;
                }
                source = quote + 1;
                if (((quote - escape) & 1) == 0) {
                    break;
                }
            }
        } else if ((current >= '0' && current <= '9') || current == '-') {
            while (source < end && *source >= '0' && *source <= '9') {
                source++;
            }
            if (source < end && (*source == '.' || *source == 'e'
                                 || *source == 'E')) {
                return 1;
            }
        }
    }
    return 0;
}

static int
decoding_error(void)
{
    return PyErr_ExceptionMatches(PyExc_ValueError)
        || PyErr_ExceptionMatches(PyExc_UnicodeError)
        || PyErr_ExceptionMatches(PyExc_RecursionError);
}

static PyObject *
precise_document(NativeState *state, PyObject *document)
{
    if (Py_EnterRecursiveCall(" while decoding JSON")) {
        return NULL;
    }
    PyObject *value;
    if (state->precise_fast != NULL) {
        PyObject *args[] = {document};
        value = state->precise_fast(state->precise_self, args, 1);
    } else {
        value = PyObject_CallOneArg(state->precise_decoder, document);
    }
    Py_LeaveRecursiveCall();
    return value;
}

static PyObject *
decode_document(NativeState *state, PyObject *document)
{
    if (state->precise_decoder == NULL || !has_headroom(3)) {
        Py_RETURN_NOTIMPLEMENTED;
    }
    Py_ssize_t size = PyBytes_CheckExact(document) ?
        PyBytes_GET_SIZE(document) : PyUnicode_GET_LENGTH(document);
    int precise = size <= 2048;
    if (!precise) {
        precise = !orjson_stack_room();
        if (!precise) {
            precise = array_float_prefix(document);
        }
    }
    PyObject *value;
    if (precise != 0) {
        value = precise < 0 ? NULL : precise_document(state, document);
    } else {
        value = state->decoder_fast(state->decoder_self, document);
        if (value != NULL && !exact_decoded_tree(value)) {
            Py_DECREF(value);
            value = precise_document(state, document);
        }
    }
    return value;
}

static int
unchanged_decoder(NativeState *state, PyObject *function)
{
    return unchanged_function(function, state->loads_function, state->loads_code)
        && PyFunction_GET_GLOBALS(function) == state->globals
        && PyFunction_GET_KW_DEFAULTS(function) == NULL
        && configuration_ready(state, 1);
}

static PyObject *
loads_vectorcall(PyObject *function, PyObject *const *args, size_t count,
                 PyObject *keywords)
{
    PyObject *module = PyState_FindModule(&definition);
    NativeState *state = module == NULL ? NULL : PyModule_GetState(module);
    if (state != NULL && PyVectorcall_NARGS(count) == 1 && keywords == NULL
        && (PyUnicode_CheckExact(args[0]) || PyBytes_CheckExact(args[0]))
        && unchanged_decoder(state, function)) {
        PyObject *value = decode_document(state, args[0]);
        if (value != NULL && value != Py_NotImplemented) {
            return value;
        }
        Py_XDECREF(value);
        if (value == NULL) {
            if (!decoding_error()) {
                return NULL;
            }
            PyErr_Clear();
        }
    }
    vectorcallfunc original = state != NULL
        && function == state->loads_function ? state->loads_original :
                                               ordinary_loads_vectorcall;
    return original(function, args, count, keywords);
}

static PyObject *
is_bound(PyObject *module, PyObject *function)
{
    NativeState *state = PyModule_GetState(module);
    return PyBool_FromLong(
        (function == state->loads_function
         && PyVectorcall_Function(function) == loads_vectorcall)
        || (function == state->dumpb_function
            && PyVectorcall_Function(function) == dumpb_vectorcall));
}

static PyObject *
dispatch_ready(PyObject *module, PyObject *function)
{
    NativeState *state = PyModule_GetState(module);
    return PyBool_FromLong(PyFunction_Check(function)
        && (unchanged_decoder(state, function)
            || (unchanged_encoder(state, function)
                && configuration_ready(state, 0))));
}

static int
configure_decoder(NativeState *state, PyObject *globals)
{
    PyObject *function = PyDict_GetItemString(globals, "loads");
    PyObject *precise = PyDict_GetItemString(globals, "_decode");
    PyObject *headroom = PyDict_GetItemString(globals, "_check_headroom");
    if (function == NULL || !PyFunction_Check(function) || precise == NULL
        || !PyCFunction_Check(precise) || headroom == NULL
        || PyFunction_GET_DEFAULTS(function) != NULL
        || PyFunction_GET_KW_DEFAULTS(function) != NULL) {
        return 0;
    }
    if (PyFunction_GET_GLOBALS(function) != globals) {
        return 0;
    }
    if (state->loads_function == NULL
        && PyVectorcall_Function(function) == loads_vectorcall) {
        /* A different module instance owns this slot and its Python fallback. */
        return 0;
    }
    if (state->loads_function != NULL) {
        /* Configuration captures trust once. Reconfiguration must not bless
         * replacement callbacks or a different function as the JSON codec. */
        if (function != state->loads_function
            || PyFunction_GET_CODE(function) != state->loads_code
            || precise != state->precise_decoder
            || headroom != state->headroom_function) {
            return 0;
        }
        if (PyVectorcall_Function(function) == loads_vectorcall) {
            return 0;
        }
    }
    PyObject *backend = PyImport_ImportModule("orjson");
    if (backend == NULL) {
        return -1;
    }
    PyObject *decoder = PyObject_GetAttrString(backend, "loads");
    Py_DECREF(backend);
    if (decoder == NULL) {
        return -1;
    }
    if (!PyCFunction_Check(decoder) || PyCFunction_GetFlags(decoder) != METH_O) {
        Py_DECREF(decoder);
        return 0;
    }
    PyObject *key_decode = PyUnicode_InternFromString("_decode");
    PyObject *key_have = PyUnicode_InternFromString("_HAVE_MSGSPEC");
    PyObject *key_headroom = PyUnicode_InternFromString("_check_headroom");
    if (key_decode == NULL || key_have == NULL || key_headroom == NULL) {
        Py_DECREF(decoder);
        Py_XDECREF(key_decode);
        Py_XDECREF(key_have);
        Py_XDECREF(key_headroom);
        return -1;
    }
    Py_XSETREF(state->decoder, decoder);
    Py_XSETREF(state->precise_decoder, Py_NewRef(precise));
    Py_XSETREF(state->headroom_function, Py_NewRef(headroom));
    Py_XSETREF(state->key_decode, key_decode);
    Py_XSETREF(state->key_have_msgspec, key_have);
    Py_XSETREF(state->key_headroom, key_headroom);
    state->decoder_fast = PyCFunction_GetFunction(decoder);
    state->decoder_self = PyCFunction_GetSelf(decoder);
    state->precise_fast = NULL;
    if (PyCFunction_GetFlags(precise) == METH_FASTCALL) {
        state->precise_fast = (NativePreciseDecoder)(void (*)(void))
            PyCFunction_GetFunction(precise);
        state->precise_self = PyCFunction_GetSelf(precise);
    }
    Py_XSETREF(state->loads_function, Py_NewRef(function));
    Py_XSETREF(state->loads_code, Py_NewRef(PyFunction_GET_CODE(function)));
    state->loads_original = PyVectorcall_Function(function);
    if (ordinary_loads_vectorcall == NULL) {
        ordinary_loads_vectorcall = state->loads_original;
    }
#if PY_VERSION_HEX >= 0x030E0000 && PY_VERSION_HEX < 0x030F0000
    /* Earlier CPython CALL specializations can bypass a custom vectorcall. */
    PyFunction_SetVectorcall((PyFunctionObject *)function, loads_vectorcall);
#endif
    return 0;
}
