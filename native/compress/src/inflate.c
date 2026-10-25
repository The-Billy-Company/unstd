/* Native codec ABI and checked allocator boundary. Optional platform routes
 * bind the already imported zlib-ng provider before any body decode. */
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <stdint.h>
#include <string.h>

#ifdef UNSTD_ZLIB_RS
#define inflateInit2_ unstd_zlib_rs_inflateInit2_
#define inflate unstd_zlib_rs_inflate
#define inflateEnd unstd_zlib_rs_inflateEnd
#define zlibVersion unstd_zlib_rs_zlibVersion
#endif

#include "zlib/zlib.h"
#include "stream.h"
#include "module.h"

#if defined(__linux__) && defined(__x86_64__) && defined(UNSTD_WITH_ISAL) \
        && !defined(UNSTD_ZLIB_RS)
#define UNSTD_LINUX_ISAL 1
#ifndef UNSTD_ISAL_VERSION
#error "UNSTD_WITH_ISAL requires a reviewed ISA-L version"
#endif
#ifndef UNSTD_ISAL_PATCH_SHA256
#error "UNSTD_WITH_ISAL requires a reviewed core patch digest"
#endif
#endif

#if defined(UNSTD_LINUX_ISAL) || (defined(__APPLE__) && \
        (defined(__aarch64__) || defined(__arm64__)) && !defined(UNSTD_ZLIB_RS))
#define UNSTD_ADAPTIVE_NG 1
#include <dlfcn.h>
#ifdef UNSTD_LINUX_ISAL
#include "igzip_lib.h"
#else
#include "shape.h"
#endif
#endif

static int
isal_cpu_supported(void)
{
#if defined(UNSTD_LINUX_ISAL) && (defined(__GNUC__) || defined(__clang__))
    __builtin_cpu_init();
    return __builtin_cpu_supports("sse4.2") && __builtin_cpu_supports("pclmul");
#else
    return 0;
#endif
}

static voidpf
allocate(voidpf opaque, uInt items, uInt size)
{
    (void)opaque;
    if (size != 0 && items > (size_t)PY_SSIZE_T_MAX / size) {
        return NULL;
    }
    /* RAW callbacks remain valid while the native loop releases the GIL. */
    return PyMem_RawMalloc((size_t)items * (size_t)size);
}

static void
release(voidpf opaque, voidpf pointer)
{
    (void)opaque;
    PyMem_RawFree(pointer);
}

#ifdef MY_ZCALLOC
/* Z_PREFIX gives the vendored library's default allocator symbols the same
 * private prefix as its codec. Retain the checked RAW allocation boundary. */
voidpf
zcalloc(voidpf opaque, unsigned items, unsigned size)
{
    return allocate(opaque, items, size);
}

void
zcfree(voidpf opaque, voidpf pointer)
{
    release(opaque, pointer);
}
#endif

#ifdef UNSTD_ADAPTIVE_NG
/* Public native zlib-ng 2.x ABI (not the compatibility z_stream ABI):
 * https://github.com/zlib-ng/zlib-ng/blob/2.3.3/zlib-ng.h.in
 * In particular, adler is uint32_t: sizeof is 104 on macOS arm64, whereas
 * z_stream is 112. Version and sizeof are checked before any payload use. */
typedef struct {
    const uint8_t *next_in;
    uint32_t avail_in;
    size_t total_in;
    uint8_t *next_out;
    uint32_t avail_out;
    size_t total_out;
    const char *msg;
    void *state;
    void *(*zalloc)(void *, unsigned int, unsigned int);
    void (*zfree)(void *, void *);
    void *opaque;
    int data_type;
    uint32_t adler;
    unsigned long reserved;
} NgStream;

typedef struct {
    void *handle;
    Py_ssize_t owners;
    int32_t (*init)(NgStream *, int32_t, const char *, int32_t);
    int32_t (*decode)(NgStream *, int32_t);
    int32_t (*end)(NgStream *);
    char version[32];
} NgAPI;

typedef struct {
    z_stream apple;
    NgStream ng;
    const NgAPI *api;
    int pending;
    int engine;
    int wbits;
#ifdef UNSTD_LINUX_ISAL
    struct inflate_state *isa;
    int isa_error;
    const char *isa_message;
#endif
} Decoder;

void
unstd_ng_api_free(void *value)
{
    NgAPI *api = value;
    if (api != NULL && --api->owners == 0) {
        dlclose(api->handle);
        PyMem_RawFree(api);
    }
}

PyObject *
unstd_ng_runtime_version(PyObject *module, PyObject *unused)
{
    (void)unused;
    ModuleState *state = PyModule_GetState(module);
    if (state == NULL) {
        return NULL;
    }
    NgAPI *api = state->ng_api;
    if (api == NULL) {
        Py_RETURN_NONE;
    }
    return PyUnicode_FromString(api->version);
}

PyObject *
unstd_configure_ng(PyObject *module, PyObject *path)
{
    ModuleState *state = PyModule_GetState(module);
    if (state == NULL) {
        return NULL;
    }
    if (state->ng_api != NULL) {
        Py_RETURN_TRUE;
    }
    PyObject *encoded = NULL;
    if (!PyUnicode_FSConverter(path, &encoded)) {
        return NULL;
    }
    /* Path conversion may have reentered this module. Configuration never
     * replaces an API that is already associated with live native states. */
    if (state->ng_api != NULL) {
        Py_DECREF(encoded);
        Py_RETURN_TRUE;
    }
    const char *name = PyBytes_AS_STRING(encoded);
    if ((Py_ssize_t)strlen(name) != PyBytes_GET_SIZE(encoded)) {
        Py_DECREF(encoded);
        PyErr_SetString(PyExc_ValueError, "embedded null byte");
        return NULL;
    }
    void *handle = dlopen(name, RTLD_LAZY | RTLD_LOCAL | RTLD_NOLOAD);
    Py_DECREF(encoded);
    if (handle == NULL) {
        Py_RETURN_FALSE;
    }
    NgAPI *api = PyMem_RawCalloc(1, sizeof(*api));
    if (api == NULL) {
        dlclose(handle);
        return PyErr_NoMemory();
    }
    api->handle = handle;
    api->owners = 1;
    api->init = (int32_t (*)(NgStream *, int32_t, const char *, int32_t))
        dlsym(handle, "zng_inflateInit2_");
    api->decode = (int32_t (*)(NgStream *, int32_t))
        dlsym(handle, "zng_inflate");
    api->end = (int32_t (*)(NgStream *))dlsym(handle, "zng_inflateEnd");
    const char *(*version_function)(void) =
        (const char *(*)(void))dlsym(handle, "zlibng_version");
    if (api->init == NULL || api->decode == NULL || api->end == NULL
            || version_function == NULL) {
        unstd_ng_api_free(api);
        Py_RETURN_FALSE;
    }
    const char *version = version_function();
    if (version == NULL || version[0] != '2' || version[1] != '.'
            || strnlen(version, sizeof(api->version)) == sizeof(api->version)) {
        unstd_ng_api_free(api);
        Py_RETURN_FALSE;
    }
    strcpy(api->version, version);
    NgStream probe = {0};
    probe.zalloc = allocate;
    probe.zfree = release;
    int status = api->init(&probe, -15, api->version, (int32_t)sizeof(probe));
    if (status != Z_OK) {
        if (probe.state != NULL) {
            api->end(&probe);
        }
        unstd_ng_api_free(api);
        if (status == Z_MEM_ERROR) {
            return PyErr_NoMemory();
        }
        Py_RETURN_FALSE;
    }
    api->end(&probe);
    /* Publish only a completely qualified, immutable API. Allocator hooks
     * during qualification may also have reentered configuration. */
    if (state->ng_api != NULL) {
        unstd_ng_api_free(api);
        Py_RETURN_TRUE;
    }
    state->ng_api = api;
    Py_RETURN_TRUE;
}

void *
unstd_inflate_create(int wbits, int *status, void *api)
{
    Decoder *decoder = PyMem_RawCalloc(1, sizeof(*decoder));
    if (decoder == NULL) {
        *status = Z_MEM_ERROR;
        return NULL;
    }
#ifdef UNSTD_LINUX_ISAL
    if (api != NULL) {
        NgAPI *binding = api;
        if (binding->owners == PY_SSIZE_T_MAX) {
            PyMem_RawFree(decoder);
            *status = Z_MEM_ERROR;
            return NULL;
        }
        binding->owners++;
        decoder->api = binding;
        decoder->ng.zalloc = allocate;
        decoder->ng.zfree = release;
        *status = binding->init(&decoder->ng, wbits, binding->version,
                               (int32_t)sizeof(decoder->ng));
        if (*status != Z_OK) {
            if (decoder->ng.state != NULL) {
                binding->end(&decoder->ng);
            }
            unstd_ng_api_free(binding);
            PyMem_RawFree(decoder);
            return NULL;
        }
        decoder->engine = 2;
        decoder->wbits = wbits;
        decoder->pending = wbits == -15 && isal_cpu_supported();
        return decoder;
    }
#endif
    decoder->apple.zalloc = allocate;
    decoder->apple.zfree = release;
    *status = inflateInit2_(&decoder->apple, wbits, unstd_inflate_version(),
                           (int)sizeof(decoder->apple));
    if (*status != Z_OK) {
        PyMem_RawFree(decoder);
        return NULL;
    }
    NgAPI *binding = api;
    if (binding != NULL) {
        if (binding->owners == PY_SSIZE_T_MAX) {
            inflateEnd(&decoder->apple);
            PyMem_RawFree(decoder);
            *status = Z_MEM_ERROR;
            return NULL;
        }
        /* Module GC may clear its heap type association before an unreachable
         * instance is deallocated. Each native decoder owns the binding too.
         * Ownership changes occur with the GIL held; decode pointers stay fixed. */
        binding->owners++;
    }
    decoder->api = binding;
    decoder->pending = api != NULL && wbits == -15;
    decoder->engine =
#ifdef UNSTD_LINUX_ISAL
        3;
#else
        1;
#endif
    decoder->wbits = wbits;
    return decoder;
}

int
unstd_inflate_backend(void *value)
{
    Decoder *decoder = value;
    return decoder->pending ? 0 : decoder->engine;
}

int
unstd_inflate_decode(void *value,
                     const unsigned char *input, uint32_t input_size,
                     unsigned char *output, uint32_t output_size,
                     uint32_t *consumed, uint32_t *produced,
                     const char **message)
{
    Decoder *decoder = value;
    *consumed = 0;
    *produced = 0;
    *message = NULL;
    if (decoder->pending) {
        /* Only an untouched initial empty input bypasses the native codec.
         * Once either engine runs, even zero-progress failures stay with it. */
        if (input_size == 0) {
            return Z_BUF_ERROR;
        }
#ifdef UNSTD_LINUX_ISAL
        /* Conservatively keep the existing NG path for short-distance,
         * fixed/stored, fragmented and small reads. A nonfinal dynamic
         * block remains eligible: most large JSON streams start that way. */
        if (input_size >= 8192 && output_size >= 16384
                && (input[0] & 6U) == 4U && (input[1] & 31U) >= 8U) {
            struct inflate_state *isa = PyMem_RawMalloc(sizeof(*isa));
            if (isa == NULL) {
                return Z_MEM_ERROR;
            }
            isal_inflate_init(isa);
            isa->crc_flag = ISAL_DEFLATE;
            decoder->api->end(&decoder->ng);
            decoder->isa = isa;
            decoder->engine = 4;
        }
        decoder->pending = 0;
    }
#else
        /* A buffered prefix read can ask for only 8 KiB and then close.
         * Inspecting an entire highly expanding block costs more than decoding
         * that prefix. Reserve the whole-block probe for larger output calls. */
        if (output_size >= 16384 && unstd_prefer_ng(input, input_size)) {
            decoder->ng.zalloc = allocate;
            decoder->ng.zfree = release;
            int status = decoder->api->init(
                &decoder->ng, decoder->wbits, decoder->api->version,
                (int32_t)sizeof(decoder->ng));
            if (status != Z_OK) {
                if (decoder->ng.state != NULL) {
                    decoder->api->end(&decoder->ng);
                }
                memset(&decoder->ng, 0, sizeof(decoder->ng));
                /* Neither engine has decoded input. Preserve Apple and the
                 * pending choice when initialization runs out of memory. */
                if (status == Z_MEM_ERROR) {
                    return status;
                }
                decoder->pending = 0;
            }
            else {
                inflateEnd(&decoder->apple);
                decoder->engine = 2;
                decoder->pending = 0;
            }
        }
        else {
            decoder->pending = 0;
        }
    }
#endif
#ifdef UNSTD_LINUX_ISAL
    if (decoder->engine == 4) {
        if (decoder->isa_error) {
            *message = decoder->isa_message;
            return decoder->isa_error;
        }
        struct inflate_state *state = decoder->isa;
        /* An incomplete saved header needs new compressed bytes. Re-entering
         * the parser with padding alone can turn truncation into bad BTYPE. */
        if (input_size == 0 && state->block_state == ISAL_BLOCK_HDR) {
            if (state->copy_overflow_length != 0 || state->write_overflow_len != 0
                    || state->tmp_out_valid < state->tmp_out_processed) {
                *message = "ISA-L pending header invariant";
                decoder->isa_error = Z_STREAM_ERROR;
                decoder->isa_message = *message;
                return Z_STREAM_ERROR;
            }
            uint32_t pending = (uint32_t)(state->tmp_out_valid - state->tmp_out_processed);
            uint32_t copied = pending < output_size ? pending : output_size;
            if (copied != 0) {
                memcpy(output, state->tmp_out_buffer + state->tmp_out_processed, copied);
                state->tmp_out_processed += (int32_t)copied;
                state->total_out += copied;
            }
            *produced = copied;
            return copied != 0 ? Z_OK : Z_BUF_ERROR;
        }
        state->next_in = (uint8_t *)input;
        state->avail_in = input_size;
        state->next_out = output;
        state->avail_out = output_size;
        int status = isal_inflate(state);
        /* An incomplete header owns all its saved bits, including full
         * bytes held before tmp_in staging begins. Returning those bytes
         * would change needs_input or duplicate the header on the next call. */
        if (status >= 0 && state->block_state != ISAL_BLOCK_HDR
                && state->tmp_in_size == 0
                && (state->avail_out == 0 || state->block_state == ISAL_BLOCK_FINISH)) {
            int bits = state->read_in_length;
            if (bits < 0 || bits > 64) {
                *message = "ISA-L input accounting invariant";
                decoder->isa_error = Z_STREAM_ERROR;
                decoder->isa_message = *message;
                return Z_STREAM_ERROR;
            }
            uint32_t buffered = (uint32_t)bits / 8U;
            uint32_t supplied = input_size - state->avail_in;
            /* Only the newest full bytes can belong to this call. Older
             * held bytes stay in read_in; EOF exposes them via its prefix. */
            uint32_t rewind = buffered < supplied ? buffered : supplied;
            state->next_in -= rewind;
            state->avail_in += rewind;
            bits -= (int)(rewind * 8U);
            if (bits < 64) state->read_in &= (UINT64_C(1) << bits) - 1U;
            state->read_in_length = bits;
        }
        *consumed = input_size - state->avail_in;
        *produced = output_size - state->avail_out;
        state->next_in = NULL;
        state->avail_in = 0;
        state->next_out = NULL;
        state->avail_out = 0;
        if (status < 0) {
            static const char *const diagnostics[] = {
                "invalid block type", "invalid stored block lengths",
                "too many length or distance symbols", "invalid code lengths set",
                "invalid bit length repeat", "invalid code -- missing end-of-block",
                "invalid literal/lengths set", "invalid distances set",
                "invalid deflate data", "invalid literal/length code", "invalid distance code"
            };
            *message = status <= -20 && status >= -30 ? diagnostics[-status - 20]
                : status == ISAL_INVALID_LOOKBACK ? "invalid distance too far back"
                : status == ISAL_INVALID_SYMBOL ? "invalid literal/length code"
                : "invalid block type";
            decoder->isa_error = Z_DATA_ERROR;
            decoder->isa_message = *message;
            return Z_DATA_ERROR;
        }
        return state->block_state == ISAL_BLOCK_FINISH ? Z_STREAM_END : Z_OK;
    }
#endif
    if (decoder->engine == 2) {
        decoder->ng.next_in = input;
        decoder->ng.avail_in = input_size;
        decoder->ng.next_out = output;
        decoder->ng.avail_out = output_size;
        int status = decoder->api->decode(&decoder->ng, Z_NO_FLUSH);
        *consumed = input_size - decoder->ng.avail_in;
        *produced = output_size - decoder->ng.avail_out;
        *message = decoder->ng.msg;
        decoder->ng.next_in = NULL;
        decoder->ng.avail_in = 0;
        decoder->ng.next_out = NULL;
        decoder->ng.avail_out = 0;
        return status;
    }
    z_stream *stream = &decoder->apple;
    stream->next_in = (Bytef *)input;
    stream->avail_in = input_size;
    stream->next_out = output;
    stream->avail_out = output_size;
    int status = inflate(stream, Z_NO_FLUSH);
    *consumed = input_size - stream->avail_in;
    *produced = output_size - stream->avail_out;
    *message = stream->msg;
    stream->next_in = NULL;
    stream->avail_in = 0;
    stream->next_out = NULL;
    stream->avail_out = 0;
    return status;
}

void
unstd_inflate_free(void *value)
{
    Decoder *decoder = value;
    if (decoder != NULL) {
#ifdef UNSTD_LINUX_ISAL
        if (decoder->engine == 4) {
            PyMem_RawFree(decoder->isa);
        }
        else
#endif
        if (decoder->engine == 2) {
            decoder->api->end(&decoder->ng);
        }
        else {
            inflateEnd(&decoder->apple);
        }
        unstd_ng_api_free((void *)decoder->api);
        PyMem_RawFree(decoder);
    }
}

#else
/* Unsupported platforms and the explicitly selected Rust archive retain
 * the original codec, native ABI and checked allocator calls unchanged. */
void
unstd_ng_api_free(void *api)
{
    (void)api;
}

PyObject *
unstd_configure_ng(PyObject *module, PyObject *path)
{
    (void)module;
    (void)path;
    Py_RETURN_FALSE;
}

PyObject *
unstd_ng_runtime_version(PyObject *module, PyObject *unused)
{
    (void)module;
    (void)unused;
    Py_RETURN_NONE;
}

void *
unstd_inflate_create(int wbits, int *status, void *api)
{
    (void)api;
    z_stream *stream = PyMem_RawCalloc(1, sizeof(*stream));
    if (stream == NULL) {
        *status = Z_MEM_ERROR;
        return NULL;
    }
    stream->zalloc = allocate;
    stream->zfree = release;
    *status = inflateInit2_(stream, wbits, unstd_inflate_version(),
                           (int)sizeof(*stream));
    if (*status != Z_OK) {
        PyMem_RawFree(stream);
        return NULL;
    }
    return stream;
}

int
unstd_inflate_backend(void *decoder)
{
    (void)decoder;
    return 3;
}

int
unstd_inflate_decode(void *decoder,
                     const unsigned char *input, uint32_t input_size,
                     unsigned char *output, uint32_t output_size,
                     uint32_t *consumed, uint32_t *produced,
                     const char **message)
{
    z_stream *stream = decoder;
    stream->next_in = (Bytef *)input;
    stream->avail_in = input_size;
    stream->next_out = output;
    stream->avail_out = output_size;
    int status = inflate(stream, Z_NO_FLUSH);
    *consumed = input_size - stream->avail_in;
    *produced = output_size - stream->avail_out;
    *message = stream->msg;
    stream->next_in = NULL;
    stream->avail_in = 0;
    stream->next_out = NULL;
    stream->avail_out = 0;
    return status;
}

void
unstd_inflate_free(void *decoder)
{
    if (decoder != NULL) {
        inflateEnd(decoder);
        PyMem_RawFree(decoder);
    }
}
#endif

const char *
unstd_inflate_version(void)
{
#ifdef MY_ZCALLOC
    return ZLIB_VERSION;
#else
    return zlibVersion();
#endif
}

int
unstd_inflate_unused_prefix(void *value, unsigned char *output)
{
#ifdef UNSTD_LINUX_ISAL
    Decoder *decoder = value;
    if (decoder->engine == 4) {
        struct inflate_state *state = decoder->isa;
        int bits = state->read_in_length;
        if (bits < 0 || bits > 64) return -1;
        int size = bits / 8;
        uint64_t bytes = state->read_in >> (bits % 8);
        for (int i = 0; i < size; ++i) {
            output[i] = (unsigned char)(bytes >> (8 * i));
        }
        return size;
    }
#else
    (void)value;
    (void)output;
#endif
    return 0;
}

void
unstd_inflate_legacy(void *value, int legacy)
{
#ifdef UNSTD_LINUX_ISAL
    Decoder *decoder = value;
    if (legacy) decoder->pending = 0;
#else
    (void)value;
    (void)legacy;
#endif
}

int
unstd_add_isal_metadata(PyObject *module)
{
#ifdef UNSTD_LINUX_ISAL
    if (PyModule_AddStringConstant(module, "ISAL_RUNTIME_VERSION", UNSTD_ISAL_VERSION) < 0
            || PyModule_AddStringConstant(module, "ISAL_PATCH_SHA256", UNSTD_ISAL_PATCH_SHA256) < 0)
        return -1;
#else
    if (PyModule_AddObjectRef(module, "ISAL_RUNTIME_VERSION", Py_None) < 0
            || PyModule_AddObjectRef(module, "ISAL_PATCH_SHA256", Py_None) < 0)
        return -1;
#endif
    return PyModule_AddObjectRef(module, "ISAL_STREAMING",
                                 isal_cpu_supported() ? Py_True : Py_False);
}
