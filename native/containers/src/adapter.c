/* Segmented-list kernels for unstd-owned sortedcontainers subclasses.
 *
 * The upstream classes and generic Python comparison protocol stay untouched.
 * METH_METHOD supplies the defining subclass, so inherited calls from arbitrary
 * user subclasses can delegate to captured upstream methods. Exact subclasses
 * use ordinary instance-dict state and exact built-in lists/integers only.
 * Every list item crossing a Python callback is owned, and all indexing after
 * a comparison uses checked public APIs: comparator reentrancy cannot leave a
 * borrowed item or cached list buffer dangling.
 */
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <limits.h>
#include <stdint.h>

#define CONFIG_NAME "unstd.containers.sorted-config.v1"
#define CONFIG_ATTRIBUTE "_unstd_sorted_native_config"

enum Method { ADD, BISECT_LEFT, BISECT_RIGHT, LOC, POS, EXPAND, METHOD_COUNT };
enum Field { LISTS, MAXES, INDEX, OFFSET, LENGTH, LOAD, FIELD_COUNT };

typedef struct {
    PyObject *methods[METHOD_COUNT];
    PyObject *fields[FIELD_COUNT];
    PyObject *expand_name;
    PyObject *loc_name;
    PyObject *build_name;
    PyObject *saved_loc_name;
    PyObject *saved_expand_name;
    PyObject *bisect[2];
    PyObject *insort;
} Config;

typedef struct {
    PyObject *capsule;
    Config *config;
    PyObject *dict;
} Context;

static const char *method_names[METHOD_COUNT] = {
    "add", "bisect_left", "bisect_right", "_loc", "_pos", "_expand"
};
static const char *field_names[FIELD_COUNT] = {
    "_lists", "_maxes", "_index", "_offset", "_len", "_load"
};

static void
config_free(PyObject *capsule)
{
    Config *config = PyCapsule_GetPointer(capsule, CONFIG_NAME);
    if (config == NULL) {
        PyErr_Clear();
        return;
    }
    for (int i = 0; i < METHOD_COUNT; i++) Py_XDECREF(config->methods[i]);
    for (int i = 0; i < FIELD_COUNT; i++) Py_XDECREF(config->fields[i]);
    Py_XDECREF(config->expand_name);
    Py_XDECREF(config->loc_name);
    Py_XDECREF(config->build_name);
    Py_XDECREF(config->saved_loc_name);
    Py_XDECREF(config->saved_expand_name);
    Py_XDECREF(config->bisect[0]);
    Py_XDECREF(config->bisect[1]);
    Py_XDECREF(config->insort);
    PyMem_Free(config);
}

static void
context_clear(Context *context)
{
    Py_XDECREF(context->dict);
    Py_XDECREF(context->capsule);
}

/* Returns one for our ordinary exact instances, zero for upstream fallback. */
static int
context_get(Context *context, PyObject *self, PyTypeObject *defining_class)
{
    context->capsule = NULL;
    context->dict = NULL;
    PyObject *capsule = PyDict_GetItemString(defining_class->tp_dict,
                                           CONFIG_ATTRIBUTE);
    if (capsule == NULL || !PyCapsule_IsValid(capsule, CONFIG_NAME)) {
        PyErr_SetString(PyExc_RuntimeError, "sorted native configuration is missing");
        return -1;
    }
    context->capsule = Py_NewRef(capsule);
    context->config = PyCapsule_GetPointer(capsule, CONFIG_NAME);
    if (context->config == NULL) return -1;
    if (Py_TYPE(self) != defining_class ||
        defining_class->tp_getattro != PyObject_GenericGetAttr)
        return 0;
    context->dict = PyObject_GenericGetDict(self, NULL);
    if (context->dict == NULL) return -1;
    if (!PyDict_CheckExact(context->dict)) return 0;
    return 1;
}

static int
context_refresh(Context *context, PyObject *self)
{
    /* A detached old dictionary can own finalizers which replace the live
     * dictionary again. Complete those releases before capturing live state. */
    Py_CLEAR(context->dict);
    PyObject *dict = PyObject_GenericGetDict(self, NULL);
    if (dict == NULL) return -1;
    context->dict = dict;
    if (!PyDict_CheckExact(dict)) {
        PyErr_SetString(PyExc_TypeError, "sorted list instance dictionary changed");
        return -1;
    }
    return 0;
}

static int
helper_unchanged(Context *context, PyTypeObject *cls, int expand)
{
    PyObject *name = expand ? context->config->expand_name : context->config->loc_name;
    PyObject *saved_name = expand ? context->config->saved_expand_name
                                 : context->config->saved_loc_name;
    if (PyDict_GetItemWithError(context->dict, name) != NULL) return 0;
    PyObject *current = PyDict_GetItemWithError(cls->tp_dict, name);
    PyObject *saved = PyDict_GetItemWithError(cls->tp_dict, saved_name);
    return current != NULL && current == saved;
}

static PyObject *loc_value(Context *, PyObject *, Py_ssize_t, Py_ssize_t);
static PyObject *expand_value(Context *, PyObject *, Py_ssize_t);

static PyObject *
field(Context *context, enum Field name)
{
    return PyDict_GetItemWithError(context->dict, context->config->fields[name]);
}

static int
integer_field(Context *context, enum Field name, Py_ssize_t *result)
{
    PyObject *value = field(context, name);
    if (value == NULL || !PyLong_CheckExact(value)) return 0;
    *result = PyLong_AsSsize_t(value);
    if (*result == -1 && PyErr_Occurred()) {
        PyErr_Clear();
        return 0;
    }
    return 1;
}

static PyObject *
list_field(Context *context, enum Field name)
{
    PyObject *value = field(context, name);
    return value != NULL && PyList_CheckExact(value) ? Py_NewRef(value) : NULL;
}

static PyObject *
fallback(Context *context, enum Method method, PyObject *self,
         PyObject *const *args, size_t nargs, PyObject *kwnames)
{
    size_t keywords = kwnames == NULL ? 0 : (size_t)PyTuple_GET_SIZE(kwnames);
    if (nargs > SIZE_MAX - keywords - 1 ||
        nargs + keywords + 1 > SIZE_MAX / sizeof(PyObject *))
        return PyErr_NoMemory();
    size_t total = nargs + keywords + 1;
    PyObject **forward = PyMem_Malloc(total * sizeof(PyObject *));
    if (forward == NULL) return PyErr_NoMemory();
    forward[0] = self;
    for (size_t i = 1; i < total; i++) forward[i] = args[i - 1];
    PyObject *result = PyObject_Vectorcall(context->config->methods[method],
                                         forward, nargs + 1, kwnames);
    PyMem_Free(forward);
    return result;
}

static PyObject *
single_argument(PyObject *const *args, size_t nargs, PyObject *kwnames,
                const char *name)
{
    if (kwnames == NULL || PyTuple_GET_SIZE(kwnames) == 0)
        return nargs == 1 ? args[0] : NULL;
    if (nargs == 0 && PyTuple_GET_SIZE(kwnames) == 1 &&
        PyUnicode_Check(PyTuple_GET_ITEM(kwnames, 0)) &&
        PyUnicode_CompareWithASCIIString(PyTuple_GET_ITEM(kwnames, 0), name) == 0)
        return args[0];
    return NULL;
}

static PyObject *
owned_item(PyObject *list, Py_ssize_t index)
{
    PyObject *item = PyList_GetItem(list, index);
    return item == NULL ? NULL : Py_NewRef(item);
}

/* Reuse the stdlib kernel itself, including its same-type comparison fast path
 * and NotImplemented/reflected-comparison behavior. It owns each fetched item
 * across callbacks and performs checked indexing after reentrant mutations. */
static int
bisect_list(Config *config, PyObject *list, PyObject *value, int right,
            Py_ssize_t *result)
{
    PyObject *args[] = {list, value};
    PyObject *rank = PyObject_Vectorcall(config->bisect[right], args, 2, NULL);
    if (rank == NULL) return -1;
    *result = PyLong_AsSsize_t(rank);
    Py_DECREF(rank);
    return *result == -1 && PyErr_Occurred() ? -1 : 0;
}

static PyObject *
call_index_helper(Context *context, PyObject *self, Py_ssize_t position,
                  Py_ssize_t index)
{
    PyObject *pos = PyLong_FromSsize_t(position);
    PyObject *idx = PyLong_FromSsize_t(index);
    PyObject *result = NULL;
    if (pos != NULL && idx != NULL)
        result = PyObject_CallMethodObjArgs(self, context->config->loc_name,
                                           pos, idx, NULL);
    Py_XDECREF(pos);
    Py_XDECREF(idx);
    return result;
}

static PyObject *
native_bisect(PyObject *self, PyTypeObject *defining_class,
              PyObject *const *args, size_t nargs, PyObject *kwnames, int right)
{
    Context context;
    int admitted = context_get(&context, self, defining_class);
    PyObject *result = NULL, *maxes = NULL, *lists = NULL, *sublist = NULL;
    PyObject *value = single_argument(args, nargs, kwnames, "value");
    enum Method method = right ? BISECT_RIGHT : BISECT_LEFT;
    if (admitted < 0) goto done;
    if (!admitted || value == NULL) goto slow;
    PyObject *storage = field(&context, LISTS);
    if (storage == NULL || !PyList_CheckExact(storage)) goto slow;
    maxes = list_field(&context, MAXES);
    if (maxes == NULL) goto slow;
    if (PyList_GET_SIZE(maxes) == 0) {
        result = PyLong_FromLong(0);
        goto done;
    }
    Py_ssize_t position, index;
    if (bisect_list(context.config, maxes, value, right, &position) < 0) goto done;
    if (context_refresh(&context, self) < 0) goto done;
    if (position == PyList_GET_SIZE(maxes)) {
        PyObject *length = field(&context, LENGTH);
        if (length == NULL) {
            if (!PyErr_Occurred())
                PyErr_SetString(PyExc_AttributeError, "_len");
        } else result = Py_NewRef(length);
        goto done;
    }
    lists = list_field(&context, LISTS);
    if (lists == NULL) {
        PyErr_SetString(PyExc_TypeError, "sorted list storage changed during comparison");
        goto done;
    }
    sublist = owned_item(lists, position);
    if (sublist == NULL) goto done;
    if (!PyList_CheckExact(sublist)) {
        PyErr_SetString(PyExc_TypeError, "sorted list block changed during comparison");
        goto done;
    }
    if (bisect_list(context.config, sublist, value, right, &index) < 0) goto done;
    if (context_refresh(&context, self) < 0) goto done;
    if (helper_unchanged(&context, defining_class, 0)) {
        result = loc_value(&context, self, position, index);
        if (result != NULL || PyErr_Occurred()) goto done;
    }
    result = call_index_helper(&context, self, position, index);
    goto done;
slow:
    result = fallback(&context, method, self, args, nargs, kwnames);
done:
    Py_XDECREF(sublist);
    Py_XDECREF(lists);
    Py_XDECREF(maxes);
    context_clear(&context);
    return result;
}

static PyObject *
native_bisect_left(PyObject *self, PyTypeObject *defining_class,
                   PyObject *const *args, size_t nargs, PyObject *kwnames)
{
    return native_bisect(self, defining_class, args, nargs, kwnames, 0);
}

static PyObject *
native_bisect_right(PyObject *self, PyTypeObject *defining_class,
                    PyObject *const *args, size_t nargs, PyObject *kwnames)
{
    return native_bisect(self, defining_class, args, nargs, kwnames, 1);
}

static PyObject *
loc_value(Context *context, PyObject *self, Py_ssize_t position, Py_ssize_t local)
{
    if (position == 0) return PyLong_FromSsize_t(local);
    PyObject *result = NULL;
    PyObject *index = list_field(context, INDEX);
    if (index == NULL) return NULL;
    if (PyList_GET_SIZE(index) == 0) {
        PyObject *built = PyObject_CallMethodObjArgs(self, context->config->build_name,
                                                    NULL);
        if (built == NULL) goto done;
        Py_DECREF(built);
        if (context_refresh(context, self) < 0) goto done;
    }
    Py_ssize_t offset;
    if (!integer_field(context, OFFSET, &offset) || offset < 0 ||
        position > PY_SSIZE_T_MAX - offset) {
        PyErr_SetString(PyExc_TypeError, "invalid sorted list index offset");
        goto done;
    }
    position += offset;
    Py_ssize_t total = local;
    while (position) {
        if (!(position & 1)) {
            PyObject *item = PyList_GetItem(index, position - 1);
            if (item == NULL) goto done;
            if (!PyLong_CheckExact(item)) {
                PyErr_SetString(PyExc_TypeError, "invalid sorted list positional index");
                goto done;
            }
            Py_ssize_t count = PyLong_AsSsize_t(item);
            if ((count == -1 && PyErr_Occurred()) || count < 0 ||
                total > PY_SSIZE_T_MAX - count) {
                if (!PyErr_Occurred())
                    PyErr_SetString(PyExc_OverflowError, "sorted list rank overflow");
                goto done;
            }
            total += count;
        }
        position = (position - 1) >> 1;
    }
    result = PyLong_FromSsize_t(total);
    goto done;
done:
    Py_XDECREF(index);
    return result;
}

static PyObject *
native_loc(PyObject *self, PyTypeObject *defining_class,
           PyObject *const *args, size_t nargs, PyObject *kwnames)
{
    /* Upstream returns idx immediately for the first block without reading
     * instance state. Keep that common path equally cheap and callback-free. */
    if (Py_TYPE(self) == defining_class && nargs == 2 &&
        (kwnames == NULL || PyTuple_GET_SIZE(kwnames) == 0) &&
        PyLong_CheckExact(args[0]) && PyObject_IsTrue(args[0]) == 0)
        return Py_NewRef(args[1]);
    Context context;
    int admitted = context_get(&context, self, defining_class);
    PyObject *result = NULL;
    if (admitted < 0) goto done;
    if (!admitted || nargs != 2 ||
        (kwnames != NULL && PyTuple_GET_SIZE(kwnames) != 0) ||
        !PyLong_CheckExact(args[0]) || !PyLong_CheckExact(args[1])) goto slow;
    Py_ssize_t position = PyLong_AsSsize_t(args[0]);
    if (position == -1 && PyErr_Occurred()) { PyErr_Clear(); goto slow; }
    if (position == 0) { result = Py_NewRef(args[1]); goto done; }
    Py_ssize_t local = PyLong_AsSsize_t(args[1]);
    if (local == -1 && PyErr_Occurred()) { PyErr_Clear(); goto slow; }
    if (position < 0 || local < 0) goto slow;
    result = loc_value(&context, self, position, local);
    if (result != NULL || PyErr_Occurred()) goto done;
slow:
    result = fallback(&context, LOC, self, args, nargs, kwnames);
done:
    context_clear(&context);
    return result;
}

static PyObject *
native_pos(PyObject *self, PyTypeObject *defining_class,
           PyObject *const *args, size_t nargs, PyObject *kwnames)
{
    Context context;
    int admitted = context_get(&context, self, defining_class);
    PyObject *result = NULL, *lists = NULL, *index = NULL, *block = NULL;
    PyObject *argument = single_argument(args, nargs, kwnames, "idx");
    if (admitted < 0) goto done;
    if (!admitted || argument == NULL || !PyLong_CheckExact(argument)) goto slow;
    Py_ssize_t local = PyLong_AsSsize_t(argument), length;
    if (local == -1 && PyErr_Occurred()) { PyErr_Clear(); goto slow; }
    lists = list_field(&context, LISTS);
    if (lists == NULL || !integer_field(&context, LENGTH, &length) || length < 0)
        goto slow;
    Py_ssize_t count = PyList_GET_SIZE(lists);
    if (local < 0) {
        block = owned_item(lists, count - 1);
        if (block == NULL) goto done;
        if (!PyList_CheckExact(block)) goto slow;
        Py_ssize_t last = PyList_GET_SIZE(block);
        Py_CLEAR(block);
        if (local >= -last) {
            result = Py_BuildValue("nn", count - 1, last + local);
            goto done;
        }
        local += length;
        if (local < 0) goto out_of_range;
    } else if (local >= length) goto out_of_range;
    block = owned_item(lists, 0);
    if (block == NULL) goto done;
    if (!PyList_CheckExact(block)) goto slow;
    Py_ssize_t first = PyList_GET_SIZE(block);
    Py_CLEAR(block);
    if (local < first) {
        result = Py_BuildValue("nn", (Py_ssize_t)0, local);
        goto done;
    }
    index = list_field(&context, INDEX);
    if (index == NULL) goto slow;
    if (PyList_GET_SIZE(index) == 0) {
        PyObject *built = PyObject_CallMethodObjArgs(self, context.config->build_name,
                                                    NULL);
        if (built == NULL) goto done;
        Py_DECREF(built);
        if (context_refresh(&context, self) < 0) goto done;
    }
    Py_ssize_t position = 0, child = 1;
    while (child < PyList_GET_SIZE(index)) {
        PyObject *item = PyList_GetItem(index, child);
        if (item == NULL) goto done;
        if (!PyLong_CheckExact(item)) {
            PyErr_SetString(PyExc_TypeError, "invalid sorted list positional index");
            goto done;
        }
        Py_ssize_t count = PyLong_AsSsize_t(item);
        if ((count == -1 && PyErr_Occurred()) || count < 0) {
            if (!PyErr_Occurred())
                PyErr_SetString(PyExc_ValueError, "negative sorted list positional index");
            goto done;
        }
        if (local < count) position = child;
        else { local -= count; position = child + 1; }
        if (position > (PY_SSIZE_T_MAX - 1) / 2) {
            PyErr_SetString(PyExc_OverflowError, "sorted list positional index overflow");
            goto done;
        }
        child = (position << 1) + 1;
    }
    Py_ssize_t offset;
    if (!integer_field(&context, OFFSET, &offset) || offset < 0) {
        PyErr_SetString(PyExc_TypeError, "invalid sorted list index offset");
        goto done;
    }
    result = Py_BuildValue("nn", position - offset, local);
    goto done;
out_of_range:
    PyErr_SetString(PyExc_IndexError, "list index out of range");
    goto done;
slow:
    result = fallback(&context, POS, self, args, nargs, kwnames);
done:
    Py_XDECREF(block);
    Py_XDECREF(index);
    Py_XDECREF(lists);
    context_clear(&context);
    return result;
}

static PyObject *
expand_value(Context *context, PyObject *self, Py_ssize_t position)
{
    (void)self;
    Py_ssize_t load;
    PyObject *result = NULL, *lists = NULL, *index = NULL, *maxes = NULL;
    PyObject *block = NULL, *half = NULL, *maximum = NULL;
    lists = list_field(context, LISTS);
    index = list_field(context, INDEX);
    if (position < 0 || lists == NULL || index == NULL ||
        !integer_field(context, LOAD, &load) || load < 1) goto done;
    block = owned_item(lists, position);
    if (block == NULL) goto done;
    if (!PyList_CheckExact(block)) goto done;
    Py_ssize_t length = PyList_GET_SIZE(block);
    if (load <= PY_SSIZE_T_MAX / 2 && length > load * 2) {
        maxes = list_field(context, MAXES);
        if (maxes == NULL) goto done;
        half = PyList_GetSlice(block, load, length);
        if (half == NULL) goto done;
        if (PyList_SetSlice(block, load, length, NULL) < 0) goto done;
        maximum = owned_item(block, PyList_GET_SIZE(block) - 1);
        if (maximum == NULL) goto done;
        if (PyList_SetItem(maxes, position, maximum) < 0) {
            maximum = NULL;
            goto done;
        }
        maximum = NULL;
        if (PyList_Insert(lists, position + 1, half) < 0) goto done;
        maximum = owned_item(half, PyList_GET_SIZE(half) - 1);
        if (maximum == NULL || PyList_Insert(maxes, position + 1, maximum) < 0)
            goto done;
        if (PyList_SetSlice(index, 0, PyList_GET_SIZE(index), NULL) < 0) goto done;
    } else if (PyList_GET_SIZE(index)) {
        Py_ssize_t offset;
        if (!integer_field(context, OFFSET, &offset) || offset < 0 ||
            position > PY_SSIZE_T_MAX - offset) goto done;
        Py_ssize_t path[sizeof(Py_ssize_t) * CHAR_BIT + 1];
        Py_ssize_t values[sizeof(Py_ssize_t) * CHAR_BIT + 1];
        Py_ssize_t used = 0, child = offset + position;
        while (1) {
            PyObject *item = PyList_GetItem(index, child);
            if (item == NULL) goto done;
            if (!PyLong_CheckExact(item)) goto done;
            Py_ssize_t value = PyLong_AsSsize_t(item);
            if ((value == -1 && PyErr_Occurred()) || value == PY_SSIZE_T_MAX) {
                PyErr_Clear();
                goto done;
            }
            path[used] = child;
            values[used++] = value + 1;
            if (child == 0) break;
            child = (child - 1) >> 1;
        }
        for (Py_ssize_t i = 0; i < used; i++) {
            PyObject *value = PyLong_FromSsize_t(values[i]);
            if (value == NULL) goto done;
            if (PyList_SetItem(index, path[i], value) < 0) goto done;
        }
    }
    result = Py_NewRef(Py_None);
    goto done;
done:
    Py_XDECREF(maximum);
    Py_XDECREF(half);
    Py_XDECREF(block);
    Py_XDECREF(maxes);
    Py_XDECREF(index);
    Py_XDECREF(lists);
    return result;
}

static PyObject *
native_expand(PyObject *self, PyTypeObject *defining_class,
              PyObject *const *args, size_t nargs, PyObject *kwnames)
{
    Context context;
    int admitted = context_get(&context, self, defining_class);
    PyObject *result = NULL;
    PyObject *argument = single_argument(args, nargs, kwnames, "pos");
    if (admitted < 0) goto done;
    if (!admitted || argument == NULL || !PyLong_CheckExact(argument)) goto slow;
    Py_ssize_t position = PyLong_AsSsize_t(argument);
    if (position == -1 && PyErr_Occurred()) { PyErr_Clear(); goto slow; }
    result = expand_value(&context, self, position);
    if (result != NULL || PyErr_Occurred()) goto done;
slow:
    result = fallback(&context, EXPAND, self, args, nargs, kwnames);
done:
    context_clear(&context);
    return result;
}

static PyObject *
native_add(PyObject *self, PyTypeObject *defining_class,
           PyObject *const *args, size_t nargs, PyObject *kwnames)
{
    Context context;
    int admitted = context_get(&context, self, defining_class);
    PyObject *result = NULL, *lists = NULL, *maxes = NULL, *block = NULL;
    PyObject *value = single_argument(args, nargs, kwnames, "value");
    if (admitted < 0) goto done;
    if (!admitted || value == NULL) goto slow;
    Py_ssize_t initial_length;
    if (!integer_field(&context, LENGTH, &initial_length) || initial_length < 0)
        goto slow;
    lists = list_field(&context, LISTS);
    maxes = list_field(&context, MAXES);
    if (lists == NULL || maxes == NULL) goto slow;
    if (PyList_GET_SIZE(maxes)) {
        Py_ssize_t position;
        if (bisect_list(context.config, maxes, value, 1, &position) < 0) goto done;
        int append = position == PyList_GET_SIZE(maxes);
        if (append) position--;
        block = owned_item(lists, position);
        if (block == NULL) goto done;
        if (!PyList_CheckExact(block)) {
            PyErr_SetString(PyExc_TypeError, "sorted list block changed during comparison");
            goto done;
        }
        if (append) {
            if (PyList_Append(block, value) < 0) goto done;
            if (PyList_SetItem(maxes, position, Py_NewRef(value)) < 0) goto done;
        } else {
            PyObject *arguments[] = {block, value};
            PyObject *inserted = PyObject_Vectorcall(context.config->insort,
                                                     arguments, 2, NULL);
            if (inserted == NULL) goto done;
            Py_DECREF(inserted);
        }
        if (context_refresh(&context, self) < 0) goto done;
        PyObject *expanded = NULL;
        if (helper_unchanged(&context, defining_class, 1))
            expanded = expand_value(&context, self, position);
        if (expanded == NULL && !PyErr_Occurred()) {
            PyObject *pos = PyLong_FromSsize_t(position);
            if (pos == NULL) goto done;
            expanded = PyObject_CallMethodObjArgs(self, context.config->expand_name,
                                                  pos, NULL);
            Py_DECREF(pos);
        }
        if (expanded == NULL) goto done;
        Py_DECREF(expanded);
    } else {
        block = PyList_New(0);
        if (block == NULL || PyList_Append(block, value) < 0 ||
            PyList_Append(lists, block) < 0 || PyList_Append(maxes, value) < 0)
            goto done;
    }
    if (context_refresh(&context, self) < 0) goto done;
    Py_ssize_t length;
    if (!integer_field(&context, LENGTH, &length) || length == PY_SSIZE_T_MAX) {
        PyErr_SetString(PyExc_TypeError, "sorted list length changed during insertion");
        goto done;
    }
    PyObject *next = PyLong_FromSsize_t(length + 1);
    if (next == NULL) goto done;
    int updated = PyDict_SetItem(context.dict, context.config->fields[LENGTH], next);
    Py_DECREF(next);
    if (updated < 0) goto done;
    result = Py_NewRef(Py_None);
    goto done;
slow:
    result = fallback(&context, ADD, self, args, nargs, kwnames);
done:
    Py_XDECREF(block);
    Py_XDECREF(maxes);
    Py_XDECREF(lists);
    context_clear(&context);
    return result;
}

#define METHOD_FLAGS (METH_METHOD | METH_FASTCALL | METH_KEYWORDS)
#define CMETHOD(function) ((PyCFunction)(void (*)(void))(function))

static PyMethodDef kernels[] = {
    {"add", CMETHOD(native_add), METHOD_FLAGS,
     "add($self, value)\n--\n\nAdd value while retaining sorted order."},
    {"bisect_left", CMETHOD(native_bisect_left), METHOD_FLAGS,
     "bisect_left($self, value)\n--\n\nReturn the insertion rank before equal values."},
    {"bisect_right", CMETHOD(native_bisect_right), METHOD_FLAGS,
     "bisect_right($self, value)\n--\n\nReturn the insertion rank after equal values."},
    {"bisect", CMETHOD(native_bisect_right), METHOD_FLAGS,
     "bisect($self, value)\n--\n\nReturn the insertion rank after equal values."},
    {"_loc", CMETHOD(native_loc), METHOD_FLAGS,
     "_loc($self, pos, idx)\n--\n\nTranslate a block offset to a global rank."},
    {"_pos", CMETHOD(native_pos), METHOD_FLAGS,
     "_pos($self, idx)\n--\n\nTranslate a global rank to a block offset."},
    {"_expand", CMETHOD(native_expand), METHOD_FLAGS,
     "_expand($self, pos)\n--\n\nSplit a block or maintain its positional index."},
    {NULL, NULL, 0, NULL}
};

static PyObject *
install(PyObject *module, PyObject *args, PyObject *kwargs)
{
    (void)module;
    static char *keywords[] = {"cls", "base", "keyed", NULL};
    PyObject *cls, *base;
    int keyed = 0;
    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "OO|p:install", keywords,
                                     &cls, &base, &keyed)) return NULL;
    if (!PyType_Check(cls) || !PyType_Check(base) || cls == base ||
        !PyType_IsSubtype((PyTypeObject *)cls, (PyTypeObject *)base) ||
        !(((PyTypeObject *)cls)->tp_flags & Py_TPFLAGS_HEAPTYPE)) {
        PyErr_SetString(PyExc_TypeError, "install requires a distinct Python subclass and its base");
        return NULL;
    }
    Config *config = PyMem_Calloc(1, sizeof(Config));
    if (config == NULL) return PyErr_NoMemory();
    PyObject *capsule = PyCapsule_New(config, CONFIG_NAME, config_free);
    if (capsule == NULL) { PyMem_Free(config); return NULL; }
    PyObject *bisect_module = PyImport_ImportModule("_bisect");
    if (bisect_module == NULL) goto failed;
    config->bisect[0] = PyObject_GetAttrString(bisect_module, "bisect_left");
    config->bisect[1] = PyObject_GetAttrString(bisect_module, "bisect_right");
    config->insort = PyObject_GetAttrString(bisect_module, "insort_right");
    Py_DECREF(bisect_module);
    if (config->bisect[0] == NULL || config->bisect[1] == NULL ||
        config->insort == NULL) goto failed;
    for (int i = 0; i < METHOD_COUNT; i++) {
        config->methods[i] = PyObject_GetAttrString(base, method_names[i]);
        if (config->methods[i] == NULL) goto failed;
    }
    for (int i = 0; i < FIELD_COUNT; i++) {
        config->fields[i] = PyUnicode_InternFromString(field_names[i]);
        if (config->fields[i] == NULL) goto failed;
    }
    config->expand_name = PyUnicode_InternFromString("_expand");
    config->loc_name = PyUnicode_InternFromString("_loc");
    config->build_name = PyUnicode_InternFromString("_build_index");
    config->saved_loc_name = PyUnicode_InternFromString("_unstd_native_loc_descriptor");
    config->saved_expand_name = PyUnicode_InternFromString("_unstd_native_expand_descriptor");
    if (config->expand_name == NULL || config->loc_name == NULL ||
        config->build_name == NULL || config->saved_loc_name == NULL ||
        config->saved_expand_name == NULL ||
        PyObject_SetAttrString(cls, CONFIG_ATTRIBUTE, capsule) < 0) goto failed;
    for (PyMethodDef *method = kernels; method->ml_name != NULL; method++) {
        if (keyed && method->ml_meth != CMETHOD(native_loc) &&
            method->ml_meth != CMETHOD(native_pos)) continue;
        PyObject *descriptor = PyDescr_NewMethod((PyTypeObject *)cls, method);
        if (descriptor == NULL) goto failed;
        int result = PyObject_SetAttrString(cls, method->ml_name, descriptor);
        if (result == 0 && method->ml_meth == CMETHOD(native_loc))
            result = PyObject_SetAttr(cls, config->saved_loc_name, descriptor);
        if (result == 0 && method->ml_meth == CMETHOD(native_expand))
            result = PyObject_SetAttr(cls, config->saved_expand_name, descriptor);
        Py_DECREF(descriptor);
        if (result < 0) goto failed;
    }
    Py_DECREF(capsule);
    Py_RETURN_NONE;
failed:
    Py_DECREF(capsule);
    return NULL;
}

static PyMethodDef module_methods[] = {
    {"install", (PyCFunction)(void (*)(void))install, METH_VARARGS | METH_KEYWORDS,
     "Install kernels on an owned SortedList subclass without changing its base."},
    {NULL, NULL, 0, NULL}
};

static struct PyModuleDef module_definition = {
    PyModuleDef_HEAD_INIT,
    "_sorted_native",
    "Native segmented-list kernels for unstd-owned sorted classes.",
    0,
    module_methods,
    NULL, NULL, NULL, NULL
};

PyMODINIT_FUNC
PyInit__sorted_native(void)
{
    PyObject *module = PyModule_Create(&module_definition);
    if (module == NULL) return NULL;
    if (PyModule_AddIntConstant(module, "KERNEL_VERSION", 1) < 0) {
        Py_DECREF(module);
        return NULL;
    }
    return module;
}
