/* Owned strict-identity snapshots. All outputs remain private until the whole
 * tree has passed validation, including across allocation-triggered GC. */
#define IDENTITY_MAX_VALUES 262144
#define IDENTITY_MAX_DEPTH 16

typedef struct {
    Py_ssize_t remaining;
    PyObject *outputs, *items_method;
    PyObject *ancestors[IDENTITY_MAX_DEPTH];
} IdentitySnapshot;

static int
identity_string(PyObject *value)
{
    if (!PyUnicode_CheckExact(value)) {
        return 0;
    }
    int kind = PyUnicode_KIND(value);
    if (kind == PyUnicode_1BYTE_KIND) {
        return 1;
    }
    const void *data = PyUnicode_DATA(value);
    Py_ssize_t size = PyUnicode_GET_LENGTH(value);
    for (Py_ssize_t index = 0; index < size; index++) {
        Py_UCS4 character = PyUnicode_READ(kind, data, index);
        if (character >= 0xd800 && character <= 0xdfff) {
            return 0;
        }
    }
    return 1;
}

static int identity_snapshot_value(PyObject *, IdentitySnapshot *, unsigned int,
                                   PyObject **);

static int
identity_snapshot_container(PyObject *source, IdentitySnapshot *context,
                            unsigned int depth, PyObject **result)
{
    int mapping = PyDict_CheckExact(source), status = -1;
    PyObject *view = NULL, *iterator = NULL, *output = NULL;
    PyObject *current = NULL, *key = NULL, *copied = NULL;
    if (Py_EnterRecursiveCall(" while copying strict identity JSON")) {
        return -1;
    }
    context->ancestors[depth] = source;
    if (!mapping) {
        iterator = PyObject_GetIter(source);
        if (iterator == NULL) {
            goto done;
        }
    }
    output = mapping ? PyDict_New() : PyList_New(0);
    if (output == NULL) {
        goto done;
    }
    PyObject_GC_UnTrack(output);
    if (PyList_Append(context->outputs, output) < 0) {
        goto done;
    }
    if (mapping) {
        /* Python allocates the result dict before constructing obj.items().
         * A collection during that allocation must precede iterator capture. */
        view = PyObject_CallOneArg(context->items_method, source);
        if (view == NULL) {
            goto done;
        }
        iterator = PyObject_GetIter(view);
        Py_CLEAR(view);
        if (iterator == NULL) {
            goto done;
        }
    }
    for (;;) {
        PyObject *next = PyIter_Next(iterator);
        if (next == NULL) {
            status = PyErr_Occurred() ? -1 : 1;
            break;
        }
        if (mapping) {
            /* The exact builtin items iterator owns both references. Pin both
             * before dropping its tuple or the preceding Python loop values. */
            PyObject *new_key = Py_NewRef(PyTuple_GET_ITEM(next, 0));
            PyObject *new_value = Py_NewRef(PyTuple_GET_ITEM(next, 1));
            Py_DECREF(next);
            Py_XSETREF(key, new_key);
            Py_XSETREF(current, new_value);
            if (!identity_string(key)) {
                status = 0;
                break;
            }
        } else {
            /* List iteration remains live when a finalizer appends, removes
             * or replaces source elements during the recursive allocation. */
            Py_XSETREF(current, next);
        }
        status = identity_snapshot_value(current, context, depth + 1, &copied);
        if (status != 1) {
            break;
        }
        int inserted = mapping ? PyDict_SetItem(output, key, copied)
                               : PyList_Append(output, copied);
        /* A dictionary insertion can track a container-valued result. No
         * allocation/callback intervenes before it becomes private again. */
        if (mapping && PyObject_GC_IsTracked(output)) {
            PyObject_GC_UnTrack(output);
        }
        Py_CLEAR(copied);
        if (inserted < 0) {
            status = -1;
            break;
        }
    }
done:
    Py_XDECREF(iterator);
    Py_XDECREF(view);
    Py_XDECREF(key);
    Py_XDECREF(current);
    Py_XDECREF(copied);
    if (status == 1) {
        *result = output;
    } else {
        Py_XDECREF(output);
    }
    Py_LeaveRecursiveCall();
    return status;
}

static int
identity_snapshot_value(PyObject *source, IdentitySnapshot *context,
                        unsigned int depth, PyObject **result)
{
    *result = NULL;
    if (--context->remaining < 0) {
        return 0;
    }
    if (PyUnicode_CheckExact(source)) {
        if (!identity_string(source)) {
            return 0;
        }
    } else if (source == Py_None || PyBool_Check(source)
               || PyLong_CheckExact(source)) {
        /* Integer conversion limits are still enforced by _IDENTITY.encode. */
    } else if (PyFloat_CheckExact(source)) {
        if (!Py_IS_FINITE(PyFloat_AS_DOUBLE(source))) {
            return 0;
        }
    } else {
        if ((!PyList_CheckExact(source) && !PyDict_CheckExact(source))
            || depth >= IDENTITY_MAX_DEPTH || !has_headroom(2 * depth + 8)) {
            return 0;
        }
        for (unsigned int index = 0; index < depth; index++) {
            if (context->ancestors[index] == source) {
                return 0;
            }
        }
        return identity_snapshot_container(source, context, depth, result);
    }
    *result = Py_NewRef(source);
    return 1;
}

static PyObject *
identity_snapshot(PyObject *module, PyObject *source)
{
    (void)module;
    if (!has_headroom(2 * IDENTITY_MAX_DEPTH + 8)) {
        Py_RETURN_NOTIMPLEMENTED;
    }
    IdentitySnapshot context = {IDENTITY_MAX_VALUES, NULL, NULL, {NULL}};
    context.outputs = PyList_New(0);
    if (context.outputs == NULL) {
        return NULL;
    }
    PyObject_GC_UnTrack(context.outputs);
    /* Static builtin type namespaces are interpreter-owned on 3.12+. Public
     * attribute lookup returns an owned descriptor and propagates allocation
     * errors, unlike the error-suppressing PyDict_GetItemString API. */
    /* Reuse the canonical name object: fresh equal strings can occupy distinct
     * CPython type-cache slots and retain one short string per invocation. */
    PyObject *items_name = PyUnicode_InternFromString("items");
    if (items_name == NULL) {
        Py_DECREF(context.outputs);
        return NULL;
    }
    context.items_method = PyObject_GetAttr((PyObject *)&PyDict_Type, items_name);
    Py_DECREF(items_name);
    if (context.items_method == NULL) {
        Py_DECREF(context.outputs);
        return NULL;
    }
    PyObject *result = NULL;
    int status = identity_snapshot_value(source, &context, 0, &result);
    if (status == 1) {
        /* This publication pass neither allocates nor calls Python. Keep the
         * collector untracked until its references can all be dropped safely. */
        Py_ssize_t size = PyList_GET_SIZE(context.outputs);
        for (Py_ssize_t index = 0; index < size; index++) {
            PyObject *output = PyList_GET_ITEM(context.outputs, index);
            if (!PyObject_GC_IsTracked(output)) {
                PyObject_GC_Track(output);
            }
        }
    }
    Py_DECREF(context.outputs);
    Py_DECREF(context.items_method);
    if (status == 1) {
        return result;
    }
    Py_XDECREF(result);
    if (status == 0 || PyErr_ExceptionMatches(PyExc_RecursionError)) {
        PyErr_Clear();
        Py_RETURN_NOTIMPLEMENTED;
    }
    return NULL;
}

#define IDENTITY_BUILTIN_COUNT 12

static const struct {const char *name; Py_ssize_t size;} identity_builtin_names[] = {
    {"type", 4}, {"str", 3}, {"int", 3}, {"bool", 4}, {"float", 5},
    {"list", 4}, {"dict", 4}, {"id", 2}, {"isinstance", 10}, {"set", 3},
    {"TypeError", 9}, {"ValueError", 10}
};

typedef struct {
    PyObject *globals, *walker, *code, *name;
    PyObject *builtins, *snapshot, *isfinite, *isfinite_name;
} IdentityAdmission;

static int
identity_builtin_index(PyObject *key)
{
    if (!PyUnicode_CheckExact(key)) {
        return -1;
    }
    Py_ssize_t size = PyUnicode_GET_LENGTH(key);
    if (size < 2 || size > 10) {
        return -1;
    }
    Py_UCS4 first = PyUnicode_READ_CHAR(key, 0);
    for (int index = 0; index < IDENTITY_BUILTIN_COUNT; index++) {
        if (size == identity_builtin_names[index].size
            && first == (unsigned char)identity_builtin_names[index].name[0]
            && PyUnicode_CompareWithASCIIString(
                key, identity_builtin_names[index].name) == 0) {
            return index;
        }
    }
    return -1;
}

/* Verify the original C method definition, not writable __module__ metadata
 * or the current (possibly monkeypatched) module attribute. */
static int
identity_standard_function(PyObject *value, const char *module_name,
                           const char *function_name)
{
    if (!Py_IS_TYPE(value, &PyCFunction_Type)) {
        return 0;
    }
    PyObject *owner = PyCFunction_GET_SELF(value);
    if (owner == NULL || !PyModule_CheckExact(owner)) {
        return 0;
    }
    PyModuleDef *module = PyModule_GetDef(owner);
    if (module == NULL || module->m_name == NULL || module->m_methods == NULL
        || strcmp(module->m_name, module_name) != 0) {
        return 0;
    }
    PyMethodDef *actual = ((PyCFunctionObject *)value)->m_ml;
    for (PyMethodDef *method = module->m_methods; method->ml_name != NULL; method++) {
        if (method == actual) {
            return strcmp(method->ml_name, function_name) == 0;
        }
    }
    return 0;
}

static int
identity_standard_builtin(int index, PyObject *value)
{
    switch (index) {
        case 0: return value == (PyObject *)&PyType_Type;
        case 1: return value == (PyObject *)&PyUnicode_Type;
        case 2: return value == (PyObject *)&PyLong_Type;
        case 3: return value == (PyObject *)&PyBool_Type;
        case 4: return value == (PyObject *)&PyFloat_Type;
        case 5: return value == (PyObject *)&PyList_Type;
        case 6: return value == (PyObject *)&PyDict_Type;
        case 7: return identity_standard_function(value, "builtins", "id");
        case 8: return identity_standard_function(value, "builtins", "isinstance");
        case 9: return value == (PyObject *)&PySet_Type;
        case 10: return value == PyExc_TypeError;
        case 11: return value == PyExc_ValueError;
        default: return 0;
    }
}

static int
identity_builtin_bindings(PyObject *mapping, PyObject **keys, PyObject **values)
{
    if (!PyDict_CheckExact(mapping)) {
        return 0;
    }
    PyObject *key, *value;
    Py_ssize_t position = 0;
    unsigned int found = 0;
    while (PyDict_Next(mapping, &position, &key, &value)) {
        if (!PyUnicode_CheckExact(key)) {
            return 0;
        }
        int index = identity_builtin_index(key);
        if (index >= 0) {
            if (!identity_standard_builtin(index, value)) {
                return 0;
            }
            keys[index] = key;
            values[index] = value;
            found |= 1U << index;
        }
    }
    return found == (1U << IDENTITY_BUILTIN_COUNT) - 1;
}

static int
identity_relevant_builtins_current(IdentityAdmission *admission)
{
    PyObject *keys[IDENTITY_BUILTIN_COUNT], *values[IDENTITY_BUILTIN_COUNT];
    if (!PyDict_CheckExact(admission->snapshot)
        || PyDict_GET_SIZE(admission->snapshot) != IDENTITY_BUILTIN_COUNT
        || !identity_builtin_bindings(admission->snapshot, keys, values)
        || !PyDict_CheckExact(admission->builtins)) {
        return 0;
    }
    PyObject *key, *value;
    Py_ssize_t position = 0;
    unsigned int found = 0;
    while (PyDict_Next(admission->builtins, &position, &key, &value)) {
        if (!PyUnicode_CheckExact(key)) {
            return 0;
        }
        for (int index = 0; index < IDENTITY_BUILTIN_COUNT; index++) {
            if (key == keys[index]) {
                if (value != values[index]) {
                    return 0;
                }
                found |= 1U << index;
                break;
            }
        }
    }
    return found == (1U << IDENTITY_BUILTIN_COUNT) - 1;
}

static int
identity_admission_matches(IdentityAdmission *admission, int executing)
{
    if (admission->globals == NULL || admission->walker == NULL
        || admission->code == NULL || admission->name == NULL
        || admission->builtins == NULL || admission->snapshot == NULL
        || admission->isfinite == NULL || admission->isfinite_name == NULL
        || !PyDict_CheckExact(admission->globals)
        || !PyFunction_Check(admission->walker)
        || PyFunction_GET_GLOBALS(admission->walker) != admission->globals
        || PyFunction_GET_CODE(admission->walker) != admission->code
        || PyFunction_GET_DEFAULTS(admission->walker) != NULL
        || PyFunction_GET_KW_DEFAULTS(admission->walker) != NULL
        || PyFunction_GET_CLOSURE(admission->walker) != NULL
        || ((PyFunctionObject *)admission->walker)->func_builtins != admission->builtins
        || admission->builtins != PyEval_GetBuiltins()
        || !identity_relevant_builtins_current(admission)
        || (executing && (!monitoring_clear(admission->code)
                          || !has_headroom(2 * IDENTITY_MAX_DEPTH + 8)))) {
        return 0;
    }
    PyObject *walker = NULL, *isfinite = NULL, *key, *value;
    Py_ssize_t position = 0;
    while (PyDict_Next(admission->globals, &position, &key, &value)) {
        if (!PyUnicode_CheckExact(key) || identity_builtin_index(key) >= 0) {
            return 0;
        }
        if (key == admission->name) {
            walker = value;
        }
        if (key == admission->isfinite_name) {
            isfinite = value;
        }
    }
    return walker == admission->walker && isfinite == admission->isfinite;
}

static void
identity_admission_clear(IdentityAdmission *admission)
{
    PyObject *error = PyErr_GetRaisedException();
    Py_XDECREF(admission->globals);
    Py_XDECREF(admission->walker);
    Py_XDECREF(admission->code);
    Py_XDECREF(admission->name);
    Py_XDECREF(admission->builtins);
    Py_XDECREF(admission->snapshot);
    Py_XDECREF(admission->isfinite);
    Py_XDECREF(admission->isfinite_name);
    PyErr_SetRaisedException(error);
}

static int
configure_identity_snapshot(NativeState *state, PyObject *globals)
{
    if (state->identity_configured) {
        return 0;
    }
    /* Capture once, including an unsupported first configuration. A retry
     * cannot bless a replacement function or a formerly altered builtin map. */
    state->identity_configured = 1;
    if (state->encoder == NULL || state->globals != globals
        || !PyDict_CheckExact(globals)) {
        return 0;
    }
    PyObject *walker = NULL, *name = NULL, *isfinite = NULL, *finite_name = NULL;
    PyObject *key, *value;
    Py_ssize_t position = 0;
    while (PyDict_Next(globals, &position, &key, &value)) {
        if (!PyUnicode_CheckExact(key) || identity_builtin_index(key) >= 0) {
            return 0;
        }
        if (PyUnicode_CompareWithASCIIString(key, "_identity_snapshot") == 0) {
            walker = value;
            name = key;
        } else if (PyUnicode_CompareWithASCIIString(key, "isfinite") == 0) {
            isfinite = value;
            finite_name = key;
        }
    }
    if (walker == NULL || !PyFunction_Check(walker) || isfinite == NULL
        || PyFunction_GET_GLOBALS(walker) != globals
        || PyFunction_GET_DEFAULTS(walker) != NULL
        || PyFunction_GET_KW_DEFAULTS(walker) != NULL
        || PyFunction_GET_CLOSURE(walker) != NULL
        || !identity_standard_function(isfinite, "math", "isfinite")) {
        return 0;
    }
    PyObject *builtins = ((PyFunctionObject *)walker)->func_builtins;
    if (builtins != PyEval_GetBuiltins()) {
        return 0;
    }
    PyObject *keys[IDENTITY_BUILTIN_COUNT], *values[IDENTITY_BUILTIN_COUNT];
    if (!identity_builtin_bindings(builtins, keys, values)) {
        return 0;
    }
    /* Everything borrowed above is pinned before the first tracked allocation,
     * including keys/values which a reentrant callback could remove. */
    IdentityAdmission admission = {
        Py_NewRef(globals), Py_NewRef(walker), Py_NewRef(PyFunction_GET_CODE(walker)),
        Py_NewRef(name), Py_NewRef(builtins), NULL,
        Py_NewRef(isfinite), Py_NewRef(finite_name)
    };
    for (int index = 0; index < IDENTITY_BUILTIN_COUNT; index++) {
        Py_INCREF(keys[index]);
        Py_INCREF(values[index]);
    }
    int status = 0;
    admission.snapshot = PyDict_New();
    if (admission.snapshot == NULL) {
        status = -1;
        goto done;
    }
    for (int index = 0; index < IDENTITY_BUILTIN_COUNT; index++) {
        if (PyDict_SetItem(admission.snapshot, keys[index], values[index]) < 0) {
            status = -1;
            goto done;
        }
    }
    if (state->encoder != NULL && state->globals == globals
        && state->identity_configured
        && identity_admission_matches(&admission, 0)) {
        state->identity_walker = admission.walker; admission.walker = NULL;
        state->identity_walker_code = admission.code; admission.code = NULL;
        state->identity_walker_key = admission.name; admission.name = NULL;
        state->identity_builtins = admission.builtins; admission.builtins = NULL;
        state->identity_builtins_snapshot = admission.snapshot; admission.snapshot = NULL;
        state->identity_isfinite = admission.isfinite; admission.isfinite = NULL;
        state->identity_isfinite_key = admission.isfinite_name; admission.isfinite_name = NULL;
    }
done:
    {
        PyObject *error = PyErr_GetRaisedException();
        for (int index = 0; index < IDENTITY_BUILTIN_COUNT; index++) {
            Py_DECREF(keys[index]);
            Py_DECREF(values[index]);
        }
        identity_admission_clear(&admission);
        PyErr_SetRaisedException(error);
    }
    return status;
}

static int
identity_snapshot_current(NativeState *state, IdentityAdmission *admission)
{
    return state != NULL && state->encoder != NULL
        && state->globals == admission->globals
        && state->identity_walker == admission->walker
        && state->identity_walker_code == admission->code
        && state->identity_walker_key == admission->name
        && state->identity_builtins == admission->builtins
        && state->identity_builtins_snapshot == admission->snapshot
        && state->identity_isfinite == admission->isfinite
        && state->identity_isfinite_key == admission->isfinite_name
        && identity_admission_matches(admission, 1);
}

static PyObject *
guarded_identity_snapshot(PyObject *module, PyObject *source)
{
    NativeState *state = PyModule_GetState(module);
    if (state == NULL || state->encoder == NULL || state->globals == NULL
        || state->identity_walker == NULL || state->identity_walker_code == NULL
        || state->identity_walker_key == NULL || state->identity_builtins == NULL
        || state->identity_builtins_snapshot == NULL || state->identity_isfinite == NULL
        || state->identity_isfinite_key == NULL) {
        Py_RETURN_NOTIMPLEMENTED;
    }
    /* Keep the original admission objects alive even if reentrant Python
     * clears/reconfigures module state during an allocation-triggered GC. */
    IdentityAdmission admission = {
        Py_NewRef(state->globals), Py_NewRef(state->identity_walker),
        Py_NewRef(state->identity_walker_code), Py_NewRef(state->identity_walker_key),
        Py_NewRef(state->identity_builtins), Py_NewRef(state->identity_builtins_snapshot),
        Py_NewRef(state->identity_isfinite), Py_NewRef(state->identity_isfinite_key)
    };
    PyObject *result;
    if (!identity_snapshot_current(state, &admission)) {
        result = Py_NewRef(Py_NotImplemented);
    } else {
        result = identity_snapshot(module, source);
        if (result != NULL
            && !identity_snapshot_current(state, &admission)) {
            Py_DECREF(result);
            result = Py_NewRef(Py_NotImplemented);
        }
    }
    identity_admission_clear(&admission);
    return result;
}
