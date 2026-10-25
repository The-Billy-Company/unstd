#ifndef UNSTD_COMPRESS_STREAM_H
#define UNSTD_COMPRESS_STREAM_H
#include <Python.h>
#include <stdint.h>

void *unstd_inflate_create(int wbits, int *status, void *api);
int unstd_inflate_backend(void *decoder);
void unstd_inflate_legacy(void *decoder, int legacy);
int unstd_inflate_unused_prefix(void *decoder, unsigned char *output);
int unstd_inflate_decode(void *decoder,
                        const unsigned char *input, uint32_t input_size,
                        unsigned char *output, uint32_t output_size,
                        uint32_t *consumed, uint32_t *produced,
                        const char **message);
void unstd_inflate_free(void *decoder);
const char *unstd_inflate_version(void);
int unstd_add_inflater(PyObject *module);
int unstd_add_isal_metadata(PyObject *module);
#endif
