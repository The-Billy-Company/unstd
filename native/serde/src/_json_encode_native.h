/* Preserve Python function metadata while bypassing its common-call dispatch. */

static vectorcallfunc ordinary_dumpb_vectorcall;

static int
unchanged_encoder(NativeState *state, PyObject *function)
{
    if (!unchanged_function(function, state->dumpb_function, state->dumpb_code)) {
        return 0;
    }
    PyObject *defaults = PyFunction_GET_KW_DEFAULTS(function);
    if (defaults == NULL || !PyDict_CheckExact(defaults)
        || PyDict_GET_SIZE(defaults) != 3) {
        return 0;
    }
    /* Inspect entries without hashing or comparing caller-supplied keys. A
     * replaced or reordered defaults table simply uses Python's normal call. */
    PyObject *names = ((PyCodeObject *)state->dumpb_code)->co_localsplusnames;
    Py_ssize_t position = 0, index = 0;
    PyObject *key, *value;
    while (PyDict_Next(defaults, &position, &key, &value)) {
        if (key != PyTuple_GET_ITEM(names, index + 1)
            || value != (index == 1 ? Py_None : Py_False)) {
            return 0;
        }
        index++;
    }
    return configuration_ready(state, 0) && state->encoder_dispatch_ready;
}

static PyObject *
dumpb_vectorcall(PyObject *function, PyObject *const *args, size_t count,
                 PyObject *keywords)
{
    PyObject *module = PyState_FindModule(&definition);
    NativeState *state = module == NULL ? NULL : PyModule_GetState(module);
    if (state != NULL && PyVectorcall_NARGS(count) == 1 && keywords == NULL
        && has_headroom(4) && unchanged_encoder(state, function)) {
        PyObject *value = transport_encode(state, args[0], NULL);
        if (value != NULL && value != Py_NotImplemented) {
            return value;
        }
        if (value == NULL) {
            return NULL;
        }
        Py_DECREF(value);
    }
    vectorcallfunc original = state != NULL
        && function == state->dumpb_function ? state->dumpb_original :
                                               ordinary_dumpb_vectorcall;
    return original(function, args, count, keywords);
}

static int
configure_encoder(NativeState *state, PyObject *globals)
{
#if PY_VERSION_HEX >= 0x030E0000 && PY_VERSION_HEX < 0x030F0000
    PyObject *function = PyDict_GetItemString(globals, "dumpb");
    if (function == NULL || !PyFunction_Check(function)) {
        return 0;
    }
    PyCodeObject *code = (PyCodeObject *)PyFunction_GET_CODE(function);
    if (PyFunction_GET_GLOBALS(function) != globals
        || (state->dumpb_function != NULL
            && (function != state->dumpb_function
                || (PyObject *)code != state->dumpb_code))) {
        return 0;
    }
    if (state->dumpb_function == NULL
        && PyVectorcall_Function(function) == dumpb_vectorcall) {
        /* A different module instance owns this slot and its Python fallback. */
        return 0;
    }
    PyObject *helper = PyDict_GetItemString(globals, "_native_dumpb");
    if (helper == NULL || !PyCFunction_Check(helper)
        || PyCFunction_GetSelf(helper) != PyState_FindModule(&definition)
        || PyCFunction_GetFunction(helper)
           != (PyCFunction)(void (*)(void))dumpb
        || (state->dumpb_helper != NULL && helper != state->dumpb_helper)) {
        return 0;
    }
    if (code->co_argcount != 1 || code->co_kwonlyargcount != 3
        || PyFunction_GET_DEFAULTS(function) != NULL
        || PyFunction_GET_CLOSURE(function) != NULL) {
        return 0;
    }
    if (function == state->dumpb_function
        && PyVectorcall_Function(function) == dumpb_vectorcall) {
        return 0;
    }
    if (state->dumpb_function != NULL
        && PyVectorcall_Function(function) != state->dumpb_original) {
        return 0;
    }
    PyObject *key = PyUnicode_InternFromString("_native_dumpb");
    if (key == NULL) {
        return -1;
    }
    if (state->dumpb_function != NULL
        && PyVectorcall_Function(state->dumpb_function) == dumpb_vectorcall) {
        PyFunction_SetVectorcall((PyFunctionObject *)state->dumpb_function,
                                 state->dumpb_original);
    }
    Py_XSETREF(state->dumpb_function, Py_NewRef(function));
    Py_XSETREF(state->dumpb_code, Py_NewRef((PyObject *)code));
    Py_XSETREF(state->dumpb_helper, Py_NewRef(helper));
    Py_XSETREF(state->key_dumpb_helper, key);
    state->config_dirty = 1;
    state->dumpb_original = PyVectorcall_Function(function);
    if (ordinary_dumpb_vectorcall == NULL) {
        ordinary_dumpb_vectorcall = state->dumpb_original;
    }
    PyFunction_SetVectorcall((PyFunctionObject *)function, dumpb_vectorcall);
#else
    (void)state;
    (void)globals;
#endif
    return 0;
}
