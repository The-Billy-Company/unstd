/* Bounded finite-float encoding.
 *
 * The writer validates and emits a private result under the GIL, sharing the
 * original proof's 262144-visit budget. It allocates only untracked PyBytes
 * buffers, raw float formatter storage and owned exact-string references.
 * No Python calls, tracked allocations, signal checks or GIL release occur
 * during the walk. Unsupported values and the 1 MiB staging limit return
 * NotImplemented before publishing any result. Every cache is operation-local.
 */
#include <cpython/longintrepr.h>
typedef struct {
    EncodeProof tree;
    uint64_t bits[4];
    unsigned int distinct, replacement;
} RepeatProof;

/* Cached spellings refer only to owned exact strings or copied float bits. */
static PyObject *ascii_text(PyObject *, PyObject *);
typedef struct {
    PyObject *bytes;
    Py_ssize_t size, capacity;
    int ascii, spaced, declined;
    RepeatProof *proof;
    char numbers[4][64];
    Py_ssize_t number_sizes[4];
    struct { PyObject *key; Py_ssize_t offset, size; } strings[8];
    unsigned int string_count;
} RepeatWriter;

static inline Py_ALWAYS_INLINE int
repeat_reserve(RepeatWriter *writer, Py_ssize_t additional)
{
    if (additional > PY_SSIZE_T_MAX - writer->size) {
        PyErr_NoMemory();
        return -1;
    }
    Py_ssize_t needed = writer->size + additional;
    if (needed > 1024 * 1024) {
        writer->declined = 1;
        return -1;
    }
    if (needed <= writer->capacity) {
        return 1;
    }
    Py_ssize_t capacity = writer->capacity;
    while (capacity < needed) {
        capacity = capacity > PY_SSIZE_T_MAX / 2 ? needed : capacity * 2;
    }
    if (_PyBytes_Resize(&writer->bytes, capacity) < 0) {
        return -1;
    }
    writer->capacity = capacity;
    return 1;
}

static inline Py_ALWAYS_INLINE int
repeat_append(RepeatWriter *writer, const char *text, Py_ssize_t size)
{
    if (repeat_reserve(writer, size) < 0) {
        return -1;
    }
    memcpy(PyBytes_AS_STRING(writer->bytes) + writer->size, text, size);
    writer->size += size;
    return 1;
}

static int
repeat_string(RepeatWriter *writer, PyObject *value)
{
    for (unsigned int index = 0; index < writer->string_count; index++) {
        if (writer->strings[index].key == value) {
            Py_ssize_t size = writer->strings[index].size;
            if (repeat_reserve(writer, size) < 0) {
                return -1;
            }
            char *buffer = PyBytes_AS_STRING(writer->bytes);
            memcpy(buffer + writer->size, buffer + writer->strings[index].offset, size);
            writer->size += size;
            return 1;
        }
    }
    if (PyUnicode_GET_LENGTH(value) > 256) { return 0; }
    static const char hex[] = "0123456789abcdef";
    Py_ssize_t start = writer->size;
    if (repeat_append(writer, "\"", 1) < 0) {
        return -1;
    }
    int kind = PyUnicode_KIND(value);
    void *data = PyUnicode_DATA(value);
    Py_ssize_t size = PyUnicode_GET_LENGTH(value);
    for (Py_ssize_t index = 0; index < size; index++) {
        Py_UCS4 point = PyUnicode_READ(kind, data, index);
        if (point >= 0xd800 && point <= 0xdfff) { return 0; }
        char escaped[12];
        Py_ssize_t length = 0;
        if (point == '"' || point == '\\') {
            escaped[0] = '\\'; escaped[1] = (char)point; length = 2;
        } else if (point < 32 || (writer->ascii && point >= 127)) {
            char short_escape = point == '\b' ? 'b' : point == '\f' ? 'f'
                : point == '\n' ? 'n' : point == '\r' ? 'r' : point == '\t' ? 't' : 0;
            if (short_escape != 0) {
                escaped[0] = '\\'; escaped[1] = short_escape; length = 2;
            } else {
                if (point > 0xffff) {
                    Py_UCS4 high = 0xd800 + ((point - 0x10000) >> 10);
                    escaped[0] = '\\'; escaped[1] = 'u';
                    escaped[2] = hex[(high >> 12) & 15]; escaped[3] = hex[(high >> 8) & 15];
                    escaped[4] = hex[(high >> 4) & 15]; escaped[5] = hex[high & 15];
                    length = 6;
                    point = 0xdc00 + ((point - 0x10000) & 1023);
                }
                escaped[length++] = '\\'; escaped[length++] = 'u';
                escaped[length++] = hex[(point >> 12) & 15]; escaped[length++] = hex[(point >> 8) & 15];
                escaped[length++] = hex[(point >> 4) & 15]; escaped[length++] = hex[point & 15];
            }
        } else if (point < 128) {
            escaped[0] = (char)point; length = 1;
        } else if (point < 0x800) {
            escaped[0] = (char)(0xc0 | (point >> 6));
            escaped[1] = (char)(0x80 | (point & 63)); length = 2;
        } else if (point < 0x10000) {
            if (point >= 0xd800 && point <= 0xdfff) {
                return 0;
            }
            escaped[0] = (char)(0xe0 | (point >> 12));
            escaped[1] = (char)(0x80 | ((point >> 6) & 63));
            escaped[2] = (char)(0x80 | (point & 63)); length = 3;
        } else {
            escaped[0] = (char)(0xf0 | (point >> 18));
            escaped[1] = (char)(0x80 | ((point >> 12) & 63));
            escaped[2] = (char)(0x80 | ((point >> 6) & 63));
            escaped[3] = (char)(0x80 | (point & 63)); length = 4;
        }
        if (repeat_append(writer, escaped, length) < 0) {
            return -1;
        }
    }
    if (repeat_append(writer, "\"", 1) < 0) {
        return -1;
    }
    if (writer->string_count < 8) {
        unsigned int slot = writer->string_count++;
        writer->strings[slot].key = Py_NewRef(value);
        writer->strings[slot].offset = start;
        writer->strings[slot].size = writer->size - start;
    }
    return 1;
}

static int repeat_write(RepeatWriter *, PyObject *, int);
static int
repeat_write(RepeatWriter *writer, PyObject *value, int depth)
{
    if (--writer->proof->tree.remaining < 0) { return 0; }
    if (PyUnicode_CheckExact(value)) {
        return repeat_string(writer, value);
    }
    if (value == Py_None) {
        return repeat_append(writer, "null", 4);
    }
    if (PyBool_Check(value)) {
        return repeat_append(writer, value == Py_True ? "true" : "false", value == Py_True ? 4 : 5);
    }
    if (PyLong_CheckExact(value)) {
        /* Never enter an overflow-error constructor during the borrowed walk. */
        if (!PyUnstable_Long_IsCompact((PyLongObject *)value)) { return 0; }
        Py_ssize_t signed_number = PyUnstable_Long_CompactValue((PyLongObject *)value);
        int negative = signed_number < 0;
        unsigned long long number = negative
            ? (unsigned long long)(-(signed_number + 1)) + 1
            : (unsigned long long)signed_number;
        char buffer[32]; char *cursor = buffer + sizeof(buffer);
        do { *--cursor = (char)('0' + number % 10); number /= 10; } while (number != 0);
        if (negative) { *--cursor = '-'; }
        return repeat_append(writer, cursor, buffer + sizeof(buffer) - cursor);
    }
    if (PyFloat_CheckExact(value)) {
        double number = PyFloat_AS_DOUBLE(value);
        if (!Py_IS_FINITE(number)) { return 0; }
        uint64_t bits; memcpy(&bits, &number, sizeof(bits));
        unsigned int slot;
        for (slot = 0; slot < writer->proof->distinct; slot++) {
            if (writer->proof->bits[slot] == bits) { break; }
        }
        if (slot == writer->proof->distinct) {
            if (slot == 4) {
                slot = writer->proof->replacement++ & 3;
                writer->proof->bits[slot] = bits;
                writer->number_sizes[slot] = 0;
            } else {
                writer->proof->bits[writer->proof->distinct++] = bits;
            }
        }
        if (writer->number_sizes[slot] == 0) {
            char *representation = PyOS_double_to_string(number, 'r', 0, Py_DTSF_ADD_DOT_0, NULL);
            if (representation == NULL) { return -1; }
            Py_ssize_t size = (Py_ssize_t)strlen(representation);
            if (size >= (Py_ssize_t)sizeof(writer->numbers[slot])) {
                PyMem_Free(representation);
                return 0;
            }
            memcpy(writer->numbers[slot], representation, size);
            PyMem_Free(representation);
            writer->number_sizes[slot] = size;
        }
        return repeat_append(writer, writer->numbers[slot], writer->number_sizes[slot]);
    }
    if (depth >= MAX_DEPTH) { return 0; }
    if (Py_EnterRecursiveCall(" while writing repeated JSON floats")) {
        return -1;
    }
    int result = 0;
    if (PyList_CheckExact(value) || PyTuple_CheckExact(value)) {
        if ((result = repeat_append(writer, "[", 1)) < 0) { goto done; }
        int list = PyList_CheckExact(value);
        Py_ssize_t size = list ? PyList_GET_SIZE(value) : PyTuple_GET_SIZE(value);
        PyObject **items = list ? ((PyListObject *)value)->ob_item : ((PyTupleObject *)value)->ob_item;
        for (Py_ssize_t index = 0; index < size; index++) {
            if (index && (result = repeat_append(writer, writer->spaced ? ", " : ",", writer->spaced ? 2 : 1)) < 0) { goto done; }
            if ((result = repeat_write(writer, items[index], depth + 1)) != 1) { goto done; }
        }
        result = repeat_append(writer, "]", 1);
    } else if (PyDict_CheckExact(value)) {
        if ((result = repeat_append(writer, "{", 1)) < 0) { goto done; }
        Py_ssize_t position = 0; PyObject *key, *item; int first = 1;
        while (PyDict_Next(value, &position, &key, &item)) {
            if (!PyUnicode_CheckExact(key)) { result = 0; goto done; }
            if (!first && (result = repeat_append(writer, writer->spaced ? ", " : ",", writer->spaced ? 2 : 1)) < 0) { goto done; }
            first = 0;
            if ((result = repeat_string(writer, key)) != 1) { goto done; }
            if ((result = repeat_append(writer, writer->spaced ? ": " : ":", writer->spaced ? 2 : 1)) < 0) { goto done; }
            if ((result = repeat_write(writer, item, depth + 1)) != 1) { goto done; }
        }
        result = repeat_append(writer, "}", 1);
    }
done:
    Py_LeaveRecursiveCall();
    return result;
}

static PyObject *
repeat_write_result(PyObject *value, int ascii, int spaced, RepeatProof *proof)
{
    RepeatWriter writer = {.bytes = NULL, .capacity = 256, .ascii = ascii, .spaced = spaced, .proof = proof};
    writer.bytes = PyBytes_FromStringAndSize(NULL, writer.capacity);
    if (writer.bytes == NULL) { return NULL; }
    int result = repeat_write(&writer, value, 0);
    for (unsigned int index = 0; index < writer.string_count; index++) {
        Py_DECREF(writer.strings[index].key);
    }
    if (result != 1) {
        Py_XDECREF(writer.bytes);
        if (result < 0 && !writer.declined) { return NULL; }
        Py_RETURN_NOTIMPLEMENTED;
    }
    if (_PyBytes_Resize(&writer.bytes, writer.size) < 0) { return NULL; }
    return writer.bytes;
}

static PyObject *
guarded_repeated_encode(NativeState *state, PyObject *value, PyObject *option, int ascii, int spaced)
{
    if (option != NULL) {
        return guarded_encode_limits(state, value, option, spaced, MAX_WIDE_VALUES, 0);
    }
    if (Py_EnterRecursiveCall(" while proving JSON encoding")) { return NULL; }
    EncodeProof ordinary = {.remaining = MAX_WIDE_VALUES};
    int result = eligible(value, &ordinary, spaced, 0);
    if (result == 1) {
        PyObject *backend_args[] = {value};
        PyObject *raw = state->encoder_fast != NULL
            ? state->encoder_fast(state->encoder_self, backend_args, 1, NULL)
            : PyObject_Vectorcall(state->encoder, backend_args, 1, NULL);
        Py_LeaveRecursiveCall();
        if (raw != NULL && spaced && ordinary.separators) {
            PyObject *text = space_encoded(raw, ordinary.separators);
            Py_DECREF(raw); return text;
        }
        return raw;
    }
    if (result != 2) {
        Py_LeaveRecursiveCall();
        if (result < 0) { return NULL; }
        Py_RETURN_NOTIMPLEMENTED;
    }
    RepeatProof proof = {.tree = {.remaining = ordinary.remaining}};
    PyObject *raw = repeat_write_result(value, ascii, spaced, &proof);
    Py_LeaveRecursiveCall();
    return raw;
}

static PyObject *
encode_text(PyObject *module, PyObject *const *args, Py_ssize_t count)
{
    if (count != 3) {
        PyErr_SetString(PyExc_TypeError,
                        "encode_text() requires three arguments");
        return NULL;
    }
    if (!PyBool_Check(args[1]) || !PyBool_Check(args[2]) || !has_headroom(2)) {
        Py_RETURN_NOTIMPLEMENTED;
    }
    NativeState *state = PyModule_GetState(module);
    if (state->encoder == NULL) {
        Py_RETURN_NOTIMPLEMENTED;
    }
    PyObject *raw = guarded_repeated_encode(state, args[0], NULL,
                                           args[1] == Py_True,
                                           args[2] == Py_True);
    if (raw == NULL || raw == Py_NotImplemented) {
        return raw;
    }
    if (!PyBytes_CheckExact(raw)) {
        Py_DECREF(raw);
        PyErr_SetString(PyExc_TypeError, "native JSON must be bytes");
        return NULL;
    }
    PyObject *text = args[1] == Py_True ? ascii_text(module, raw)
        : PyUnicode_DecodeUTF8(PyBytes_AS_STRING(raw), PyBytes_GET_SIZE(raw), "strict");
    Py_DECREF(raw);
    return text;
}

