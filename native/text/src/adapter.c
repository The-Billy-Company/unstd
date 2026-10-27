/* Bounded exact-string text kernels.
 *
 * Inputs stay strongly held by the call, strings are immutable, and the GIL
 * remains held throughout. No Python callbacks or borrowed mutable-container
 * references are used. Unsupported input shapes/work budgets defer to Python.
 */
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <stdint.h>
#include <string.h>

#define MAX_PATTERN 65536
#define MAX_TEXT 262144
#define MAX_WORD_STEPS 4194304
#define MAX_AFFIX_STEPS MAX_WORD_STEPS
#define MAX_JARO_LENGTH 65536
#define MAX_JARO_PAIRS 4194304
#define MAX_ALPHABET 256
#define HASH_SLOTS 512
#define EMPTY_CHAR UINT32_MAX

typedef struct {
    const void *data;
    int kind;
    Py_ssize_t length;
} Text;

static Text
text_view(PyObject *value)
{
    Text result = {PyUnicode_DATA(value), PyUnicode_KIND(value),
                   PyUnicode_GET_LENGTH(value)};
    return result;
}

static inline Py_UCS4
character(Text text, Py_ssize_t offset)
{
    return PyUnicode_READ(text.kind, text.data, offset);
}

static size_t
character_slot(Py_UCS4 value, const Py_UCS4 *keys)
{
    size_t slot = ((uint32_t)value * UINT32_C(2654435761)) & (HASH_SLOTS - 1);
    while (keys[slot] != EMPTY_CHAR && keys[slot] != value) {
        slot = (slot + 1) & (HASH_SLOTS - 1);
    }
    return slot;
}

static PyObject *
distance(PyObject *module, PyObject *const *args, Py_ssize_t nargs)
{
    (void)module;
    if (nargs != 2) {
        PyErr_SetString(PyExc_TypeError, "distance() takes exactly two arguments");
        return NULL;
    }
    if (!PyUnicode_CheckExact(args[0]) || !PyUnicode_CheckExact(args[1])) {
        Py_RETURN_NOTIMPLEMENTED;
    }
    Text pattern = text_view(args[0]);
    Text text = text_view(args[1]);
    /* Equal ends can be removed without changing Levenshtein distance. Work
     * on immutable views rather than copying substrings, and measure the
     * expensive bit-vector/alphabet budgets on only the remaining problem.
     * Bound the scan separately so huge common ends cannot hold the GIL for
     * unbounded native work before the ordinary kernel limits are checked. */
    Py_ssize_t shared = pattern.length < text.length
        ? pattern.length : text.length;
    Py_ssize_t prefix = 0;
    Py_ssize_t suffix = 0;
    size_t affix_steps = 0;
    while (prefix < shared) {
        if (affix_steps == MAX_AFFIX_STEPS) {
            Py_RETURN_NOTIMPLEMENTED;
        }
        affix_steps++;
        if (character(pattern, prefix) != character(text, prefix)) {
            break;
        }
        prefix++;
    }
    while (suffix < shared - prefix) {
        if (affix_steps == MAX_AFFIX_STEPS) {
            Py_RETURN_NOTIMPLEMENTED;
        }
        affix_steps++;
        if (character(pattern, pattern.length - suffix - 1) !=
            character(text, text.length - suffix - 1)) {
            break;
        }
        suffix++;
    }
    pattern.data = (const char *)pattern.data + (size_t)prefix * pattern.kind;
    text.data = (const char *)text.data + (size_t)prefix * text.kind;
    pattern.length -= prefix + suffix;
    text.length -= prefix + suffix;
    if (pattern.length > text.length) {
        Text temporary = pattern;
        pattern = text;
        text = temporary;
    }
    if (pattern.length == 0) {
        return PyLong_FromSsize_t(text.length);
    }
    if (pattern.length > MAX_PATTERN || text.length > MAX_TEXT) {
        Py_RETURN_NOTIMPLEMENTED;
    }
    size_t words = ((size_t)pattern.length + 63) / 64;
    if ((size_t)text.length > MAX_WORD_STEPS / words) {
        Py_RETURN_NOTIMPLEMENTED;
    }

    Py_UCS4 keys[HASH_SLOTS];
    unsigned short slots[HASH_SLOTS];
    for (size_t i = 0; i < HASH_SLOTS; i++) {
        keys[i] = EMPTY_CHAR;
    }
    size_t alphabet = 0;
    for (Py_ssize_t i = 0; i < pattern.length; i++) {
        Py_UCS4 value = character(pattern, i);
        size_t slot = character_slot(value, keys);
        if (keys[slot] == EMPTY_CHAR) {
            if (alphabet == MAX_ALPHABET) {
                Py_RETURN_NOTIMPLEMENTED;
            }
            keys[slot] = value;
            slots[slot] = (unsigned short)alphabet++;
        }
    }

    /* These bounds cap the allocation at 261 * 1024 uint64_t elements.
     * Check the product as well, so later changes cannot invalidate it. */
    if (words > SIZE_MAX / (alphabet + 5) / sizeof(uint64_t)) {
        return PyErr_NoMemory();
    }
    size_t count = (alphabet + 5) * words;
    uint64_t single_word[MAX_ALPHABET + 5];
    uint64_t *storage;
    if (words == 1) {
        storage = single_word;
        memset(storage, 0, count * sizeof(uint64_t));
    }
    else {
        storage = PyMem_Calloc(count, sizeof(uint64_t));
        if (storage == NULL) {
            return PyErr_NoMemory();
        }
    }
    uint64_t *positive = storage + alphabet * words;
    uint64_t *negative = positive + words;
    uint64_t *differences = negative + words;
    uint64_t *horizontal_positive = differences + words;
    uint64_t *horizontal_negative = horizontal_positive + words;
    for (Py_ssize_t i = 0; i < pattern.length; i++) {
        size_t slot = character_slot(character(pattern, i), keys);
        storage[(size_t)slots[slot] * words + (size_t)i / 64]
            |= UINT64_C(1) << ((size_t)i % 64);
    }
    size_t top_offset = ((size_t)pattern.length - 1) % 64;
    uint64_t high_bit = UINT64_C(1) << top_offset;
    size_t remainder = (size_t)pattern.length % 64;
    uint64_t final_mask = remainder == 0
        ? UINT64_MAX : (UINT64_C(1) << remainder) - 1;
    for (size_t word = 0; word < words; word++) {
        positive[word] = UINT64_MAX;
    }
    positive[words - 1] &= final_mask;
    Py_ssize_t score = pattern.length;

    for (Py_ssize_t i = 0; i < text.length; i++) {
        size_t slot = character_slot(character(text, i), keys);
        const uint64_t *matches = keys[slot] == EMPTY_CHAR
            ? NULL : storage + (size_t)slots[slot] * words;
        uint64_t carry = 0;
        for (size_t word = 0; word < words; word++) {
            uint64_t equal = matches == NULL ? 0 : matches[word];
            uint64_t addend = equal & positive[word];
            uint64_t sum = addend + positive[word];
            uint64_t next_carry = sum < addend;
            uint64_t with_carry = sum + carry;
            carry = next_carry | (with_carry < sum);
            uint64_t changed = (with_carry ^ positive[word])
                | equal | negative[word];
            differences[word] = changed;
            horizontal_positive[word] = negative[word]
                | ~(changed | positive[word]);
            horizontal_negative[word] = changed & positive[word];
        }
        score += (horizontal_positive[words - 1] & high_bit) != 0;
        score -= (horizontal_negative[words - 1] & high_bit) != 0;
        uint64_t carry_positive = 1;
        uint64_t carry_negative = 0;
        for (size_t word = 0; word < words; word++) {
            uint64_t hp = horizontal_positive[word];
            uint64_t hn = horizontal_negative[word];
            uint64_t shifted_positive = (hp << 1) | carry_positive;
            uint64_t shifted_negative = (hn << 1) | carry_negative;
            carry_positive = hp >> 63;
            carry_negative = hn >> 63;
            positive[word] = shifted_negative
                | ~(differences[word] | shifted_positive);
            negative[word] = differences[word] & shifted_positive;
        }
        positive[words - 1] &= final_mask;
        negative[words - 1] &= final_mask;
    }
    if (storage != single_word) {
        PyMem_Free(storage);
    }
    return PyLong_FromSsize_t(score);
}

static PyObject *
jaro(PyObject *module, PyObject *const *args, Py_ssize_t nargs)
{
    (void)module;
    if (nargs != 2) {
        PyErr_SetString(PyExc_TypeError, "jaro() takes exactly two arguments");
        return NULL;
    }
    if (!PyUnicode_CheckExact(args[0]) || !PyUnicode_CheckExact(args[1])) {
        Py_RETURN_NOTIMPLEMENTED;
    }
    Text a = text_view(args[0]);
    Text b = text_view(args[1]);
    if (a.length > MAX_JARO_LENGTH || b.length > MAX_JARO_LENGTH) {
        Py_RETURN_NOTIMPLEMENTED;
    }
    if (a.length == 0 || b.length == 0) {
        return PyFloat_FromDouble(a.length == b.length ? 1.0 : 0.0);
    }
    if ((size_t)a.length > MAX_JARO_PAIRS / (size_t)b.length) {
        Py_RETURN_NOTIMPLEMENTED;
    }
    Py_ssize_t capacity = a.length < b.length ? a.length : b.length;
    if ((size_t)capacity > SIZE_MAX / sizeof(Py_UCS4)) {
        return PyErr_NoMemory();
    }
    unsigned char *taken = PyMem_Calloc((size_t)b.length, 1);
    if (taken == NULL) {
        return PyErr_NoMemory();
    }
    Py_UCS4 *matched = PyMem_Malloc((size_t)capacity * sizeof(Py_UCS4));
    if (matched == NULL) {
        PyMem_Free(taken);
        return PyErr_NoMemory();
    }
    Py_ssize_t window = (a.length > b.length ? a.length : b.length) / 2 - 1;
    if (window < 0) {
        window = 0;
    }
    Py_ssize_t matches = 0;
    for (Py_ssize_t i = 0; i < a.length; i++) {
        Py_UCS4 value = character(a, i);
        Py_ssize_t start = i > window ? i - window : 0;
        Py_ssize_t stop = i + window + 1;
        if (stop > b.length) {
            stop = b.length;
        }
        for (Py_ssize_t j = start; j < stop; j++) {
            if (!taken[j] && character(b, j) == value) {
                taken[j] = 1;
                matched[matches++] = value;
                break;
            }
        }
    }
    if (matches == 0) {
        PyMem_Free(matched);
        PyMem_Free(taken);
        return PyFloat_FromDouble(0.0);
    }
    Py_ssize_t mismatch = 0;
    Py_ssize_t index = 0;
    for (Py_ssize_t i = 0; i < b.length; i++) {
        if (taken[i]) {
            mismatch += character(b, i) != matched[index++];
        }
    }
    /* Match Python's separate division/addition operations and integer floor.
     * The build must not use floating-point contraction or fast-math flags. */
    double score = ((double)matches / (double)a.length
                    + (double)matches / (double)b.length
                    + (double)(matches - mismatch / 2) / (double)matches) / 3.0;
    PyMem_Free(matched);
    PyMem_Free(taken);
    return PyFloat_FromDouble(score);
}

static PyMethodDef methods[] = {
    {"distance", (PyCFunction)(void(*)(void))distance, METH_FASTCALL,
     PyDoc_STR("Bounded exact-string Levenshtein distance, or NotImplemented.")},
    {"jaro", (PyCFunction)(void(*)(void))jaro, METH_FASTCALL,
     PyDoc_STR("Bounded exact-string Jaro similarity, or NotImplemented.")},
    {NULL, NULL, 0, NULL}
};

static PyModuleDef_Slot module_slots[] = {
    {Py_mod_multiple_interpreters, Py_MOD_PER_INTERPRETER_GIL_SUPPORTED},
#if PY_VERSION_HEX >= 0x030D0000
    {Py_mod_gil, Py_MOD_GIL_USED},
#endif
    {0, NULL}
};

static struct PyModuleDef definition = {
    PyModuleDef_HEAD_INIT,
    "_text_native",
    "Bounded exact-string kernels with portable Python fallbacks.",
    0,
    methods,
    module_slots,
    NULL,
    NULL,
    NULL
};

PyMODINIT_FUNC
PyInit__text_native(void)
{
#ifdef Py_GIL_DISABLED
    PyErr_SetString(PyExc_ImportError, "native text kernels require a GIL build");
    return NULL;
#else
    return PyModuleDef_Init(&definition);
#endif
}
