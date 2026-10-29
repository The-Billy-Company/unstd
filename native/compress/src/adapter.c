#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <stdint.h>
#include <string.h>

#include "libdeflate.h"
#include "stream.h"
#include "module.h"

/* ISIZE is an untrusted hint and wraps at 4 GiB. Larger members, unusual
 * buffers, concatenations and unsupported framing use the streaming reader.
 * A forged trailer must not reserve gigabytes before it is validated. */
#define MAX_OUTPUT_BYTES ((uint32_t)64 * 1024 * 1024)
#define MIN_HINT_BUDGET ((size_t)64 * 1024)
#define MAX_EXPANSION ((size_t)1032)
#define RELEASE_GIL_BYTES ((uint32_t)64 * 1024)

static void
module_free(void *module)
{
    ModuleState *state = PyModule_GetState((PyObject *)module);
    if (state != NULL) {
        libdeflate_free_decompressor(state->small_decoder);
        state->small_decoder = NULL;
        unstd_ng_api_free(state->ng_api);
        state->ng_api = NULL;
    }
}

static PyObject *
decompress(PyObject *module, PyObject *data)
{
    if (!PyBytes_CheckExact(data)) {
        Py_RETURN_NONE;
    }

    const unsigned char *input =
        (const unsigned char *)PyBytes_AS_STRING(data);
    Py_ssize_t input_size = PyBytes_GET_SIZE(data);
    if (input_size < 18 || input[0] != 0x1f || input[1] != 0x8b
            || input[2] != 8) {
        Py_RETURN_NONE;
    }
    /* libdeflate skips FHCRC validation. CPython 3.15 verifies it, while
     * older interpreters ignore it. Let the running gzip parser own that
     * policy and its exact diagnostics, including every optional header field. */
    if (input[3] & 2) {
        Py_RETURN_NONE;
    }
    uint32_t output_size =
        (uint32_t)input[input_size - 4]
        | ((uint32_t)input[input_size - 3] << 8)
        | ((uint32_t)input[input_size - 2] << 16)
        | ((uint32_t)input[input_size - 1] << 24);
    if (output_size > MAX_OUTPUT_BYTES) {
        Py_RETURN_NONE;
    }
    /* Every valid DEFLATE match emits at most 258 bytes with at least two
     * bits of coding. This loose bound permits highly compressible members,
     * while keeping a tiny malformed input's speculative allocation small.
     * Saturate before multiplication so the check works on 32-bit builds. */
    size_t compressed_size = (size_t)input_size - 18;
    size_t hint_budget = MAX_OUTPUT_BYTES;
    if (compressed_size <= MAX_OUTPUT_BYTES / MAX_EXPANSION) {
        hint_budget = compressed_size * MAX_EXPANSION;
        if (hint_budget < MIN_HINT_BUDGET) {
            hint_budget = MIN_HINT_BUDGET;
        }
    }
    if (output_size > hint_budget) {
        Py_RETURN_NONE;
    }

    PyObject *output = PyBytes_FromStringAndSize(NULL, output_size);
    if (output == NULL) {
        return NULL;
    }
    int cached = output_size < RELEASE_GIL_BYTES;
    struct libdeflate_decompressor *decoder;
    if (cached) {
        ModuleState *state = PyModule_GetState(module);
        if (state == NULL) {
            Py_DECREF(output);
            return NULL;
        }
        if (state->small_decoder == NULL) {
            state->small_decoder = libdeflate_alloc_decompressor();
        }
        decoder = state->small_decoder;
    }
    else {
        decoder = libdeflate_alloc_decompressor();
    }
    if (decoder == NULL) {
        Py_DECREF(output);
        return PyErr_NoMemory();
    }

    size_t input_used = 0;
    size_t output_used = 0;
    enum libdeflate_result result;
    char *output_buffer = PyBytes_AS_STRING(output);
    if (output_size >= RELEASE_GIL_BYTES) {
        Py_BEGIN_ALLOW_THREADS
        result = libdeflate_gzip_decompress_ex(
            decoder, input, (size_t)input_size,
            output_buffer, output_size, &input_used, &output_used);
        Py_END_ALLOW_THREADS
    }
    else {
        /* Exact immutable input and no GIL release or Python callbacks make
         * this interpreter's small decoder safe to reuse between calls. */
        result = libdeflate_gzip_decompress_ex(
            decoder, input, (size_t)input_size,
            output_buffer, output_size, &input_used, &output_used);
    }
    if (!cached) {
        libdeflate_free_decompressor(decoder);
    }

    /* The native decoder validates framing, CRC and ISIZE. It stops at the
     * first member; only an entirely consumed member may be returned here.
     * Replaying every other result preserves the interpreter's exact errors
     * and its treatment of reserved header bits and checksums. */
    if (result != LIBDEFLATE_SUCCESS || output_used != output_size) {
        Py_DECREF(output);
        Py_RETURN_NONE;
    }
    while (input_used < (size_t)input_size && input[input_used] == 0) {
        input_used++;
    }
    if (input_used != (size_t)input_size) {
        Py_DECREF(output);
        Py_RETURN_NONE;
    }
    return output;
}

static PyObject *
checksum(PyObject *module, PyObject *const *args, Py_ssize_t nargs)
{
    (void)module;
    if (nargs < 1 || nargs > 2) {
        PyErr_SetString(PyExc_TypeError, "crc32 expects one or two arguments");
        return NULL;
    }
    unsigned long initial = 0;
    if (nargs == 2) {
        initial = PyLong_AsUnsignedLongMask(args[1]);
        if (initial == (unsigned long)-1 && PyErr_Occurred()) {
            return NULL;
        }
    }
    Py_buffer view;
    if (PyObject_GetBuffer(args[0], &view, PyBUF_SIMPLE) < 0) {
        return NULL;
    }
    uint32_t result = libdeflate_crc32((uint32_t)initial,
                                     view.buf, (size_t)view.len);
    PyBuffer_Release(&view);
    return PyLong_FromUnsignedLong(result);
}

static int
prefer_compression(const unsigned char *input, Py_ssize_t size)
{
    /* This selection reads only immutable held input before any compression.
     * Low-entropy samples retain the existing fast NG run encoder. */
    if (size < 256) return 0;
    uint32_t samples = size < 2048 ? (uint32_t)size : 2048;
    uint16_t counts[256] = {0};
    uint32_t distinct = 0;
    uint32_t limit = (samples * 64U + 99U) / 100U;
    for (uint32_t i = 0; i < samples; ++i) {
        if (counts[input[i]]++ == 0) ++distinct;
        if (counts[input[i]] >= limit) return 0;
    }
    if (distinct < 24) return 0;
    for (uint32_t period = 1; period <= 32; ++period) {
        if (memcmp(input, input + period, samples - period) == 0) return 0;
    }
    return 1;
}

static int
ascii_bytes(const unsigned char *input, Py_ssize_t size)
{
    uint64_t combined = 0;
    Py_ssize_t words = size / 8;
    for (Py_ssize_t i = 0; i < words; ++i) {
        uint64_t value;
        memcpy(&value, input + i * 8, sizeof(value));
        combined |= value;
    }
    for (Py_ssize_t i = words * 8; i < size; ++i) combined |= input[i];
    return (combined & UINT64_C(0x8080808080808080)) == 0;
}

static int
compact_json_prefix(const unsigned char *input, Py_ssize_t size)
{
    uint32_t samples = size < 2048 ? (uint32_t)size : 2048;
    if (!ascii_bytes(input, size)) return 0;
    uint32_t start = 0;
    while (start < samples && (input[start] == ' ' || input[start] == '\t')) ++start;
    if (start == samples || (input[start] != '{' && input[start] != '[')) return 0;
    for (uint32_t i = 0; i < samples; ++i) {
        if (input[i] == '\n' || input[i] == '\r') return 0;
    }
    return 1;
}

static PyObject *
compress_buffer(PyObject *module, PyObject *const *args, Py_ssize_t nargs)
{
    (void)module;
    if (nargs != 2) {
        PyErr_SetString(PyExc_TypeError, "compress expects data and level");
        return NULL;
    }
    if (!PyBytes_CheckExact(args[0]) || !PyLong_CheckExact(args[1])) {
        Py_RETURN_NONE;
    }
    long level = PyLong_AsLong(args[1]);
    if (level == -1 && PyErr_Occurred()) {
        PyErr_Clear();
        Py_RETURN_NONE;
    }
    if ((level != 9 && level != 6)
            || !prefer_compression((const unsigned char *)PyBytes_AS_STRING(args[0]), PyBytes_GET_SIZE(args[0]))
            || (level == 6 && !compact_json_prefix((const unsigned char *)PyBytes_AS_STRING(args[0]), PyBytes_GET_SIZE(args[0])))) {
        Py_RETURN_NONE;
    }
    struct libdeflate_options options = {
        .sizeof_options = sizeof(options),
        .malloc_func = PyMem_RawMalloc,
        .free_func = PyMem_RawFree,
    };
    struct libdeflate_compressor *encoder =
        libdeflate_alloc_compressor_ex(level == 9 ? 6 : 1, &options);
    if (encoder == NULL) {
        return PyErr_NoMemory();
    }
    Py_ssize_t input_size = PyBytes_GET_SIZE(args[0]);
    size_t bound = libdeflate_gzip_compress_bound(encoder, (size_t)input_size);
    if (bound < 18 || bound > PY_SSIZE_T_MAX) {
        libdeflate_free_compressor(encoder);
        return PyErr_NoMemory();
    }
    PyObject *output = PyBytes_FromStringAndSize(NULL, (Py_ssize_t)bound);
    if (output == NULL) {
        libdeflate_free_compressor(encoder);
        return NULL;
    }
    const char *input = PyBytes_AS_STRING(args[0]);
    char *destination = PyBytes_AS_STRING(output);
    size_t used;
    if (input_size >= RELEASE_GIL_BYTES) {
        Py_BEGIN_ALLOW_THREADS
        used = libdeflate_gzip_compress(encoder, input, (size_t)input_size,
                                        destination, bound);
        Py_END_ALLOW_THREADS
    }
    else {
        used = libdeflate_gzip_compress(encoder, input, (size_t)input_size,
                                        destination, bound);
    }
    libdeflate_free_compressor(encoder);
    if (used == 0 || used > bound) {
        Py_DECREF(output);
        PyErr_SetString(PyExc_RuntimeError, "bounded native gzip compression failed");
        return NULL;
    }
    destination[8] = level == 9 ? 2 : level == 1 ? 4 : 0;
    destination[9] = (char)255;
    if (_PyBytes_Resize(&output, (Py_ssize_t)used) < 0) {
        return NULL;
    }
    return output;
}

static PyMethodDef methods[] = {
    {"compress", (PyCFunction)(void (*)(void))compress_buffer, METH_FASTCALL,
     "Encode an exact byte buffer with a zero gzip timestamp."},
    {"_ng_runtime_version", unstd_ng_runtime_version, METH_NOARGS,
     "Return the bound native ng ABI version, if available."},
    {"_configure_ng", unstd_configure_ng, METH_O,
     "Bind an already loaded native ng provider once."},
    {"decompress", decompress, METH_O,
     "Decode one validated gzip member, or return None for a streaming replay."},
    {"crc32", (PyCFunction)(void (*)(void))checksum, METH_FASTCALL,
     "Update the gzip CRC32 with the native checksum kernel."},
    {NULL, NULL, 0, NULL}
};

static struct PyModuleDef definition = {
    PyModuleDef_HEAD_INIT,
    "_gzip_native",
    "Native gzip buffer decompression, bounded inflation and checksums.",
    sizeof(ModuleState),
    methods,
    NULL,
    NULL,
    NULL,
    module_free
};

PyMODINIT_FUNC
PyInit__gzip_native(void)
{
    PyObject *module = PyModule_Create(&definition);
    if (module == NULL) {
        return NULL;
    }
    if (unstd_add_inflater(module) < 0
            || unstd_add_isal_metadata(module) < 0
            || PyModule_AddStringConstant(module, "ZLIB_RUNTIME_VERSION",
                                          unstd_inflate_version()) < 0
            || PyModule_AddStringConstant(module, "LIBDEFLATE_VERSION",
                                          LIBDEFLATE_VERSION_STRING) < 0) {
        Py_DECREF(module);
        return NULL;
    }
    return module;
}
