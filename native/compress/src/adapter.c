#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <stdint.h>

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
     * and its permissive treatment of reserved header bits and checksums. */
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

static PyMethodDef methods[] = {
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
            || PyModule_AddStringConstant(module, "ZLIB_RUNTIME_VERSION",
                                          unstd_inflate_version()) < 0
            || PyModule_AddStringConstant(module, "LIBDEFLATE_VERSION",
                                          LIBDEFLATE_VERSION_STRING) < 0) {
        Py_DECREF(module);
        return NULL;
    }
    return module;
}
