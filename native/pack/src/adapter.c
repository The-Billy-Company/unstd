/* Immutable numeric frame validation; no NumPy ABI or application policy. */
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <stdint.h>
#include <string.h>

#if defined(__aarch64__) && defined(__ARM_NEON)
#include <arm_neon.h>
#define PACK_NEON 1
#elif defined(__SSE2__)
#include <emmintrin.h>
#define PACK_SSE2 1
#endif

#define SCAN_BYTES ((Py_ssize_t)1 << 20)
#define THREAD_BYTES ((Py_ssize_t)1 << 16)

static int
invalid(const char *message)
{
    PyErr_SetString(PyExc_ValueError, message);
    return -1;
}

static int
code(PyObject *object, const char *accepted, const char *message)
{
    if (!PyUnicode_Check(object)) {
        PyErr_SetString(PyExc_TypeError, "numeric format codes must be strings");
        return -1;
    }
    if (PyUnicode_GetLength(object) != 1) return invalid(message);
    Py_UCS4 value = PyUnicode_ReadChar(object, 0);
    if (value == 0 || value > 127 || strchr(accepted, (int)value) == NULL)
        return invalid(message);
    return (int)value;
}

static int
dimension(PyObject *object, Py_ssize_t *result)
{
    PyObject *integer = PyNumber_Index(object);
    if (integer == NULL) return -1;
    *result = PyLong_AsSsize_t(integer);
    Py_DECREF(integer);
    if (*result == -1 && PyErr_Occurred()) {
        if (PyErr_ExceptionMatches(PyExc_OverflowError))
            return invalid("numeric frame dimensions exceed platform limits");
        return -1;
    }
    return 0;
}

static unsigned int
width(int character)
{
    switch (character) {
        case 'b': case 'B': return 1;
        case 'h': case 'H': case 'e': return 2;
        case 'i': case 'I': case 'f': return 4;
        default: return 8;
    }
}

static int
native_little(void)
{
    const uint16_t value = 1;
    return *(const unsigned char *)&value != 0;
}

static uint64_t
word(const unsigned char *data, unsigned int size, int little)
{
    uint64_t value = 0;
    if (little) {
        for (unsigned int i = 0; i < size; i++)
            value |= (uint64_t)data[i] << (8 * i);
    } else {
        for (unsigned int i = 0; i < size; i++) value = (value << 8) | data[i];
    }
    return value;
}

static int
finite_scalar(const unsigned char *data, Py_ssize_t count,
              unsigned int size, int little)
{
    const uint64_t mask = size == 2 ? UINT64_C(0x7c00) :
                          size == 4 ? UINT64_C(0x7f800000) :
                                      UINT64_C(0x7ff0000000000000);
    for (Py_ssize_t i = 0; i < count; i++, data += size)
        if ((word(data, size, little) & mask) == mask) return 0;
    return 1;
}

static int
finite_f32(const unsigned char *data, Py_ssize_t count, int little)
{
    /* Independent reductions avoid a dependency-bound one-vector scan. */
#if defined(PACK_NEON)
    if (native_little()) {
        uint32x4_t mask = vdupq_n_u32(UINT32_C(0x7f800000));
        uint32x4_t bad = vdupq_n_u32(0), b1 = bad, b2 = bad, b3 = bad;
        if (little) {
            for (; count >= 16; count -= 16, data += 64) {
                uint32x4_t a = vreinterpretq_u32_u8(vld1q_u8(data));
                uint32x4_t b = vreinterpretq_u32_u8(vld1q_u8(data + 16));
                uint32x4_t c = vreinterpretq_u32_u8(vld1q_u8(data + 32));
                uint32x4_t d = vreinterpretq_u32_u8(vld1q_u8(data + 48));
                bad = vorrq_u32(bad, vceqq_u32(vandq_u32(a, mask), mask));
                b1 = vorrq_u32(b1, vceqq_u32(vandq_u32(b, mask), mask));
                b2 = vorrq_u32(b2, vceqq_u32(vandq_u32(c, mask), mask));
                b3 = vorrq_u32(b3, vceqq_u32(vandq_u32(d, mask), mask));
            }
        } else {
            for (; count >= 16; count -= 16, data += 64) {
                uint32x4_t a = vreinterpretq_u32_u8(vrev32q_u8(vld1q_u8(data)));
                uint32x4_t b = vreinterpretq_u32_u8(vrev32q_u8(vld1q_u8(data + 16)));
                uint32x4_t c = vreinterpretq_u32_u8(vrev32q_u8(vld1q_u8(data + 32)));
                uint32x4_t d = vreinterpretq_u32_u8(vrev32q_u8(vld1q_u8(data + 48)));
                bad = vorrq_u32(bad, vceqq_u32(vandq_u32(a, mask), mask));
                b1 = vorrq_u32(b1, vceqq_u32(vandq_u32(b, mask), mask));
                b2 = vorrq_u32(b2, vceqq_u32(vandq_u32(c, mask), mask));
                b3 = vorrq_u32(b3, vceqq_u32(vandq_u32(d, mask), mask));
            }
        }
        bad = vorrq_u32(vorrq_u32(bad, b1), vorrq_u32(b2, b3));
        for (; count >= 4; count -= 4, data += 16) {
            uint8x16_t raw = vld1q_u8(data);
            if (!little) raw = vrev32q_u8(raw);
            uint32x4_t bits = vreinterpretq_u32_u8(raw);
            bad = vorrq_u32(bad, vceqq_u32(vandq_u32(bits, mask), mask));
        }
        if (vmaxvq_u32(bad) != 0) return 0;
    }
#elif defined(PACK_SSE2)
    if (little && native_little()) {
        __m128i mask = _mm_set1_epi32(0x7f800000), bad = _mm_setzero_si128();
        __m128i b1 = bad, b2 = bad, b3 = bad;
        for (; count >= 16; count -= 16, data += 64) {
            __m128i a, b, c, d;
            memcpy(&a, data, sizeof(a));
            memcpy(&b, data + 16, sizeof(b));
            memcpy(&c, data + 32, sizeof(c));
            memcpy(&d, data + 48, sizeof(d));
            bad = _mm_or_si128(bad, _mm_cmpeq_epi32(_mm_and_si128(a, mask), mask));
            b1 = _mm_or_si128(b1, _mm_cmpeq_epi32(_mm_and_si128(b, mask), mask));
            b2 = _mm_or_si128(b2, _mm_cmpeq_epi32(_mm_and_si128(c, mask), mask));
            b3 = _mm_or_si128(b3, _mm_cmpeq_epi32(_mm_and_si128(d, mask), mask));
        }
        bad = _mm_or_si128(_mm_or_si128(bad, b1), _mm_or_si128(b2, b3));
        for (; count >= 4; count -= 4, data += 16) {
            __m128i bits;
            memcpy(&bits, data, sizeof(bits));
            bad = _mm_or_si128(bad, _mm_cmpeq_epi32(_mm_and_si128(bits, mask), mask));
        }
        if (_mm_movemask_epi8(bad) != 0) return 0;
    }
#endif
    return finite_scalar(data, count, 4, little);
}

static int
finite_f64(const unsigned char *data, Py_ssize_t count, int little)
{
#if defined(PACK_NEON)
    if (native_little()) {
        uint64x2_t mask = vdupq_n_u64(UINT64_C(0x7ff0000000000000));
        uint64x2_t bad = vdupq_n_u64(0), b1 = bad, b2 = bad, b3 = bad;
        if (little) {
            for (; count >= 8; count -= 8, data += 64) {
                uint64x2_t a = vreinterpretq_u64_u8(vld1q_u8(data));
                uint64x2_t b = vreinterpretq_u64_u8(vld1q_u8(data + 16));
                uint64x2_t c = vreinterpretq_u64_u8(vld1q_u8(data + 32));
                uint64x2_t d = vreinterpretq_u64_u8(vld1q_u8(data + 48));
                bad = vorrq_u64(bad, vceqq_u64(vandq_u64(a, mask), mask));
                b1 = vorrq_u64(b1, vceqq_u64(vandq_u64(b, mask), mask));
                b2 = vorrq_u64(b2, vceqq_u64(vandq_u64(c, mask), mask));
                b3 = vorrq_u64(b3, vceqq_u64(vandq_u64(d, mask), mask));
            }
        } else {
            for (; count >= 8; count -= 8, data += 64) {
                uint64x2_t a = vreinterpretq_u64_u8(vrev64q_u8(vld1q_u8(data)));
                uint64x2_t b = vreinterpretq_u64_u8(vrev64q_u8(vld1q_u8(data + 16)));
                uint64x2_t c = vreinterpretq_u64_u8(vrev64q_u8(vld1q_u8(data + 32)));
                uint64x2_t d = vreinterpretq_u64_u8(vrev64q_u8(vld1q_u8(data + 48)));
                bad = vorrq_u64(bad, vceqq_u64(vandq_u64(a, mask), mask));
                b1 = vorrq_u64(b1, vceqq_u64(vandq_u64(b, mask), mask));
                b2 = vorrq_u64(b2, vceqq_u64(vandq_u64(c, mask), mask));
                b3 = vorrq_u64(b3, vceqq_u64(vandq_u64(d, mask), mask));
            }
        }
        bad = vorrq_u64(vorrq_u64(bad, b1), vorrq_u64(b2, b3));
        for (; count >= 2; count -= 2, data += 16) {
            uint8x16_t raw = vld1q_u8(data);
            if (!little) raw = vrev64q_u8(raw);
            uint64x2_t bits = vreinterpretq_u64_u8(raw);
            bad = vorrq_u64(bad, vceqq_u64(vandq_u64(bits, mask), mask));
        }
        if (vmaxvq_u32(vreinterpretq_u32_u64(bad)) != 0) return 0;
    }
#elif defined(PACK_SSE2)
    if (little && native_little()) {
        __m128i mask = _mm_set1_epi32(0x7ff00000), bad = _mm_setzero_si128();
        __m128i b1 = bad, b2 = bad, b3 = bad;
        for (; count >= 8; count -= 8, data += 64) {
            __m128i a, b, c, d;
            memcpy(&a, data, sizeof(a));
            memcpy(&b, data + 16, sizeof(b));
            memcpy(&c, data + 32, sizeof(c));
            memcpy(&d, data + 48, sizeof(d));
            bad = _mm_or_si128(bad, _mm_cmpeq_epi32(_mm_and_si128(a, mask), mask));
            b1 = _mm_or_si128(b1, _mm_cmpeq_epi32(_mm_and_si128(b, mask), mask));
            b2 = _mm_or_si128(b2, _mm_cmpeq_epi32(_mm_and_si128(c, mask), mask));
            b3 = _mm_or_si128(b3, _mm_cmpeq_epi32(_mm_and_si128(d, mask), mask));
        }
        bad = _mm_or_si128(_mm_or_si128(bad, b1), _mm_or_si128(b2, b3));
        for (; count >= 2; count -= 2, data += 16) {
            __m128i bits;
            memcpy(&bits, data, sizeof(bits));
            bad = _mm_or_si128(bad, _mm_cmpeq_epi32(_mm_and_si128(bits, mask), mask));
        }
        /* Binary64's exponent is in the high 32-bit word of each pair. */
        if ((_mm_movemask_epi8(bad) & 0xf0f0) != 0) return 0;
    }
#endif
    return finite_scalar(data, count, 8, little);
}

static int
indices(const unsigned char *data, Py_ssize_t start, Py_ssize_t end,
        unsigned int size, int little, int signed_values, int allow_empty,
        uint64_t rows, uint64_t *previous)
{
    const uint64_t sign = UINT64_C(1) << (8 * size - 1);
    for (Py_ssize_t i = start; i < end; i++, data += size) {
        uint64_t value = word(data, size, little);
        if ((signed_values && (value & sign) != 0) || value > rows ||
            (i == 0 ? value != 0 :
             allow_empty ? value < *previous : value <= *previous)) return 0;
        *previous = value;
    }
    return 1;
}

static PyObject *
validate(PyObject *module, PyObject *args)
{
    (void)module;
    PyObject *data, *index_count_object, *columns_object, *value_code, *value_order;
    PyObject *index_code, *index_order, *finite_object, *empty_object;
    if (!PyArg_ParseTuple(args, "OOOOOOOOO:validate", &data, &index_count_object,
                          &columns_object, &value_code, &value_order, &index_code,
                          &index_order, &finite_object, &empty_object)) return NULL;
    if (!PyBytes_CheckExact(data)) {
        PyErr_SetString(PyExc_TypeError, "numeric frame validation requires exact bytes");
        return NULL;
    }
    if (!PyBool_Check(finite_object) || !PyBool_Check(empty_object)) {
        PyErr_SetString(PyExc_TypeError, "numeric validation flags must be bool");
        return NULL;
    }
    int vc = code(value_code, "bBhHiIqQefd", "unsupported numeric value code");
    if (vc < 0) return NULL;
    int vo = code(value_order, "<>=", "unsupported numeric value byte order");
    if (vo < 0) return NULL;
    int ic = code(index_code, "bBhHiIqQ", "unsupported numeric index code");
    if (ic < 0) return NULL;
    int io = code(index_order, "<>=", "unsupported numeric index byte order");
    if (io < 0) return NULL;
    Py_ssize_t index_count, columns;
    if (dimension(index_count_object, &index_count) < 0 ||
        dimension(columns_object, &columns) < 0) return NULL;
    unsigned int value_size = width(vc), index_size = width(ic);
    Py_ssize_t length = PyBytes_GET_SIZE(data);
    if (index_count < 1 || columns < 1 ||
        columns > PY_SSIZE_T_MAX / value_size || index_count > length / index_size) {
        invalid("numeric frame dimensions do not fit its byte storage");
        return NULL;
    }
    Py_ssize_t prefix = index_count * index_size, row_size = columns * value_size;
    Py_ssize_t remaining = length - prefix;
    if (remaining % row_size != 0) {
        invalid("numeric frame has a partial matrix row");
        return NULL;
    }
    Py_ssize_t rows = remaining / row_size;
    int vl = vo == '=' ? native_little() : vo == '<';
    int il = io == '=' ? native_little() : io == '<';
    int signed_values = strchr("bhiq", ic) != NULL;
    int allow_empty = empty_object == Py_True;
    const unsigned char *bytes = (const unsigned char *)PyBytes_AS_STRING(data);
    uint64_t previous = 0;
    for (Py_ssize_t start = 0; start < index_count;) {
        Py_ssize_t count = index_count - start, limit = SCAN_BYTES / index_size;
        if (count > limit) count = limit;
        int valid;
        if (count * index_size >= THREAD_BYTES) {
            Py_BEGIN_ALLOW_THREADS
            valid = indices(bytes + start * index_size, start, start + count,
                            index_size, il, signed_values, allow_empty,
                            (uint64_t)rows, &previous);
            Py_END_ALLOW_THREADS
        } else {
            valid = indices(bytes + start * index_size, start, start + count,
                            index_size, il, signed_values, allow_empty,
                            (uint64_t)rows, &previous);
        }
        if (PyErr_CheckSignals() < 0) return NULL;
        if (!valid) {
            invalid("numeric frame indices must span its rows monotonically");
            return NULL;
        }
        start += count;
    }
    if (previous != (uint64_t)rows) {
        invalid("numeric frame indices do not end at its row count");
        return NULL;
    }
    if (finite_object == Py_True && strchr("efd", vc) != NULL) {
        const unsigned char *values = bytes + prefix;
        while (remaining != 0) {
            Py_ssize_t amount = remaining > SCAN_BYTES ? SCAN_BYTES : remaining;
            Py_ssize_t count = amount / value_size;
            int valid;
            if (amount >= THREAD_BYTES) {
                Py_BEGIN_ALLOW_THREADS
                valid = vc == 'f' ? finite_f32(values, count, vl) :
                        vc == 'd' ? finite_f64(values, count, vl) :
                                    finite_scalar(values, count, value_size, vl);
                Py_END_ALLOW_THREADS
            } else {
                valid = vc == 'f' ? finite_f32(values, count, vl) :
                        vc == 'd' ? finite_f64(values, count, vl) :
                                    finite_scalar(values, count, value_size, vl);
            }
            if (PyErr_CheckSignals() < 0) return NULL;
            if (!valid) {
                invalid("numeric frame contains nonfinite values");
                return NULL;
            }
            values += amount;
            remaining -= amount;
        }
    }
    return PyLong_FromSsize_t(rows);
}

static PyMethodDef methods[] = {
    {"validate", validate, METH_VARARGS,
     "Validate immutable index-prefixed numeric rows and return their row count."},
    {NULL, NULL, 0, NULL}
};

static struct PyModuleDef definition = {
    PyModuleDef_HEAD_INIT, "_pack_native", "Immutable numeric frame validation.",
    0, methods, NULL, NULL, NULL, NULL
};

PyMODINIT_FUNC
PyInit__pack_native(void)
{
    PyObject *module = PyModule_Create(&definition);
    if (module == NULL) return NULL;
    if (PyModule_AddIntConstant(module, "KERNEL_VERSION", 1) < 0) {
        Py_DECREF(module);
        return NULL;
    }
    return module;
}
