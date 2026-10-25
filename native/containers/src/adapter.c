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
#include <stdint.h>

#ifdef UNSTD_CONTAINERS_TESTING
#define CONFIG_NAME "unstd.containers.sorted-config.v3.testing"
#else
#define CONFIG_NAME "unstd.containers.sorted-config.v3"
#endif
#define BULK_HELPERS 8
#define CONFIG_ATTRIBUTE "_unstd_sorted_native_config"

enum Method { BISECT_LEFT, BISECT_RIGHT, LOC, POS, DELITEM, UPDATE, METHOD_COUNT };
enum Field { LISTS, MAXES, INDEX, OFFSET, LENGTH, LOAD, FIELD_COUNT };

typedef struct {
    PyObject *methods[METHOD_COUNT];
    PyObject *fields[FIELD_COUNT];
    PyObject *loc_name;
    PyObject *build_name;
    PyObject *saved_loc_name;
    PyObject *bisect[2];
    PyObject *bulk_names[BULK_HELPERS];
    PyObject *bulk_methods[BULK_HELPERS];
    PyObject *saved_pos_name;
    uint64_t delete_fast, delete_fallback, update_fast, update_fallback;
    unsigned int layout_version;
#ifdef UNSTD_CONTAINERS_TESTING
    Py_ssize_t test_fail_after;
    uint64_t test_seen;
    PyObject *test_hook;
#endif
} Config;

typedef struct {
    PyObject *capsule;
    Config *config;
    PyObject *dict;
} Context;

static const char *method_names[METHOD_COUNT] = {
    "bisect_left", "bisect_right", "_loc", "_pos", "__delitem__", "update"};
static const char *field_names[FIELD_COUNT] = {"_lists",  "_maxes", "_index",
                                               "_offset", "_len",   "_load"};

static void
config_free(PyObject *capsule)
{
    Config *config = PyCapsule_GetPointer(capsule, CONFIG_NAME);
    if (config == NULL) {
        PyErr_Clear();
        return;
    }
    for (int i = 0; i < METHOD_COUNT; i++) {
        Py_XDECREF(config->methods[i]);
    }
    for (int i = 0; i < FIELD_COUNT; i++) {
        Py_XDECREF(config->fields[i]);
    }
    Py_XDECREF(config->loc_name);
    Py_XDECREF(config->build_name);
    Py_XDECREF(config->saved_loc_name);
    Py_XDECREF(config->bisect[0]);
    Py_XDECREF(config->bisect[1]);
    for (int i = 0; i < BULK_HELPERS; i++) {
        Py_XDECREF(config->bulk_names[i]);
        Py_XDECREF(config->bulk_methods[i]);
    }
    Py_XDECREF(config->saved_pos_name);
#ifdef UNSTD_CONTAINERS_TESTING
    Py_XDECREF(config->test_hook);
#endif
    PyMem_Free(config);
}

static void
context_clear(Context *context)
{
    Py_XDECREF(context->dict);
    Py_XDECREF(context->capsule);
}

static int
ordinary_keys(PyObject *dict)
{
    Py_ssize_t position = 0;
    PyObject *key, *value;
    while (PyDict_Next(dict, &position, &key, &value)) {
        if (!PyUnicode_CheckExact(key)) {
            return 0;
        }
    }
    return 1;
}

/* Internal config lookup never hashes a caller-supplied namespace key. */
static PyObject *
owned_class_config(PyTypeObject *cls)
{
    PyObject *namespace = PyType_GetDict(cls);
    if (namespace == NULL) {
        return NULL;
    }
    PyObject *key, *value, *capsule = NULL;
    Py_ssize_t position = 0;
    while (PyDict_Next(namespace, &position, &key, &value)) {
        if (PyCapsule_CheckExact(value) && PyUnicode_CheckExact(key) &&
            PyUnicode_CompareWithASCIIString(key, CONFIG_ATTRIBUTE) == 0) {
            capsule = Py_NewRef(value);
            break;
        }
    }
    Py_DECREF(namespace);
    return capsule;
}

static unsigned int
layout_version(PyTypeObject *cls)
{
#if PY_VERSION_HEX >= 0x030C0000 && PY_VERSION_HEX < 0x030D0000
    /* In 3.12 a failed version assignment can leave a nonzero invalid tag. */
    if (!(PyType_GetFlags(cls) & Py_TPFLAGS_VALID_VERSION_TAG)) {
        return 0;
    }
    return cls->tp_version_tag;
#elif PY_VERSION_HEX >= 0x030D0000 && PY_VERSION_HEX < 0x030F0000
    return cls->tp_version_tag;
#else
    /* New interpreter versions use the checked, uncached admission path. */
    (void)cls;
    return 0;
#endif
}

static int
ordinary_layout(PyTypeObject *cls, Config *config)
{
    unsigned int version = layout_version(cls);
    if (version != 0 && version == config->layout_version) {
        return 1;
    }
    PyObject *mro = Py_XNewRef(cls->tp_mro);
    int ordinary = 0;
    if (mro == NULL || !PyTuple_CheckExact(mro)) {
        goto done;
    }
    for (Py_ssize_t j = 0; j < PyTuple_GET_SIZE(mro); j++) {
        PyObject *owner = PyTuple_GET_ITEM(mro, j);
        if (!PyType_Check(owner)) {
            goto done;
        }
        PyObject *namespace = PyType_GetDict((PyTypeObject *)owner);
        if (namespace == NULL) {
            goto done;
        }
        if (!ordinary_keys(namespace)) {
            Py_DECREF(namespace);
            goto done;
        }
        for (int i = 0; i < FIELD_COUNT; i++) {
            PyObject *found = PyDict_GetItemWithError(namespace, config->fields[i]);
            if (found != NULL || PyErr_Occurred()) {
                Py_DECREF(namespace);
                goto done;
            }
        }
        Py_DECREF(namespace);
    }
    ordinary = 1;
done:
    Py_XDECREF(mro);
    if (ordinary && layout_version(cls) != version) {
        ordinary = 0;
    }
    if (ordinary && version != 0) {
        config->layout_version = version;
    }
    return ordinary;
}

static int
context_config(Context *context, PyTypeObject *defining_class)
{
    context->capsule = NULL;
    context->dict = NULL;
    PyObject *capsule = owned_class_config(defining_class);
    if (capsule == NULL || !PyCapsule_IsValid(capsule, CONFIG_NAME)) {
        Py_XDECREF(capsule);
        PyErr_SetString(PyExc_RuntimeError, "sorted native configuration is missing");
        return -1;
    }
    context->capsule = capsule;
    context->config = PyCapsule_GetPointer(capsule, CONFIG_NAME);
    return context->config == NULL ? -1 : 1;
}

static int
context_get(Context *context, PyObject *self, PyTypeObject *defining_class)
{
    if (context_config(context, defining_class) < 0) {
        return -1;
    }
    if (Py_TYPE(self) != defining_class ||
        defining_class->tp_getattro != PyObject_GenericGetAttr) {
        return 0;
    }
    if (!ordinary_layout(defining_class, context->config)) {
        return PyErr_Occurred() ? -1 : 0;
    }
    context->dict = PyObject_GenericGetDict(self, NULL);
    if (context->dict == NULL) {
        return -1;
    }
    if (Py_TYPE(self) != defining_class ||
        defining_class->tp_getattro != PyObject_GenericGetAttr ||
        !PyDict_CheckExact(context->dict) || !ordinary_keys(context->dict)) {
        return 0;
    }
    if (!ordinary_layout(defining_class, context->config)) {
        return PyErr_Occurred() ? -1 : 0;
    }
    return 1;
}

static int
context_refresh(Context *context, PyObject *self)
{
    /* A detached old dictionary can own finalizers which replace the live
     * dictionary again. Complete those releases before capturing live state. */
    Py_CLEAR(context->dict);
    PyObject *dict = PyObject_GenericGetDict(self, NULL);
    if (dict == NULL) {
        return -1;
    }
    context->dict = dict;
    if (!PyDict_CheckExact(dict)) {
        return 0;
    }
    return ordinary_keys(dict);
}

static int
helper_unchanged(Context *context, PyTypeObject *cls)
{
    PyObject *name = context->config->loc_name;
    PyObject *saved_name = context->config->saved_loc_name;
    if (PyDict_GetItemWithError(context->dict, name) != NULL) {
        return 0;
    }
    PyObject *current = PyDict_GetItemWithError(cls->tp_dict, name);
    PyObject *saved = PyDict_GetItemWithError(cls->tp_dict, saved_name);
    return current != NULL && current == saved;
}

static PyObject *loc_value(Context *, PyObject *, Py_ssize_t, Py_ssize_t);

static PyObject *
field(Context *context, enum Field name)
{
    return PyDict_GetItemWithError(context->dict, context->config->fields[name]);
}

static int
integer_field(Context *context, enum Field name, Py_ssize_t *result)
{
    PyObject *value = field(context, name);
    if (value == NULL || !PyLong_CheckExact(value)) {
        return 0;
    }
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
fallback(Context *context, enum Method method, PyObject *self, PyObject *const *args,
         size_t nargs, PyObject *kwnames)
{
    Py_CLEAR(context->dict);
    size_t keywords = kwnames == NULL ? 0 : (size_t)PyTuple_GET_SIZE(kwnames);
    if (nargs > SIZE_MAX - keywords - 1 ||
        nargs + keywords + 1 > SIZE_MAX / sizeof(PyObject *)) {
        return PyErr_NoMemory();
    }
    size_t total = nargs + keywords + 1;
    PyObject *small[8];
    PyObject **forward = total <= 8 ? small : PyMem_Malloc(total * sizeof(PyObject *));
    if (forward == NULL) {
        return PyErr_NoMemory();
    }
    forward[0] = self;
    for (size_t i = 1; i < total; i++) {
        forward[i] = args[i - 1];
    }
    /* The upstream binder/call may replace __dict__ and run its finalizers.
     * No field lookup follows on this path, so do not postpone that cleanup. */
    Py_CLEAR(context->dict);
    PyObject *result = PyObject_Vectorcall(context->config->methods[method], forward,
                                           nargs + 1, kwnames);
    if (forward != small) {
        PyMem_Free(forward);
    }
    return result;
}

static PyObject *
single_argument(PyObject *const *args, size_t nargs, PyObject *kwnames,
                const char *name)
{
    if (kwnames == NULL || PyTuple_GET_SIZE(kwnames) == 0) {
        return nargs == 1 ? args[0] : NULL;
    }
    if (nargs == 0 && PyTuple_GET_SIZE(kwnames) == 1 &&
        PyUnicode_CheckExact(PyTuple_GET_ITEM(kwnames, 0)) &&
        PyUnicode_CompareWithASCIIString(PyTuple_GET_ITEM(kwnames, 0), name) == 0) {
        return args[0];
    }
    return NULL;
}

static PyObject *
owned_item(PyObject *list, Py_ssize_t index)
{
    PyObject *item = PyList_GetItem(list, index);
    return item == NULL ? NULL : Py_NewRef(item);
}

/* Exact integers cannot run Python comparison callbacks. If every item
 * visited by the binary search is exact, its borrowed references and the
 * captured instance dictionary remain live for this callback-free phase.
 * Reject subclasses before comparing them, preserving the generic path's
 * reflected comparisons, exceptions and reentrant mutation semantics. */
static int
bisect_integers(PyObject *list, PyObject *value, int right, Py_ssize_t *result)
{
    if (!PyLong_CheckExact(value)) {
        return 0;
    }
    int overflow = 0;
    long long target = PyLong_AsLongLongAndOverflow(value, &overflow);
    if (overflow) {
        return 0;
    }
    if (target == -1 && PyErr_Occurred()) {
        return -1;
    }
    Py_ssize_t low = 0, high = PyList_GET_SIZE(list);
    while (low < high) {
        Py_ssize_t middle = low + (high - low) / 2;
        PyObject *item = PyList_GET_ITEM(list, middle);
        if (!PyLong_CheckExact(item)) {
            return 0;
        }
        long long number = PyLong_AsLongLongAndOverflow(item, &overflow);
        if (overflow) {
            return 0;
        }
        if (number == -1 && PyErr_Occurred()) {
            return -1;
        }
        int less = right ? target < number : number < target;
        if (right ? less : !less) {
            high = middle;
        } else {
            low = middle + 1;
        }
    }
    *result = low;
    return 1;
}

static PyObject *
native_bisect(PyObject *self, PyTypeObject *defining_class, PyObject *const *args,
              size_t nargs, PyObject *kwnames, int right)
{
    Context context;
    int admitted = context_get(&context, self, defining_class);
    PyObject *result = NULL, *maxes = NULL, *lists = NULL, *sublist = NULL;
    PyObject *value = single_argument(args, nargs, kwnames, "value");
    enum Method method = right ? BISECT_RIGHT : BISECT_LEFT;
    if (admitted < 0) {
        goto done;
    }
    if (!admitted || value == NULL || !helper_unchanged(&context, defining_class)) {
        goto slow;
    }
    maxes = list_field(&context, MAXES);
    lists = list_field(&context, LISTS);
    if (maxes == NULL || lists == NULL) {
        goto slow;
    }
    if (!PyList_GET_SIZE(maxes)) {
        result = PyLong_FromLong(0);
        goto done;
    }
    Py_ssize_t position, local;
    int primitive = bisect_integers(maxes, value, right, &position);
    if (primitive < 0) {
        goto done;
    }
    if (!primitive) {
        goto slow;
    }
    if (position == PyList_GET_SIZE(maxes)) {
        PyObject *length = field(&context, LENGTH);
        if (length == NULL) {
            goto slow;
        }
        result = Py_NewRef(length);
        goto done;
    }
    sublist = owned_item(lists, position);
    if (sublist == NULL) {
        goto done;
    }
    Py_CLEAR(lists);
    if (!PyList_CheckExact(sublist)) {
        goto slow;
    }
    primitive = bisect_integers(sublist, value, right, &local);
    if (primitive < 0) {
        goto done;
    }
    if (!primitive) {
        goto slow;
    }
    Py_CLEAR(sublist);
    result = loc_value(&context, self, position, local);
    if (result != NULL || PyErr_Occurred()) {
        goto done;
    }
slow:
    Py_CLEAR(sublist);
    Py_CLEAR(lists);
    Py_CLEAR(maxes);
    result = fallback(&context, method, self, args, nargs, kwnames);
done:
    Py_XDECREF(sublist);
    Py_XDECREF(lists);
    Py_XDECREF(maxes);
    context_clear(&context);
    return result;
}

static PyObject *
native_bisect_left(PyObject *self, PyTypeObject *defining_class, PyObject *const *args,
                   size_t nargs, PyObject *kwnames)
{
    return native_bisect(self, defining_class, args, nargs, kwnames, 0);
}

static PyObject *
native_bisect_right(PyObject *self, PyTypeObject *defining_class, PyObject *const *args,
                    size_t nargs, PyObject *kwnames)
{
    return native_bisect(self, defining_class, args, nargs, kwnames, 1);
}

static PyObject *
loc_value(Context *context, PyObject *self, Py_ssize_t position, Py_ssize_t local)
{
    (void)self;
    if (!position) {
        Py_CLEAR(context->dict);
        return PyLong_FromSsize_t(local);
    }
    PyObject *result = NULL, *index = list_field(context, INDEX);
    if (index == NULL) {
        return NULL;
    }
    if (!PyList_GET_SIZE(index)) {
        goto done;
    }
    Py_ssize_t offset;
    PyObject *offset_value = field(context, OFFSET);
    if (offset_value == NULL || !PyLong_CheckExact(offset_value)) {
        goto done;
    }
    offset = PyLong_AsSsize_t(offset_value);
    if (offset == -1 && PyErr_Occurred()) {
        goto done;
    }
    if (offset < 0) {
        PyErr_SetString(PyExc_ValueError, "negative sorted list index offset");
        goto done;
    }
    if (position > PY_SSIZE_T_MAX - offset) {
        PyErr_SetString(PyExc_OverflowError, "sorted list index offset overflow");
        goto done;
    }
    position += offset;
    Py_ssize_t total = local;
    while (position) {
        if (!(position & 1)) {
            if (position - 1 >= PyList_GET_SIZE(index)) {
                goto done;
            }
            PyObject *item = PyList_GET_ITEM(index, position - 1);
            if (!PyLong_CheckExact(item)) {
                goto done;
            }
            Py_ssize_t count = PyLong_AsSsize_t(item);
            if (count == -1 && PyErr_Occurred()) {
                goto done;
            }
            if (count < 0) {
                PyErr_SetString(PyExc_ValueError,
                                "negative sorted list positional count");
                goto done;
            }
            if (total > PY_SSIZE_T_MAX - count) {
                PyErr_SetString(PyExc_OverflowError, "sorted list rank overflow");
                goto done;
            }
            total += count;
        }
        position = (position - 1) >> 1;
    }
    Py_CLEAR(context->dict);
    result = PyLong_FromSsize_t(total);
done:
    Py_DECREF(index);
    return result;
}

static PyObject *
native_loc(PyObject *self, PyTypeObject *defining_class, PyObject *const *args,
           size_t nargs, PyObject *kwnames)
{
    /* Upstream returns idx immediately for the first block without reading
     * instance state. Keep that common path equally cheap and callback-free. */
    if (Py_TYPE(self) == defining_class && nargs == 2 &&
        (kwnames == NULL || PyTuple_GET_SIZE(kwnames) == 0) &&
        PyLong_CheckExact(args[0]) && PyObject_IsTrue(args[0]) == 0) {
        return Py_NewRef(args[1]);
    }
    Context context;
    int admitted = context_get(&context, self, defining_class);
    PyObject *result = NULL;
    if (admitted < 0) {
        goto done;
    }
    if (!admitted || nargs != 2 ||
        (kwnames != NULL && PyTuple_GET_SIZE(kwnames) != 0) ||
        !PyLong_CheckExact(args[0]) || !PyLong_CheckExact(args[1])) {
        goto slow;
    }
    Py_ssize_t position = PyLong_AsSsize_t(args[0]);
    if (position == -1 && PyErr_Occurred()) {
        PyErr_Clear();
        goto slow;
    }
    if (position == 0) {
        result = Py_NewRef(args[1]);
        goto done;
    }
    Py_ssize_t local = PyLong_AsSsize_t(args[1]);
    if (local == -1 && PyErr_Occurred()) {
        PyErr_Clear();
        goto slow;
    }
    if (position < 0 || local < 0) {
        goto slow;
    }
    result = loc_value(&context, self, position, local);
    if (result != NULL || PyErr_Occurred()) {
        goto done;
    }
slow:
    result = fallback(&context, LOC, self, args, nargs, kwnames);
done:
    context_clear(&context);
    return result;
}

static PyObject *
native_pos(PyObject *self, PyTypeObject *defining_class, PyObject *const *args,
           size_t nargs, PyObject *kwnames)
{
    Context context;
    int admitted = context_get(&context, self, defining_class);
    PyObject *result = NULL, *lists = NULL, *index = NULL, *block = NULL;
    PyObject *argument = single_argument(args, nargs, kwnames, "idx");
    if (admitted < 0) {
        goto done;
    }
    if (!admitted || argument == NULL || !PyLong_CheckExact(argument)) {
        goto slow;
    }
    Py_ssize_t local = PyLong_AsSsize_t(argument), length;
    if (local == -1 && PyErr_Occurred()) {
        PyErr_Clear();
        goto slow;
    }
    lists = list_field(&context, LISTS);
    if (lists == NULL || !integer_field(&context, LENGTH, &length) || length < 0) {
        goto slow;
    }
    Py_ssize_t count = PyList_GET_SIZE(lists);
    if (local < 0) {
        block = owned_item(lists, count - 1);
        if (block == NULL) {
            goto done;
        }
        if (!PyList_CheckExact(block)) {
            goto slow;
        }
        Py_ssize_t last = PyList_GET_SIZE(block);
        Py_CLEAR(block);
        if (local >= -last) {
            Py_CLEAR(context.dict);
            Py_CLEAR(lists);
            result = Py_BuildValue("nn", count - 1, last + local);
            goto done;
        }
        local += length;
        if (local < 0) {
            goto out_of_range;
        }
    } else if (local >= length) {
        goto out_of_range;
    }
    block = owned_item(lists, 0);
    if (block == NULL) {
        goto done;
    }
    if (!PyList_CheckExact(block)) {
        goto slow;
    }
    Py_ssize_t first = PyList_GET_SIZE(block);
    Py_CLEAR(block);
    if (local < first) {
        Py_CLEAR(context.dict);
        Py_CLEAR(lists);
        result = Py_BuildValue("nn", (Py_ssize_t)0, local);
        goto done;
    }
    index = list_field(&context, INDEX);
    if (index == NULL) {
        goto slow;
    }
    if (!PyList_GET_SIZE(index)) {
        goto slow;
    }
    Py_ssize_t position = 0, child = 1;
    while (child < PyList_GET_SIZE(index)) {
        PyObject *item = PyList_GetItem(index, child);
        if (item == NULL) {
            goto done;
        }
        if (!PyLong_CheckExact(item)) {
            goto slow;
        }
        Py_ssize_t count = PyLong_AsSsize_t(item);
        if (count == -1 && PyErr_Occurred()) {
            goto done;
        }
        if (count < 0) {
            PyErr_SetString(PyExc_ValueError, "negative sorted list positional count");
            goto done;
        }
        if (local < count) {
            position = child;
        } else {
            local -= count;
            position = child + 1;
        }
        if (position > (PY_SSIZE_T_MAX - 1) / 2) {
            PyErr_SetString(PyExc_OverflowError,
                            "sorted list positional index overflow");
            goto done;
        }
        child = (position << 1) + 1;
    }
    Py_ssize_t offset;
    if (!integer_field(&context, OFFSET, &offset) || offset < 0) {
        goto slow;
    }
    Py_CLEAR(context.dict);
    Py_CLEAR(lists);
    result = Py_BuildValue("nn", position - offset, local);
    goto done;
out_of_range:
    PyErr_SetString(PyExc_IndexError, "list index out of range");
    goto done;
slow:
    Py_CLEAR(block);
    Py_CLEAR(index);
    Py_CLEAR(lists);
    result = fallback(&context, POS, self, args, nargs, kwnames);
done:
    Py_XDECREF(block);
    Py_XDECREF(index);
    Py_XDECREF(lists);
    context_clear(&context);
    return result;
}

#ifdef UNSTD_CONTAINERS_TESTING
static int
stage_point(Config *config)
{
    config->test_seen++;
    if (config->test_fail_after == 0) {
        config->test_fail_after = -1;
        PyErr_NoMemory();
        return -1;
    }
    if (config->test_fail_after > 0) {
        config->test_fail_after--;
    }
    return 0;
}

static int
stage_callback(Config *config)
{
    if (config->test_hook == NULL) {
        return 0;
    }
    PyObject *callback = Py_NewRef(config->test_hook);
    PyObject *result = PyObject_CallNoArgs(callback);
    Py_DECREF(callback);
    if (result == NULL) {
        return -1;
    }
    Py_DECREF(result);
    return 0;
}
#define STAGE_POINT(config) stage_point(config)
#define STAGE_CALLBACK(config) stage_callback(config)
#else
#define STAGE_POINT(config) 0
#define STAGE_CALLBACK(config) 0
#endif

typedef struct {
    PyObject *fields[FIELD_COUNT];
    PyObject *lists, *maxes, *index;
    PyObject *next_lists, *next_maxes, *next_index, *next_length, *remainder;
    Py_ssize_t length, cutoff, start, stop, first, last, dropped, residual;
    int prefix;
} Bulk;

static void
bulk_clear(Bulk *b)
{
    for (int i = 0; i < FIELD_COUNT; i++) {
        Py_XDECREF(b->fields[i]);
    }
    Py_XDECREF(b->next_lists);
    Py_XDECREF(b->next_maxes);
    Py_XDECREF(b->next_index);
    Py_XDECREF(b->next_length);
    Py_XDECREF(b->remainder);
    memset(b, 0, sizeof(*b));
}

static PyObject *
bulk_descriptor(PyTypeObject *cls, PyObject *name)
{
    PyObject *mro = Py_XNewRef(cls->tp_mro), *found = NULL;
    if (mro == NULL || !PyTuple_CheckExact(mro)) {
        goto done;
    }
    for (Py_ssize_t i = 0; i < PyTuple_GET_SIZE(mro); i++) {
        PyObject *owner = PyTuple_GET_ITEM(mro, i);
        if (!PyType_Check(owner)) {
            goto done;
        }
        PyObject *namespace = PyType_GetDict((PyTypeObject *)owner);
        if (namespace == NULL) {
            goto done;
        }
        if (!ordinary_keys(namespace)) {
            Py_DECREF(namespace);
            goto done;
        }
        found = PyDict_GetItemWithError(namespace, name);
        Py_DECREF(namespace);
        if (found != NULL || PyErr_Occurred()) {
            break;
        }
    }
done:
    Py_XDECREF(mro);
    return found;
}

static int
bulk_helpers(Context *c, PyTypeObject *cls)
{
    if (cls->tp_getattro != PyObject_GenericGetAttr ||
        cls->tp_setattro != PyObject_GenericSetAttr ||
        !ordinary_layout(cls, c->config)) {
        return 0;
    }
    for (int i = 0; i < FIELD_COUNT; i++) {
        if (bulk_descriptor(cls, c->config->fields[i]) != NULL) {
            return 0;
        }
    }
    for (int i = 0; i < BULK_HELPERS; i++) {
        PyObject *name = c->config->bulk_names[i];
        if (PyDict_GetItemWithError(c->dict, name) != NULL) {
            return 0;
        }
        PyObject *actual = bulk_descriptor(cls, name);
        PyObject *expected =
            i == 0 ? PyDict_GetItemWithError(cls->tp_dict, c->config->saved_pos_name)
                   : c->config->bulk_methods[i];
        if (actual == NULL || actual != expected) {
            return 0;
        }
    }
    return 1;
}

static int
bulk_snapshot(Context *c, Bulk *b)
{
    for (int i = 0; i < FIELD_COUNT; i++) {
        PyObject *value = field(c, (enum Field)i);
        if (value == NULL) {
            return 0;
        }
        b->fields[i] = Py_NewRef(value);
    }
    b->lists = b->fields[LISTS];
    b->maxes = b->fields[MAXES];
    b->index = b->fields[INDEX];
    if (!PyList_CheckExact(b->lists) || !PyList_CheckExact(b->maxes) ||
        !PyList_CheckExact(b->index) || !integer_field(c, LENGTH, &b->length) ||
        b->length < 1) {
        return 0;
    }
    Py_ssize_t ignored;
    if (!integer_field(c, LOAD, &ignored) || ignored < 1 ||
        !integer_field(c, OFFSET, &ignored) || ignored < 0) {
        return 0;
    }
    return 1;
}

static int
bulk_plan(Bulk *b)
{
    Py_ssize_t blocks = PyList_GET_SIZE(b->lists), total = 0, dropped = 0, residual = 0;
    Py_ssize_t first = 0, last = blocks;
    if (!blocks || PyList_GET_SIZE(b->maxes) != blocks) {
        return 0;
    }
    for (Py_ssize_t i = 0; i < blocks; i++) {
        PyObject *block = PyList_GET_ITEM(b->lists, i);
        if (!PyList_CheckExact(block)) {
            return 0;
        }
        Py_ssize_t count = PyList_GET_SIZE(block);
        if (count < 1 || total > PY_SSIZE_T_MAX - count ||
            PyList_GET_ITEM(b->maxes, i) != PyList_GET_ITEM(block, count - 1)) {
            return 0;
        }
        Py_ssize_t following = total + count;
        if (b->prefix) {
            if (following <= b->cutoff) {
                first = i + 1;
                dropped = following;
            } else if (total < b->cutoff) {
                residual = b->cutoff - total;
            }
        } else if (following <= b->cutoff) {
            first = i + 1;
        } else if (total < b->cutoff) {
            first = i + 1;
            residual = following - b->cutoff;
        }
        total = following;
    }
    if (total != b->length) {
        return 0;
    }
    if (!b->prefix) {
        Py_ssize_t retained = 0;
        for (Py_ssize_t i = 0; i < first; i++) {
            retained += PyList_GET_SIZE(PyList_GET_ITEM(b->lists, i));
        }
        dropped = b->length - retained;
        last = first;
        first = 0;
    }
    if (!dropped) {
        return 0;
    }
    b->first = first;
    b->last = last;
    b->dropped = dropped;
    b->residual = residual;
    return 1;
}

static int
bulk_validate(Bulk *b, int staged)
{
    if (Py_REFCNT(b->lists) != 2 || Py_REFCNT(b->maxes) != 2 ||
        Py_REFCNT(b->index) != 2) {
        return 0;
    }
    Py_ssize_t blocks = PyList_GET_SIZE(b->lists), total = 0;
    for (Py_ssize_t i = 0; i < blocks; i++) {
        PyObject *block = PyList_GET_ITEM(b->lists, i);
        int kept = i >= b->first && i < b->last;
        if (Py_REFCNT(block) != (staged && kept ? 2 : 1)) {
            return 0;
        }
        Py_ssize_t size = PyList_GET_SIZE(block);
        Py_ssize_t begin = b->start > total ? b->start - total : 0;
        Py_ssize_t end = b->stop < total + size ? b->stop - total : size;
        for (Py_ssize_t j = begin; j < end; j++) {
            if (!PyLong_CheckExact(PyList_GET_ITEM(block, j))) {
                return 0;
            }
        }
        total += size;
    }
    for (Py_ssize_t i = 0; i < PyList_GET_SIZE(b->index); i++) {
        if (!PyLong_CheckExact(PyList_GET_ITEM(b->index, i))) {
            return 0;
        }
    }
    if (staged) {
        Py_ssize_t count = b->last - b->first;
        if (PyList_GET_SIZE(b->next_lists) != count ||
            PyList_GET_SIZE(b->next_maxes) != count || PyList_GET_SIZE(b->next_index)) {
            return 0;
        }
        for (Py_ssize_t i = 0; i < count; i++) {
            if (PyList_GET_ITEM(b->next_lists, i) !=
                    PyList_GET_ITEM(b->lists, i + b->first) ||
                PyList_GET_ITEM(b->next_maxes, i) !=
                    PyList_GET_ITEM(b->maxes, i + b->first)) {
                return 0;
            }
        }
    }
    return 1;
}

static int
bulk_stage(Context *c, Bulk *b)
{
    (void)c;
    if (STAGE_POINT(c->config) < 0) {
        return -1;
    }
    b->next_lists = PyList_GetSlice(b->lists, b->first, b->last);
    if (b->next_lists == NULL) {
        return -1;
    }
    if (STAGE_POINT(c->config) < 0) {
        return -1;
    }
    b->next_maxes = PyList_GetSlice(b->maxes, b->first, b->last);
    if (b->next_maxes == NULL) {
        return -1;
    }
    if (STAGE_POINT(c->config) < 0) {
        return -1;
    }
    b->next_index = PyList_New(0);
    if (b->next_index == NULL) {
        return -1;
    }
    if (STAGE_POINT(c->config) < 0) {
        return -1;
    }
    b->next_length = PyLong_FromSsize_t(b->length - b->dropped);
    if (b->next_length == NULL) {
        return -1;
    }
    if (b->residual) {
        if (STAGE_POINT(c->config) < 0) {
            return -1;
        }
        PyObject *bound = PyLong_FromSsize_t(
            b->prefix ? b->residual : b->length - b->dropped - b->residual);
        if (bound == NULL) {
            return -1;
        }
        if (STAGE_POINT(c->config) < 0) {
            Py_DECREF(bound);
            return -1;
        }
        b->remainder = b->prefix ? PySlice_New(Py_None, bound, Py_None)
                                 : PySlice_New(bound, Py_None, Py_None);
        Py_DECREF(bound);
        if (b->remainder == NULL) {
            return -1;
        }
    }
    return 0;
}

static int
bulk_revalidate(Context *c, Bulk *b, PyObject *self, PyTypeObject *cls)
{
    if (Py_TYPE(self) != cls) {
        return 0;
    }
    PyObject *capsule = owned_class_config(cls);
    int configured = capsule == c->capsule;
    Py_XDECREF(capsule);
    if (!configured) {
        return 0;
    }
    int refreshed = context_refresh(c, self);
    if (refreshed <= 0) {
        return refreshed;
    }
    if (!bulk_helpers(c, cls)) {
        return 0;
    }
    for (int i = 0; i < FIELD_COUNT; i++) {
        if (field(c, (enum Field)i) != b->fields[i]) {
            return 0;
        }
    }
    Py_ssize_t first = b->first, last = b->last, dropped = b->dropped,
               residual = b->residual;
    if (!bulk_plan(b) || b->first != first || b->last != last ||
        b->dropped != dropped || b->residual != residual) {
        return 0;
    }
    return bulk_validate(b, 1);
}

static int
bulk_commit(Context *c, Bulk *b)
{
    enum Field fields[] = {LISTS, MAXES, INDEX, LENGTH};
    PyObject *values[] = {b->next_lists, b->next_maxes, b->next_index, b->next_length};
    for (int i = 0; i < 4; i++) {
        if (STAGE_POINT(c->config) < 0 ||
            PyDict_SetItem(c->dict, c->config->fields[fields[i]], values[i]) < 0) {
            PyObject *type, *value, *traceback;
            PyErr_Fetch(&type, &value, &traceback);
            for (int j = 0; j < i; j++) {
                if (PyDict_SetItem(c->dict, c->config->fields[fields[j]],
                                   b->fields[fields[j]]) < 0) {
                    PyErr_Clear();
                }
            }
            PyErr_Restore(type, value, traceback);
            return -1;
        }
    }
    return 0;
}

static PyObject *
native_delitem(PyObject *self, PyTypeObject *cls, PyObject *const *args, size_t nargs,
               PyObject *kwnames)
{
    Context c;
    int admitted;
    Bulk b = {0};
    PyObject *result = NULL;
    PyObject *argument = single_argument(args, nargs, kwnames, "index");
    if (argument == NULL || !Py_IS_TYPE(argument, &PySlice_Type)) {
        if (context_config(&c, cls) < 0) {
            goto done;
        }
        c.config->delete_fallback++;
        result = fallback(&c, DELITEM, self, args, nargs, kwnames);
        goto done;
    }
    admitted = context_get(&c, self, cls);
    if (admitted < 0) {
        goto done;
    }
    if (!admitted || argument == NULL || !Py_IS_TYPE(argument, &PySlice_Type) ||
        !bulk_helpers(&c, cls)) {
        goto slow;
    }
    PySliceObject *slice = (PySliceObject *)argument;
    if ((slice->start != Py_None && !PyLong_CheckExact(slice->start)) ||
        (slice->stop != Py_None && !PyLong_CheckExact(slice->stop)) ||
        (slice->step != Py_None && !PyLong_CheckExact(slice->step))) {
        goto slow;
    }
    if (!bulk_snapshot(&c, &b)) {
        goto slow;
    }
    Py_ssize_t step;
    if (PySlice_Unpack(argument, &b.start, &b.stop, &step) < 0) {
        goto done;
    }
    if (step != 1) {
        goto slow;
    }
    Py_ssize_t removed = PySlice_AdjustIndices(b.length, &b.start, &b.stop, step);
    if (removed < 1 || removed > (b.length - 1) / 8) {
        goto slow;
    }
    if (b.start == 0) {
        b.prefix = 1;
        b.cutoff = b.stop;
    } else if (b.stop == b.length) {
        b.prefix = 0;
        b.cutoff = b.start;
    } else {
        goto slow;
    }
    if (!bulk_plan(&b) || !bulk_validate(&b, 0)) {
        goto slow;
    }
    Py_CLEAR(c.dict);
    if (bulk_stage(&c, &b) < 0 || STAGE_CALLBACK(c.config) < 0) {
        goto done;
    }
    int valid = bulk_revalidate(&c, &b, self, cls);
    if (valid < 0) {
        goto done;
    }
    if (!valid) {
        goto slow;
    }
    if (bulk_commit(&c, &b) < 0) {
        goto done;
    }
    c.config->delete_fast++;
    if (b.remainder != NULL) {
        PyObject *remainder = Py_NewRef(b.remainder);
        bulk_clear(&b);
        Py_CLEAR(c.dict);
        PyObject *forward[] = {self, remainder};
        result = PyObject_Vectorcall(c.config->methods[DELITEM], forward, 2, NULL);
        Py_DECREF(remainder);
    } else {
        result = Py_NewRef(Py_None);
    }
    goto done;
slow:
    bulk_clear(&b);
    Py_CLEAR(c.dict);
    c.config->delete_fallback++;
    if (!PyErr_Occurred()) {
        result = fallback(&c, DELITEM, self, args, nargs, kwnames);
    }
done:
    bulk_clear(&b);
    context_clear(&c);
    return result;
}

typedef struct {
    Bulk state;
    PyObject *source, *last_block;
    PyObject **values;
    Py_ssize_t used, tail, added, count, load, blocks;
} Append;

static void
append_clear(Append *a)
{
    for (Py_ssize_t i = 0; i < a->used; i++) {
        Py_DECREF(a->values[i]);
    }
    PyMem_Free(a->values);
    Py_XDECREF(a->source);
    Py_XDECREF(a->last_block);
    bulk_clear(&a->state);
    memset(a, 0, sizeof(*a));
}

static int
append_number(PyObject *value, long long *number)
{
    if (!PyLong_CheckExact(value)) {
        return 0;
    }
    int overflow = 0;
    *number = PyLong_AsLongLongAndOverflow(value, &overflow);
    return overflow ? 0 : (*number == -1 && PyErr_Occurred() ? -1 : 1);
}

static Py_ssize_t
append_size(PyObject *source)
{
    return PyList_CheckExact(source) ? PyList_GET_SIZE(source)
                                     : PyTuple_GET_SIZE(source);
}

static PyObject *
append_item(PyObject *source, Py_ssize_t index)
{
    return PyList_CheckExact(source) ? PyList_GET_ITEM(source, index)
                                     : PyTuple_GET_ITEM(source, index);
}

static int
append_shape(Append *a, int staged)
{
    Bulk *b = &a->state;
    if (Py_REFCNT(b->lists) != 2 || Py_REFCNT(b->maxes) != 2 ||
        Py_REFCNT(b->index) != 2) {
        return 0;
    }
    Py_ssize_t blocks = PyList_GET_SIZE(b->lists), total = 0;
    if (!blocks || PyList_GET_SIZE(b->maxes) != blocks ||
        (staged && blocks != a->blocks)) {
        return 0;
    }
    long long prior = 0;
    for (Py_ssize_t i = 0; i < blocks; i++) {
        PyObject *block = PyList_GET_ITEM(b->lists, i);
        if (!PyList_CheckExact(block) || Py_REFCNT(block) != (staged ? 2 : 1)) {
            return 0;
        }
        Py_ssize_t size = PyList_GET_SIZE(block);
        if (size < 1 || size > a->load * 2 || total > PY_SSIZE_T_MAX - size ||
            PyList_GET_ITEM(b->maxes, i) != PyList_GET_ITEM(block, size - 1)) {
            return 0;
        }
        long long maximum;
        if (append_number(PyList_GET_ITEM(b->maxes, i), &maximum) != 1 ||
            (i && maximum < prior)) {
            return 0;
        }
        prior = maximum;
        total += size;
    }
    if (total != b->length) {
        return 0;
    }
    for (Py_ssize_t i = 0; i < PyList_GET_SIZE(b->index); i++) {
        if (!PyLong_CheckExact(PyList_GET_ITEM(b->index, i))) {
            return 0;
        }
    }
    return 1;
}

static int
append_snapshot(Context *c, Append *a, PyObject *source)
{
    Bulk *b = &a->state;
    if (!bulk_snapshot(c, b) || !integer_field(c, LOAD, &a->load) || a->load < 1 ||
        a->load > PY_SSIZE_T_MAX / 2) {
        return 0;
    }
    a->added = append_size(source);
    if (a->added < 256 || a->added > (b->length - 1) / 4 ||
        a->added > PY_SSIZE_T_MAX - b->length) {
        return 0;
    }
    a->blocks = PyList_GET_SIZE(b->lists);
    if (!a->blocks || PyList_GET_SIZE(b->maxes) != a->blocks) {
        return 0;
    }
    PyObject *last = PyList_GET_ITEM(b->lists, a->blocks - 1);
    if (!PyList_CheckExact(last)) {
        return 0;
    }
    a->tail = PyList_GET_SIZE(last);
    if (!a->tail || a->added > PY_SSIZE_T_MAX - a->tail ||
        a->blocks > PY_SSIZE_T_MAX - a->tail || a->added < (a->blocks + a->tail) / 4) {
        return 0;
    }
    a->count = a->tail + a->added;
    if ((size_t)a->count > SIZE_MAX / sizeof(PyObject *)) {
        return 0;
    }
    long long prior;
    if (append_number(PyList_GET_ITEM(last, a->tail - 1), &prior) != 1) {
        return 0;
    }
    for (Py_ssize_t i = 0; i < a->added; i++) {
        long long number;
        if (append_number(append_item(source, i), &number) != 1 || number < prior) {
            return 0;
        }
        prior = number;
    }
    if (!append_shape(a, 0)) {
        return 0;
    }
    for (Py_ssize_t i = 0; i < a->tail; i++) {
        if (append_number(PyList_GET_ITEM(last, i), &prior) != 1) {
            return 0;
        }
    }
    Py_CLEAR(c->dict);
    if (STAGE_POINT(c->config) < 0) {
        return -1;
    }
    a->values = PyMem_Malloc((size_t)a->count * sizeof(PyObject *));
    if (a->values == NULL) {
        PyErr_NoMemory();
        return -1;
    }
    for (Py_ssize_t i = 0; i < a->tail; i++) {
        a->values[a->used++] = Py_NewRef(PyList_GET_ITEM(last, i));
    }
    for (Py_ssize_t i = 0; i < a->added; i++) {
        a->values[a->used++] = Py_NewRef(append_item(source, i));
    }
    a->source = Py_NewRef(source);
    a->last_block = Py_NewRef(last);
    return 1;
}

static int
append_stage(Context *c, Append *a)
{
    (void)c;
    Bulk *b = &a->state;
    if (STAGE_POINT(c->config) < 0) {
        return -1;
    }
    b->next_lists = PyList_GetSlice(b->lists, 0, a->blocks - 1);
    if (b->next_lists == NULL) {
        return -1;
    }
    if (STAGE_POINT(c->config) < 0) {
        return -1;
    }
    b->next_maxes = PyList_GetSlice(b->maxes, 0, a->blocks - 1);
    if (b->next_maxes == NULL) {
        return -1;
    }
    Py_ssize_t cursor = 0;
    while (cursor < a->count) {
        Py_ssize_t size = a->count - cursor > a->load * 2 ? a->load : a->count - cursor;
        if (STAGE_POINT(c->config) < 0) {
            return -1;
        }
        PyObject *block = PyList_New(0);
        if (block == NULL) {
            return -1;
        }
        for (Py_ssize_t i = 0; i < size; i++) {
            if (STAGE_POINT(c->config) < 0 ||
                PyList_Append(block, a->values[cursor + i]) < 0) {
                Py_DECREF(block);
                return -1;
            }
        }
        if (STAGE_POINT(c->config) < 0 || PyList_Append(b->next_lists, block) < 0 ||
            STAGE_POINT(c->config) < 0 ||
            PyList_Append(b->next_maxes, a->values[cursor + size - 1]) < 0) {
            Py_DECREF(block);
            return -1;
        }
        Py_DECREF(block);
        cursor += size;
    }
    if (STAGE_POINT(c->config) < 0) {
        return -1;
    }
    b->next_index = PyList_New(0);
    if (b->next_index == NULL) {
        return -1;
    }
    if (STAGE_POINT(c->config) < 0) {
        return -1;
    }
    b->next_length = PyLong_FromSsize_t(b->length + a->added);
    return b->next_length == NULL ? -1 : 0;
}

static int
append_revalidate(Context *c, Append *a, PyObject *self, PyTypeObject *cls)
{
    Bulk *b = &a->state;
    if (Py_TYPE(self) != cls) {
        return 0;
    }
    PyObject *capsule = owned_class_config(cls);
    int configured = capsule == c->capsule;
    Py_XDECREF(capsule);
    if (!configured) {
        return 0;
    }
    int refreshed = context_refresh(c, self);
    if (refreshed <= 0) {
        return refreshed;
    }
    if (!bulk_helpers(c, cls)) {
        return 0;
    }
    for (int i = 0; i < FIELD_COUNT; i++) {
        if (field(c, (enum Field)i) != b->fields[i]) {
            return 0;
        }
    }
    if (!append_shape(a, 1) ||
        PyList_GET_ITEM(b->lists, a->blocks - 1) != a->last_block ||
        PyList_GET_SIZE(a->last_block) != a->tail ||
        append_size(a->source) != a->added) {
        return 0;
    }
    for (Py_ssize_t i = 0; i < a->tail; i++) {
        if (PyList_GET_ITEM(a->last_block, i) != a->values[i]) {
            return 0;
        }
    }
    for (Py_ssize_t i = 0; i < a->added; i++) {
        if (append_item(a->source, i) != a->values[a->tail + i]) {
            return 0;
        }
    }
    if (PyList_GET_SIZE(b->next_index) ||
        PyList_GET_SIZE(b->next_lists) != PyList_GET_SIZE(b->next_maxes)) {
        return 0;
    }
    for (Py_ssize_t i = 0; i < a->blocks - 1; i++) {
        if (i >= PyList_GET_SIZE(b->next_lists) ||
            PyList_GET_ITEM(b->next_lists, i) != PyList_GET_ITEM(b->lists, i) ||
            PyList_GET_ITEM(b->next_maxes, i) != PyList_GET_ITEM(b->maxes, i)) {
            return 0;
        }
    }
    Py_ssize_t cursor = 0, position = a->blocks - 1;
    while (cursor < a->count) {
        if (position >= PyList_GET_SIZE(b->next_lists)) {
            return 0;
        }
        PyObject *block = PyList_GET_ITEM(b->next_lists, position);
        Py_ssize_t size = a->count - cursor > a->load * 2 ? a->load : a->count - cursor;
        if (!PyList_CheckExact(block) || PyList_GET_SIZE(block) != size ||
            Py_REFCNT(block) != 1 ||
            PyList_GET_ITEM(b->next_maxes, position) != a->values[cursor + size - 1]) {
            return 0;
        }
        for (Py_ssize_t i = 0; i < size; i++) {
            if (PyList_GET_ITEM(block, i) != a->values[cursor + i]) {
                return 0;
            }
        }
        cursor += size;
        position++;
    }
    return position == PyList_GET_SIZE(b->next_lists);
}

static PyObject *
native_update(PyObject *self, PyTypeObject *cls, PyObject *const *args, size_t nargs,
              PyObject *kwnames)
{
    Context c;
    int admitted;
    Append a = {0};
    PyObject *result = NULL;
    PyObject *source = single_argument(args, nargs, kwnames, "iterable");
    if (source == NULL || (!PyList_CheckExact(source) && !PyTuple_CheckExact(source)) ||
        append_size(source) < 256) {
        if (context_config(&c, cls) < 0) {
            goto done;
        }
        c.config->update_fallback++;
        result = fallback(&c, UPDATE, self, args, nargs, kwnames);
        goto done;
    }
    admitted = context_get(&c, self, cls);
    if (admitted < 0) {
        goto done;
    }
    if (!admitted || source == NULL ||
        (!PyList_CheckExact(source) && !PyTuple_CheckExact(source)) ||
        !bulk_helpers(&c, cls)) {
        goto slow;
    }
    int ready = append_snapshot(&c, &a, source);
    if (ready < 0) {
        goto done;
    }
    if (!ready) {
        goto slow;
    }
    if (append_stage(&c, &a) < 0 || STAGE_CALLBACK(c.config) < 0) {
        goto done;
    }
    ready = append_revalidate(&c, &a, self, cls);
    if (ready < 0) {
        goto done;
    }
    if (!ready) {
        goto slow;
    }
    if (bulk_commit(&c, &a.state) < 0) {
        goto done;
    }
    c.config->update_fast++;
    result = Py_NewRef(Py_None);
    goto done;
slow:
    append_clear(&a);
    Py_CLEAR(c.dict);
    c.config->update_fallback++;
    if (!PyErr_Occurred()) {
        result = fallback(&c, UPDATE, self, args, nargs, kwnames);
    }
done:
    append_clear(&a);
    context_clear(&c);
    return result;
}

#define METHOD_FLAGS (METH_METHOD | METH_FASTCALL | METH_KEYWORDS)
#define CMETHOD(function) ((PyCFunction)(void (*)(void))(function))

static PyMethodDef kernels[] = {
    {"update", CMETHOD(native_update), METHOD_FLAGS,
     "update($self, iterable)\n--\n\nAdd an iterable."},
    {"__delitem__", CMETHOD(native_delitem), METHOD_FLAGS,
     "__delitem__($self, index)\n--\n\nDelete an index or slice."},
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
    {NULL, NULL, 0, NULL}};

static PyObject *
install(PyObject *module, PyObject *args, PyObject *kwargs)
{
    (void)module;
    static char *keywords[] = {"cls", "base", "keyed", NULL};
    PyObject *cls, *base;
    int keyed = 0;
    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "OO|p:install", keywords, &cls,
                                     &base, &keyed)) {
        return NULL;
    }
    if (!PyType_Check(cls) || !PyType_Check(base) || cls == base ||
        !PyType_IsSubtype((PyTypeObject *)cls, (PyTypeObject *)base) ||
        !(((PyTypeObject *)cls)->tp_flags & Py_TPFLAGS_HEAPTYPE)) {
        PyErr_SetString(PyExc_TypeError,
                        "install requires a distinct Python subclass and its base");
        return NULL;
    }
    Config *config = PyMem_Calloc(1, sizeof(Config));
    if (config == NULL) {
        return PyErr_NoMemory();
    }
#ifdef UNSTD_CONTAINERS_TESTING
    config->test_fail_after = -1;
#endif
    PyObject *capsule = PyCapsule_New(config, CONFIG_NAME, config_free);
    if (capsule == NULL) {
        PyMem_Free(config);
        return NULL;
    }
    PyObject *bisect_module = PyImport_ImportModule("_bisect");
    if (bisect_module == NULL) {
        goto failed;
    }
    config->bisect[0] = PyObject_GetAttrString(bisect_module, "bisect_left");
    config->bisect[1] = PyObject_GetAttrString(bisect_module, "bisect_right");
    Py_DECREF(bisect_module);
    if (config->bisect[0] == NULL || config->bisect[1] == NULL) {
        goto failed;
    }
    for (int i = 0; i < METHOD_COUNT; i++) {
        config->methods[i] = PyObject_GetAttrString(base, method_names[i]);
        if (config->methods[i] == NULL) {
            goto failed;
        }
    }
    for (int i = 0; i < FIELD_COUNT; i++) {
        config->fields[i] = PyUnicode_InternFromString(field_names[i]);
        if (config->fields[i] == NULL) {
            goto failed;
        }
    }
    const char *bulk_names[] = {"_pos",    "_delete", "_clear",       "_getitem",
                                "_update", "_expand", "_build_index", "add"};
    for (int i = 0; i < BULK_HELPERS; i++) {
        config->bulk_names[i] = PyUnicode_InternFromString(bulk_names[i]);
        config->bulk_methods[i] = PyObject_GetAttrString(base, bulk_names[i]);
        if (!config->bulk_names[i] || !config->bulk_methods[i]) {
            goto failed;
        }
    }
    config->saved_pos_name = PyUnicode_InternFromString("_unstd_native_pos_descriptor");
    if (!config->saved_pos_name) {
        goto failed;
    }
    config->loc_name = PyUnicode_InternFromString("_loc");
    config->build_name = PyUnicode_InternFromString("_build_index");
    config->saved_loc_name = PyUnicode_InternFromString("_unstd_native_loc_descriptor");
    if (config->loc_name == NULL || config->build_name == NULL ||
        config->saved_loc_name == NULL ||
        PyObject_SetAttrString(cls, CONFIG_ATTRIBUTE, capsule) < 0) {
        goto failed;
    }
    for (PyMethodDef *method = kernels; method->ml_name != NULL; method++) {
        if (keyed && method->ml_meth != CMETHOD(native_loc) &&
            method->ml_meth != CMETHOD(native_pos)) {
            continue;
        }
        PyObject *descriptor = PyDescr_NewMethod((PyTypeObject *)cls, method);
        if (descriptor == NULL) {
            goto failed;
        }
        int result = PyObject_SetAttrString(cls, method->ml_name, descriptor);
        if (result == 0 && method->ml_meth == CMETHOD(native_loc)) {
            result = PyObject_SetAttr(cls, config->saved_loc_name, descriptor);
        }
        if (result == 0 && method->ml_meth == CMETHOD(native_pos)) {
            result = PyObject_SetAttr(cls, config->saved_pos_name, descriptor);
        }
        Py_DECREF(descriptor);
        if (result < 0) {
            goto failed;
        }
    }
    Py_DECREF(capsule);
    Py_RETURN_NONE;
failed:
    Py_DECREF(capsule);
    return NULL;
}

static PyObject *
mutation_stats(PyObject *module, PyObject *args, PyObject *kwargs)
{
    (void)module;
    static char *names[] = {"cls", "reset", NULL};
    PyObject *cls;
    int reset = 0;
    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "O|p:mutation_stats", names, &cls,
                                     &reset)) {
        return NULL;
    }
    if (!PyType_Check(cls)) {
        PyErr_SetString(PyExc_TypeError, "cls must be a configured type");
        return NULL;
    }
    PyObject *capsule = owned_class_config((PyTypeObject *)cls);
    if (capsule == NULL || !PyCapsule_IsValid(capsule, CONFIG_NAME)) {
        Py_XDECREF(capsule);
        PyErr_SetString(PyExc_TypeError, "cls has no native configuration");
        return NULL;
    }
    Config *config = PyCapsule_GetPointer(capsule, CONFIG_NAME);
    if (config == NULL) {
        Py_DECREF(capsule);
        return NULL;
    }
    uint64_t df = config->delete_fast, db = config->delete_fallback;
    uint64_t uf = config->update_fast, ub = config->update_fallback;
    if (reset) {
        config->delete_fast = config->delete_fallback = config->update_fast =
            config->update_fallback = 0;
    }
    PyObject *result = Py_BuildValue(
        "{sK,sK,sK,sK}", "delete_fast", (unsigned long long)df, "delete_fallback",
        (unsigned long long)db, "update_fast", (unsigned long long)uf,
        "update_fallback", (unsigned long long)ub);
    Py_DECREF(capsule);
    return result;
}

#ifdef UNSTD_CONTAINERS_TESTING
static PyObject *
set_test_hook(PyObject *module, PyObject *args, PyObject *kwargs)
{
    (void)module;
    static char *names[] = {"cls", "callback", "fail_after", NULL};
    PyObject *cls, *callback = Py_None;
    Py_ssize_t fail_after = -1;
    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "O|On:_set_test_hook", names, &cls,
                                     &callback, &fail_after)) {
        return NULL;
    }
    if (!PyType_Check(cls) || (callback != Py_None && !PyCallable_Check(callback)) ||
        fail_after < -1) {
        PyErr_SetString(
            PyExc_TypeError,
            "expected a configured class, callable or None, and fail_after >= -1");
        return NULL;
    }
    PyObject *capsule = owned_class_config((PyTypeObject *)cls);
    if (capsule == NULL || !PyCapsule_IsValid(capsule, CONFIG_NAME)) {
        Py_XDECREF(capsule);
        PyErr_SetString(PyExc_TypeError, "cls has no testing native configuration");
        return NULL;
    }
    Config *config = PyCapsule_GetPointer(capsule, CONFIG_NAME);
    if (config == NULL) {
        Py_DECREF(capsule);
        return NULL;
    }
    uint64_t previous = config->test_seen;
    PyObject *old_hook = config->test_hook;
    config->test_hook = callback == Py_None ? NULL : Py_NewRef(callback);
    config->test_seen = 0;
    config->test_fail_after = fail_after;
    Py_XDECREF(old_hook);
    PyObject *result = PyLong_FromUnsignedLongLong((unsigned long long)previous);
    Py_DECREF(capsule);
    return result;
}
#endif

static PyMethodDef module_methods[] = {
#ifdef UNSTD_CONTAINERS_TESTING
    {"_set_test_hook", (PyCFunction)(void (*)(void))set_test_hook,
     METH_VARARGS | METH_KEYWORDS, "Configure test-only staging hooks."},
#endif
    {"mutation_stats", (PyCFunction)(void (*)(void))mutation_stats,
     METH_VARARGS | METH_KEYWORDS, "Read mutation dispatch counters."},
    {"install", (PyCFunction)(void (*)(void))install, METH_VARARGS | METH_KEYWORDS,
     "Install kernels on an owned SortedList subclass without changing its base."},
    {NULL, NULL, 0, NULL}};

static struct PyModuleDef module_definition = {
    PyModuleDef_HEAD_INIT,
    "_sorted_native",
    "Native segmented-list kernels for unstd-owned sorted classes.",
    0,
    module_methods,
    NULL,
    NULL,
    NULL,
    NULL};

PyMODINIT_FUNC
PyInit__sorted_native(void)
{
    PyObject *module = PyModule_Create(&module_definition);
    if (module == NULL) {
        return NULL;
    }
    if (PyModule_AddIntConstant(module, "KERNEL_VERSION", 3) < 0) {
        Py_DECREF(module);
        return NULL;
    }
    return module;
}
