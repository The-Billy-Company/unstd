/* Exact builtin JSON proofs and serialization in one uninterrupted GIL span.
 *
 * No allocation or Python callback occurs while validating an eligible tree.
 * The cached orjson builtin retains the GIL, so another thread, trace hook or
 * signal callback cannot insert an unvalidated value before serialization.
 * Other values return NotImplemented for the stdlib-compatible Python path.
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

/* Container sizes are bounded by MAX_VALUES before counting punctuation. */
typedef struct {
    Py_ssize_t remaining, separators;
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
    if (PyList_CheckExact(value) || PyTuple_CheckExact(value)) {
        int list = PyList_CheckExact(value);
        Py_ssize_t size = list ? PyList_GET_SIZE(value) : PyTuple_GET_SIZE(value);
        if (size <= proof->remaining) {
            if (spaced && size > 0) {
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
            if (spaced && PyDict_GET_SIZE(value) > 0) {
                proof->separators += PyDict_GET_SIZE(value) * 2 - 1;
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
    }
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

static PyObject *
guarded_encode(NativeState *state, PyObject *value, PyObject *option, int spaced)
{
    if (Py_EnterRecursiveCall(" while proving JSON encoding")) {
        return NULL;
    }
    EncodeProof proof = {MAX_VALUES, 0};
    int result = eligible(value, &proof, spaced, 0);
    if (result != 1) {
        Py_LeaveRecursiveCall();
        if (result < 0) {
            return NULL;
        }
        Py_RETURN_NOTIMPLEMENTED;
    }
    PyObject *backend_args[] = {value, Py_None, option};
    Py_ssize_t count = option == NULL ? 1 : 3;
    PyObject *encoded = state->encoder_fast != NULL
        ? state->encoder_fast(state->encoder_self, backend_args, count, NULL)
        : PyObject_Vectorcall(state->encoder, backend_args, count, NULL);
    Py_LeaveRecursiveCall();
    if (encoded != NULL && spaced && proof.separators != 0) {
        PyObject *result = space_encoded(encoded, proof.separators);
        Py_DECREF(encoded);
        return result;
    }
    return encoded;
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

#include "decode.h"
#include "lines.h"

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
    PyObject *encoded = guarded_encode(state, value, option, 0);
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

static PyMethodDef methods[] = {
    {"encode", (PyCFunction)(void (*)(void))encode,
     METH_FASTCALL | METH_KEYWORDS,
     "encode(obj, option=0, spaced=False)\n--\n\n"
     "Serialize exact builtins, adding default spaces when requested."},
    {"configure", configure, METH_O, "Bind the Python codec's module globals."},
    {"dumpb", (PyCFunction)(void (*)(void))dumpb, METH_FASTCALL,
     "Compact transport bytes for the existing Python facade."},
    {"line", line, METH_O, "One compact record for the existing Python facade."},
    {"loads_lines", loads_lines, METH_O, "Strict NDJSON values or NotImplemented."},
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
