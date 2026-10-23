/* Narrow ownership boundary over the native zlib ABI. macOS links the platform
 * codec; other platforms compile the prefixed, vendored source closure. */
#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include "zlib/zlib.h"
#include "stream.h"

static voidpf
allocate(voidpf opaque, uInt items, uInt size)
{
    (void)opaque;
    if (size != 0 && items > (size_t)PY_SSIZE_T_MAX / size) {
        return NULL;
    }
    /* Match CPython's zlib allocator, including tracking and custom RAW-domain
     * allocators. These callbacks remain valid without the GIL. */
    return PyMem_RawMalloc((size_t)items * (size_t)size);
}

static void
release(voidpf opaque, voidpf pointer)
{
    (void)opaque;
    PyMem_RawFree(pointer);
}

void *
unstd_inflate_create(int wbits, int *status)
{
    z_stream *stream = PyMem_RawCalloc(1, sizeof(*stream));
    if (stream == NULL) {
        *status = Z_MEM_ERROR;
        return NULL;
    }
    stream->zalloc = allocate;
    stream->zfree = release;
    *status = inflateInit2_(stream, wbits, zlibVersion(), (int)sizeof(*stream));
    if (*status != Z_OK) {
        PyMem_RawFree(stream);
        return NULL;
    }
    return stream;
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
    /* Input and output are borrowed only during this call. */
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

const char *
unstd_inflate_version(void)
{
    return zlibVersion();
}
