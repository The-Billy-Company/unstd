/* Strict NDJSON batching over the same trusted single-document decoder.
 * A rejected row delegates the original whole input to the Python line walker,
 * which owns exact line numbers, callbacks, skips and stdlib diagnostics.
 */
#include <string.h>

static PyObject *
loads_lines(PyObject *module, PyObject *document)
{
    int binary = PyBytes_CheckExact(document);
    if ((!binary && !PyUnicode_CheckExact(document)) ||
        !has_headroom(3)) {
        Py_RETURN_NOTIMPLEMENTED;
    }
    NativeState *state = PyModule_GetState(module);
    if (state->loads_function == NULL ||
        PyDict_GetItemString(state->globals, "loads") != state->loads_function ||
        !unchanged_decoder(state, state->loads_function)) {
        Py_RETURN_NOTIMPLEMENTED;
    }
    PyObject *rows = PyList_New(0);
    if (rows == NULL) {
        return NULL;
    }
    Py_ssize_t length = binary ? PyBytes_GET_SIZE(document) :
                                 PyUnicode_GET_LENGTH(document);
    const char *bytes = binary ? PyBytes_AS_STRING(document) : NULL;
    int kind = binary ? 0 : PyUnicode_KIND(document);
    const void *data = binary ? NULL : PyUnicode_DATA(document);
    Py_ssize_t position = 0;
    while (position < length) {
        Py_ssize_t end;
        if (binary) {
            const char *newline = memchr(bytes + position, '\n', length - position);
            end = newline == NULL ? length : newline - bytes;
        } else {
            end = PyUnicode_FindChar(document, '\n', position, length, 1);
            if (end == -2) {
                Py_DECREF(rows);
                return NULL;
            }
            if (end == -1) {
                end = length;
            }
        }
        Py_ssize_t start = position;
        position = end < length ? end + 1 : length;
        /* Match bytes.strip()/str.strip(), including Unicode-only padding.
         * JSON strings retain their quotes, so internal spaces are untouched. */
        while (start < end) {
            Py_UCS4 c = binary ? (unsigned char)bytes[start] :
                                PyUnicode_READ(kind, data, start);
            int space = binary ? (c == ' ' || (c >= '\t' && c <= '\r')) :
                                 Py_UNICODE_ISSPACE(c);
            if (!space) {
                break;
            }
            start++;
        }
        while (end > start) {
            Py_UCS4 c = binary ? (unsigned char)bytes[end - 1] :
                                PyUnicode_READ(kind, data, end - 1);
            int space = binary ? (c == ' ' || (c >= '\t' && c <= '\r')) :
                                 Py_UNICODE_ISSPACE(c);
            if (!space) {
                break;
            }
            end--;
        }
        if (start == end) {
            continue;
        }
        PyObject *line = binary ? PyBytes_FromStringAndSize(bytes + start, end - start) :
                                  PyUnicode_Substring(document, start, end);
        if (line == NULL) {
            Py_DECREF(rows);
            return NULL;
        }
        PyObject *value = decode_document(state, line);
        Py_DECREF(line);
        if (value == NULL || value == Py_NotImplemented) {
            Py_XDECREF(value);
            Py_DECREF(rows);
            if (value != NULL || PyErr_ExceptionMatches(PyExc_ValueError) ||
                PyErr_ExceptionMatches(PyExc_RecursionError)) {
                PyErr_Clear();
                Py_RETURN_NOTIMPLEMENTED;
            }
            return NULL;
        }
        int appended = PyList_Append(rows, value);
        Py_DECREF(value);
        if (appended < 0) {
            Py_DECREF(rows);
            return NULL;
        }
    }
    return rows;
}
