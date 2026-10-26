/* Escape trusted UTF-8 output with the running stdlib's ASCII JSON spelling. */
static PyObject *
ascii_text(PyObject *module, PyObject *encoded)
{
    (void)module;
    if (!PyBytes_CheckExact(encoded)) {
        PyErr_SetString(PyExc_TypeError, "ascii_text() requires bytes");
        return NULL;
    }
    PyObject *text = PyUnicode_DecodeUTF8(PyBytes_AS_STRING(encoded),
                                        PyBytes_GET_SIZE(encoded), "strict");
    if (text == NULL) {
        return NULL;
    }
    Py_ssize_t size = PyUnicode_GET_LENGTH(text), extra = 0;
    int kind = PyUnicode_KIND(text);
    void *data = PyUnicode_DATA(text);
    if (PyUnicode_IS_ASCII(text) && memchr(data, 127, size) == NULL) {
        return text;
    }
    for (Py_ssize_t index = 0; index < size; index++) {
        Py_UCS4 character = PyUnicode_READ(kind, data, index);
        if (character >= 127) {
            Py_ssize_t addition = character > 0xffff ? 11 : 5;
            if (extra > PY_SSIZE_T_MAX - size - addition) {
                Py_DECREF(text);
                return PyErr_NoMemory();
            }
            extra += addition;
        }
    }
    if (extra == 0) {
        return text;
    }
    PyObject *result = PyUnicode_New(size + extra, 127);
    if (result == NULL) {
        Py_DECREF(text);
        return NULL;
    }
    static const char hex[] = "0123456789abcdef";
    Py_UCS1 *output = PyUnicode_1BYTE_DATA(result);
    for (Py_ssize_t index = 0; index < size; index++) {
        Py_UCS4 character = PyUnicode_READ(kind, data, index);
        if (character < 127) {
            *output++ = (Py_UCS1)character;
            continue;
        }
        if (character > 0xffff) {
            Py_UCS4 high = 0xd800 + ((character - 0x10000) >> 10);
            *output++ = '\\';
            *output++ = 'u';
            *output++ = hex[(high >> 12) & 15];
            *output++ = hex[(high >> 8) & 15];
            *output++ = hex[(high >> 4) & 15];
            *output++ = hex[high & 15];
            character = 0xdc00 + ((character - 0x10000) & 1023);
        }
        *output++ = '\\';
        *output++ = 'u';
        *output++ = hex[(character >> 12) & 15];
        *output++ = hex[(character >> 8) & 15];
        *output++ = hex[(character >> 4) & 15];
        *output++ = hex[character & 15];
    }
    Py_DECREF(text);
    return result;
}
