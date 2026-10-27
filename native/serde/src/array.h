/* Delimiter framing for one bounded JSON-array window.
 *
 * The caller starts at a top-level item boundary. This helper only identifies
 * complete item spans; the existing JSON decoder remains authoritative for
 * values, syntax, depth and diagnostics. Incomplete suffixes stay on the Python
 * stream walker's incremental path, so no accumulated item is rescanned here.
 * Exact Unicode/bytes are immutable and held by the call. The scan neither
 * allocates, invokes Python nor releases the GIL; only the final tuple allocates.
 */

static PyObject *
array_boundary(PyObject *module, PyObject *const *args, Py_ssize_t count)
{
    (void)module;
    if (count != 2) {
        PyErr_SetString(PyExc_TypeError,
                        "array_boundary() requires text and start");
        return NULL;
    }
    PyObject *text = args[0];
    int binary = PyBytes_CheckExact(text);
    if (!binary && !PyUnicode_CheckExact(text)) {
        Py_RETURN_NOTIMPLEMENTED;
    }
    if (!PyLong_CheckExact(args[1])) {
        PyErr_SetString(PyExc_TypeError,
                        "array_boundary() start must be an exact integer");
        return NULL;
    }
    Py_ssize_t start = PyLong_AsSsize_t(args[1]);
    if (start == -1 && PyErr_Occurred()) {
        return NULL;
    }
    Py_ssize_t length = binary ? PyBytes_GET_SIZE(text)
                              : PyUnicode_GET_LENGTH(text);
    if (start < 0 || start > length) {
        PyErr_SetString(PyExc_ValueError,
                        "array_boundary() start is outside text");
        return NULL;
    }
    int kind = binary ? PyUnicode_1BYTE_KIND : PyUnicode_KIND(text);
    const void *data = binary ? (const void *)PyBytes_AS_STRING(text)
                              : PyUnicode_DATA(text);
    Py_ssize_t depth = 0;
    Py_ssize_t boundary = -1;
    int quoted = 0;
    int escaped = 0;
    int closed = 0;
    for (Py_ssize_t position = start; position < length; position++) {
        Py_UCS4 symbol = PyUnicode_READ(kind, data, position);
        if (quoted) {
            if (escaped) {
                escaped = 0;
            } else if (symbol == '\\') {
                escaped = 1;
            } else if (symbol == '"') {
                quoted = 0;
            }
        } else if (symbol == '"') {
            if (kind == PyUnicode_1BYTE_KIND) {
                /* The shared quote finder uses memchr and backslash parity,
                 * skipping string payload in bulk. It is a framing operation:
                 * malformed escapes still go to the authoritative decoder. */
                const char *bytes = data;
                const char *after = skip_json_string(bytes + position + 1,
                                                     bytes + length);
                if (after == NULL) {
                    break;
                }
                position = after - bytes - 1;
            } else {
                quoted = 1;
            }
        } else if (symbol == '[' || symbol == '{') {
            depth++;
        } else if (symbol == ']' && depth == 0) {
            boundary = position;
            closed = 1;
            break;
        } else if (symbol == ']' || symbol == '}') {
            /* An unmatched object closer is malformed. Keep any earlier
             * complete prefix and let the decoder/walker reject the suffix. */
            if (depth == 0) {
                break;
            }
            depth--;
        } else if (symbol == ',' && depth == 0) {
            boundary = position;
        }
    }
    return Py_BuildValue("(nO)", boundary, closed ? Py_True : Py_False);
}

static PyObject *
array_decode(PyObject *module, PyObject *const *args, Py_ssize_t count)
{
    if (count != 4) {
        PyErr_SetString(PyExc_TypeError,
                        "array_decode() requires text, start, end and decoder");
        return NULL;
    }
    PyObject *text = args[0];
    int binary = PyBytes_CheckExact(text);
    if (!binary && !PyUnicode_CheckExact(text)) {
        Py_RETURN_NOTIMPLEMENTED;
    }
    if (!PyLong_CheckExact(args[1]) || !PyLong_CheckExact(args[2])) {
        PyErr_SetString(PyExc_TypeError,
                        "array_decode() offsets must be exact integers");
        return NULL;
    }
    Py_ssize_t start = PyLong_AsSsize_t(args[1]);
    if (start == -1 && PyErr_Occurred()) {
        return NULL;
    }
    Py_ssize_t end = PyLong_AsSsize_t(args[2]);
    if (end == -1 && PyErr_Occurred()) {
        return NULL;
    }
    Py_ssize_t length = binary ? PyBytes_GET_SIZE(text)
                              : PyUnicode_GET_LENGTH(text);
    if (start < 0 || start > end || end > length) {
        PyErr_SetString(PyExc_ValueError,
                        "array_decode() offsets are outside text or reversed");
        return NULL;
    }
    Py_ssize_t span = end - start;
    if (span > PY_SSIZE_T_MAX - 2) {
        return PyErr_NoMemory();
    }
    NativeState *state = PyModule_GetState(module);
    PyObject *expected = args[3];
    if (state->globals == NULL || expected != state->loads_function) {
        Py_RETURN_NOTIMPLEMENTED;
    }
    PyObject *bound = PyDict_GetItemString(state->globals, "loads");
    if (bound == NULL && PyErr_Occurred()) {
        return NULL;
    }
    if (bound != expected || !unchanged_decoder(state, expected)) {
        Py_RETURN_NOTIMPLEMENTED;
    }

    /* Exact immutable text and the call arguments remain owned throughout.
     * String construction is untracked and invokes no Python callback. Both
     * representations allocate the bounded bracketed document once. */
    PyObject *document;
    if (binary) {
        document = PyBytes_FromStringAndSize(NULL, span + 2);
        if (document == NULL) {
            return NULL;
        }
        char *data = PyBytes_AS_STRING(document);
        data[0] = '[';
        memcpy(data + 1, PyBytes_AS_STRING(text) + start, span);
        data[span + 1] = ']';
    } else {
        document = PyUnicode_New(span + 2, PyUnicode_MAX_CHAR_VALUE(text));
        if (document == NULL) {
            return NULL;
        }
        int kind = PyUnicode_KIND(document);
        void *data = PyUnicode_DATA(document);
        PyUnicode_WRITE(kind, data, 0, '[');
        PyUnicode_WRITE(kind, data, span + 1, ']');
        if (span > 0
            && PyUnicode_CopyCharacters(document, 1, text, start, span) < 0) {
            Py_DECREF(document);
            return NULL;
        }
    }
    /* No speculative stdlib retry here: the Python stream walker owns item
     * diagnostics and delivery of valid values before a malformed suffix. */
    PyObject *value = decode_document(state, document);
    Py_DECREF(document);
    return value;
}
