/* Checked immutable-dataset offset reads. JSON decoding stays with serde.
 * Buffer ownership spans every pointer read/copy, and each offset is checked
 * against fixed buffer geometry after any signal callback. No GIL release.
 */
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <stdint.h>
#include <string.h>
#ifndef MS_WINDOWS
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>
#endif

#define HEADER_SIZE 56

typedef struct {
    PyObject_HEAD
    char *data;
    Py_ssize_t length, exports;
    int closed;
} Mapping;

static int
mapping_opened(Mapping *self)
{
    if (self->closed) {
        PyErr_SetString(PyExc_ValueError, "mapping is closed");
        return -1;
    }
    return 0;
}

static Py_ssize_t
mapping_length(Mapping *self)
{
    if (mapping_opened(self) < 0) return -1;
    return self->length;
}

static int
mapping_buffer(Mapping *self, Py_buffer *view, int flags)
{
    if (mapping_opened(self) < 0) return -1;
    if (PyBuffer_FillInfo(view, (PyObject *)self, self->data, self->length, 1, flags) < 0)
        return -1;
    self->exports++;
    return 0;
}

static void
mapping_release(Mapping *self, Py_buffer *view)
{
    (void)view;
    if (self->exports > 0) self->exports--;
}

static PyObject *
mapping_close(Mapping *self, PyObject *unused)
{
    (void)unused;
    if (self->closed) Py_RETURN_NONE;
    if (self->exports != 0) {
        PyErr_SetString(PyExc_BufferError, "cannot close mapping with exported buffers");
        return NULL;
    }
#ifndef MS_WINDOWS
    if (munmap(self->data, (size_t)self->length) < 0)
        return PyErr_SetFromErrno(PyExc_OSError);
#endif
    self->closed = 1;
    self->data = NULL;
    self->length = 0;
    Py_RETURN_NONE;
}

static void
mapping_free(Mapping *self)
{
#ifndef MS_WINDOWS
    /* An exported Py_buffer owns a reference, so finalization cannot race it. */
    if (!self->closed) (void)munmap(self->data, (size_t)self->length);
#endif
    Py_TYPE(self)->tp_free((PyObject *)self);
}

static PyObject *
mapping_item(Mapping *self, PyObject *key)
{
    if (PySlice_Check(key)) {
        Py_ssize_t start, stop, step;
        /* Index conversion can execute Python and close the owner. Recheck
         * afterward, then pin the buffer across allocation and signals. */
        if (PySlice_Unpack(key, &start, &stop, &step) < 0 || mapping_opened(self) < 0)
            return NULL;
        Py_ssize_t length = PySlice_AdjustIndices(self->length, &start, &stop, step);
        Py_buffer view;
        if (PyObject_GetBuffer((PyObject *)self, &view, PyBUF_SIMPLE) < 0) return NULL;
        PyObject *result = PyBytes_FromStringAndSize(NULL, length);
        if (result != NULL) {
            char *output = PyBytes_AS_STRING(result);
            if (step == 1 && length != 0) memcpy(output, (const char *)view.buf + start, (size_t)length);
            else for (Py_ssize_t i = 0, position = start; i < length; i++) {
                if ((i & 1023) == 0 && PyErr_CheckSignals() < 0) {
                    Py_CLEAR(result);
                    break;
                }
                output[i] = ((const char *)view.buf)[position];
                if (i + 1 < length) position += step;
            }
        }
        PyBuffer_Release(&view);
        return result;
    }
    Py_ssize_t position = PyNumber_AsSsize_t(key, PyExc_IndexError);
    if ((position == -1 && PyErr_Occurred()) || mapping_opened(self) < 0) return NULL;
    if (position < 0) position += self->length;
    if (position < 0 || position >= self->length) {
        PyErr_SetString(PyExc_IndexError, "mapping index out of range");
        return NULL;
    }
    return PyLong_FromLong((unsigned char)self->data[position]);
}

static PyObject *
mapping_find(Mapping *self, PyObject *args)
{
    PyObject *needle;
    Py_ssize_t start, stop;
    if (!PyArg_ParseTuple(args, "O!nn:find", &PyBytes_Type, &needle, &start, &stop)) return NULL;
    if (mapping_opened(self) < 0) return NULL;
    if (PyBytes_GET_SIZE(needle) != 1) {
        PyErr_SetString(PyExc_ValueError, "mapping find requires one byte");
        return NULL;
    }
    if (start < 0) start += self->length;
    if (stop < 0) stop += self->length;
    if (start < 0) start = 0;
    if (stop > self->length) stop = self->length;
    if (start >= stop || start > self->length) return PyLong_FromLong(-1);
    const char *found = memchr(self->data + start, (unsigned char)PyBytes_AS_STRING(needle)[0], (size_t)(stop - start));
    return PyLong_FromSsize_t(found == NULL ? -1 : found - self->data);
}

static PyMappingMethods mapping_operations = {
    .mp_length = (lenfunc)mapping_length,
    .mp_subscript = (binaryfunc)mapping_item
};

static PyBufferProcs mapping_buffers = {
    .bf_getbuffer = (getbufferproc)mapping_buffer,
    .bf_releasebuffer = (releasebufferproc)mapping_release
};

static PyMethodDef mapping_methods[] = {
    {"close", (PyCFunction)mapping_close, METH_NOARGS, "Close after every exported buffer is released."},
    {"find", (PyCFunction)mapping_find, METH_VARARGS, "Find one byte inside checked slice bounds."},
    {NULL, NULL, 0, NULL}
};

static PyTypeObject MappingType = {
    PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = "unstd.dataset._dataset_native.Mapping",
    .tp_basicsize = sizeof(Mapping),
    .tp_dealloc = (destructor)mapping_free,
    .tp_as_mapping = &mapping_operations,
    .tp_as_buffer = &mapping_buffers,
    .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_DISALLOW_INSTANTIATION,
    .tp_doc = "Owned read-only mapping of a published immutable regular file.",
    .tp_methods = mapping_methods
};

static PyObject *
map_file(PyObject *module, PyObject *args)
{
    (void)module;
    int descriptor;
    if (!PyArg_ParseTuple(args, "i:map", &descriptor)) return NULL;
#ifdef MS_WINDOWS
    (void)descriptor;
    Py_RETURN_NONE;
#else
    /* Preserve the standard audit boundary before its callbacks can affect the
     * descriptor; the final size/type checks follow object allocation too. */
    if (PySys_Audit("mmap.__new__", "inin", descriptor, (Py_ssize_t)0, 1, (Py_ssize_t)0) < 0)
        return NULL;
    Mapping *mapping = PyObject_New(Mapping, &MappingType);
    if (mapping == NULL) return NULL;
    mapping->closed = 1;
    mapping->data = NULL;
    mapping->length = 0;
    mapping->exports = 0;
    struct stat status;
    if (fstat(descriptor, &status) < 0) {
        PyErr_SetFromErrno(PyExc_OSError);
        Py_DECREF(mapping);
        return NULL;
    }
    if (!S_ISREG(status.st_mode) || status.st_size <= 0) {
        Py_DECREF(mapping);
        PyErr_SetString(PyExc_ValueError, "mapping requires a nonempty regular file");
        return NULL;
    }
    if ((uintmax_t)status.st_size > (uintmax_t)PY_SSIZE_T_MAX) {
        Py_DECREF(mapping);
        PyErr_SetString(PyExc_OverflowError, "mapping exceeds the addressable buffer size");
        return NULL;
    }
    mapping->length = (Py_ssize_t)status.st_size;
    void *address = mmap(NULL, (size_t)mapping->length, PROT_READ, MAP_PRIVATE, descriptor, 0);
    if (address == MAP_FAILED) {
        PyErr_SetFromErrno(PyExc_OSError);
        Py_DECREF(mapping);
        return NULL;
    }
    mapping->data = address;
    mapping->closed = 0;
    return (PyObject *)mapping;
#endif
}

typedef struct {
    Py_buffer view;
    const unsigned char *data;
    Py_ssize_t index, count;
} Snapshot;

static uint64_t
little64(const unsigned char *data)
{
    uint64_t value = 0;
    for (unsigned int i = 0; i < 8; i++) value |= (uint64_t)data[i] << (8 * i);
    return value;
}

static int
invalid(const char *detail)
{
    PyErr_Format(PyExc_ValueError, "invalid dataset %s", detail);
    return -1;
}

static int
snapshot_open(Snapshot *snapshot, PyObject *source, Py_ssize_t index,
              Py_ssize_t count)
{
    static const unsigned char magic[8] = {'U', 'N', 'S', 'T', 'D', 'D', 'S', 2};
    memset(snapshot, 0, sizeof(*snapshot));
    if (PyObject_GetBuffer(source, &snapshot->view, PyBUF_CONTIG_RO) < 0) return -1;
    snapshot->data = snapshot->view.buf;
    snapshot->index = index;
    snapshot->count = count;
    Py_ssize_t length = snapshot->view.len;
    if (length < HEADER_SIZE || index < HEADER_SIZE || index > length || count < 0 ||
        length - index < 8 || (length - index) % 8 != 0 ||
        (length - index) / 8 - 1 != count ||
        memcmp(snapshot->data, magic, sizeof(magic)) != 0 ||
        little64(snapshot->data + 8) != (uint64_t)count ||
        little64(snapshot->data + 16) != (uint64_t)index ||
        little64(snapshot->data + index) != HEADER_SIZE ||
        little64(snapshot->data + index + 8 * count) != (uint64_t)index) {
        PyBuffer_Release(&snapshot->view);
        return invalid("geometry");
    }
    return 0;
}

static int
bounds(Snapshot *snapshot, Py_ssize_t position, Py_ssize_t *start, Py_ssize_t *stop)
{
    if (position < 0 || position >= snapshot->count) {
        PyErr_SetString(PyExc_IndexError, "dataset index out of range");
        return -1;
    }
    const unsigned char *entry = snapshot->data + snapshot->index + 8 * position;
    uint64_t first = little64(entry), last = little64(entry + 8);
    if (first < HEADER_SIZE || first >= (uint64_t)snapshot->index ||
        last <= first || last - first <= 1 || last > (uint64_t)snapshot->index ||
        snapshot->data[last - 1] != '\n') return invalid("row offsets or terminator");
    if (memchr(snapshot->data + first, '\n', (size_t)(last - first - 1)) != NULL)
        return invalid("interior row newline");
    *start = (Py_ssize_t)first;
    *stop = (Py_ssize_t)last;
    return 0;
}

static PyObject *
row(PyObject *module, PyObject *args)
{
    (void)module;
    PyObject *source;
    Py_ssize_t index, count, position, start, stop;
    if (!PyArg_ParseTuple(args, "Onnn:row", &source, &index, &count, &position)) return NULL;
    Snapshot snapshot;
    if (snapshot_open(&snapshot, source, index, count) < 0) return NULL;
    PyObject *result = NULL;
    if (bounds(&snapshot, position, &start, &stop) == 0)
        result = PyBytes_FromStringAndSize((const char *)snapshot.data + start, stop - start - 1);
    PyBuffer_Release(&snapshot.view);
    return result;
}

static PyObject *
take(PyObject *module, PyObject *args)
{
    (void)module;
    PyObject *source, *positions;
    Py_ssize_t index, count;
    if (!PyArg_ParseTuple(args, "OnnO:take", &source, &index, &count, &positions)) return NULL;
    if (!PyTuple_CheckExact(positions)) {
        PyErr_SetString(PyExc_TypeError, "positions must be a tuple of integers");
        return NULL;
    }
    Snapshot snapshot;
    if (snapshot_open(&snapshot, source, index, count) < 0) return NULL;
    Py_ssize_t length = PyTuple_GET_SIZE(positions);
    PyObject *result = PyList_New(length);
    if (result == NULL) goto done;
    for (Py_ssize_t i = 0; i < length; i++) {
        if ((i & 1023) == 0 && PyErr_CheckSignals() < 0) goto fail;
        PyObject *item = PyTuple_GET_ITEM(positions, i);
        if (!PyLong_Check(item)) {
            PyErr_SetString(PyExc_TypeError, "positions must contain integers");
            goto fail;
        }
        Py_ssize_t position = PyLong_AsSsize_t(item), start, stop;
        if ((position == -1 && PyErr_Occurred()) ||
            bounds(&snapshot, position, &start, &stop) < 0) goto fail;
        PyObject *payload = PyBytes_FromStringAndSize(
            (const char *)snapshot.data + start, stop - start - 1);
        if (payload == NULL) goto fail;
        PyList_SET_ITEM(result, i, payload);
    }
    goto done;
fail:
    Py_CLEAR(result);
done:
    PyBuffer_Release(&snapshot.view);
    return result;
}

static PyObject *
span(PyObject *module, PyObject *args)
{
    (void)module;
    PyObject *source;
    Py_ssize_t index, count, first, last;
    if (!PyArg_ParseTuple(args, "Onnnn:span", &source, &index, &count, &first, &last)) return NULL;
    Snapshot snapshot;
    if (snapshot_open(&snapshot, source, index, count) < 0) return NULL;
    PyObject *result = NULL;
    Py_ssize_t start = HEADER_SIZE, stop = HEADER_SIZE;
    if (first < 0 || last < first || last > count) {
        PyErr_SetString(PyExc_IndexError, "dataset span out of range");
        goto done;
    }
    for (Py_ssize_t i = first; i < last; i++) {
        Py_ssize_t row_start, row_stop;
        if (((i - first) & 1023) == 0 && PyErr_CheckSignals() < 0) goto done;
        if (bounds(&snapshot, i, &row_start, &row_stop) < 0) goto done;
        if (i == first) start = row_start;
        else if (row_start != stop) {
            invalid("noncontiguous row offsets");
            goto done;
        }
        stop = row_stop;
    }
    result = PyBytes_FromStringAndSize((const char *)snapshot.data + start, stop - start);
done:
    PyBuffer_Release(&snapshot.view);
    return result;
}

static PyObject *
validate(PyObject *module, PyObject *args)
{
    (void)module;
    PyObject *source;
    Py_ssize_t index, count;
    if (!PyArg_ParseTuple(args, "Onn:validate", &source, &index, &count)) return NULL;
    Snapshot snapshot;
    if (snapshot_open(&snapshot, source, index, count) < 0) return NULL;
    int valid = 1;
    Py_ssize_t previous = HEADER_SIZE;
    for (Py_ssize_t i = 0; i < count; i++) {
        Py_ssize_t start, stop;
        if ((i & 1023) == 0 && PyErr_CheckSignals() < 0) { valid = 0; break; }
        if (bounds(&snapshot, i, &start, &stop) < 0) { valid = 0; break; }
        if (start != previous) { invalid("noncontiguous row offsets"); valid = 0; break; }
        previous = stop;
    }
    PyBuffer_Release(&snapshot.view);
    if (!valid) return NULL;
    Py_RETURN_NONE;
}

static PyMethodDef methods[] = {
    {"map", map_file, METH_VARARGS, "Map a regular file read-only on POSIX; return None on Windows."},
    {"row", row, METH_VARARGS, "Copy one checked JSON row without its framing newline."},
    {"take", take, METH_VARARGS, "Copy checked rows in requested order."},
    {"span", span, METH_VARARGS, "Copy a checked contiguous row span."},
    {"validate", validate, METH_VARARGS, "Validate every row offset and terminator."},
    {NULL, NULL, 0, NULL}
};

static struct PyModuleDef definition = {
    PyModuleDef_HEAD_INIT, "_dataset_native", "Checked dataset offset-copy kernels.",
    0, methods, NULL, NULL, NULL, NULL
};

PyMODINIT_FUNC
PyInit__dataset_native(void)
{
    PyObject *module = PyModule_Create(&definition);
    if (module == NULL) return NULL;
    if (PyType_Ready(&MappingType) < 0 ||
        PyModule_AddObjectRef(module, "Mapping", (PyObject *)&MappingType) < 0 ||
        PyModule_AddIntConstant(module, "KERNEL_VERSION", 2) < 0) {
        Py_DECREF(module);
        return NULL;
    }
    return module;
}
