#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <limits.h>
#include <stdint.h>
#include <string.h>

#include "stream.h"

/* The GIL serializes each decoder's state and retained input. Unlike a public
 * zlib object this private inflater only supports the gzip reader's raw stream
 * operation. Legacy mode exposes unconsumed_tail for pre-backport CPython. */
typedef struct {
    PyObject_HEAD
    void *decoder;
    PyObject *input;
    Py_ssize_t offset;
    PyObject *unused_data;
    int eof;
    int needs_input;
    int legacy;
} Inflater;

static void
set_codec_error(int status, const char *message)
{
    if (status == -4) {
        PyErr_NoMemory();
        return;
    }
    PyObject *module = PyImport_ImportModule("zlib");
    if (module == NULL) {
        return;
    }
    PyObject *error = PyObject_GetAttrString(module, "error");
    Py_DECREF(module);
    if (error == NULL) {
        return;
    }
    PyErr_Format(error, "Error %d while decompressing data: %s", status,
                 message != NULL ? message : "invalid stream state");
    Py_DECREF(error);
}

static void
Inflater_dealloc(Inflater *self)
{
    unstd_inflate_free(self->decoder);
    Py_XDECREF(self->input);
    Py_XDECREF(self->unused_data);
    Py_TYPE(self)->tp_free((PyObject *)self);
}

static int
Inflater_init(Inflater *self, PyObject *args, PyObject *kwargs)
{
    static char *keywords[] = {"wbits", "legacy", NULL};
    int wbits = -15;
    int legacy = 0;
    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|ip:Inflater", keywords,
                                     &wbits, &legacy)) {
        return -1;
    }
    unstd_inflate_free(self->decoder);
    self->decoder = NULL;
    Py_CLEAR(self->input);
    Py_CLEAR(self->unused_data);
    self->offset = 0;
    self->eof = 0;
    self->needs_input = 1;
    self->legacy = legacy;
    self->unused_data = PyBytes_FromStringAndSize(NULL, 0);
    if (self->unused_data == NULL) {
        return -1;
    }
    int status;
    self->decoder = unstd_inflate_create(wbits, &status);
    if (self->decoder == NULL) {
        if (status == -4) {
            PyErr_NoMemory();
        }
        else {
            PyErr_SetString(PyExc_ValueError, "Invalid initialization option");
        }
        return -1;
    }
    return 0;
}

static PyObject *
input_bytes(PyObject *data)
{
    if (PyBytes_CheckExact(data)) {
        return Py_NewRef(data);
    }
    Py_buffer view;
    if (PyObject_GetBuffer(data, &view, PyBUF_SIMPLE) < 0) {
        return NULL;
    }
    /* Mutable exporters cannot be retained across calls. */
    PyObject *result = PyBytes_FromStringAndSize(view.buf, view.len);
    PyBuffer_Release(&view);
    return result;
}

static int
retain_input(Inflater *self, PyObject *data)
{
    PyObject *incoming = input_bytes(data);
    if (incoming == NULL) {
        return -1;
    }
    Py_ssize_t remaining = self->input != NULL
        ? PyBytes_GET_SIZE(self->input) - self->offset : 0;
    Py_ssize_t size = PyBytes_GET_SIZE(incoming);
    if (self->legacy || remaining == 0) {
        Py_XSETREF(self->input, incoming);
        self->offset = 0;
    }
    else if (size == 0) {
        Py_DECREF(incoming);
    }
    else {
        if (size > PY_SSIZE_T_MAX - remaining) {
            Py_DECREF(incoming);
            PyErr_NoMemory();
            return -1;
        }
        PyObject *joined = PyBytes_FromStringAndSize(NULL, remaining + size);
        if (joined == NULL) {
            Py_DECREF(incoming);
            return -1;
        }
        memcpy(PyBytes_AS_STRING(joined),
               PyBytes_AS_STRING(self->input) + self->offset, remaining);
        memcpy(PyBytes_AS_STRING(joined) + remaining,
               PyBytes_AS_STRING(incoming), size);
        Py_DECREF(incoming);
        Py_SETREF(self->input, joined);
        self->offset = 0;
    }
    return 0;
}

static PyObject *
Inflater_decompress(Inflater *self, PyObject *const *args, Py_ssize_t nargs,
                    PyObject *kwnames)
{
    if (nargs > 2) {
        PyErr_SetString(PyExc_TypeError, "decompress accepts at most two arguments");
        return NULL;
    }
    PyObject *data = nargs > 0 ? args[0] : NULL;
    PyObject *length = nargs > 1 ? args[1] : NULL;
    Py_ssize_t keywords = kwnames != NULL ? PyTuple_GET_SIZE(kwnames) : 0;
    for (Py_ssize_t i = 0; i < keywords; i++) {
        PyObject *name = PyTuple_GET_ITEM(kwnames, i);
        PyObject **target;
        if (PyUnicode_CompareWithASCIIString(name, "data") == 0) {
            target = &data;
        }
        else if (PyUnicode_CompareWithASCIIString(name, "max_length") == 0) {
            target = &length;
        }
        else {
            PyErr_Format(PyExc_TypeError, "unexpected decompress keyword %R", name);
            return NULL;
        }
        if (*target != NULL) {
            PyErr_Format(PyExc_TypeError, "duplicate decompress argument %R", name);
            return NULL;
        }
        *target = args[nargs + i];
    }
    if (data == NULL) {
        PyErr_SetString(PyExc_TypeError, "decompress requires data");
        return NULL;
    }
    Py_ssize_t maximum = -1;
    if (length != NULL) {
        maximum = PyNumber_AsSsize_t(length, PyExc_OverflowError);
        if (maximum == -1 && PyErr_Occurred()) {
            return NULL;
        }
    }
    if (self->eof) {
        PyErr_SetString(PyExc_EOFError, "End of stream already reached");
        return NULL;
    }
    if (self->decoder == NULL) {
        PyErr_SetString(PyExc_ValueError, "uninitialized inflater");
        return NULL;
    }
    if (retain_input(self, data) < 0) {
        return NULL;
    }
    /* Buffer acquisition/release can invoke Python exporter callbacks which
     * reinitialize this private object. Validate state after those callbacks. */
    if (self->eof) {
        PyErr_SetString(PyExc_EOFError, "End of stream already reached");
        return NULL;
    }
    if (self->decoder == NULL) {
        PyErr_SetString(PyExc_ValueError, "uninitialized inflater");
        return NULL;
    }
    Py_ssize_t limit = maximum < 0 || (self->legacy && maximum == 0)
        ? PY_SSIZE_T_MAX : maximum;
    if (limit == 0) {
        self->needs_input = PyBytes_GET_SIZE(self->input) == self->offset;
        return PyBytes_FromStringAndSize(NULL, 0);
    }
    /* The gzip reader usually supplies a 128 KiB read budget. Allocate its
     * actual bounded request once, while keeping arbitrary huge requests and
     * unlimited calls from reserving their entire nominal size up front. */
    Py_ssize_t capacity = maximum >= 0 && limit <= 1024 * 1024
        ? limit : (limit < 65536 ? limit : 65536);
    PyObject *output = PyBytes_FromStringAndSize(NULL, capacity);
    if (output == NULL) {
        return NULL;
    }
    Py_ssize_t used = 0;
    int status = 0;
    for (;;) {
        if (used == capacity) {
            if (capacity == limit) {
                break;
            }
            capacity = capacity > limit / 2 ? limit : capacity * 2;
            if (_PyBytes_Resize(&output, capacity) < 0) {
                return NULL;
            }
        }
        Py_ssize_t available = PyBytes_GET_SIZE(self->input) - self->offset;
        Py_ssize_t space = capacity - used;
        uint32_t input_size = available > UINT32_MAX
            ? UINT32_MAX : (uint32_t)available;
        uint32_t output_size = space > UINT32_MAX
            ? UINT32_MAX : (uint32_t)space;
        uint32_t consumed = 0;
        uint32_t produced = 0;
        const char *message = NULL;
        status = unstd_inflate_decode(
            self->decoder,
            (const unsigned char *)PyBytes_AS_STRING(self->input) + self->offset,
            input_size,
            (unsigned char *)PyBytes_AS_STRING(output) + used,
            output_size, &consumed, &produced, &message);
        self->offset += consumed;
        used += produced;
        if (status == 1) {
            self->eof = 1;
            if (!self->legacy) {
                /* Match CPython's input-buffer inflater: release the native
                 * window as soon as EOF is known, before resizing output. */
                unstd_inflate_free(self->decoder);
                self->decoder = NULL;
            }
            break;
        }
        if (status != 0 && status != -5) {
            Py_DECREF(output);
            set_codec_error(status, message);
            return NULL;
        }
        if (consumed == 0 && produced == 0) {
            break;
        }
        if (used < capacity && self->offset == PyBytes_GET_SIZE(self->input)) {
            break;
        }
    }
    if (self->eof) {
        PyObject *unused = PyBytes_FromStringAndSize(
            PyBytes_AS_STRING(self->input) + self->offset,
            PyBytes_GET_SIZE(self->input) - self->offset);
        if (unused == NULL) {
            Py_DECREF(output);
            return NULL;
        }
        Py_SETREF(self->unused_data, unused);
        Py_CLEAR(self->input);
        self->offset = 0;
        self->needs_input = 0;
    }
    else if (self->offset == PyBytes_GET_SIZE(self->input)) {
        Py_CLEAR(self->input);
        self->offset = 0;
        self->needs_input = 1;
    }
    else {
        self->needs_input = 0;
    }
    if (_PyBytes_Resize(&output, used) < 0) {
        return NULL;
    }
    return output;
}

static PyObject *
Inflater_get_eof(Inflater *self, void *closure)
{
    (void)closure;
    return PyBool_FromLong(self->eof);
}

static PyObject *
Inflater_get_needs_input(Inflater *self, void *closure)
{
    (void)closure;
    return PyBool_FromLong(self->needs_input);
}

static PyObject *
Inflater_get_unused_data(Inflater *self, void *closure)
{
    (void)closure;
    if (self->unused_data == NULL) {
        return PyBytes_FromStringAndSize(NULL, 0);
    }
    return Py_NewRef(self->unused_data);
}

static PyObject *
Inflater_get_unconsumed_tail(Inflater *self, void *closure)
{
    (void)closure;
    if (self->input == NULL) {
        return PyBytes_FromStringAndSize(NULL, 0);
    }
    return PyBytes_FromStringAndSize(
        PyBytes_AS_STRING(self->input) + self->offset,
        PyBytes_GET_SIZE(self->input) - self->offset);
}

static PyMethodDef Inflater_methods[] = {
    {"decompress", (PyCFunction)(void (*)(void))Inflater_decompress,
     METH_FASTCALL | METH_KEYWORDS, "Inflate within the requested output budget."},
    {NULL, NULL, 0, NULL}
};

static PyGetSetDef Inflater_getset[] = {
    {"eof", (getter)Inflater_get_eof, NULL, "End of DEFLATE stream.", NULL},
    {"needs_input", (getter)Inflater_get_needs_input, NULL,
     "Whether more compressed input is needed.", NULL},
    {"unused_data", (getter)Inflater_get_unused_data, NULL,
     "Input bytes following the completed stream.", NULL},
    {"unconsumed_tail", (getter)Inflater_get_unconsumed_tail, NULL,
     "Input bytes not consumed by the last bounded read.", NULL},
    {NULL, NULL, NULL, NULL, NULL}
};

static PyTypeObject Inflater_type = {
    PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = "unstd.compress._gzip_native.Inflater",
    .tp_basicsize = sizeof(Inflater),
    .tp_dealloc = (destructor)Inflater_dealloc,
    .tp_flags = Py_TPFLAGS_DEFAULT,
    .tp_doc = "Bounded native raw DEFLATE inflater.",
    .tp_methods = Inflater_methods,
    .tp_getset = Inflater_getset,
    .tp_init = (initproc)Inflater_init,
    .tp_new = PyType_GenericNew,
};

int
unstd_add_inflater(PyObject *module)
{
    if (PyType_Ready(&Inflater_type) < 0) {
        return -1;
    }
    return PyModule_AddObjectRef(module, "Inflater", (PyObject *)&Inflater_type);
}
