/* Exact builtin JSON encoding in one uninterrupted GIL span.
 *
 * The ordinary proof does not allocate; the finite-float writer uses private
 * untracked buffers and exact scalar spellings. Neither encoding path calls
 * Python or releases the GIL while traversing the tree. Unsupported values
 * return NotImplemented for the stdlib-compatible Python path.
 */
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <string.h>

#if PY_VERSION_HEX >= 0x030E0000 && PY_VERSION_HEX < 0x030F0000
#define Py_BUILD_CORE
#include "internal/pycore_instruments.h"
#undef Py_BUILD_CORE
#endif

#define MAX_DEPTH 16
#define MAX_VALUES 65536
#define MAX_WIDE_VALUES 262144

typedef PyObject *(*NativeEncoder)(PyObject *, PyObject *const *, Py_ssize_t,
                                   PyObject *);

typedef PyObject *(*NativePreciseDecoder)(PyObject *, PyObject *const *, Py_ssize_t);

static struct PyModuleDef definition;

typedef struct {
    PyObject *encoder;
    NativeEncoder encoder_fast;
    PyObject *encoder_self; /* Borrowed from the strongly held encoder. */
    PyObject *globals;
    int globals_watcher, config_dirty, encoder_ready, decoder_ready;
    PyObject *key_encode;
    PyObject *key_have_orjson;
    PyObject *sort_option;
    PyObject *newline_option;
    PyObject *decoder, *precise_decoder, *headroom_function;
    PyCFunction decoder_fast;
    NativePreciseDecoder precise_fast;
    PyObject *decoder_self, *precise_self; /* Borrowed from their callables. */
    PyObject *loads_function, *loads_code;
    vectorcallfunc loads_original;
    PyObject *dumpb_function, *dumpb_code;
    vectorcallfunc dumpb_original;
    PyObject *dumpb_helper, *key_dumpb_helper;
    int encoder_dispatch_ready;
    PyObject *key_decode, *key_have_msgspec, *key_headroom;
    int identity_configured;
    PyObject *identity_walker, *identity_walker_code, *identity_walker_key;
    PyObject *identity_builtins, *identity_builtins_snapshot;
    PyObject *identity_isfinite, *identity_isfinite_key;
} NativeState;

static inline int
has_headroom(int needed)
{
    /* The requested budget includes the stdlib entry frames used by a codec's
     * independent reference. Read the versioned CPython budget directly. */
    return PyThreadState_Get()->py_recursion_remaining >= needed;
}

static int
monitoring_clear(PyObject *code_object)
{
    PyThreadState *thread = PyThreadState_Get();
    if (thread->c_tracefunc != NULL || thread->c_profilefunc != NULL
        || _PyInterpreterState_GetEvalFrameFunc(thread->interp)
           != _PyEval_EvalFrameDefault) {
        return 0;
    }
    PyCodeObject *code = (PyCodeObject *)code_object;
#if PY_VERSION_HEX >= 0x030E0000 && PY_VERSION_HEX < 0x030F0000
    /* CPython reserves the low eight eval_breaker bits for pending events.
     * A version mismatch must enter Python once to refresh instrumentation.
     * Monitoring data survives profiling, so its pointer alone is not a gate. */
    if (code->_co_instrumentation_version
        != (thread->eval_breaker & ~(uintptr_t)255)) {
        return 0;
    }
    if (code->_co_monitoring != NULL) {
        static const uint8_t inactive[_PY_MONITORING_LOCAL_EVENTS] = {0};
        if (memcmp(code->_co_monitoring->active_monitors.tools, inactive,
                   sizeof(inactive)) != 0) {
            return 0;
        }
    }
    return 1;
#else
    return code->_co_monitoring == NULL;
#endif
}

static int
unchanged_function(PyObject *function, PyObject *expected, PyObject *code)
{
    return function == expected
        && PyFunction_GET_CODE(function) == code
        && PyFunction_GET_DEFAULTS(function) == NULL
        && PyFunction_GET_CLOSURE(function) == NULL
        && monitoring_clear(code);
}

/* Every proof has a visit budget, including repeated aliases in a tiny DAG. */
typedef struct {
    Py_ssize_t remaining, separators;
    int max_container_depth;
#if PY_VERSION_HEX < 0x030D0000
    int allow_floats;
    Py_ssize_t floats;
#endif
} EncodeProof;

static int eligible_container(PyObject *, EncodeProof *, int, int);

static inline Py_ALWAYS_INLINE int
eligible(PyObject *value, EncodeProof *proof, int spaced, int depth)
{
    if (--proof->remaining < 0) {
        return 0;
    }
    if (PyUnicode_CheckExact(value)) {
        return 1;
    }
    if (value == Py_None || PyBool_Check(value) || PyLong_CheckExact(value)) {
        /* orjson rejects oversized integers without exposing a lossy result;
         * its error sends the original tree through the stdlib fallback. */
        return 1;
    }
#if PY_VERSION_HEX < 0x030D0000
    if (proof->allow_floats && PyFloat_CheckExact(value)
        && Py_IS_FINITE(PyFloat_AS_DOUBLE(value))) {
        proof->floats++;
        return 1;
    }
#endif
    if (depth >= MAX_DEPTH) {
        return 0;
    }
    return eligible_container(value, proof, spaced, depth);
}

static int
eligible_container(PyObject *value, EncodeProof *proof, int spaced, int depth)
{
    if (Py_EnterRecursiveCall(" while proving JSON encoding")) {
        return -1;
    }

    int result = 0;
    if (depth + 1 > proof->max_container_depth) {
        proof->max_container_depth = depth + 1;
    }
    if (PyList_CheckExact(value) || PyTuple_CheckExact(value)) {
        int list = PyList_CheckExact(value);
        Py_ssize_t size = list ? PyList_GET_SIZE(value) : PyTuple_GET_SIZE(value);
        if (size <= proof->remaining) {
            if (spaced && size > 0) {
                if (size - 1 > PY_SSIZE_T_MAX - proof->separators) {
                    goto done;
                }
                proof->separators += size - 1;
            }
            result = 1;
            PyObject **items = list ? ((PyListObject *)value)->ob_item
                                   : ((PyTupleObject *)value)->ob_item;
            for (Py_ssize_t index = 0; index < size; index++) {
                result = eligible(items[index], proof, spaced, depth + 1);
                if (result != 1) {
                    break;
                }
            }
        }
    } else if (PyDict_CheckExact(value)) {
        if (PyDict_GET_SIZE(value) <= proof->remaining) {
            Py_ssize_t size = PyDict_GET_SIZE(value);
            if (spaced && size > 0) {
                if (size - 1 > PY_SSIZE_T_MAX - size) {
                    goto done;
                }
                Py_ssize_t separators = size + (size - 1);
                if (separators > PY_SSIZE_T_MAX - proof->separators) {
                    goto done;
                }
                proof->separators += separators;
            }
            Py_ssize_t position = 0;
            PyObject *key, *item;
            result = 1;
            while (PyDict_Next(value, &position, &key, &item)) {
                if (!PyUnicode_CheckExact(key)) {
                    result = 0;
                    break;
                }
                result = eligible(item, proof, spaced, depth + 1);
                if (result != 1) {
                    break;
                }
            }
        }
    } else if (PyFloat_CheckExact(value)) {
        /* Only this private rejection status may enter the finite-float path. */
        result = 2;
    }
done:
    Py_LeaveRecursiveCall();
    return result;
}

/* cursor follows an opening quote in a trusted JSON byte span. */
static const char *
skip_json_string(const char *cursor, const char *end)
{
    for (;;) {
        const char *quote = memchr(cursor, '\"', end - cursor);
        if (quote == NULL) {
            return NULL;
        }
        const char *escape = quote;
        while (escape > cursor && escape[-1] == '\\') {
            escape--;
        }
        cursor = quote + 1;
        if (((quote - escape) & 1) == 0) {
            return cursor;
        }
    }
}

/* The proof counts separators; this pass only inserts JSON whitespace.
 * Skip whole quoted spans so punctuation and escaped quotes stay untouched. */
static PyObject *
space_encoded(PyObject *encoded, Py_ssize_t separators)
{
    if (!PyBytes_CheckExact(encoded)) {
        PyErr_SetString(PyExc_TypeError, "native JSON must be bytes");
        return NULL;
    }
    Py_ssize_t length = PyBytes_GET_SIZE(encoded);
    if (separators > PY_SSIZE_T_MAX - length) {
        return PyErr_NoMemory();
    }
    PyObject *result = PyBytes_FromStringAndSize(NULL, length + separators);
    if (result == NULL) {
        return NULL;
    }
    const char *cursor = PyBytes_AS_STRING(encoded);
    const char *end = cursor + length;
    const char *segment = cursor;
    char *output = PyBytes_AS_STRING(result);
    while (cursor < end) {
        char current = *cursor++;
        if (current == '\"') {
            cursor = skip_json_string(cursor, end);
            if (cursor == NULL) {
                goto invalid;
            }
        } else if (current == ',' || current == ':') {
            if (separators-- == 0) {
                goto invalid;
            }
            Py_ssize_t size = cursor - segment;
            memcpy(output, segment, size);
            output += size;
            *output++ = ' ';
            segment = cursor;
        }
    }
    if (separators != 0) {
        goto invalid;
    }
    memcpy(output, segment, end - segment);
    return result;
invalid:
    Py_DECREF(result);
    PyErr_SetString(PyExc_TypeError, "inconsistent native JSON separators");
    return NULL;
}

#if PY_VERSION_HEX < 0x030D0000
/* orjson's finite floats roundtrip exactly, but use different exponent
 * spacing and decimal cutoffs. Parse only its marked float tokens, then use
 * the same CPython conversion as the stdlib encoder for their final spelling.
 * Strings and integers retain the backend's exact bytes. */
static PyObject *
normalize_float_numbers(PyObject *encoded, Py_ssize_t floats)
{
    Py_ssize_t length = PyBytes_GET_SIZE(encoded);
    if (floats > (PY_SSIZE_T_MAX - length) / 4) {
        return PyErr_NoMemory();
    }
    Py_ssize_t capacity = length + floats * 4;
    PyObject *result = PyBytes_FromStringAndSize(NULL, capacity);
    if (result == NULL) {
        return NULL;
    }
    /* A per-operation cache avoids repeated decimal conversions for common
     * measurements such as zero and shared prices. No object or result is
     * retained after this encoding call. */
    struct {
        uint64_t hash;
        Py_ssize_t token_size, representation_size;
        char token[64], representation[64];
    } cache[16] = {{0}};
    const char *cursor = PyBytes_AS_STRING(encoded);
    const char *end = cursor + length;
    const char *segment = cursor;
    char *output = PyBytes_AS_STRING(result);
    while (cursor < end) {
        char current = *cursor++;
        if (current == '\"') {
            cursor = skip_json_string(cursor, end);
            if (cursor == NULL) {
                goto invalid;
            }
        } else if (current == '-' || (current >= '0' && current <= '9')) {
            const char *start = cursor - 1;
            int floating = 0;
            while (cursor < end) {
                char next = *cursor;
                if (next == '.' || next == 'e' || next == 'E') {
                    floating = 1;
                } else if (!((next >= '0' && next <= '9')
                             || next == '-' || next == '+')) {
                    break;
                }
                cursor++;
            }
            if (!floating) {
                continue;
            }
            char token[64];
            Py_ssize_t size = cursor - start;
            if (size >= (Py_ssize_t)sizeof(token)) {
                goto invalid;
            }
            uint64_t hash = UINT64_C(14695981039346656037);
            for (Py_ssize_t index = 0; index < size; index++) {
                token[index] = start[index];
                hash = (hash ^ (unsigned char)token[index])
                    * UINT64_C(1099511628211);
            }
            token[size] = 0;
            unsigned int slot = (unsigned int)(hash & 15);
            if (cache[slot].hash != hash || cache[slot].token_size != size
                || memcmp(cache[slot].token, token, size) != 0) {
                double value = PyOS_string_to_double(token, NULL, NULL);
                if (value == -1.0 && PyErr_Occurred()) {
                    Py_DECREF(result);
                    return NULL;
                }
                char *representation = PyOS_double_to_string(
                    value, 'r', 0, Py_DTSF_ADD_DOT_0, NULL);
                if (representation == NULL) {
                    Py_DECREF(result);
                    return NULL;
                }
                Py_ssize_t numeric = (Py_ssize_t)strlen(representation);
                if (numeric >= (Py_ssize_t)sizeof(cache[slot].representation)) {
                    PyMem_Free(representation);
                    goto invalid;
                }
                memcpy(cache[slot].representation, representation, numeric);
                PyMem_Free(representation);
                memcpy(cache[slot].token, token, size);
                cache[slot].hash = hash;
                cache[slot].token_size = size;
                cache[slot].representation_size = numeric;
            }
            Py_ssize_t prefix = start - segment;
            Py_ssize_t numeric = cache[slot].representation_size;
            Py_ssize_t used = output - PyBytes_AS_STRING(result);
            /* CPython's exponent spelling adds at most a sign and a zero.
             * Validate the allocation bound before copying regardless. */
            if (prefix > capacity - used
                || numeric > capacity - used - prefix) {
                goto invalid;
            }
            memcpy(output, segment, prefix);
            output += prefix;
            memcpy(output, cache[slot].representation, numeric);
            output += numeric;
            segment = cursor;
        }
    }
    Py_ssize_t suffix = end - segment;
    Py_ssize_t used = output - PyBytes_AS_STRING(result);
    if (suffix > capacity - used) {
        goto invalid;
    }
    memcpy(output, segment, suffix);
    if (_PyBytes_Resize(&result, used + suffix) < 0) {
        return NULL;
    }
    return result;
invalid:
    Py_DECREF(result);
    PyErr_SetString(PyExc_TypeError, "inconsistent native JSON float token");
    return NULL;
}

#endif

static PyObject *
guarded_encode_limits(NativeState *state, PyObject *value, PyObject *option,
                      int spaced, Py_ssize_t max_values, int indented)
{
    if (Py_EnterRecursiveCall(" while proving JSON encoding")) {
        return NULL;
    }
    /* Python 3.13 gained a native indented stdlib encoder. Reformatting every
     * distinct float would add a second conversion there; retain that encoder
     * for float trees, while accelerating 3.12's Python generator traversal. */
    EncodeProof proof = {max_values, 0, 0
#if PY_VERSION_HEX < 0x030D0000
                         , indented, 0
#endif
    };
    int result = eligible(value, &proof, spaced, 0);
    if (result != 1) {
        Py_LeaveRecursiveCall();
        if (result < 0) {
            return NULL;
        }
        Py_RETURN_NOTIMPLEMENTED;
    }
    /* The stdlib indented encoder keeps generator frames alive along the
     * current branch. Reserve both its container and item frames, plus the
     * public encoder entry frames, before bypassing that Python traversal. */
    if (indented && !has_headroom(2 * proof.max_container_depth + 8)) {
        Py_LeaveRecursiveCall();
        Py_RETURN_NOTIMPLEMENTED;
    }
    PyObject *backend_args[] = {value, Py_None, option};
    Py_ssize_t count = option == NULL ? 1 : 3;
    PyObject *encoded = state->encoder_fast != NULL
        ? state->encoder_fast(state->encoder_self, backend_args, count, NULL)
        : PyObject_Vectorcall(state->encoder, backend_args, count, NULL);
    Py_LeaveRecursiveCall();
#if PY_VERSION_HEX < 0x030D0000
    if (encoded != NULL && proof.floats != 0) {
        PyObject *normalized = normalize_float_numbers(encoded, proof.floats);
        Py_DECREF(encoded);
        encoded = normalized;
    }
#endif
    if (encoded != NULL && spaced && proof.separators != 0) {
        PyObject *result = space_encoded(encoded, proof.separators);
        Py_DECREF(encoded);
        return result;
    }
    return encoded;
}

static PyObject *
guarded_encode(NativeState *state, PyObject *value, PyObject *option, int spaced)
{
    return guarded_encode_limits(state, value, option, spaced, MAX_VALUES, 0);
}

#include "repeat.h"

static PyObject *
encode_wide(PyObject *module, PyObject *const *args, Py_ssize_t count)
{
    if (count != 2 && count != 3) {
        PyErr_SetString(PyExc_TypeError, "encode_wide() requires two or three arguments");
        return NULL;
    }
    if (!PyLong_CheckExact(args[1])
        || (count == 3 && !PyBool_Check(args[2])) || !has_headroom(2)) {
        Py_RETURN_NOTIMPLEMENTED;
    }
    return guarded_encode_limits(PyModule_GetState(module), args[0], args[1],
                                 count == 3 && args[2] == Py_True,
                                 MAX_WIDE_VALUES, 0);
}

static PyObject *
encode_indent(PyObject *module, PyObject *const *args, Py_ssize_t count)
{
    if (count != 2) {
        PyErr_SetString(PyExc_TypeError, "encode_indent() requires two arguments");
        return NULL;
    }
    if (!PyLong_CheckExact(args[1]) || !has_headroom(2)) {
        Py_RETURN_NOTIMPLEMENTED;
    }
    /* Atomic validation avoids snapshots, but retains a finite visit budget
     * so a small alias-heavy graph cannot cause unbounded validation work. */
    return guarded_encode_limits(PyModule_GetState(module), args[0], args[1],
                                 0, MAX_WIDE_VALUES, 1);
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

    if (!has_headroom(2)) {
        Py_RETURN_NOTIMPLEMENTED;
    }
    return guarded_encode(PyModule_GetState(module), value, option,
                          spaced == Py_True);
}

static int
configuration_changed(PyDict_WatchEvent event, PyObject *globals,
                      PyObject *key, PyObject *value)
{
    (void)event;
    (void)key;
    (void)value;
    PyObject *module = PyState_FindModule(&definition);
    if (module != NULL) {
        NativeState *state = PyModule_GetState(module);
        if (globals == state->globals) {
            state->config_dirty = 1;
        }
    }
    return 0;
}

static int
configuration_ready(NativeState *state, int decoding)
{
    if (state->globals == NULL) {
        return 0;
    }
    if (state->config_dirty || state->globals_watcher < 0) {
        state->encoder_ready = state->key_encode != NULL
            && PyDict_GetItemWithError(state->globals, state->key_encode)
               == state->encoder
            && PyDict_GetItemWithError(state->globals, state->key_have_orjson)
               == Py_True;
        state->encoder_dispatch_ready = state->encoder_ready
            && state->key_dumpb_helper != NULL
            && PyDict_GetItemWithError(state->globals, state->key_dumpb_helper)
               == state->dumpb_helper;
        state->decoder_ready = state->key_decode != NULL
            && PyDict_GetItemWithError(state->globals, state->key_decode)
               == state->precise_decoder
            && PyDict_GetItemWithError(state->globals, state->key_have_msgspec)
               == Py_True
            && PyDict_GetItemWithError(state->globals, state->key_headroom)
               == state->headroom_function;
        state->config_dirty = 0;
    }
    return decoding ? state->decoder_ready : state->encoder_ready;
}

static PyObject *dumpb_vectorcall(PyObject *, PyObject *const *, size_t,
                                  PyObject *);
static int unchanged_encoder(NativeState *, PyObject *);
static int configure_encoder(NativeState *, PyObject *);
static void detach_watcher(NativeState *);
static int configure_identity_snapshot(NativeState *, PyObject *);

#include "decode.h"
#include "lines.h"
#include "array.h"

static PyObject *
configure(PyObject *module, PyObject *globals)
{
    if (!PyDict_CheckExact(globals)) {
        PyErr_SetString(PyExc_TypeError, "configure() requires module globals");
        return NULL;
    }
    NativeState *state = PyModule_GetState(module);
    PyObject *loads_function = PyDict_GetItemString(globals, "loads");
    PyObject *dumpb_function = PyDict_GetItemString(globals, "dumpb");
    if ((state->globals != NULL && globals != state->globals)
        || (loads_function != NULL && PyFunction_Check(loads_function)
         && PyFunction_GET_GLOBALS(loads_function) != globals)
        || (dumpb_function != NULL && PyFunction_Check(dumpb_function)
            && PyFunction_GET_GLOBALS(dumpb_function) != globals)) {
        PyErr_SetString(PyExc_ValueError,
                        "configure() requires the functions' actual globals");
        return NULL;
    }
    PyObject *key_encode = PyUnicode_InternFromString("_encode");
    PyObject *key_have = PyUnicode_InternFromString("_HAVE_ORJSON");
    if (key_encode == NULL || key_have == NULL) {
        Py_XDECREF(key_encode);
        Py_XDECREF(key_have);
        return NULL;
    }
    Py_XSETREF(state->key_encode, key_encode);
    Py_XSETREF(state->key_have_orjson, key_have);
    Py_XSETREF(state->globals, Py_NewRef(globals));
    state->config_dirty = 1;
    if (configure_decoder(state, globals) < 0) {
        return NULL;
    }
    if (configure_identity_snapshot(state, globals) < 0) {
        return NULL;
    }
    if (configure_encoder(state, globals) < 0) {
        return NULL;
    }
    if (PyState_FindModule(&definition) != module) {
        /* The shared callback resolves the current instance. Older instances
         * must keep their ordinary per-call checks when reconfigured. */
        detach_watcher(state);
        Py_RETURN_NONE;
    }
    if (state->globals_watcher < 0) {
        state->globals_watcher = PyDict_AddWatcher(configuration_changed);
        if (state->globals_watcher < 0) {
            /* Watcher slots are shared with other extensions. A full table
             * keeps the ordinary per-call identity checks instead. */
            if (!PyErr_ExceptionMatches(PyExc_RuntimeError)) {
                return NULL;
            }
            PyErr_Clear();
        }
    }
    if (state->globals_watcher >= 0
        && PyDict_Watch(state->globals_watcher, globals) < 0) {
        return NULL;
    }
    Py_RETURN_NONE;
}

static int
encoding_available(NativeState *state)
{
    return configuration_ready(state, 0);
}

static PyObject *
transport_encode(NativeState *state, PyObject *value, PyObject *option)
{
    /* The compact-byte reference adds one wrapper above dumps/encode/iterencode;
     * our public facade supplies its first frame and reserves the other three. */
    if (!encoding_available(state) || !has_headroom(3)) {
        Py_RETURN_NOTIMPLEMENTED;
    }
    PyObject *encoded = guarded_repeated_encode(state, value, option, 0, 0);
    if (encoded == NULL
        && (PyErr_ExceptionMatches(PyExc_TypeError)
            || PyErr_ExceptionMatches(PyExc_RecursionError)
            || PyErr_ExceptionMatches(PyExc_RuntimeError))) {
        /* Match the Python facade's existing backend-error fallback. */
        PyErr_Clear();
        Py_RETURN_NOTIMPLEMENTED;
    }
    return encoded;
}

static PyObject *
dumpb(PyObject *module, PyObject *const *args, Py_ssize_t count)
{
    if (count != 4) {
        PyErr_SetString(PyExc_TypeError, "dumpb() requires four arguments");
        return NULL;
    }
    if (!PyBool_Check(args[1]) || args[2] != Py_None || args[3] != Py_False) {
        Py_RETURN_NOTIMPLEMENTED;
    }
    NativeState *state = PyModule_GetState(module);
    return transport_encode(state, args[0],
                            args[1] == Py_True ? state->sort_option : NULL);
}

static PyObject *
line(PyObject *module, PyObject *value)
{
    NativeState *state = PyModule_GetState(module);
    return transport_encode(state, value, state->newline_option);
}

#include "encode.h"
#include "escape.h"
#include "identity.h"

static PyMethodDef methods[] = {
    {"identity_snapshot", guarded_identity_snapshot, METH_O,
     "Own a bounded strict identity tree or return NotImplemented."},
    {"encode", (PyCFunction)(void (*)(void))encode,
     METH_FASTCALL | METH_KEYWORDS,
     "encode(obj, option=0, spaced=False)\n--\n\n"
     "Serialize exact builtins, adding default spaces when requested."},
    {"encode_indent", (PyCFunction)(void (*)(void))encode_indent, METH_FASTCALL,
     "Indented exact builtin JSON or NotImplemented."},
    {"encode_wide", (PyCFunction)(void (*)(void))encode_wide, METH_FASTCALL,
     "Exact builtin JSON with a bounded wide proof."},
    {"encode_text", (PyCFunction)(void (*)(void))encode_text, METH_FASTCALL,
     "Exact default or compact JSON text with operation-local scalar spellings."},
    {"ascii_text", ascii_text, METH_O, "ASCII-escape UTF-8 JSON output."},
    {"configure", configure, METH_O, "Bind the Python codec's module globals."},
    {"dumpb", (PyCFunction)(void (*)(void))dumpb, METH_FASTCALL,
     "Compact transport bytes for the existing Python facade."},
    {"line", line, METH_O, "One compact record for the existing Python facade."},
    {"loads_lines", loads_lines, METH_O, "Strict NDJSON values or NotImplemented."},
    {"array_boundary", (PyCFunction)(void (*)(void))array_boundary, METH_FASTCALL,
     "Last complete top-level item delimiter in a bounded JSON-array window."},
    {"array_decode", (PyCFunction)(void (*)(void))array_decode, METH_FASTCALL,
     "Decode a bounded array span through the unchanged default native decoder."},
    {"is_bound", is_bound, METH_O, "Whether the Python codec uses native dispatch."},
    {"dispatch_ready", dispatch_ready, METH_O, "Whether native codec guards currently pass."},
    {NULL, NULL, 0, NULL}
};

static int
traverse(PyObject *module, visitproc visit, void *arg)
{
    NativeState *state = PyModule_GetState(module);
    Py_VISIT(state->encoder);
    Py_VISIT(state->globals);
    Py_VISIT(state->key_encode);
    Py_VISIT(state->key_have_orjson);
    Py_VISIT(state->sort_option);
    Py_VISIT(state->newline_option);
    Py_VISIT(state->decoder);
    Py_VISIT(state->precise_decoder);
    Py_VISIT(state->headroom_function);
    Py_VISIT(state->loads_function);
    Py_VISIT(state->loads_code);
    Py_VISIT(state->dumpb_function);
    Py_VISIT(state->dumpb_code);
    Py_VISIT(state->dumpb_helper);
    Py_VISIT(state->key_dumpb_helper);
    Py_VISIT(state->key_decode);
    Py_VISIT(state->key_have_msgspec);
    Py_VISIT(state->key_headroom);
    Py_VISIT(state->identity_walker);
    Py_VISIT(state->identity_walker_code);
    Py_VISIT(state->identity_walker_key);
    Py_VISIT(state->identity_builtins);
    Py_VISIT(state->identity_builtins_snapshot);
    Py_VISIT(state->identity_isfinite);
    Py_VISIT(state->identity_isfinite_key);
    return 0;
}

static void
detach_watcher(NativeState *state)
{
    if (state->globals != NULL && state->globals_watcher >= 0) {
        PyObject *error = PyErr_GetRaisedException();
        if (PyDict_Unwatch(state->globals_watcher, state->globals) < 0) {
            PyErr_Clear();
        }
        if (PyDict_ClearWatcher(state->globals_watcher) < 0) {
            PyErr_Clear();
        }
        state->globals_watcher = -1;
        PyErr_SetRaisedException(error);
    }
}

static int
clear(PyObject *module)
{
    NativeState *state = PyModule_GetState(module);
    detach_watcher(state);
    Py_CLEAR(state->encoder);
    Py_CLEAR(state->globals);
    Py_CLEAR(state->key_encode);
    Py_CLEAR(state->key_have_orjson);
    Py_CLEAR(state->sort_option);
    Py_CLEAR(state->newline_option);
    if (state->loads_function != NULL
        && PyVectorcall_Function(state->loads_function) == loads_vectorcall) {
        PyFunction_SetVectorcall((PyFunctionObject *)state->loads_function,
                                 state->loads_original);
    }
    if (state->dumpb_function != NULL
        && PyVectorcall_Function(state->dumpb_function) == dumpb_vectorcall) {
        PyFunction_SetVectorcall((PyFunctionObject *)state->dumpb_function,
                                 state->dumpb_original);
    }
    Py_CLEAR(state->decoder);
    Py_CLEAR(state->precise_decoder);
    Py_CLEAR(state->headroom_function);
    Py_CLEAR(state->loads_function);
    Py_CLEAR(state->loads_code);
    Py_CLEAR(state->dumpb_function);
    Py_CLEAR(state->dumpb_code);
    Py_CLEAR(state->dumpb_helper);
    Py_CLEAR(state->key_dumpb_helper);
    Py_CLEAR(state->key_decode);
    Py_CLEAR(state->key_have_msgspec);
    Py_CLEAR(state->key_headroom);
    Py_CLEAR(state->identity_walker);
    Py_CLEAR(state->identity_walker_code);
    Py_CLEAR(state->identity_walker_key);
    Py_CLEAR(state->identity_builtins);
    Py_CLEAR(state->identity_builtins_snapshot);
    Py_CLEAR(state->identity_isfinite);
    Py_CLEAR(state->identity_isfinite_key);
    return 0;
}

static void
free_module(void *module)
{
    /* Refcount destruction need not run the GC clear hook first. */
    (void)clear((PyObject *)module);
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
    free_module
};

PyMODINIT_FUNC
PyInit__json_native(void)
{
    /* A forced no-GIL runtime cannot provide this module's atomic proof. */
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
    PyObject *sort_option = PyObject_GetAttrString(backend, "OPT_SORT_KEYS");
    PyObject *newline_option = PyObject_GetAttrString(backend, "OPT_APPEND_NEWLINE");
    Py_DECREF(backend);
    if (encoder == NULL || sort_option == NULL || newline_option == NULL) {
        Py_XDECREF(encoder);
        Py_XDECREF(sort_option);
        Py_XDECREF(newline_option);
        return NULL;
    }
    if (!PyCFunction_Check(encoder)) {
        Py_DECREF(encoder);
        Py_DECREF(sort_option);
        Py_DECREF(newline_option);
        PyErr_SetString(PyExc_ImportError, "orjson.dumps must be a native builtin");
        return NULL;
    }
    PyObject *previous = PyState_FindModule(&definition);
    if (previous != NULL) {
        /* Alias initialization changes which state the shared callback finds.
         * Older helpers retain their ordinary per-call configuration checks. */
        detach_watcher(PyModule_GetState(previous));
    }
    PyObject *module = PyModule_Create(&definition);
    if (module == NULL) {
        Py_DECREF(encoder);
        Py_DECREF(sort_option);
        Py_DECREF(newline_option);
        return NULL;
    }
    NativeState *state = PyModule_GetState(module);
    state->globals_watcher = -1;
    state->config_dirty = 1;
    state->encoder = encoder;
    state->sort_option = sort_option;
    state->newline_option = newline_option;
    if (PyCFunction_GetFlags(encoder) == (METH_FASTCALL | METH_KEYWORDS)) {
        state->encoder_fast = (NativeEncoder)(void (*)(void))
            PyCFunction_GetFunction(encoder);
        state->encoder_self = PyCFunction_GetSelf(encoder);
    }
    return module;
}
