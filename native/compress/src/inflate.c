/* Native zlib ABI and checked allocator boundary. The optional macOS arm64
 * route binds the already imported zlib-ng provider before any body decode. */
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

#if defined(__APPLE__) && (defined(__aarch64__) || defined(__arm64__)) \
        && !defined(UNSTD_ZLIB_RS)
#define UNSTD_ADAPTIVE_NG 1
#include <dlfcn.h>
#include "shape.h"
#endif

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
    decoder->apple.zalloc = allocate;
    decoder->apple.zfree = release;
    *status = inflateInit2_(&decoder->apple, wbits, zlibVersion(),
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
    decoder->engine = 1;
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
        if (output_size >= 4096 && unstd_prefer_ng(input, input_size)) {
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
/* Linux, macOS x86 and the explicitly selected Rust archive retain the
 * original codec, native ABI and checked allocator calls unchanged. */
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
