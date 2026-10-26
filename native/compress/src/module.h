#ifndef UNSTD_COMPRESS_MODULE_H
#define UNSTD_COMPRESS_MODULE_H
#include <Python.h>

struct libdeflate_decompressor;

typedef struct {
    struct libdeflate_decompressor *small_decoder;
    void *ng_api;
} ModuleState;

PyObject *unstd_configure_ng(PyObject *module, PyObject *path);
void unstd_ng_api_free(void *api);
PyObject *unstd_ng_runtime_version(PyObject *module, PyObject *unused);
#endif
