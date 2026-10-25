#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <limits.h>
#include <stdint.h>
#include <string.h>

#include "stream.h"
#include "module.h"

#define RELEASE_GIL_BYTES ((uint32_t)32 * 1024)

/* A per-object lock serializes decoder state while large calls release the
 * GIL. Exporter and argument callbacks run before locking. Legacy mode exposes
 * unconsumed_tail for pre-backport CPython. */
typedef struct {
    PyObject_HEAD
    PyThread_type_lock lock;
    void *decoder;
    PyObject *input;
    Py_ssize_t offset;
    PyObject *unused_data;
    int eof;
    int needs_input;
    int legacy;
    int selected_backend;
} Inflater;

typedef struct {
    int status;
    PyObject *message;
} CodecError;

static void
enter_inflater(Inflater *self)
{
    if (!PyThread_acquire_lock(self->lock, NOWAIT_LOCK)) {
        /* The running decoder needs the GIL again before it can unlock. */
        Py_BEGIN_ALLOW_THREADS
        PyThread_acquire_lock(self->lock, WAIT_LOCK);
        Py_END_ALLOW_THREADS
    }
}

static void
leave_inflater(Inflater *self, CodecError *failure)
{
    PyThread_release_lock(self->lock);
    if (failure->status == 0) {
        return;
    }
    if (failure->status == -4) {
        PyErr_NoMemory();
        return;
    }
    if (failure->message == NULL) {
        /* Copying the codec's message already raised an allocation error. */
        return;
    }
    /* Import and module attribute lookup may invoke Python and reenter this
     * object. The owned message remains valid after another thread resets it. */
    PyObject *module = PyImport_ImportModule("zlib");
    if (module == NULL) {
        Py_CLEAR(failure->message);
        return;
    }
    PyObject *error = PyObject_GetAttrString(module, "error");
    Py_DECREF(module);
    if (error == NULL) {
        Py_CLEAR(failure->message);
        return;
    }
    PyErr_Format(error, "Error %d while decompressing data: %U", failure->status,
                 failure->message);
    Py_DECREF(error);
    Py_CLEAR(failure->message);
}

static int
Inflater_traverse(Inflater *self, visitproc visit, void *arg)
{
    /* The heap type retains its module. Visit that edge so a module which
     * owns an Inflater can be collected; retained buffers are exact bytes. */
    Py_VISIT(Py_TYPE(self));
    return 0;
}

static void
Inflater_dealloc(Inflater *self)
{
    PyObject_GC_UnTrack(self);
    unstd_inflate_free(self->decoder);
    Py_XDECREF(self->input);
    Py_XDECREF(self->unused_data);
    if (self->lock != NULL) {
        PyThread_free_lock(self->lock);
    }
    PyTypeObject *type = Py_TYPE(self);
    type->tp_free((PyObject *)self);
    Py_DECREF(type);
}

static PyObject *
Inflater_new(PyTypeObject *type, PyObject *args, PyObject *kwargs)
{
    (void)args;
    (void)kwargs;
    Inflater *self = (Inflater *)type->tp_alloc(type, 0);
    if (self == NULL) {
        return NULL;
    }
    self->lock = PyThread_allocate_lock();
    if (self->lock == NULL) {
        Py_DECREF(self);
        return PyErr_NoMemory();
    }
    return (PyObject *)self;
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
    ModuleState *module_state = PyType_GetModuleState(Py_TYPE(self));
    if (module_state == NULL) {
        return -1;
    }
    void *api = module_state->ng_api;
    enter_inflater(self);
    unstd_inflate_free(self->decoder);
    self->decoder = NULL;
    Py_CLEAR(self->input);
    Py_CLEAR(self->unused_data);
    self->offset = 0;
    self->eof = 0;
    self->selected_backend = 0;
    self->needs_input = 1;
    self->legacy = legacy;
    self->unused_data = PyBytes_FromStringAndSize(NULL, 0);
    if (self->unused_data == NULL) {
        PyThread_release_lock(self->lock);
        return -1;
    }
    int status;
    self->decoder = unstd_inflate_create(wbits, &status, api);
    if (self->decoder == NULL) {
        if (status == -4) {
            PyErr_NoMemory();
        }
        else {
            PyErr_SetString(PyExc_ValueError, "Invalid initialization option");
        }
        PyThread_release_lock(self->lock);
        return -1;
    }
    self->selected_backend = unstd_inflate_backend(self->decoder);
    PyThread_release_lock(self->lock);
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
retain_input(Inflater *self, PyObject *incoming)
{
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

static int
check_ready(Inflater *self)
{
    if (self->eof) {
        PyErr_SetString(PyExc_EOFError, "End of stream already reached");
        return -1;
    }
    if (self->decoder == NULL) {
        PyErr_SetString(PyExc_ValueError, "uninitialized inflater");
        return -1;
    }
    return 0;
}

static int
prepare_input(Inflater *self, PyObject *incoming)
{
    /* Own exact bytes before acquiring the lock: exporters may reinitialize
     * the decoder, so validate its final state only after those callbacks. */
    if (check_ready(self) < 0) {
        Py_DECREF(incoming);
        return -1;
    }
    return retain_input(self, incoming);
}

static int
inflate_buffer(Inflater *self, unsigned char *output, Py_ssize_t capacity,
               Py_ssize_t *used, CodecError *failure)
{
    while (*used < capacity) {
        Py_ssize_t available = PyBytes_GET_SIZE(self->input) - self->offset;
        Py_ssize_t space = capacity - *used;
        uint32_t input_size = available > UINT32_MAX
            ? UINT32_MAX : (uint32_t)available;
        uint32_t output_size = space > UINT32_MAX
            ? UINT32_MAX : (uint32_t)space;
        uint32_t consumed = 0;
        uint32_t produced = 0;
        const char *message = NULL;
        const unsigned char *input =
            (const unsigned char *)PyBytes_AS_STRING(self->input) + self->offset;
        unsigned char *destination = output + *used;
        int status;
        if (output_size >= RELEASE_GIL_BYTES) {
            Py_BEGIN_ALLOW_THREADS
            status = unstd_inflate_decode(self->decoder, input, input_size,
                                         destination, output_size,
                                         &consumed, &produced, &message);
            Py_END_ALLOW_THREADS
        }
        else {
            status = unstd_inflate_decode(self->decoder, input, input_size,
                                         destination, output_size,
                                         &consumed, &produced, &message);
        }
        self->selected_backend = unstd_inflate_backend(self->decoder);
        self->offset += consumed;
        *used += produced;
        if (status == 1) {
            self->eof = 1;
            if (!self->legacy) {
                /* Match CPython's input-buffer inflater: release the native
                 * window as soon as EOF is known, before resizing output. */
                unstd_inflate_free(self->decoder);
                self->decoder = NULL;
            }
            return 0;
        }
        if (status != 0 && status != -5) {
            failure->status = status;
            if (status != -4) {
                failure->message = PyUnicode_FromString(
                    message != NULL ? message : "invalid stream state");
            }
            return -1;
        }
        if (consumed == 0 && produced == 0) {
            return 0;
        }
        if (*used < capacity && self->offset == PyBytes_GET_SIZE(self->input)) {
            return 0;
        }
    }
    return 0;
}

static int
finish_input(Inflater *self)
{
    if (self->eof) {
        PyObject *unused = PyBytes_FromStringAndSize(
            PyBytes_AS_STRING(self->input) + self->offset,
            PyBytes_GET_SIZE(self->input) - self->offset);
        if (unused == NULL) {
            return -1;
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
    return 0;
}

static PyObject *
decompress_bytes(Inflater *self, PyObject *incoming, Py_ssize_t maximum,
                 CodecError *failure)
{
    if (prepare_input(self, incoming) < 0) {
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
        if (inflate_buffer(self, (unsigned char *)PyBytes_AS_STRING(output),
                           capacity, &used, failure) < 0) {
            Py_DECREF(output);
            return NULL;
        }
        if (self->eof || used < capacity) {
            break;
        }
    }
    if (finish_input(self) < 0) {
        Py_DECREF(output);
        return NULL;
    }
    if (_PyBytes_Resize(&output, used) < 0) {
        return NULL;
    }
    return output;
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
    if (check_ready(self) < 0) {
        return NULL;
    }
    PyObject *incoming = input_bytes(data);
    if (incoming == NULL) {
        return NULL;
    }
    CodecError failure = {0, NULL};
    enter_inflater(self);
    PyObject *result = decompress_bytes(self, incoming, maximum, &failure);
    leave_inflater(self, &failure);
    return result;
}

static PyObject *
Inflater_decompress_into(Inflater *self, PyObject *const *args, Py_ssize_t nargs,
                         PyObject *kwnames)
{
    if (nargs > 2) {
        PyErr_SetString(PyExc_TypeError,
                        "decompress_into accepts at most two arguments");
        return NULL;
    }
    PyObject *data = nargs > 0 ? args[0] : NULL;
    PyObject *buffer = nargs > 1 ? args[1] : NULL;
    Py_ssize_t keywords = kwnames != NULL ? PyTuple_GET_SIZE(kwnames) : 0;
    for (Py_ssize_t i = 0; i < keywords; i++) {
        PyObject *name = PyTuple_GET_ITEM(kwnames, i);
        PyObject **target;
        if (PyUnicode_CompareWithASCIIString(name, "data") == 0) {
            target = &data;
        }
        else if (PyUnicode_CompareWithASCIIString(name, "buffer") == 0) {
            target = &buffer;
        }
        else {
            PyErr_Format(PyExc_TypeError,
                         "unexpected decompress_into keyword %R", name);
            return NULL;
        }
        if (*target != NULL) {
            PyErr_Format(PyExc_TypeError,
                         "duplicate decompress_into argument %R", name);
            return NULL;
        }
        *target = args[nargs + i];
    }
    if (data == NULL || buffer == NULL) {
        PyErr_SetString(PyExc_TypeError,
                        "decompress_into requires data and buffer");
        return NULL;
    }
    if (check_ready(self) < 0) {
        return NULL;
    }
    /* Own the export before acquiring input: either exporter may invoke Python
     * and reinitialize the decoder. The shared input preparation validates the
     * final state after those callbacks. Slices retain this export's owner. */
    PyObject *view = PyMemoryView_GetContiguous(buffer, PyBUF_WRITE, 'C');
    if (view == NULL) {
        return NULL;
    }
    PyObject *bytes = PyObject_CallMethod(view, "cast", "s", "B");
    Py_DECREF(view);
    if (bytes == NULL) {
        return NULL;
    }
    PyObject *incoming = input_bytes(data);
    if (incoming == NULL) {
        Py_DECREF(bytes);
        return NULL;
    }
    CodecError failure = {0, NULL};
    enter_inflater(self);
    if (prepare_input(self, incoming) < 0) {
        leave_inflater(self, &failure);
        Py_DECREF(bytes);
        return NULL;
    }
    Py_buffer *target = PyMemoryView_GET_BUFFER(bytes);
    Py_ssize_t used = 0;
    if (target->len == 0) {
        self->needs_input = PyBytes_GET_SIZE(self->input) == self->offset;
    }
    else if (inflate_buffer(self, target->buf, target->len, &used, &failure) < 0
             || finish_input(self) < 0) {
        leave_inflater(self, &failure);
        Py_DECREF(bytes);
        return NULL;
    }
    leave_inflater(self, &failure);
    /* Like other into-buffer APIs, codec errors may leave a written prefix.
     * Callers requiring transactional writes must retain their staging path. */
    PyObject *result = PySequence_GetSlice(bytes, 0, used);
    Py_DECREF(bytes);
    return result;
}

static PyObject *
Inflater_get_eof(Inflater *self, void *closure)
{
    (void)closure;
    enter_inflater(self);
    PyObject *result = PyBool_FromLong(self->eof);
    PyThread_release_lock(self->lock);
    return result;
}

static PyObject *
Inflater_get_needs_input(Inflater *self, void *closure)
{
    (void)closure;
    enter_inflater(self);
    PyObject *result = PyBool_FromLong(self->needs_input);
    PyThread_release_lock(self->lock);
    return result;
}

static PyObject *
Inflater_get_unused_data(Inflater *self, void *closure)
{
    (void)closure;
    enter_inflater(self);
    PyObject *result = self->unused_data == NULL
        ? PyBytes_FromStringAndSize(NULL, 0) : Py_NewRef(self->unused_data);
    PyThread_release_lock(self->lock);
    return result;
}

static PyObject *
Inflater_get_unconsumed_tail(Inflater *self, void *closure)
{
    (void)closure;
    enter_inflater(self);
    PyObject *result;
    if (self->input == NULL) {
        result = PyBytes_FromStringAndSize(NULL, 0);
    }
    else {
        result = PyBytes_FromStringAndSize(
            PyBytes_AS_STRING(self->input) + self->offset,
            PyBytes_GET_SIZE(self->input) - self->offset);
    }
    PyThread_release_lock(self->lock);
    return result;
}

static PyMethodDef Inflater_methods[] = {
    {"decompress", (PyCFunction)(void (*)(void))Inflater_decompress,
     METH_FASTCALL | METH_KEYWORDS, "Inflate within the requested output budget."},
    {"decompress_into", (PyCFunction)(void (*)(void))Inflater_decompress_into,
     METH_FASTCALL | METH_KEYWORDS,
     "Inflate into writable contiguous storage; errors may leave a prefix."},
    {NULL, NULL, 0, NULL}
};

static PyObject *
Inflater_get_backend(Inflater *self, void *closure)
{
    (void)closure;
    enter_inflater(self);
    const char *name = self->selected_backend == 2 ? "ng"
        : self->selected_backend == 1 ? "apple"
        : self->selected_backend == 3 ? "zlib" : "pending";
    PyObject *result = PyUnicode_FromString(name);
    PyThread_release_lock(self->lock);
    return result;
}

static PyGetSetDef Inflater_getset[] = {
    {"_selected_backend", (getter)Inflater_get_backend, NULL,
     "Selected native inflater engine.", NULL},
    {"eof", (getter)Inflater_get_eof, NULL, "End of DEFLATE stream.", NULL},
    {"needs_input", (getter)Inflater_get_needs_input, NULL,
     "Whether more compressed input is needed.", NULL},
    {"unused_data", (getter)Inflater_get_unused_data, NULL,
     "Input bytes following the completed stream.", NULL},
    {"unconsumed_tail", (getter)Inflater_get_unconsumed_tail, NULL,
     "Input bytes not consumed by the last bounded read.", NULL},
    {NULL, NULL, NULL, NULL, NULL}
};

static PyType_Slot Inflater_slots[] = {
    {Py_tp_traverse, (void *)Inflater_traverse},
    {Py_tp_dealloc, (void *)Inflater_dealloc},
    {Py_tp_doc, (void *)"Bounded native raw DEFLATE inflater."},
    {Py_tp_methods, Inflater_methods},
    {Py_tp_getset, Inflater_getset},
    {Py_tp_init, (void *)Inflater_init},
    {Py_tp_new, (void *)Inflater_new},
    {0, NULL}
};
static PyType_Spec Inflater_spec = {
    .name = "unstd.compress._gzip_native.Inflater",
    .basicsize = sizeof(Inflater),
    .flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_IMMUTABLETYPE | Py_TPFLAGS_HAVE_GC,
    .slots = Inflater_slots,
};
int
unstd_add_inflater(PyObject *module)
{
    PyObject *type = PyType_FromModuleAndSpec(module, &Inflater_spec, NULL);
    if (type == NULL) return -1;
    int result = PyModule_AddObjectRef(module, "Inflater", type);
    Py_DECREF(type);
    return result;
}
