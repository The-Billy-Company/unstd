/* Optional builtin tree and admitted record cloning.
 *
 * clone/clone_checked refuse foreign nodes without invoking copy/hash/equality
 * hooks. clone_records receives the Python walker for foreign nodes, so it can
 * finish already allocated twins without retrying observable reconstruction.
 *
 * Container allocation can run cyclic GC and arbitrary Python finalizers even
 * while the GIL is held. Every item is owned across recursive allocations;
 * dictionary cursors retain Python's mutation checks. Private presized
 * sequences stay untracked until initialized. Memo-visible mutable results
 * expose only initialized prefixes and stay tracked throughout reconstruction.
 */
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <stdint.h>
#include <stddef.h>
#include <datetime.h>

#define MAX_DEPTH 128
#define MAX_CONTAINERS 65536
#define INLINE_MEMO_SIZE 16
#define RECORD_CACHE_SIZE 32
#define MAX_RECORD_TYPES 32
#define TEMPORAL_DEFERRED 2

typedef struct {
    PyObject *source;
    PyObject *copied;
} MemoEntry;

typedef struct {
    PyObject *reference;
    unsigned int version;
} RecordEntry;

typedef struct {
    PyObject *dict_name;
    PyObject *update_name;
    RecordEntry records[RECORD_CACHE_SIZE];
    PyObject *datetime_module;
    PyObject *datetime_capsule;
    PyDateTime_CAPI *datetime_api;
    PyObject *copy_module;
    PyObject *deepcopy;
    PyObject *tz_keywords;
    PyObject *temporal_names;
    PyObject *model_names;
    PyObject *closed_names;
} ModuleState;

typedef struct {
    PyTypeObject *type;
    PyObject *handler;
    unsigned int version;
    int native;
} RecordRecipe;

typedef struct {
    Py_ssize_t remaining;
    unsigned int count;
    PyObject *ancestors[MAX_DEPTH];
} Proof;

typedef struct {
    Py_ssize_t remaining;
    ModuleState *module_state;
    PyTypeObject *record_type;
    unsigned int record_version;
    PyObject *walk;
    PyObject *admit;
    PyObject *memo_identity;
    PyObject *identity;
    PyObject *model_handler;
    PyObject *model_code;
    PyObject *model_data_walk;
    PyObject *model_data_code;
    PyObject *model_data_body;
    PyObject *model_body_code;
    PyObject *model_native_walk;
    PyObject *model_feature;
    PyObject *model_walk_code;
    PyObject *model_admit_code;
    RecordRecipe recipes[MAX_RECORD_TYPES];
    unsigned int recipe_count;
    int direct_graph;
    int canonical_graph;
    PyObject *loop_walk_code;
    int temporal_loop_dynamic;
    int temporal_graph;
    int refuse_cycles;
    PyObject *cycle_walk;
    PyObject *dict_name;
    PyObject *update_name;
    PyObject *memo;
    MemoEntry *entries;
    Py_ssize_t capacity;
    Py_ssize_t used;
    MemoEntry inline_entries[INLINE_MEMO_SIZE];
    unsigned int count;
    uint64_t ancestor_filter[4];
    PyObject *ancestors[MAX_DEPTH];
} CopyContext;

/* Canonical admission may run descriptors. Before resuming temporal leaves,
 * verify that its memo still has only identity keys and an ordinary pin list.
 * This scan happens at admission boundaries, never once per temporal value. */
static int
temporal_memo_safe(CopyContext *context)
{
    Py_ssize_t position = 0;
    PyObject *key, *value;
    while (PyDict_Next(context->memo, &position, &key, &value)) {
        if (!PyLong_CheckExact(key)) {
            return 0;
        }
    }
    PyObject *alive = PyDict_GetItemWithError(context->memo, context->memo_identity);
    return alive != NULL && PyList_CheckExact(alive);
}

static int clone_value(PyObject *, PyObject **, unsigned int, CopyContext *);
static int clone_closed_value(PyObject *, PyObject **, unsigned int, CopyContext *);
static int clone_value_impl(PyObject *, PyObject **, unsigned int, CopyContext *, PyTypeObject *);
static int atom(PyObject *);
static int eligible(PyObject *, unsigned int, Proof *);
static int record_current(CopyContext *);
static int record_cached(PyTypeObject *, ModuleState *);
static int record_cache_store(PyTypeObject *, unsigned int, ModuleState *);
static int clone_record(PyObject *, PyObject **, unsigned int, CopyContext *);
static int clone_record_impl(PyObject *, PyObject **, unsigned int, CopyContext *);
static int clone_budget(unsigned int *, int *);
static PyObject *clone_graph(PyObject *, PyObject *const *, Py_ssize_t);
static int clone_model(PyObject *, PyObject **, unsigned int, CopyContext *);
static PyObject *clone_walk_canonical_graph(PyObject *, PyObject *const *, Py_ssize_t);

enum {
    MODEL_NEW, MODEL_EXTRA, MODEL_FIELDS, MODEL_PRIVATE,
    MODEL_DATA_WALK, MODEL_ADMIT, MODEL_WALK, MODEL_NATIVE, MODEL_PROJECT,
    MODEL_CANONICAL, MODEL_CODE, MODEL_UNDEFINED, MODEL_CANONICAL_DATA, MODEL_DATA_CODE,
    MODEL_DATA_BODY, MODEL_CANONICAL_BODY, MODEL_BODY_CODE, MODEL_NATIVE_WALK,
    MODEL_HANDLER, MODEL_FEATURE
};

static int
recipe_candidate(PyTypeObject *type, CopyContext *context)
{
    if (context->admit == NULL || !(type->tp_flags & Py_TPFLAGS_HEAPTYPE)) {
        return 0;
    }
    PyTypeObject *meta = Py_TYPE(type);
    return (meta == &PyType_Type && type->tp_base == &PyBaseObject_Type)
        || (context->model_handler != NULL
            && meta->tp_hash == PyType_Type.tp_hash
            && meta->tp_richcompare == PyType_Type.tp_richcompare);
}

static int
model_current(CopyContext *context)
{
    if (PyFunction_GetCode(context->model_handler) != context->model_code) {
        return 0;
    }
    if (context->model_data_walk != NULL) {
        return 1;
    }
    /* Non-model graphs do not pay for model-global resolution. Hold the entry
     * code across callbacks; resolve the state continuation only on first use. */
    PyObject *globals = PyFunction_GetGlobals(context->model_handler);
    PyObject *names = context->module_state->model_names;
    PyObject *data_walk = PyDict_GetItem(globals,
        PyTuple_GET_ITEM(names, MODEL_DATA_WALK));
    PyObject *canonical = PyDict_GetItem(globals, PyTuple_GET_ITEM(names, MODEL_CANONICAL));
    PyObject *code = PyDict_GetItem(globals, PyTuple_GET_ITEM(names, MODEL_CODE));
    PyObject *undefined = PyDict_GetItem(globals, PyTuple_GET_ITEM(names, MODEL_UNDEFINED));
    PyObject *canonical_data = PyDict_GetItem(globals, PyTuple_GET_ITEM(names, MODEL_CANONICAL_DATA));
    PyObject *data_code = PyDict_GetItem(globals, PyTuple_GET_ITEM(names, MODEL_DATA_CODE));
    PyObject *body = PyDict_GetItem(globals, PyTuple_GET_ITEM(names, MODEL_DATA_BODY));
    PyObject *canonical_body = PyDict_GetItem(globals, PyTuple_GET_ITEM(names, MODEL_CANONICAL_BODY));
    PyObject *body_code = PyDict_GetItem(globals, PyTuple_GET_ITEM(names, MODEL_BODY_CODE));
    PyObject *native_walk = PyDict_GetItem(globals, PyTuple_GET_ITEM(names, MODEL_NATIVE_WALK));
    PyObject *feature = PyDict_GetItem(globals, PyTuple_GET_ITEM(names, MODEL_FEATURE));
    int feature_overflow = 0;
    int canonical_feature = feature != NULL && PyLong_CheckExact(feature)
        && PyLong_AsLongAndOverflow(feature, &feature_overflow) == 1 && !feature_overflow;
    if (context->model_handler == canonical && context->model_code == code
        && undefined != NULL && undefined != Py_None
        && data_walk != NULL && data_walk == canonical_data
        && PyFunction_Check(data_walk) && PyFunction_GetCode(data_walk) == data_code
        && body != NULL && body == canonical_body && PyFunction_Check(body)
        && PyFunction_GetCode(body) == body_code
        && native_walk != NULL && PyCFunction_Check(native_walk)
        && PyCFunction_GET_FUNCTION(native_walk)
            == (PyCFunction)(void(*)(void))clone_walk_canonical_graph
        && canonical_feature
        && PyFunction_Check(context->walk) && PyFunction_Check(context->admit)) {
        context->model_data_walk = Py_NewRef(data_walk);
        context->model_data_code = Py_NewRef(PyFunction_GetCode(data_walk));
        context->model_data_body = Py_NewRef(body);
        context->model_body_code = Py_NewRef(body_code);
        context->model_native_walk = Py_NewRef(native_walk);
        context->model_feature = Py_NewRef(feature);
        context->model_walk_code = Py_NewRef(PyFunction_GetCode(context->walk));
        context->model_admit_code = Py_NewRef(PyFunction_GetCode(context->admit));
        return 1;
    }
    context->model_handler = NULL;
    return 0;
}

/* Only completed containers enter this private pointer-key table. Owning both
 * references keeps source addresses stable and never exposes NULL sequence
 * slots to Python, even if allocation-triggered GC mutates the source graph.
 * The inline table handles small graphs without allocating any Python memo
 * objects. Optional caller memos are exported only after the whole walk succeeds. */
/* A policy probe must not add foreign-key equality callbacks to the Python
 * branch. These bounded namespace scans allocate and invoke no Python code. */
/* CPython 3.12--3.14 share this dictionary-key prefix. Reading its kind
 * byte is the same O(1) proof used by _PyDict_HasOnlyStringKeys; that function
 * is hidden in 3.13/3.14. Unicode/split tables contain exact str keys. General
 * tables still need the exact-str scan, since str subclasses may run hooks. */
typedef struct {
    Py_ssize_t references;
    uint8_t log_size;
    uint8_t log_index_bytes;
    uint8_t kind;
} DictKeysPrefix;

static int
ordinary_namespace(PyObject *dictionary)
{
    if (dictionary == NULL || !PyDict_CheckExact(dictionary)
        || PyDict_GET_SIZE(dictionary) > 4096) {
        return 0;
    }
#if PY_VERSION_HEX >= 0x030C0000 && PY_VERSION_HEX < 0x030F0000 && !defined(Py_GIL_DISABLED)
    const unsigned char *keys = (const unsigned char *)((PyDictObject *)dictionary)->ma_keys;
    unsigned char kind = keys[offsetof(DictKeysPrefix, kind)];
    if (kind == 1 || kind == 2) {
        return 1;
    }
#endif
    Py_ssize_t position = 0;
    PyObject *key, *value;
    while (PyDict_Next(dictionary, &position, &key, &value)) {
        if (!PyUnicode_CheckExact(key)) {
            return 0;
        }
    }
    return 1;
}

static int
closed_temporal_policy(ModuleState *state, PyObject *walk)
{
    PyDateTime_CAPI *api = state->datetime_api;
    if (!PyFunction_Check(walk)) {
        return 0;
    }
    PyObject *globals = PyFunction_GetGlobals(walk);
    if (!ordinary_namespace(globals)) {
        return 0;
    }
    if (PyDict_GetItemWithError(globals, PyTuple_GET_ITEM(state->temporal_names, 9)) != walk
        || PyDict_GetItemWithError(globals, PyTuple_GET_ITEM(state->temporal_names, 0)) != walk
        || PyDict_GetItemWithError(globals, PyTuple_GET_ITEM(state->temporal_names, 1))
            != PyFunction_GetCode(walk)
        || PyDict_GetItemWithError(globals, PyTuple_GET_ITEM(state->temporal_names, 3))
            != state->datetime_module) {
        return 0;
    }
    PyObject *namespace = PyModule_GetDict(state->datetime_module);
    if (!ordinary_namespace(namespace)) {
        return 0;
    }
    if (PyDict_GetItemWithError(namespace, PyTuple_GET_ITEM(state->temporal_names, 4))
            != (PyObject *)api->DateTimeType
        || PyDict_GetItemWithError(namespace, PyTuple_GET_ITEM(state->temporal_names, 5))
            != (PyObject *)api->TimeType
        || PyDict_GetItemWithError(namespace, PyTuple_GET_ITEM(state->temporal_names, 10))
            != (PyObject *)Py_TYPE(api->TimeZone_UTC)
        || PyDict_GetItemWithError(namespace, PyTuple_GET_ITEM(state->closed_names, 1))
            != (PyObject *)api->DateType
        || PyDict_GetItemWithError(namespace, PyTuple_GET_ITEM(state->closed_names, 2))
            != (PyObject *)api->DeltaType) {
        return 0;
    }
    PyObject *graph_atoms = PyDict_GetItemWithError(globals,
        PyTuple_GET_ITEM(state->closed_names, 3));
    if (graph_atoms == NULL || !PyFrozenSet_CheckExact(graph_atoms)
        || PySet_GET_SIZE(graph_atoms) > 64 || ((PySetObject *)graph_atoms)->mask >= 128) {
        return 0;
    }
    unsigned int primitive_types = 0;
    PySetObject *graph_policy = (PySetObject *)graph_atoms;
    for (Py_ssize_t i = 0; i <= graph_policy->mask; i++) {
        setentry entry = graph_policy->table[i];
        if (entry.key != NULL && entry.hash != -1
            && Py_TYPE(entry.key) != &PyType_Type) {
            return 0;
        }
        primitive_types |= (entry.key == (PyObject *)&PyLong_Type)
            | ((entry.key == (PyObject *)&PyUnicode_Type) << 1)
            | ((entry.key == (PyObject *)&PyFloat_Type) << 2)
            | ((entry.key == (PyObject *)&PyBool_Type) << 3)
            | ((entry.key == (PyObject *)Py_TYPE(Py_None)) << 4)
            | ((entry.key == (PyObject *)&PyBytes_Type) << 5)
            | ((entry.key == (PyObject *)&PyComplex_Type) << 6)
            | ((entry.key == (PyObject *)Py_TYPE(Py_Ellipsis)) << 7)
            | ((entry.key == (PyObject *)Py_TYPE(Py_NotImplemented)) << 8);
    }
    if (primitive_types != 511) {
        return 0;
    }
    PyObject *atoms = PyDict_GetItemWithError(globals,
        PyTuple_GET_ITEM(state->closed_names, 0));
    if (atoms == NULL || !PyFrozenSet_CheckExact(atoms)
        || PySet_GET_SIZE(atoms) > 64 || ((PySetObject *)atoms)->mask >= 128) {
        return 0;
    }
    int flags = 0, none_atom = 0, delta_atom = 0;
    PySetObject *atom_policy = (PySetObject *)atoms;
    for (Py_ssize_t i = 0; i <= atom_policy->mask; i++) {
        setentry entry = atom_policy->table[i];
        if (entry.key == NULL || entry.hash == -1) {
            continue;
        }
        if (Py_TYPE(entry.key) != &PyType_Type) {
            return 0;
        }
        flags |= entry.key == (PyObject *)api->DateType;
        none_atom |= entry.key == (PyObject *)Py_TYPE(Py_None);
        delta_atom |= entry.key == (PyObject *)api->DeltaType;
    }
    PyObject *zones = PyDict_GetItemWithError(globals,
        PyTuple_GET_ITEM(state->temporal_names, 11));
    if (zones == NULL || !PyFrozenSet_CheckExact(zones)
        || PySet_GET_SIZE(zones) > 8 || ((PySetObject *)zones)->mask >= 32) {
        return 0;
    }
    PySetObject *zone_policy = (PySetObject *)zones;
    for (Py_ssize_t i = 0; i <= zone_policy->mask; i++) {
        setentry entry = zone_policy->table[i];
        if (entry.key == NULL || entry.hash == -1) {
            continue;
        }
        if (Py_TYPE(entry.key) != &PyType_Type) {
            return 0;
        }
        if (none_atom && entry.key == (PyObject *)Py_TYPE(Py_None)) {
            flags |= 2;
        }
        if (delta_atom && entry.key == (PyObject *)Py_TYPE(api->TimeZone_UTC)) {
            flags |= 4;
        }
    }
    return flags;
}

static inline int
closed_temporal_atom(PyObject *value, int flags, PyDateTime_CAPI *api)
{
    if (flags == 0) {
        return 0;
    }
    PyTypeObject *type = Py_TYPE(value);
    if (type != api->DateType && type != api->DateTimeType && type != api->TimeType) {
        return 0;
    }
    if (type == api->DateType) {
        return flags & 1;
    }
    PyObject *zone = type == api->DateTimeType
        ? PyDateTime_DATE_GET_TZINFO(value) : PyDateTime_TIME_GET_TZINFO(value);
    return (zone == Py_None && (flags & 2))
        || (zone == api->TimeZone_UTC && (flags & 4));
}



/* Independent bounded proof of the standard CPython probe geometry:
 * https://github.com/python/cpython/blob/3.12/Objects/setobject.c#L49
 * https://github.com/python/cpython/blob/3.14/Objects/setobject.c#L70
 * Qualified GIL builds use the default nine-step linear window and perturb5.
 * Only matching hashes can compare keys.
 * Foreign comparisons defer before running them; exact type keys compare by
 * identity. No policy references escape this allocation-free check, so policy
 * replacement retains its ordinary weakref/finalizer timing. */
static int
exact_type_membership(PyObject *set, PyTypeObject *kind, Py_ssize_t limit,
                      Py_ssize_t mask_limit)
{
#if PY_VERSION_HEX < 0x030C0000 || PY_VERSION_HEX >= 0x030F0000 || defined(Py_GIL_DISABLED) \
    || (defined(LINEAR_PROBES) && LINEAR_PROBES != 9)
    (void)set; (void)kind; (void)limit; (void)mask_limit;
    return -1;
#else
    if (set == NULL || !PyFrozenSet_CheckExact(set) || PySet_GET_SIZE(set) > limit) {
        return -1;
    }
    PySetObject *policy = (PySetObject *)set;
    if (policy->mask >= mask_limit) {
        return -1;
    }
    Py_hash_t hash = PyObject_Hash((PyObject *)kind);
    if (hash == -1) {
        return -1;
    }
    size_t mask = (size_t)policy->mask;
    size_t perturb = (size_t)hash, index = (size_t)hash & mask;
    size_t budget = (mask + 1) * 4;
    while (budget != 0) {
        size_t end = index + 9 <= mask ? index + 9 : index;
        for (size_t i = index; i <= end && budget != 0; i++, budget--) {
            setentry entry = policy->table[i];
            if (entry.hash == 0 && entry.key == NULL) {
                return 0;
            }
            if (entry.hash == hash) {
                if (entry.key == (PyObject *)kind) {
                    return 1;
                }
                if (Py_TYPE(entry.key) != &PyType_Type) {
                    return -1;
                }
            }
        }
        perturb >>= 5;
        index = (index * 5 + 1 + perturb) & mask;
    }
    return -1;
#endif
}

static int
temporal_memberships_safe(ModuleState *state, PyObject *globals, PyTypeObject *kind)
{
    if (!ordinary_namespace(globals)) {
        return 0;
    }
    PyObject *atoms = PyDict_GetItemWithError(globals, PyTuple_GET_ITEM(state->closed_names, 0));
    PyObject *graph = PyDict_GetItemWithError(globals, PyTuple_GET_ITEM(state->closed_names, 3));
    return exact_type_membership(atoms, kind, 64, 128) == 0
        && exact_type_membership(graph, kind, 64, 128) == 0
        && exact_type_membership(graph, Py_TYPE(state->datetime_api->TimeZone_UTC), 64, 128) == 0
        && exact_type_membership(graph, Py_TYPE(Py_None), 64, 128) == 1;
}

/* Only a complete root sequence can return immutable temporal atoms without
 * recording each leaf in a Python memo. A later allocating child could change
 * policy; refusing and replaying an earlier temporal prefix would then change
 * its copy protocol. After the sole output allocation, validate each leaf
 * before filling its slot. No allocation/callback occurs in this bounded loop;
 * source retains every approved immutable prefix leaf if later refusal must
 * discard the private output before the ordinary continuation starts.
 *
 * Bound the allocation-free scan/fill to the existing 65536-node work budget.
 * Larger sequences keep the ordinary walker and its interruption behavior;
 * the bounded native section takes under a millisecond on qualification runs. */
static int
clone_temporal_sequence(PyObject *source, PyObject *walk, ModuleState *state,
                        int recursion_limit, PyObject **result)
{
    int tuple = PyTuple_CheckExact(source);
    if (!tuple && !PyList_CheckExact(source)) {
        return 0;
    }
    Py_ssize_t size = Py_SIZE(source);
    if (size == 0 || size > MAX_CONTAINERS) {
        return 0;
    }
    PyDateTime_CAPI *api = state->datetime_api;
    PyObject *first = tuple ? PyTuple_GET_ITEM(source, 0) : PyList_GET_ITEM(source, 0);
    PyTypeObject *kind = Py_TYPE(first);
    if (kind != api->DateType && kind != api->DateTimeType && kind != api->TimeType) {
        return 0;
    }
    if (PyErr_CheckSignals() < 0) {
        return -1;
    }
    /* Reject nested/foreign shapes without adding an allocating attempt to
     * their existing closed-graph/canonical continuation. */
    if (Py_SIZE(source) != size) {
        return 0;
    }
    for (Py_ssize_t i = 0; i < size; i++) {
        PyObject *item = tuple ? PyTuple_GET_ITEM(source, i) : PyList_GET_ITEM(source, i);
        kind = Py_TYPE(item);
        if (kind != api->DateType && kind != api->DateTimeType
            && kind != api->TimeType && !atom(item)) {
            return 0;
        }
    }
    PyObject *output = tuple ? PyTuple_New(size) : PyList_New(size);
    if (output == NULL) {
        return -1;
    }
    int flags = closed_temporal_policy(state, walk);
    if (flags == 0 || Py_SIZE(source) != size || Py_GetRecursionLimit() != recursion_limit) {
        Py_DECREF(output);
        return 0;
    }
    for (Py_ssize_t i = 0; i < size; i++) {
        PyObject *item = tuple ? PyTuple_GET_ITEM(source, i) : PyList_GET_ITEM(source, i);
        if (!closed_temporal_atom(item, flags, api) && !atom(item)) {
            Py_DECREF(output);
            return 0;
        }
        if (tuple) {
            PyTuple_SET_ITEM(output, i, Py_NewRef(item));
        } else {
            PyList_SET_ITEM(output, i, Py_NewRef(item));
        }
    }
    *result = output;
    return 1;
}

static size_t
memo_hash(PyObject *source)
{
    uintptr_t address = (uintptr_t)source >> 4;
    /* Fold nearby allocator bits into the low table-index bits so aligned
     * small containers do not cluster at the same initial slot. */
    return (size_t)(address ^ (address >> 4));
}

static MemoEntry *
memo_entry(MemoEntry *entries, Py_ssize_t capacity, PyObject *source)
{
    size_t mask = (size_t)capacity - 1;
    size_t hash = memo_hash(source);
    size_t perturb = hash;
    size_t position = hash & mask;
    while (entries[position].source != NULL
           && entries[position].source != source) {
        perturb >>= 5;
        position = (5 * position + 1 + perturb) & mask;
    }
    return &entries[position];
}

static int
memo_lookup_direct(PyObject *source, PyObject **result, CopyContext *context)
{
    int owned = context->identity == NULL;
    PyObject *identity = owned ? PyLong_FromVoidPtr(source) : context->identity;
    if (identity == NULL) {
        return -1;
    }
    PyObject *copied = PyDict_GetItemWithError(context->memo, identity);
    if (copied != NULL) {
        *result = Py_NewRef(copied);
        if (owned) {
            Py_DECREF(identity);
        }
        return 1;
    }
    if (owned) {
        Py_DECREF(identity);
    }
    return PyErr_Occurred() ? -1 : 0;
}

static int
memo_lookup_closed(PyObject *source, PyObject **result, CopyContext *context)
{
    /* Preserve the caller's memo precedence, including entries installed by a
     * reentrant collector callback. Pure builtin root calls have no outer memo. */
    if (context->memo != NULL && PyDict_GET_SIZE(context->memo) != 0) {
        PyObject *identity = PyLong_FromVoidPtr(source);
        if (identity == NULL) {
            return -1;
        }
        PyObject *copied = PyDict_GetItemWithError(context->memo, identity);
        Py_DECREF(identity);
        if (copied != NULL) {
            *result = Py_NewRef(copied);
            return 1;
        }
        if (PyErr_Occurred()) {
            return -1;
        }
    }
    MemoEntry *entry = memo_entry(context->entries, context->capacity, source);
    if (entry->source != NULL) {
        *result = Py_NewRef(entry->copied);
        return 1;
    }
    return 0;
}

static int
memo_lookup(PyObject *source, PyObject **result, CopyContext *context)
{
    return context->direct_graph
        ? memo_lookup_direct(source, result, context)
        : memo_lookup_closed(source, result, context);
}

static int
memo_grow(CopyContext *context)
{
    Py_ssize_t capacity = context->capacity * 2;
    MemoEntry *entries = PyMem_Calloc((size_t)capacity, sizeof(MemoEntry));
    if (entries == NULL) {
        PyErr_NoMemory();
        return -1;
    }
    for (Py_ssize_t i = 0; i < context->capacity; i++) {
        MemoEntry entry = context->entries[i];
        if (entry.source != NULL) {
            *memo_entry(entries, capacity, entry.source) = entry;
        }
    }
    if (context->entries != context->inline_entries) {
        PyMem_Free(context->entries);
    }
    context->entries = entries;
    context->capacity = capacity;
    return 0;
}

static int
memo_store_closed(PyObject *source, PyObject *copied, CopyContext *context)
{
    if ((context->used + 1) * 3 >= context->capacity * 2
        && memo_grow(context) < 0) {
        return -1;
    }
    MemoEntry *entry = memo_entry(context->entries, context->capacity, source);
    if (entry->source == NULL) {
        entry->source = Py_NewRef(source);
        entry->copied = Py_NewRef(copied);
        ++context->used;
    } else {
        Py_SETREF(entry->copied, Py_NewRef(copied));
    }
    return 0;
}

static int
memo_store(PyObject *source, PyObject *copied, CopyContext *context)
{
    if (context->direct_graph) {
        int owned = context->identity == NULL;
        PyObject *identity = owned ? PyLong_FromVoidPtr(source) : context->identity;
        if (identity == NULL) {
            return -1;
        }
        int status = PyDict_SetItem(context->memo, identity, copied);
        if (owned) {
            Py_DECREF(identity);
        }
        return status;
    }
    return memo_store_closed(source, copied, context);
}

static void
memo_clear(CopyContext *context)
{
    for (Py_ssize_t i = 0; i < context->capacity; i++) {
        MemoEntry entry = context->entries[i];
        if (entry.source != NULL) {
            /* Clear first: decrefs can run source finalizers during cleanup. */
            context->entries[i].source = NULL;
            context->entries[i].copied = NULL;
            Py_DECREF(entry.source);
            Py_DECREF(entry.copied);
        }
    }
    if (context->entries != context->inline_entries) {
        PyMem_Free(context->entries);
    }
}

static int
memo_export(CopyContext *context)
{
    PyObject *pending = _PyDict_NewPresized(context->used);
    if (pending == NULL) {
        return -1;
    }
    if (PyObject_GC_IsTracked(pending)) {
        PyObject_GC_UnTrack(pending);
    }
    PyObject *sources = PyList_New(context->used);
    if (sources == NULL) {
        Py_DECREF(pending);
        return -1;
    }
    if (PyObject_GC_IsTracked(sources)) {
        PyObject_GC_UnTrack(sources);
    }
    Py_ssize_t written = 0;
    int status = 0;
    for (Py_ssize_t i = 0; i < context->capacity; i++) {
        MemoEntry entry = context->entries[i];
        if (entry.source == NULL) {
            continue;
        }
        PyList_SET_ITEM(sources, written++, Py_NewRef(entry.source));
        PyObject *identity = PyLong_FromVoidPtr(entry.source);
        if (identity == NULL) {
            status = -1;
            break;
        }
        status = PyDict_SetItem(pending, identity, entry.copied);
        if (PyObject_GC_IsTracked(pending)) {
            PyObject_GC_UnTrack(pending);
        }
        Py_DECREF(identity);
        if (status < 0) {
            break;
        }
    }
    if (status == 0) {
        /* Match copy._keep_alive's reserved ID(memo) entry so Python deepcopy
         * can continue using these identities without source address reuse. */
        PyObject *identity = PyLong_FromVoidPtr(context->memo);
        PyObject *alive = identity == NULL ? NULL
            : PyDict_GetItemWithError(context->memo, identity);
        if (identity == NULL || (alive == NULL && PyErr_Occurred())) {
            status = -1;
        } else if (alive == NULL) {
            status = PyDict_SetItem(context->memo, identity, sources);
        } else {
            Py_INCREF(alive);
            if (PyList_CheckExact(alive)) {
                Py_ssize_t size = PyList_GET_SIZE(alive);
                status = PyList_SetSlice(alive, size, size, sources);
            } else {
                /* Observable append must happen before each node's children,
                 * as in Python. No foreign hook has run in this closed graph;
                 * defer without committing this postorder scratch memo. */
                status = 1;
            }
            Py_DECREF(alive);
        }
        Py_XDECREF(identity);
        if (status == 0) {
            status = PyDict_Update(context->memo, pending);
        }
    }
    /* Scratch mappings and pin lists stay hidden across allocating export
     * steps. Track the fully initialized pin list after commit, including an
     * error after publication, so a callback-created cycle remains collectable. */
    if (written == context->used && !PyObject_GC_IsTracked(sources)) {
        PyObject_GC_Track(sources);
    }
    Py_DECREF(sources);
    Py_DECREF(pending);
    return status;
}

static int
memo_pin(PyObject *source, CopyContext *context)
{
    PyObject *alive = PyDict_GetItemWithError(context->memo,
                                            context->memo_identity);
    if (alive == NULL) {
        if (PyErr_Occurred()) {
            return -1;
        }
        alive = PyList_New(0);
        if (alive == NULL) {
            return -1;
        }
        if (PyDict_SetItem(context->memo, context->memo_identity, alive) < 0) {
            Py_DECREF(alive);
            return -1;
        }
    } else {
        Py_INCREF(alive);
    }
    int status;
    if (PyList_CheckExact(alive)) {
        status = PyList_Append(alive, source);
    } else {
        /* Match Python's append semantics for a caller-modified reserved memo
         * entry, including errors or an overridden append on a list subclass. */
        context->temporal_graph = 0;
        PyObject *returned = PyObject_CallMethod(alive, "append", "O", source);
        status = returned == NULL ? -1 : 0;
        Py_XDECREF(returned);
    }
    Py_DECREF(alive);
    return status;
}

/* The Python admission layer owns copy protocol eligibility. Recheck the
 * narrower allocation/attribute contract here without invoking descriptors or
 * metaclass hooks. Inherited/extension layouts and finalizers stay in Python. */
static int
record_admissible(PyTypeObject *type)
{
    if (Py_TYPE(type) != &PyType_Type || type->tp_base != &PyBaseObject_Type
        || type->tp_new != PyBaseObject_Type.tp_new
        || type->tp_alloc != PyType_GenericAlloc
        || type->tp_getattro != PyObject_GenericGetAttr
        || !(type->tp_flags & Py_TPFLAGS_HEAPTYPE)
        || !(type->tp_flags & Py_TPFLAGS_HAVE_GC)
        || (type->tp_flags & Py_TPFLAGS_IS_ABSTRACT)
        || type->tp_dict == NULL) {
        return 0;
    }
    int dictionary = 0;
    Py_ssize_t position = 0;
    PyObject *key, *value;
    while (PyDict_Next(type->tp_dict, &position, &key, &value)) {
        if (!PyUnicode_CheckExact(key)) {
            return 0;
        }
        if (PyUnicode_CompareWithASCIIString(key, "__dict__") == 0) {
            dictionary = Py_IS_TYPE(value, &PyGetSetDescr_Type)
                && PyDescr_TYPE(value) == type;
        } else if (PyUnicode_CompareWithASCIIString(key, "__del__") == 0
                   || PyUnicode_CompareWithASCIIString(key, "__slots__") == 0) {
            return 0;
        }
    }
    return dictionary;
}

static unsigned int
record_version(PyTypeObject *type)
{
#if PY_VERSION_HEX >= 0x030C0000 && PY_VERSION_HEX < 0x030D0000
    /* Failed base assignment in 3.12 may leave a nonzero invalid tag. */
    if (!(PyType_GetFlags(type) & Py_TPFLAGS_VALID_VERSION_TAG)) {
        return 0;
    }
    return type->tp_version_tag;
#elif PY_VERSION_HEX >= 0x030D0000 && PY_VERSION_HEX < 0x030F0000
    return type->tp_version_tag;
#else
    /* Other interpreter versions retain uncached allocation admission. */
    (void)type;
    return 0;
#endif
}

static int
record_current(CopyContext *context)
{
    PyTypeObject *type = context->record_type;
    if (context->record_version != 0
        && context->record_version == record_version(type)) {
        return 1;
    }
    if (context->module_state != NULL
        && record_cached(type, context->module_state)) {
        context->record_version = record_version(type);
        return 1;
    }
    if (!record_admissible(type)) {
        return 0;
    }
    context->record_version = record_version(type);
    return 1;
}

static int
python_value(PyObject *source, PyObject **result, CopyContext *context)
{
    /* Arbitrary copy hooks may inject foreign memo keys. Retain the general
     * walk from this boundary onward instead of adding observable lookups. */
    context->temporal_graph = 0;
#if PY_VERSION_HEX >= 0x030C0000 && PY_VERSION_HEX < 0x030F0000
    PyThreadState *state = NULL;
    if (context->direct_graph) {
        state = PyThreadState_Get();
        /* The real _walk callback replaces this node's virtual _walk frame. */
        ++state->py_recursion_remaining;
    }
#endif
    *result = context->direct_graph
        ? PyObject_CallFunctionObjArgs(context->walk, source, context->memo, NULL)
        : PyObject_CallOneArg(context->walk, source);
#if PY_VERSION_HEX >= 0x030C0000 && PY_VERSION_HEX < 0x030F0000
    if (state != NULL) {
        --state->py_recursion_remaining;
    }
#endif
    return *result == NULL ? -1 : 1;
}

/* Only the canonical structural continuation opts into this specialization.
 * The generic custom-walker API continues to delegate every temporal node. */
static int
temporal_value(PyObject *source, PyObject **result, CopyContext *context)
{
    ModuleState *state = context->module_state;
    PyDateTime_CAPI *api = state->datetime_api;
    PyTypeObject *type = Py_TYPE(source);
    if (type != api->DateTimeType && type != api->TimeType) {
        return 0;
    }
    /* Admission can modify the memo, but most admitted graphs contain no
     * temporal nodes. Certify only at the first temporal boundary; foreign
     * Python continuations still disable this specialization permanently. */
    if (context->temporal_graph == TEMPORAL_DEFERRED) {
        context->temporal_graph = temporal_memo_safe(context);
        if (!context->temporal_graph) {
            return 0;
        }
    }
    if (!PyFunction_Check(context->walk)) {
        return 0;
    }
    PyObject *globals = PyFunction_GetGlobals(context->walk);
    if (!temporal_memberships_safe(state, globals, type)) {
        return 0;
    }
    if (PyDict_GetItemWithError(globals, PyTuple_GET_ITEM(state->temporal_names, 9)) != context->walk
        || PyDict_GetItemWithError(globals, PyTuple_GET_ITEM(state->temporal_names, 0)) != context->walk
        || PyDict_GetItemWithError(globals, PyTuple_GET_ITEM(state->temporal_names, 1))
            != PyFunction_GetCode(context->walk)) {
        return PyErr_Occurred() ? -1 : 0;
    }
    if (PyDict_GetItemWithError(globals, PyTuple_GET_ITEM(state->temporal_names, 3)) != state->datetime_module) {
        return PyErr_Occurred() ? -1 : 0;
    }
    PyObject *datetime_namespace = PyModule_GetDict(state->datetime_module);
    if (PyDict_GetItemWithError(datetime_namespace, PyTuple_GET_ITEM(state->temporal_names, 4)) != (PyObject *)api->DateTimeType
        || PyDict_GetItemWithError(datetime_namespace, PyTuple_GET_ITEM(state->temporal_names, 5)) != (PyObject *)api->TimeType
        || PyDict_GetItemWithError(datetime_namespace, PyTuple_GET_ITEM(state->temporal_names, 10))
            != (PyObject *)Py_TYPE(api->TimeZone_UTC)) {
        return PyErr_Occurred() ? -1 : 0;
    }
    /* Exact None/UTC temporal values use the structural walk's immutable
     * branch. That branch never consults copy's namespace or dispatch tables;
     * probing them here would add foreign-key callbacks absent in Python. */
    PyObject *zone = type == api->DateTimeType
        ? PyDateTime_DATE_GET_TZINFO(source) : PyDateTime_TIME_GET_TZINFO(source);
    if (zone != Py_None && zone != api->TimeZone_UTC) {
        return 0;
    }
    /* A rebound policy can route even None/UTC through deepcopy. Inspect
     * only an exact, small frozenset of ordinary classes: membership in a
     * foreign-key set can itself invoke equality hooks owned by Python. */
    PyObject *zones = PyDict_GetItemWithError(globals,
        PyTuple_GET_ITEM(state->temporal_names, 11));
    if (zones == NULL) {
        return PyErr_Occurred() ? -1 : 0;
    }
    if (exact_type_membership(zones, Py_TYPE(zone), 8, 32) != 1) {
        return 0;
    }
    PyObject *identity = PyLong_FromVoidPtr(source);
    if (identity == NULL) {
        return -1;
    }
    int found = PyDict_Contains(context->memo, identity);
    if (found != 0) {
        if (found > 0) {
            *result = PyObject_GetItem(context->memo, identity);
        }
        Py_DECREF(identity);
        return *result != NULL ? 1 : -1;
    }
    if (memo_pin(source, context) < 0) {
        Py_DECREF(identity);
        return -1;
    }
    /* Mirror the canonical timezone branch with the same shared memo. UTC's
     * offset still uses the Python continuation; source pins, memo overrides
     * and recursive frame accounting keep their established order. */
    PyObject *copied_zone = NULL;
#if PY_VERSION_HEX >= 0x030C0000 && PY_VERSION_HEX < 0x030F0000
    PyThreadState *thread = PyThreadState_Get();
    if (thread->py_recursion_remaining <= 0) {
        Py_DECREF(identity);
        PyErr_SetString(PyExc_RecursionError,
                        "maximum recursion depth while cloning a timezone");
        return -1;
    }
    --thread->py_recursion_remaining;
#endif
    if (zone == Py_None) {
        /* _walk returns graph atoms before consulting caller memo entries. */
        copied_zone = Py_NewRef(zone);
    } else {
        PyObject *zone_identity = PyLong_FromVoidPtr(zone);
        if (zone_identity != NULL) {
            int zone_found = PyDict_Contains(context->memo, zone_identity);
            if (zone_found > 0) {
                copied_zone = PyObject_GetItem(context->memo, zone_identity);
            } else if (zone_found == 0 && memo_pin(zone, context) == 0) {
                PyObject *offset = PyObject_CallMethod(zone, "utcoffset", "O", Py_None);
                if (offset != NULL) {
                    /* The first timezone still walks its offset with the same
                     * Python continuation; subsequent references use its memo. */
                    PyObject *copied_offset = PyObject_CallFunctionObjArgs(
                        context->walk, offset, context->memo, NULL);
                    if (copied_offset == offset) {
                        copied_zone = Py_NewRef(zone);
                    } else if (copied_offset != NULL) {
                        PyObject *name = PyObject_CallMethod(zone, "tzname", "O", Py_None);
                        PyObject *zone_type = PyObject_GetAttrString(state->datetime_module, "timezone");
                        if (name != NULL && zone_type != NULL) {
                            copied_zone = PyObject_CallFunctionObjArgs(
                                zone_type, copied_offset, name, NULL);
                        }
                        Py_XDECREF(name);
                        Py_XDECREF(zone_type);
                    }
                    Py_XDECREF(copied_offset);
                    Py_DECREF(offset);
                }
                if (copied_zone != NULL
                    && PyDict_SetItem(context->memo, zone_identity, copied_zone) < 0) {
                    Py_CLEAR(copied_zone);
                }
            }
            Py_DECREF(zone_identity);
        }
    }
#if PY_VERSION_HEX >= 0x030C0000 && PY_VERSION_HEX < 0x030F0000
    ++thread->py_recursion_remaining;
#endif
    if (copied_zone == NULL) {
        Py_DECREF(identity);
        return -1;
    }
    if (copied_zone == zone) {
        *result = Py_NewRef(source);
    } else {
        PyObject *replace = PyObject_GetAttrString(source, "replace");
        if (replace != NULL) {
            PyObject *arguments[] = {copied_zone};
            *result = PyObject_Vectorcall(replace, arguments, 0, state->tz_keywords);
            Py_DECREF(replace);
        }
    }
    Py_DECREF(copied_zone);
    if (*result != NULL && PyDict_SetItem(context->memo, identity, *result) < 0) {
        Py_CLEAR(*result);
    }
    Py_DECREF(identity);
    return *result == NULL ? -1 : 1;
}

static RecordRecipe *
record_recipe(PyTypeObject *type, CopyContext *context)
{
    for (unsigned int i = 0; i < context->recipe_count; i++) {
        if (context->recipes[i].type == type) {
            return &context->recipes[i];
        }
    }
    /* The caller pins the current node and retains its parent iteration state
     * BEFORE invoking admission. Descriptors may mutate live source parents. */
    Py_INCREF(type);
#if PY_VERSION_HEX >= 0x030C0000 && PY_VERSION_HEX < 0x030F0000
    PyThreadState *state = PyThreadState_Get();
    /* Admission's Python helper performs the inline dispatch work that the
     * currently reserved virtual _walk frame represents. */
    ++state->py_recursion_remaining;
#endif
    int temporal = context->temporal_graph;
    context->temporal_graph = 0;
    PyObject *pair = PyObject_CallOneArg(context->admit, (PyObject *)type);
    if (pair != NULL && temporal) {
        context->temporal_graph = TEMPORAL_DEFERRED;
    }
#if PY_VERSION_HEX >= 0x030C0000 && PY_VERSION_HEX < 0x030F0000
    --state->py_recursion_remaining;
#endif
    if (pair == NULL) {
        Py_DECREF(type);
        return NULL;
    }
    if (!PyTuple_CheckExact(pair) || PyTuple_GET_SIZE(pair) != 2
        || (PyTuple_GET_ITEM(pair, 0) != Py_True
            && PyTuple_GET_ITEM(pair, 0) != Py_False)
        || !PyCallable_Check(PyTuple_GET_ITEM(pair, 1))) {
        Py_DECREF(pair);
        Py_DECREF(type);
        PyErr_SetString(PyExc_TypeError,
                        "record admission must return (bool, callable)");
        return NULL;
    }
    RecordRecipe *recipe = &context->recipes[context->recipe_count];
    recipe->type = type;
    recipe->handler = Py_NewRef(PyTuple_GET_ITEM(pair, 1));
    recipe->native = PyTuple_GET_ITEM(pair, 0) == Py_True;
    recipe->version = 0;
    ++context->recipe_count;
    Py_DECREF(pair);
    return recipe;
}

static int
python_recipe(PyObject *source, PyObject **result, CopyContext *context,
              RecordRecipe *recipe)
{
    context->temporal_graph = 0;
    *result = PyObject_CallFunctionObjArgs(recipe->handler, source,
                                          context->memo, NULL);
    if (*result == NULL) {
        return -1;
    }
    if (memo_store(source, *result, context) < 0) {
        Py_CLEAR(*result);
        return -1;
    }
    return 1;
}

static int
shallow_atoms(PyObject *source)
{
    if (PyList_CheckExact(source) || PyTuple_CheckExact(source)) {
        Py_ssize_t size = Py_SIZE(source);
        for (Py_ssize_t i = 0; i < size; i++) {
            PyObject *item = PyList_CheckExact(source)
                ? PyList_GET_ITEM(source, i) : PyTuple_GET_ITEM(source, i);
            if (!atom(item)) {
                return 0;
            }
        }
        return 1;
    }
    if (PyDict_CheckExact(source)) {
        Py_ssize_t position = 0;
        PyObject *key, *value;
        while (PyDict_Next(source, &position, &key, &value)) {
            if (!PyUnicode_CheckExact(key) || !atom(value)) {
                return 0;
            }
        }
        return 1;
    }
    return 0;
}

/* An allocation-free, bounded eligibility pass avoids constructing partial
 * clones of ordinary foreign trees. The GIL and absence of callbacks make
 * borrowed references safe in this pass only. The copying pass revalidates
 * everything because its allocations can trigger source-mutating finalizers. */
static int
eligible_contents(PyObject *source, unsigned int depth, Proof *proof)
{
    if (PyList_CheckExact(source) || PyTuple_CheckExact(source)) {
        Py_ssize_t size = Py_SIZE(source);
        for (Py_ssize_t i = 0; i < size; i++) {
            PyObject *item = PyList_CheckExact(source)
                ? PyList_GET_ITEM(source, i) : PyTuple_GET_ITEM(source, i);
            int status = eligible(item, depth - 1, proof);
            if (status != 1) {
                return status;
            }
        }
        return 1;
    }
    if (PyDict_CheckExact(source)) {
        Py_ssize_t position = 0;
        PyObject *key, *value;
        while (PyDict_Next(source, &position, &key, &value)) {
            if (!PyUnicode_CheckExact(key)) {
                return 0;
            }
            int status = eligible(value, depth - 1, proof);
            if (status != 1) {
                return status;
            }
        }
        return 1;
    }
    return 0;
}

static int
eligible(PyObject *source, unsigned int depth, Proof *proof)
{
    if (atom(source)) {
        return 1;
    }
    if (depth == 0 || (!PyList_CheckExact(source) && !PyTuple_CheckExact(source)
                       && !PyDict_CheckExact(source))) {
        return 0;
    }
    if (--proof->remaining < 0) {
        return 0;
    }
    for (unsigned int i = 0; i < proof->count; i++) {
        if (proof->ancestors[i] == source) {
            return 2;
        }
    }
    /* Also guard the proof itself against a nearly exhausted native C stack.
     * No allocation/callback occurs unless entering fails; errors unwind the
     * proof immediately without touching another borrowed source reference. */
    if (Py_EnterRecursiveCall(" while checking a builtin clone tree")) {
        /* Native stack pressure must defer to the Python walker. It does not
         * prove a cycle and must not select the whole-graph cycle fallback. */
        if (PyErr_ExceptionMatches(PyExc_RecursionError)) {
            PyErr_Clear();
            return 0;
        }
        return -1;
    }
    proof->ancestors[proof->count++] = source;
    int status = eligible_contents(source, depth, proof);
    --proof->count;
    Py_LeaveRecursiveCall();
    return status;
}

static int
primitive_atom_kind(PyObject *value, PyTypeObject *kind)
{
    return kind == &PyLong_Type || kind == &PyUnicode_Type
        || kind == &PyFloat_Type || kind == &PyBool_Type || value == Py_None
        || kind == &PyBytes_Type || kind == &PyComplex_Type
        || value == Py_Ellipsis || value == Py_NotImplemented;
}

static int
graph_atom_kind(PyObject *value, PyTypeObject *kind)
{
    return primitive_atom_kind(value, kind)
        || kind == &PyRange_Type || kind == &PyType_Type
        || kind == &PyProperty_Type || kind == &PyCode_Type
        || kind == &PyFunction_Type || kind == &PyCFunction_Type
        || kind == &_PyWeakref_RefType;
}

static int
atom(PyObject *value)
{
    return primitive_atom_kind(value, Py_TYPE(value));
}

/* Keep closed recursion out of the callback wrapper's larger stack frame. */
static inline int
clone_child(PyObject *source, PyObject **result, unsigned int depth,
            CopyContext *context)
{
    return context->direct_graph
        ? clone_value(source, result, depth, context)
        : clone_closed_value(source, result, depth, context);
}


/* Python container loops test graph-atom membership before calling _walk.
 * A foreign temporal policy must retain BOTH that parent test and the leaf's
 * own test, even on a repeated leaf already present in the shared memo. */

/* Inspect certificates without adding foreign namespace-key comparisons.
 * Actual parent identifiers are still resolved through the ordinary dict API. */
static PyObject *
loop_certificate(PyObject *globals, PyObject *name, int ordinary)
{
    if (ordinary) {
        return PyDict_GetItemWithError(globals, name);
    }
    Py_ssize_t position = 0;
    PyObject *key, *value;
    while (PyDict_Next(globals, &position, &key, &value)) {
        if (PyUnicode_CheckExact(key)
            && (key == name || PyUnicode_Compare(key, name) == 0)) {
            return value;
        }
    }
    return NULL;
}

static int
clone_loop_value(PyObject *source, PyObject **result, unsigned int depth,
                 CopyContext *context)
{
    if (context->canonical_graph && PyFunction_Check(context->walk)) {
        PyDateTime_CAPI *api = context->module_state->datetime_api;
        PyTypeObject *kind = Py_TYPE(source);
        if (kind == api->DateTimeType || kind == api->TimeType || kind == api->DateType) {
            PyObject *globals = PyFunction_GetGlobals(context->walk);
            PyObject *names = context->module_state->temporal_names;
            PyObject *graph_name = PyTuple_GET_ITEM(context->module_state->closed_names, 3);
            PyObject *policy = NULL;
            if (!context->temporal_loop_dynamic) {
                int ordinary = ordinary_namespace(globals);
                int membership = -1;
                int changed_walk = 1;
                if (ordinary) {
                    policy = PyDict_GetItemWithError(globals, graph_name);
                    membership = exact_type_membership(policy, kind, 64, 128);
                    changed_walk = PyDict_GetItemWithError(globals,
                        PyTuple_GET_ITEM(names, 9)) != context->walk
                        || PyFunction_GetCode(context->walk) != context->loop_walk_code;
                }
                if (membership == 0 && !changed_walk) {
                    return clone_value(source, result, depth, context);
                }
                /* Retain the entry code as Python's active parent frame does:
                 * a preceding hook may change the function's current code. */
                if (loop_certificate(globals, PyTuple_GET_ITEM(names, 0), ordinary)
                        != context->walk
                    || loop_certificate(globals, PyTuple_GET_ITEM(names, 1), ordinary)
                        != context->loop_walk_code) {
                    return clone_value(source, result, depth, context);
                }
                if (membership == 1) {
                    *result = Py_NewRef(source);
                    return 1;
                }
            }
            if (policy == NULL) {
                /* These are actual parent lookups; preserve their exceptions,
                 * including foreign-key equality after earlier callbacks. */
                policy = PyDict_GetItemWithError(globals, graph_name);
            }
            if (policy == NULL) {
                if (!PyErr_Occurred()) {
                    PyErr_SetString(PyExc_NameError, "name '_GRAPH_ATOMS' is not defined");
                }
                return -1;
            }
            context->temporal_graph = 0;
            context->temporal_loop_dynamic = 1;
            Py_INCREF(policy);
            int contains = PySequence_Contains(policy, (PyObject *)kind);
            Py_DECREF(policy);
            if (contains < 0) {
                return -1;
            }
            if (contains) {
                *result = Py_NewRef(source);
                return 1;
            }
            PyObject *walk = PyDict_GetItemWithError(globals, PyTuple_GET_ITEM(names, 9));
            if (walk == NULL) {
                if (!PyErr_Occurred()) {
                    PyErr_SetString(PyExc_NameError, "name '_walk' is not defined");
                }
                return -1;
            }
            Py_INCREF(walk);
            *result = PyObject_CallFunctionObjArgs(walk, source, context->memo, NULL);
            Py_DECREF(walk);
            return *result == NULL ? -1 : 1;
        }
    }
    return clone_value(source, result, depth, context);
}

/* Copy a first repeated non-atomic run into private reserved slots. */
static int
clone_initial_run(PyObject *source, PyObject *output, Py_ssize_t *written,
                  Py_ssize_t size, int tuple, PyObject *first,
                  unsigned int depth, CopyContext *context)
{
    Py_INCREF(first);
    PyObject *copied = NULL;
    int status = clone_closed_value(first, &copied, depth - 1, context);
    Py_DECREF(first);
    if (status != 1) {
        return status;
    }
    /* The private memo owns source/result even if a callback replaced source
     * slots. No allocation occurs while reusing the completed child. */
    if (tuple) {
        PyTuple_SET_ITEM(output, *written, copied);
    } else {
        PyList_SET_ITEM(output, *written, copied);
    }
    ++*written;
    while (*written < size && *written < Py_SIZE(source)) {
        PyObject *next = tuple
            ? PyTuple_GET_ITEM(source, *written)
            : PyList_GET_ITEM(source, *written);
        if (next != first) {
            break;
        }
        if (tuple) {
            PyTuple_SET_ITEM(output, *written, Py_NewRef(copied));
        } else {
            PyList_SET_ITEM(output, *written, Py_NewRef(copied));
        }
        ++*written;
    }
    return 1;
}

/* 1: owned result; 0: unsupported (no exception); -1: real failure. */
static inline Py_ALWAYS_INLINE int
clone_sequence_impl(PyObject *source, PyObject **result, unsigned int depth,
               CopyContext *context, int direct_graph)
{
    int tuple = PyTuple_CheckExact(source);
    Py_ssize_t size = Py_SIZE(source);
    PyObject *output = tuple ? PyTuple_New(size) : PyList_New(size);
    if (output == NULL) {
        return -1;
    }
    /* Atom-only prefixes cannot invoke callbacks or allocate. Complete a
     * scalar sequence while its storage is private before recursive work
     * requires hiding any incomplete slots from collector callbacks. */
    Py_ssize_t written = 0;
    if (!direct_graph && context->walk == NULL) {
        while (written < size && written < Py_SIZE(source)) {
            PyObject *item = tuple
                ? PyTuple_GET_ITEM(source, written)
                : PyList_GET_ITEM(source, written);
            if (!atom(item)) {
                break;
            }
            if (tuple) {
                PyTuple_SET_ITEM(output, written, Py_NewRef(item));
            } else {
                PyList_SET_ITEM(output, written, Py_NewRef(item));
            }
            ++written;
        }
        if (written == size && written == Py_SIZE(source)) {
            *result = output;
            return 1;
        }
    }
    /* No partially initialized list/tuple may appear in gc.get_objects() or
     * gc.get_referrers() during allocations further down the tree. A completed
     * result is tracked again before it can be published to its parent. */
    if (PyObject_GC_IsTracked(output)) {
        PyObject_GC_UnTrack(output);
    }
    if (direct_graph && !tuple) {
        /* Reserve capacity without exposing NULL slots: foreign hooks see the
         * same initialized prefix that Python's append-based walker exposes.
         * Unused reserved slots own no references and lie outside ob_size. */
        Py_SET_SIZE(output, 0);
        PyObject_GC_Track(output);
        if (memo_store(source, output, context) < 0) {
            Py_DECREF(output);
            return -1;
        }
    }
    if (written < size - 1 && written < Py_SIZE(source) - 1
        && !direct_graph && context->entries != NULL
        && context->memo == NULL && context->walk == NULL) {
        PyObject *first = tuple
            ? PyTuple_GET_ITEM(source, written)
            : PyList_GET_ITEM(source, written);
        PyObject *next = tuple
            ? PyTuple_GET_ITEM(source, written + 1)
            : PyList_GET_ITEM(source, written + 1);
        if (first == next && !atom(first)) {
            int run_status = clone_initial_run(source, output, &written,
                                              size, tuple, first, depth, context);
            if (run_status != 1) {
                Py_DECREF(output);
                return run_status;
            }
        }
    }
    PyObject *held = NULL;
    int status = 1;
    while (written < Py_SIZE(source)) {
        /* Re-read the live list's size and item after every recursive copy.
         * A finalizer may clear, replace or append source items. Tuples are
         * immutable, but use the same owned-item discipline. */
        PyObject *item = Py_NewRef(tuple
            ? PyTuple_GET_ITEM(source, written)
            : PyList_GET_ITEM(source, written));
        if (context->walk != NULL) {
            /* Python's comprehension pins the next item before replacing its
             * previous loop variable. Releasing a hook-bearing old item any
             * earlier could append/remove source elements at a different step. */
            PyObject *previous = held;
            held = item;
            Py_XDECREF(previous);
        }
        PyObject *copied = NULL;
        if (atom(item)) {
            copied = context->walk != NULL ? Py_NewRef(item) : item;
        } else {
            status = direct_graph
                ? clone_loop_value(item, &copied, depth - 1, context)
                : clone_closed_value(item, &copied, depth - 1, context);
            if (context->walk == NULL) {
                Py_DECREF(item);
            }
        }
        if (status != 1) {
            break;
        }
        if (tuple) {
            PyTuple_SET_ITEM(output, written, copied);
        } else if (!direct_graph && written < size) {
            PyList_SET_ITEM(output, written, copied);
        } else {
            int appended = PyList_Append(output, copied);
            Py_DECREF(copied);
            if (appended < 0) {
                status = -1;
                break;
            }
        }
        ++written;
    }
    Py_XDECREF(held);
    if (status != 1) {
        Py_DECREF(output);
        return status;
    }
    if (!direct_graph && !tuple && written < size) {
        /* A source-shrinking finalizer can leave unused NULL slots. Slice only
         * the initialized prefix; never expose those slots to Python or GC. */
        *result = PyList_GetSlice(output, 0, written);
        Py_DECREF(output);
        return *result == NULL ? -1 : 1;
    }
    /* Private sequences stayed untracked; published direct lists stayed
     * tracked from their initialized-prefix publication onward. */
    if ((tuple && Py_SIZE(output) != 0) || (!tuple && !direct_graph)) {
        PyObject_GC_Track(output);
    }
    *result = output;
    return 1;
}

static inline Py_ALWAYS_INLINE int
clone_dict_impl(PyObject *source, PyObject **result, unsigned int depth,
           CopyContext *context, int direct_graph)
{
    PyObject *output = _PyDict_NewPresized(PyDict_Size(source));
    if (output == NULL) {
        return -1;
    }
    if (!direct_graph && PyObject_GC_IsTracked(output)) {
        PyObject_GC_UnTrack(output);
    }
    if (direct_graph && memo_store(source, output, context) < 0) {
        Py_DECREF(output);
        return -1;
    }
    Py_ssize_t expected = PyDict_GET_SIZE(source);
    Py_ssize_t remaining = expected;
    Py_ssize_t position = 0;
    PyObject *held_key = NULL, *held_value = NULL;
    int status = 1;
    for (;;) {
        /* Match the GIL dict-item iterator's size and remaining-item checks.
         * Only the integer cursor survives an allocation; PyDict_Next rereads
         * the live table on every call and performs no key lookup or callback.
         * In particular, do not require remaining == 0 on normal exhaustion:
         * equal-sized key replacements can legitimately end iteration early. */
        if (PyDict_GET_SIZE(source) != expected) {
            PyErr_SetString(PyExc_RuntimeError,
                            "dictionary changed size during iteration");
            status = -1;
            break;
        }
        PyObject *key, *value;
        if (!PyDict_Next(source, &position, &key, &value)) {
            break;
        }
        if (remaining == 0) {
            PyErr_SetString(PyExc_RuntimeError,
                            "dictionary keys changed during iteration");
            status = -1;
            break;
        }
        --remaining;
        /* An items iterator retains its last pair until the next pair is
         * already pinned. Preserve that lifetime and decref order: finalizers
         * reached through a source-mutated old value may change later items. */
        Py_INCREF(key);
        Py_INCREF(value);
        PyObject *previous_key = held_key, *previous_value = held_value;
        held_key = key;
        held_value = value;
        Py_XDECREF(previous_key);
        Py_XDECREF(previous_value);
        if (!PyUnicode_CheckExact(key) && context->walk == NULL) {
            status = 0;
            break;
        }
        PyObject *copied_key = NULL;
        if (PyUnicode_CheckExact(key) || atom(key)) {
            copied_key = Py_NewRef(key);
        } else {
            status = direct_graph
                ? clone_loop_value(key, &copied_key, depth - 1, context)
                : clone_closed_value(key, &copied_key, depth - 1, context);
            if (status != 1) {
                break;
            }
        }
        PyObject *copied = NULL;
        if (atom(value)) {
            copied = Py_NewRef(value);
        } else {
            status = direct_graph
                ? clone_loop_value(value, &copied, depth - 1, context)
                : clone_closed_value(value, &copied, depth - 1, context);
        }
        if (status != 1) {
            Py_DECREF(copied_key);
            break;
        }
        int inserted = PyDict_SetItem(output, copied_key, copied);
        /* Only the closed builtin walk hides its result from GC observers.
         * The callback walk publishes a normal, fully initialized dictionary
         * in the caller's memo and must keep its ordinary GC tracking. */
        if (!direct_graph && PyObject_GC_IsTracked(output)) {
            PyObject_GC_UnTrack(output);
        }
        Py_DECREF(copied_key);
        Py_DECREF(copied);
        if (inserted < 0) {
            status = -1;
            break;
        }
    }
    Py_XDECREF(held_key);
    Py_XDECREF(held_value);
    if (status != 1) {
        Py_DECREF(output);
        return status;
    }
    if (!direct_graph || !PyObject_GC_IsTracked(output)) {
        PyObject_GC_Track(output);
    }
    *result = output;
    return 1;
}

/* Separate entrypoints let the compiler erase per-item memo/GC dispatch from
 * the closed builtin loop while preserving the same checked traversal. */
static int
clone_sequence_closed(PyObject *source, PyObject **result, unsigned int depth,
                      CopyContext *context)
{
    return clone_sequence_impl(source, result, depth, context, 0);
}

static int
clone_sequence_direct(PyObject *source, PyObject **result, unsigned int depth,
                      CopyContext *context)
{
    return clone_sequence_impl(source, result, depth, context, 1);
}

static int
clone_dict_closed(PyObject *source, PyObject **result, unsigned int depth,
                  CopyContext *context)
{
    return clone_dict_impl(source, result, depth, context, 0);
}

static int
clone_dict_direct(PyObject *source, PyObject **result, unsigned int depth,
                  CopyContext *context)
{
    return clone_dict_impl(source, result, depth, context, 1);
}

static int
clone_set_direct(PyObject *source, PyObject **result, unsigned int depth,
                 CopyContext *context)
{
    PyObject *output = PySet_New(NULL);
    if (output == NULL) {
        return -1;
    }
    if (memo_store(source, output, context) < 0) {
        Py_DECREF(output);
        return -1;
    }
    /* Publish the empty twin before acquiring the live iterator. Copy/hash
     * hooks can inspect or mutate it, and source mutations retain setiter's
     * normal size-change errors rather than reading a stale table cursor. */
    PyObject *iterator = PyObject_GetIter(source);
    if (iterator == NULL) {
        Py_DECREF(output);
        return -1;
    }
    PyObject *held = NULL;
    unsigned long iterations = 0;
    int status = 1;
    for (;;) {
        PyObject *item = PyIter_Next(iterator);
        if (item == NULL) {
            if (PyErr_Occurred()) {
                status = -1;
            }
            break;
        }
        PyObject *previous = held;
        held = item;
        Py_XDECREF(previous);
        PyObject *copied = NULL;
        if (atom(item)) {
            copied = Py_NewRef(item);
        } else {
            status = clone_loop_value(item, &copied, depth - 1, context);
        }
        if (status != 1) {
            break;
        }
        int inserted = PySet_Add(output, copied);
        Py_DECREF(copied);
        if (inserted < 0) {
            status = -1;
            break;
        }
        if ((++iterations & 1023UL) == 0 && PyErr_CheckSignals() < 0) {
            status = -1;
            break;
        }
    }
    Py_DECREF(iterator);
    /* Keep the last loop variable alive through the final memo write, as in
     * Python's set branch; a descendant may have overwritten that entry. */
    if (status == 1 && memo_store(source, output, context) < 0) {
        status = -1;
    }
    Py_XDECREF(held);
    if (status != 1) {
        Py_DECREF(output);
        return status;
    }
    *result = output;
    return 1;
}

static int
clone_closed_value(PyObject *source, PyObject **result, unsigned int depth,
            CopyContext *context)
{
    if (atom(source)) {
        *result = Py_NewRef(source);
        return 1;
    }
    if (context->entries != NULL) {
        int found = memo_lookup_closed(source, result, context);
        if (found != 0) {
            return found;
        }
    }
    if (depth == 0) {
        return context->walk != NULL
            ? python_value(source, result, context) : 0;
    }
    int record = Py_TYPE(source) == context->record_type;
    if (!PyList_CheckExact(source) && !PyTuple_CheckExact(source)
        && !PyDict_CheckExact(source) && !record) {
        return context->walk != NULL
            ? python_value(source, result, context) : 0;
    }
    if (--context->remaining < 0) {
        return context->walk != NULL
            ? python_value(source, result, context) : 0;
    }
    int filtered = context->walk == NULL && context->entries != NULL;
    size_t filter_word = 0;
    uint64_t filter_before = 0, filter_bit = 0;
    if (context->walk != NULL || context->entries != NULL) {
        if (record && !record_current(context)) {
            return python_value(source, result, context);
        }
        if (filtered) {
            size_t hash = memo_hash(source);
            filter_word = (hash >> 6) & 3;
            filter_bit = UINT64_C(1) << (hash & 63);
            filter_before = context->ancestor_filter[filter_word];
        }
        /* A missing bit proves this address is not active. Collisions still
         * run the original exact scan; no hash match alone proves a cycle. */
        if (!filtered || (filter_before & filter_bit)) {
            for (unsigned int i = 0; i < context->count; i++) {
                if (context->ancestors[i] == source) {
                    if (context->refuse_cycles && PyFunction_Check(context->cycle_walk)) {
                        PyObject *walk = context->cycle_walk;
                        PyObject *globals = PyFunction_GetGlobals(walk);
                        PyObject *names = context->module_state->temporal_names;
                        if (ordinary_namespace(globals)
                            && PyDict_GetItem(globals, PyTuple_GET_ITEM(names, 0)) == walk
                            && PyDict_GetItem(globals, PyTuple_GET_ITEM(names, 1))
                                == PyFunction_GetCode(walk)
                            && PyDict_GetItem(globals, PyTuple_GET_ITEM(names, 9)) == walk) {
                            return 0;
                        }
                    }
                    PyErr_SetString(PyExc_RecursionError, "cyclic clone graph");
                    return -1;
                }
            }
        }
    }
    if (Py_EnterRecursiveCall(" while cloning a builtin tree")) {
        if (PyErr_ExceptionMatches(PyExc_RecursionError)) {
            PyErr_Clear();
            return context->walk != NULL
                ? python_value(source, result, context) : 0;
        }
        return -1;
    }
    if (context->walk != NULL || context->entries != NULL) {
        context->ancestors[context->count++] = source;
        if (filtered) {
            context->ancestor_filter[filter_word] = filter_before | filter_bit;
        }
    }
    int status;
    if (record) {
        status = clone_record(source, result, depth, context);
    } else if (PyDict_CheckExact(source) && shallow_atoms(source)) {
        PyObject *copied = PyDict_Copy(source);
        if (copied == NULL) {
            status = -1;
        } else {
            if (shallow_atoms(copied)) {
                *result = copied;
                status = 1;
            } else {
                Py_DECREF(copied);
                status = context->walk != NULL
                    ? python_value(source, result, context) : 0;
            }
        }
    } else {
        status = PyDict_CheckExact(source)
            ? clone_dict_closed(source, result, depth, context)
            : clone_sequence_closed(source, result, depth, context);
    }
    if (context->walk != NULL || context->entries != NULL) {
        --context->count;
        if (filtered) {
            /* Restore the whole prior word so nested hash collisions cannot
             * clear an ancestor's bit while unwinding this frame. */
            context->ancestor_filter[filter_word] = filter_before;
        }
    }
    Py_LeaveRecursiveCall();
    if (status == 1 && context->entries != NULL
        && memo_store_closed(source, *result, context) < 0) {
        Py_CLEAR(*result);
        return -1;
    }
    return status;
}

/* The closed graph retains a compact hot path. Only a callback continuation
 * reserves virtual Python frames and creates Python identity keys. */
static int
clone_value(PyObject *source, PyObject **result, unsigned int depth,
            CopyContext *context)
{
    if (!context->direct_graph) {
        return clone_closed_value(source, result, depth, context);
    }
    /* Python container loops return every graph atom before calling _walk.
     * Keep rare immutable types off the primitive closed-walker hot path. */
    PyTypeObject *selected_type = Py_TYPE(source);
    int builtin_container = selected_type == &PyList_Type
        || selected_type == &PyTuple_Type || selected_type == &PyDict_Type
        || selected_type == &PySet_Type;
    int candidate = recipe_candidate(selected_type, context);
    /* Exact containers and ordinary heap records are disjoint from every
     * static graph atom. Retain the captured type across later memo hooks. */
    if (!builtin_container && !candidate && graph_atom_kind(source, selected_type)) {
        *result = Py_NewRef(source);
        return 1;
    }
#if PY_VERSION_HEX >= 0x030C0000 && PY_VERSION_HEX < 0x030F0000
    PyThreadState *thread = PyThreadState_Get();
    if (thread->py_recursion_remaining <= 0) {
        PyErr_SetString(PyExc_RecursionError,
                        "maximum recursion depth while cloning a graph");
        return -1;
    }
    --thread->py_recursion_remaining;
#endif
    int status;
    if (depth == 0 || context->remaining <= 0) {
        /* Let Python capture the type, look up the memo and pin exactly once. */
        status = python_value(source, result, context);
    } else {
        /* Python captures type(o) before a memo lookup. Even an exact memo can
         * contain foreign keys whose equality hook changes source.__class__. */
        Py_INCREF(selected_type);
        int delegate = !builtin_container
            && selected_type != context->record_type && !candidate;
        if (candidate && context->recipe_count == MAX_RECORD_TYPES) {
            delegate = 1;
            for (unsigned int i = 0; i < context->recipe_count; i++) {
                if (context->recipes[i].type == selected_type) {
                    delegate = 0;
                    break;
                }
            }
        }
        int temporal = delegate && context->temporal_graph
            ? temporal_value(source, result, context) : 0;
        if (temporal != 0) {
            status = temporal;
        } else if (delegate) {
            /* Python owns unsupported-node dispatch from its first lookup.
             * Repeating a memo lookup could invoke foreign key equality
             * twice or select a class changed by that first comparison. */
            status = python_value(source, result, context);
        } else {
            PyObject *previous = context->identity;
            PyObject *identity = PyLong_FromVoidPtr(source);
            if (identity == NULL) {
                status = -1;
            } else {
                context->identity = identity;
                status = clone_value_impl(source, result, depth, context, selected_type);
                context->identity = previous;
            }
            Py_XDECREF(identity);
        }
        Py_DECREF(selected_type);
    }
#if PY_VERSION_HEX >= 0x030C0000 && PY_VERSION_HEX < 0x030F0000
    ++thread->py_recursion_remaining;
#endif
    return status;
}

static int
clone_value_impl(PyObject *source, PyObject **result, unsigned int depth,
                 CopyContext *context, PyTypeObject *source_type)
{
    if (context->direct_graph || context->entries != NULL) {
        int found = memo_lookup(source, result, context);
        if (found != 0) {
            return found;
        }
    }
    /* Refuse before admission and source pinning. A Python continuation owns
     * those operations itself, including observable custom keep-alive append. */
    if (depth == 0 || context->remaining <= 0) {
        return context->walk != NULL
            ? python_value(source, result, context) : 0;
    }
    int pinned = 0;
    RecordRecipe *recipe = NULL;
    if (recipe_candidate(source_type, context)) {
        /* Pin the old class as Python's local `t` does: source.__class__ can
         * change during a keep-alive list allocation before admission runs. */
        Py_INCREF(source_type);
        if (memo_pin(source, context) < 0) {
            Py_DECREF(source_type);
            return -1;
        }
        pinned = 1;
        recipe = record_recipe(source_type, context);
        Py_DECREF(source_type);
        if (recipe == NULL) {
            return -1;
        }
        if (!recipe->native || Py_TYPE(source) != recipe->type) {
            if (recipe->handler == context->model_handler
                && Py_TYPE(source) == recipe->type
                && model_current(context)) {
                return clone_model(source, result, depth, context);
            }
            return python_recipe(source, result, context, recipe);
        }
        context->record_type = recipe->type;
        context->record_version = recipe->version;
        if (!record_current(context)) {
            return python_recipe(source, result, context, recipe);
        }
        recipe->version = context->record_version;
    }
    int record = Py_TYPE(source) == context->record_type;
    if (!PyList_CheckExact(source) && !PyTuple_CheckExact(source)
        && !PyDict_CheckExact(source) && !PySet_CheckExact(source) && !record) {
        return context->walk != NULL
            ? python_value(source, result, context) : 0;
    }
    if (--context->remaining < 0) {
        return context->walk != NULL
            ? python_value(source, result, context) : 0;
    }
    if (context->direct_graph && !pinned && memo_pin(source, context) < 0) {
        return -1;
    }
    if (context->walk != NULL || context->direct_graph
        || context->entries != NULL) {
        if (record && !record_current(context)) {
            return recipe != NULL
                ? python_recipe(source, result, context, recipe)
                : python_value(source, result, context);
        }
        for (unsigned int i = 0; i < context->count; i++) {
            if (context->ancestors[i] == source) {
                if (context->direct_graph) {
                    return python_value(source, result, context);
                }
                PyErr_SetString(PyExc_RecursionError, "cyclic clone graph");
                return -1;
            }
        }
    }
    if (Py_EnterRecursiveCall(" while cloning a builtin tree")) {
        if (PyErr_ExceptionMatches(PyExc_RecursionError)) {
            PyErr_Clear();
            return context->walk != NULL
                ? python_value(source, result, context) : 0;
        }
        return -1;
    }
    if (context->walk != NULL || context->direct_graph
        || context->entries != NULL) {
        context->ancestors[context->count++] = source;
    }
    int status;
    if (record) {
        status = clone_record(source, result, depth, context);
    } else if (PySet_CheckExact(source)) {
        status = clone_set_direct(source, result, depth, context);
    } else if (!context->direct_graph && PyDict_CheckExact(source)
               && shallow_atoms(source)) {
        PyObject *copied = PyDict_Copy(source);
        if (copied == NULL) {
            status = -1;
        } else {
            if (shallow_atoms(copied)) {
                *result = copied;
                status = 1;
            } else {
                Py_DECREF(copied);
                status = context->walk != NULL
                    ? python_value(source, result, context) : 0;
            }
        }
    } else {
        status = PyDict_CheckExact(source)
            ? clone_dict_direct(source, result, depth, context)
            : clone_sequence_direct(source, result, depth, context);
    }
    if (context->walk != NULL || context->direct_graph
        || context->entries != NULL) {
        --context->count;
    }
    Py_LeaveRecursiveCall();
    if (status == 1 && context->direct_graph && PyTuple_CheckExact(source)) {
        /* A child hook may have finished this tuple through a mutable backref.
         * Reuse that completed tuple rather than overwriting its memo entry. */
        PyObject *existing = NULL;
        int found = memo_lookup(source, &existing, context);
        if (found < 0) {
            Py_CLEAR(*result);
            return -1;
        }
        if (found) {
            Py_SETREF(*result, existing);
        } else if (memo_store(source, *result, context) < 0) {
            Py_CLEAR(*result);
            return -1;
        }
    }
    if (status == 1 && context->direct_graph
        && (record || PyList_CheckExact(source) || PyDict_CheckExact(source))
        && memo_store(source, *result, context) < 0) {
        Py_CLEAR(*result);
        return -1;
    }
    if (status == 1 && context->entries != NULL
        && memo_store(source, *result, context) < 0) {
        Py_CLEAR(*result);
        return -1;
    }
    return status;
}

static int
clone_model_state(PyObject *source, PyObject **result, unsigned int depth,
                  CopyContext *context)
{
    PyObject *globals = PyFunction_GetGlobals(context->model_handler);
    PyObject *names = context->module_state->model_names;
    PyObject *walk = PyDict_GetItem(globals, PyTuple_GET_ITEM(names, MODEL_DATA_WALK));
    if (walk == NULL) {
        PyErr_SetString(PyExc_NameError, "model state walker is unavailable");
        return -1;
    }
    if (walk != context->model_data_walk || !PyFunction_Check(walk)
        || PyFunction_GetCode(walk) != context->model_data_code) {
        Py_INCREF(walk);
        *result = PyObject_CallFunctionObjArgs(walk, source, context->memo, NULL);
        Py_DECREF(walk);
        return *result == NULL ? -1 : 1;
    }
#if PY_VERSION_HEX >= 0x030C0000 && PY_VERSION_HEX < 0x030F0000
    PyThreadState *thread = PyThreadState_Get();
    if (thread->py_recursion_remaining <= 0) {
        PyErr_SetString(PyExc_RecursionError, "maximum recursion depth while cloning model state");
        return -1;
    }
    --thread->py_recursion_remaining;
#endif
    /* _walk_data tests membership before its native dispatch, including atoms.
     * Keep its two lookups on a hit: foreign memo keys may observe each one. */
    PyObject *identity = PyLong_FromVoidPtr(source);
    int present = identity == NULL ? -1 : PyDict_Contains(context->memo, identity);
    Py_XDECREF(identity);
    int status = -1;
    if (present > 0) {
        identity = PyLong_FromVoidPtr(source);
        if (identity != NULL) {
            PyObject *copied = PyDict_GetItemWithError(context->memo, identity);
            if (copied != NULL) {
                *result = Py_NewRef(copied);
                status = 1;
            } else if (!PyErr_Occurred()) {
                PyErr_SetObject(PyExc_KeyError, identity);
            }
            Py_DECREF(identity);
        }
    } else if (present == 0) {
        /* Equality during membership can replace dispatch globals. The Python
         * wrapper has already completed that lookup: continue at its shared
         * body rather than replaying the wrapper and its observable probes. */
        PyObject *body = PyDict_GetItem(globals, PyTuple_GET_ITEM(names, MODEL_DATA_BODY));
        PyObject *native = PyDict_GetItem(globals, PyTuple_GET_ITEM(names, MODEL_NATIVE));
        PyObject *undefined = PyDict_GetItem(globals, PyTuple_GET_ITEM(names, MODEL_UNDEFINED));
        int current = body != NULL && body == context->model_data_body
            && PyFunction_Check(body) && PyFunction_GetCode(body) == context->model_body_code
            && PyDict_GetItem(globals, PyTuple_GET_ITEM(names, MODEL_WALK)) == context->walk
            && PyFunction_GetCode(context->walk) == context->model_walk_code
            && PyDict_GetItem(globals, PyTuple_GET_ITEM(names, MODEL_ADMIT)) == context->admit
            && PyFunction_GetCode(context->admit) == context->model_admit_code
            && PyDict_GetItem(globals, PyTuple_GET_ITEM(names, MODEL_NATIVE_WALK))
                == context->model_native_walk
            && native != NULL && native != Py_None
            && PyDict_GetItem(globals, PyTuple_GET_ITEM(names, MODEL_HANDLER))
                == context->model_handler
            && PyFunction_GetCode(context->model_handler) == context->model_code
            && PyDict_GetItem(globals, PyTuple_GET_ITEM(names, MODEL_CANONICAL))
                == context->model_handler
            && PyDict_GetItem(globals, PyTuple_GET_ITEM(names, MODEL_CODE)) == context->model_code
            && PyDict_GetItem(globals, PyTuple_GET_ITEM(names, MODEL_FEATURE))
                == context->model_feature
            && undefined != NULL && undefined != Py_None;
        if (current) {
#if PY_VERSION_HEX >= 0x030C0000 && PY_VERSION_HEX < 0x030F0000
            if (thread->py_recursion_remaining <= 0) {
                PyErr_SetString(PyExc_RecursionError,
                                "maximum recursion depth while dispatching model state");
            } else {
                --thread->py_recursion_remaining;
#endif
                status = clone_child(source, result, depth, context);
#if PY_VERSION_HEX >= 0x030C0000 && PY_VERSION_HEX < 0x030F0000
                ++thread->py_recursion_remaining;
            }
#endif
        } else if (body == NULL) {
            PyErr_Format(PyExc_NameError, "name '%U' is not defined",
                         PyTuple_GET_ITEM(names, MODEL_DATA_BODY));
        } else {
            Py_INCREF(body);
            *result = PyObject_CallFunctionObjArgs(body, source, context->memo, NULL);
            Py_DECREF(body);
            status = *result == NULL ? -1 : 1;
        }
    }
#if PY_VERSION_HEX >= 0x030C0000 && PY_VERSION_HEX < 0x030F0000
    ++thread->py_recursion_remaining;
#endif
    return status;
}

static int
clone_model_impl(PyObject *source, PyObject **result, unsigned int depth,
                 CopyContext *context)
{
    context->temporal_graph = 0;
    PyObject *names = context->module_state->model_names;
    PyTypeObject *type = Py_TYPE(source);
    PyObject *allocate = PyObject_GetAttr((PyObject *)type,
                                         PyTuple_GET_ITEM(names, MODEL_NEW));
    if (allocate == NULL) {
        return -1;
    }
    PyObject *twin = PyObject_CallOneArg(allocate, (PyObject *)type);
    Py_DECREF(allocate);
    if (twin == NULL) {
        return -1;
    }
    if (memo_store(source, twin, context) < 0) {
        Py_DECREF(twin);
        return -1;
    }
    for (int phase = 0; phase < 4; phase++) {
        PyObject *name = phase == 0 ? context->dict_name
            : PyTuple_GET_ITEM(names, phase == 1 ? MODEL_EXTRA
                : phase == 2 ? MODEL_FIELDS : MODEL_PRIVATE);
        PyObject *state = PyObject_GetAttr(source, name);
        if (state == NULL && phase == 3 && PyErr_ExceptionMatches(PyExc_AttributeError)) {
            PyErr_Clear();
            state = Py_NewRef(Py_None);
        }
        if (state == NULL) {
            Py_DECREF(twin);
            return -1;
        }
        PyObject *copied = NULL;
        int status;
        if ((phase == 1 || phase == 3) && state == Py_None) {
            copied = Py_NewRef(Py_None);
            status = 1;
        } else if (phase == 3) {
            PyObject *globals = PyFunction_GetGlobals(context->model_handler);
            PyObject *project = PyDict_GetItem(globals, PyTuple_GET_ITEM(names, MODEL_PROJECT));
            if (project == NULL) {
                PyErr_SetString(PyExc_NameError, "model private-state projection is unavailable");
            } else {
                copied = PyObject_CallFunctionObjArgs(project, state, context->memo, NULL);
            }
            status = copied == NULL ? -1 : 1;
        } else {
            status = clone_model_state(state, &copied, depth - 1, context);
        }
        Py_DECREF(state);
        if (status != 1) {
            Py_XDECREF(copied);
            Py_DECREF(twin);
            return status;
        }
        int applied = PyObject_GenericSetAttr(twin, name, copied);
        Py_DECREF(copied);
        if (applied < 0) {
            Py_DECREF(twin);
            return -1;
        }
    }
    if (memo_store(source, twin, context) < 0) {
        Py_DECREF(twin);
        return -1;
    }
    *result = twin;
    return 1;
}

static int
clone_model(PyObject *source, PyObject **result, unsigned int depth,
            CopyContext *context)
{
#if PY_VERSION_HEX >= 0x030C0000 && PY_VERSION_HEX < 0x030F0000
    PyThreadState *thread = PyThreadState_Get();
    if (thread->py_recursion_remaining <= 0) {
        PyErr_SetString(PyExc_RecursionError, "maximum recursion depth while cloning a model");
        return -1;
    }
    --thread->py_recursion_remaining;
#endif
    int status = clone_model_impl(source, result, depth, context);
#if PY_VERSION_HEX >= 0x030C0000 && PY_VERSION_HEX < 0x030F0000
    ++thread->py_recursion_remaining;
#endif
    return status;
}

static int
clone_record(PyObject *source, PyObject **result, unsigned int depth,
             CopyContext *context)
{
#if PY_VERSION_HEX >= 0x030C0000 && PY_VERSION_HEX < 0x030F0000
    if (context->direct_graph) {
        PyThreadState *state = PyThreadState_Get();
        if (state->py_recursion_remaining <= 0) {
            PyErr_SetString(PyExc_RecursionError,
                            "maximum recursion depth while cloning a record");
            return -1;
        }
        --state->py_recursion_remaining;
        int status = clone_record_impl(source, result, depth, context);
        ++state->py_recursion_remaining;
        return status;
    }
#endif
    return clone_record_impl(source, result, depth, context);
}

static int
clone_record_impl(PyObject *source, PyObject **result, unsigned int depth,
                  CopyContext *context)
{
    if (Py_TYPE(source) != context->record_type) {
        return python_value(source, result, context);
    }
    /* record_current() already selected object.__new__'s allocation slot.
     * After allocating, finish THIS twin even if a GC callback changes the
     * class. Refusing and restarting would create extra observable instances. */
    PyObject *twin = PyType_GenericAlloc(context->record_type, 0);
    if (twin == NULL) {
        return -1;
    }
    if (PyObject_GC_IsTracked(twin)) {
        PyObject_GC_UnTrack(twin);
    }
    if (context->direct_graph) {
        PyObject_GC_Track(twin);
        if (memo_store(source, twin, context) < 0) {
            Py_DECREF(twin);
            return -1;
        }
    }
    PyObject *state = PyObject_GetAttr(source, context->dict_name);
    if (state == NULL) {
        Py_DECREF(twin);
        return -1;
    }
    int truth = PyObject_IsTrue(state);
    if (truth <= 0) {
        if (truth < 0) {
            Py_DECREF(state);
            Py_DECREF(twin);
            return -1;
        }
    } else {
        /* Python resolves twin.__dict__.update before traversing state. Keep
         * that order and the selected destination across descendant hooks. */
        PyObject *destination = PyObject_GetAttr(twin, context->dict_name);
        if (destination == NULL) {
            Py_DECREF(state);
            Py_DECREF(twin);
            return -1;
        }
        PyObject *update = NULL;
        int builtin_update = PyDict_CheckExact(state)
            && PyDict_CheckExact(destination);
        if (!builtin_update) {
            update = PyObject_GetAttr(destination, context->update_name);
            if (update == NULL) {
                Py_DECREF(destination);
                Py_DECREF(state);
                Py_DECREF(twin);
                return -1;
            }
        }
        PyObject *copied = NULL;
        int status;
#if PY_VERSION_HEX >= 0x030C0000 && PY_VERSION_HEX < 0x030F0000
        PyThreadState *thread = PyThreadState_Get();
        if (context->direct_graph && thread->py_recursion_remaining < 2) {
            PyErr_SetString(PyExc_RecursionError,
                            "maximum recursion depth while cloning record state");
            status = -1;
        } else {
            if (context->direct_graph) {
                thread->py_recursion_remaining -= 2;
            }
            status = clone_child(state, &copied, depth - 1, context);
            if (context->direct_graph) {
                thread->py_recursion_remaining += 2;
            }
        }
#else
        status = clone_child(state, &copied, depth - 1, context);
#endif
        if (status != 1) {
            Py_XDECREF(update);
            Py_DECREF(destination);
            Py_DECREF(state);
            Py_DECREF(twin);
            return status;
        }
        int applied;
        if (builtin_update) {
            applied = PyDict_Update(destination, copied);
        } else {
            PyObject *returned = PyObject_CallOneArg(update, copied);
            applied = returned == NULL ? -1 : 0;
            Py_XDECREF(returned);
            Py_DECREF(update);
        }
        Py_DECREF(copied);
        Py_DECREF(destination);
        if (applied < 0) {
            Py_DECREF(state);
            Py_DECREF(twin);
            return -1;
        }
    }
    if (!PyObject_GC_IsTracked(twin)) {
        PyObject_GC_Track(twin);
    }
    Py_DECREF(state);
    *result = twin;
    return 1;
}

static int
clone_budget(unsigned int *budget, int *limit)
{
    /* The C stack guard alone cannot model Python's frame budget on 3.12+.
     * These versioned CPython wheels use the same Python-frame budget field
     * as the JSON guard on our tested interpreter range. Future interpreters
     * use the conservative public frame walk until their ABI is validated. */
    int recursion_limit = Py_GetRecursionLimit();
    *limit = recursion_limit;
#if PY_VERSION_HEX >= 0x030C0000 && PY_VERSION_HEX < 0x030F0000
    int remaining = PyThreadState_Get()->py_recursion_remaining - 8;
#else
    int remaining = recursion_limit - 8;
    PyFrameObject *frame = PyThreadState_GetFrame(PyThreadState_Get());
    while (frame != NULL) {
        --remaining;
        PyFrameObject *previous = PyFrame_GetBack(frame);
        Py_DECREF(frame);
        frame = previous;
    }
    if (PyErr_Occurred()) {
        return -1;
    }
#endif
    if (remaining <= 0) {
        return 0;
    }
    *budget = remaining < MAX_DEPTH
        ? (unsigned int)remaining : MAX_DEPTH;
    return 1;
}

static PyObject *
clone_builtin_impl(PyObject *source, int cycle_error)
{
    unsigned int budget;
    int recursion_limit;
    int allowance = clone_budget(&budget, &recursion_limit);
    if (allowance < 0) {
        return NULL;
    }
    if (allowance == 0) {
        Py_RETURN_NOTIMPLEMENTED;
    }
    if (shallow_atoms(source)) {
        /* Bulk copies remove per-item iterator/append overhead on flat data.
         * Revalidate the owned snapshot: allocation may have run a finalizer
         * which inserted a foreign value into the source after the proof. */
        PyObject *copied;
        if (PyTuple_CheckExact(source)) {
            Py_ssize_t size = PyTuple_GET_SIZE(source);
            copied = PyTuple_New(size);
            if (copied != NULL) {
                for (Py_ssize_t i = 0; i < size; i++) {
                    PyTuple_SET_ITEM(copied, i,
                                    Py_NewRef(PyTuple_GET_ITEM(source, i)));
                }
            }
        } else {
            copied = PyDict_CheckExact(source)
                ? PyDict_Copy(source) : PyList_GetSlice(source, 0, PY_SSIZE_T_MAX);
        }
        if (copied == NULL) {
            return NULL;
        }
        if (shallow_atoms(copied) && Py_GetRecursionLimit() == recursion_limit) {
            return copied;
        }
        Py_DECREF(copied);
        Py_RETURN_NOTIMPLEMENTED;
    }
    Proof proof = {MAX_CONTAINERS, 0, {NULL}};
    int accepted = eligible(source, budget, &proof);
    if (accepted < 0) {
        return NULL;
    }
    if (accepted != 1) {
        if (accepted == 2 && cycle_error) {
            PyErr_SetString(PyExc_RecursionError, "cyclic builtin clone tree");
            return NULL;
        }
        Py_RETURN_NOTIMPLEMENTED;
    }
    CopyContext context = {0};
    context.remaining = MAX_CONTAINERS;
    PyObject *result = NULL;
    int status = clone_closed_value(source, &result, budget, &context);
    if (status < 0) {
        return NULL;
    }
    if (status == 0) {
        Py_RETURN_NOTIMPLEMENTED;
    }
    if (Py_GetRecursionLimit() != recursion_limit) {
        Py_DECREF(result);
        Py_RETURN_NOTIMPLEMENTED;
    }
    return result;
}

static PyObject *
clone_records(PyObject *module, PyObject *const *args, Py_ssize_t count)
{
    (void)module;
    if (count != 3) {
        PyErr_SetString(PyExc_TypeError,
                        "clone_records requires source, class and walker");
        return NULL;
    }
    PyObject *source = args[0];
    if (!PyType_Check(args[1]) || !PyCallable_Check(args[2])) {
        PyErr_SetString(PyExc_TypeError,
                        "clone_records requires a class and callable walker");
        return NULL;
    }
    PyTypeObject *type = (PyTypeObject *)args[1];
    if ((Py_TYPE(source) != type && !PyList_CheckExact(source)
         && !PyTuple_CheckExact(source) && !PyDict_CheckExact(source))
        || !record_admissible(type)) {
        Py_RETURN_NOTIMPLEMENTED;
    }
    unsigned int budget;
    int recursion_limit;
    int allowance = clone_budget(&budget, &recursion_limit);
    if (allowance < 0) {
        return NULL;
    }
    if (allowance == 0) {
        Py_RETURN_NOTIMPLEMENTED;
    }
    /* Allocate names before admitting the first custom-class allocation.
     * Initial refusal is safe; after entering the walk every unsupported node
     * delegates at its current position instead of abandoning partial twins. */
    PyObject *dict_name = PyUnicode_InternFromString("__dict__");
    if (dict_name == NULL) {
        return NULL;
    }
    PyObject *update_name = PyUnicode_InternFromString("update");
    if (update_name == NULL) {
        Py_DECREF(dict_name);
        return NULL;
    }
    if (!record_admissible(type)) {
        Py_DECREF(dict_name);
        Py_DECREF(update_name);
        Py_RETURN_NOTIMPLEMENTED;
    }
    /* Refuse stack pressure before copying begins. A later recursive limit
     * uses the Python walker at that node with all parent items still owned. */
    if (Py_EnterRecursiveCall(" while entering a record clone tree")) {
        Py_DECREF(dict_name);
        Py_DECREF(update_name);
        if (PyErr_ExceptionMatches(PyExc_RecursionError)) {
            PyErr_Clear();
            Py_RETURN_NOTIMPLEMENTED;
        }
        return NULL;
    }
    CopyContext context = {0};
    context.remaining = MAX_CONTAINERS;
    context.record_type = type;
    context.record_version = record_version(type);
    context.walk = args[2];
    context.dict_name = dict_name;
    context.update_name = update_name;
    PyObject *result = NULL;
    int status = clone_closed_value(source, &result, budget, &context);
    Py_LeaveRecursiveCall();
    Py_DECREF(dict_name);
    Py_DECREF(update_name);
    if (status != 1) {
        return NULL;
    }
    return result;
}

static PyObject *
clone_builtin(PyObject *module, PyObject *source)
{
    (void)module;
    return clone_builtin_impl(source, 0);
}

static PyObject *
clone_direct_graph(PyObject *module, PyObject *source, PyTypeObject *type, PyObject *walk,
                   PyObject *admit, PyObject *memo, int temporal, PyObject *model)
{
#if PY_VERSION_HEX < 0x030C0000 || PY_VERSION_HEX >= 0x030F0000
    /* The versioned frame-budget ABI is validated on supported wheel targets;
     * other interpreters retain the Python walker instead of bypassing limits. */
    Py_RETURN_NOTIMPLEMENTED;
#endif
    if (type != NULL
        && ((Py_TYPE(source) != type && !PyList_CheckExact(source)
             && !PyTuple_CheckExact(source) && !PyDict_CheckExact(source))
            || !record_admissible(type))) {
        Py_RETURN_NOTIMPLEMENTED;
    }
#if PY_VERSION_HEX >= 0x030C0000 && PY_VERSION_HEX < 0x030E0000
    /* Leave room for the bounded native path AND a Python copy hook. Deferring
     * before any twin/hook is created avoids replaying observable protocols
     * when an already-nested extension caller has little C budget left. */
    if (PyThreadState_Get()->c_recursion_remaining < MAX_DEPTH * 4 + 64) {
        Py_RETURN_NOTIMPLEMENTED;
    }
#endif
    unsigned int budget;
    int recursion_limit;
    int allowance = clone_budget(&budget, &recursion_limit);
    if (allowance < 0) {
        return NULL;
    }
    if (allowance == 0) {
        Py_RETURN_NOTIMPLEMENTED;
    }
    ModuleState *state = PyModule_GetState(module);
    PyObject *entry_walk_code = temporal && PyFunction_Check(walk)
        ? Py_NewRef(PyFunction_GetCode(walk)) : NULL;
    PyObject *dict_name = Py_NewRef(state->dict_name);
    PyObject *update_name = Py_NewRef(state->update_name);
    PyObject *memo_identity = PyLong_FromVoidPtr(memo);
    if (memo_identity == NULL) {
        Py_DECREF(dict_name);
        Py_DECREF(update_name);
        Py_XDECREF(entry_walk_code);
        return NULL;
    }
    if (type != NULL && !record_admissible(type)) {
        Py_DECREF(dict_name);
        Py_DECREF(update_name);
        Py_XDECREF(entry_walk_code);
        Py_DECREF(memo_identity);
        Py_RETURN_NOTIMPLEMENTED;
    }
    if (Py_EnterRecursiveCall(" while entering a record graph")) {
        Py_DECREF(dict_name);
        Py_DECREF(update_name);
        Py_XDECREF(entry_walk_code);
        Py_DECREF(memo_identity);
        if (PyErr_ExceptionMatches(PyExc_RecursionError)) {
            PyErr_Clear();
            Py_RETURN_NOTIMPLEMENTED;
        }
        return NULL;
    }
    /* Direct mode uses only the initialized ancestor/recipe prefixes. Its
     * private inline memo arrays belong to the separate closed mode. */
    CopyContext context;
    context.identity = NULL;
    context.model_handler = NULL;
    context.model_code = NULL;
    context.model_data_walk = NULL;
    context.model_data_code = NULL;
    context.model_data_body = NULL;
    context.model_body_code = NULL;
    context.model_native_walk = NULL;
    context.model_feature = NULL;
    context.model_walk_code = NULL;
    context.model_admit_code = NULL;
    if (model != NULL) {
        context.model_handler = model;
        context.model_code = Py_NewRef(PyFunction_GetCode(model));
    }
    context.recipe_count = 0;
    context.entries = NULL;
    context.capacity = 0;
    context.used = 0;
    context.count = 0;
    for (size_t i = 0; i < 4; i++) {
        context.ancestor_filter[i] = 0;
    }
    context.remaining = MAX_CONTAINERS;
    context.module_state = state;
    context.record_type = type;
    context.record_version = type != NULL ? record_version(type) : 0;
    context.walk = walk;
    context.admit = admit;
    context.dict_name = dict_name;
    context.update_name = update_name;
    context.memo = memo;
    context.memo_identity = memo_identity;
    context.direct_graph = 1;
    context.canonical_graph = temporal;
    context.loop_walk_code = entry_walk_code;
    context.temporal_loop_dynamic = 0;
    context.temporal_graph = temporal && PyDict_Size(memo) == 0;
    context.refuse_cycles = 0;
    context.cycle_walk = NULL;
    PyObject *result = NULL;
    int status = clone_value(source, &result, budget, &context);
    Py_LeaveRecursiveCall();
    Py_DECREF(dict_name);
    Py_DECREF(update_name);
    Py_DECREF(memo_identity);
    Py_XDECREF(context.model_code);
    Py_XDECREF(context.model_data_walk);
    Py_XDECREF(context.model_data_code);
    Py_XDECREF(context.model_data_body);
    Py_XDECREF(context.model_body_code);
    Py_XDECREF(context.model_native_walk);
    Py_XDECREF(context.model_feature);
    Py_XDECREF(context.model_walk_code);
    Py_XDECREF(context.model_admit_code);
    for (unsigned int i = 0; i < context.recipe_count; i++) {
        if (status == 1 && context.recipes[i].native
            && record_cache_store(context.recipes[i].type,
                                  context.recipes[i].version, state) < 0) {
            Py_CLEAR(result);
            status = -1;
        }
        Py_DECREF(context.recipes[i].handler);
        Py_DECREF(context.recipes[i].type);
    }
    Py_XDECREF(entry_walk_code);
    return status == 1 ? result : NULL;
}

static PyObject *
clone_record_graph(PyObject *module, PyObject *const *args, Py_ssize_t count)
{
    if (count != 4 || !PyType_Check(args[1]) || !PyCallable_Check(args[2])
        || !PyDict_CheckExact(args[3])) {
        PyErr_SetString(PyExc_TypeError,
                        "clone_record_graph requires source, class, walker and memo");
        return NULL;
    }
    return clone_direct_graph(module, args[0], (PyTypeObject *)args[1], args[2],
                              NULL, args[3], 0, NULL);
}

static PyObject *
clone_walk_graph(PyObject *module, PyObject *const *args, Py_ssize_t count)
{
    if ((count != 4 && count != 5) || !PyCallable_Check(args[1])
        || !PyCallable_Check(args[2])
        || !PyDict_CheckExact(args[3])
        || (count == 5 && args[4] != Py_None && !PyFunction_Check(args[4]))) {
        PyErr_SetString(PyExc_TypeError,
                        "clone_walk_graph requires source, walker, admission and memo");
        return NULL;
    }
    return clone_direct_graph(module, args[0], NULL, args[1], args[2], args[3], 0,
                              count == 5 && args[4] != Py_None ? args[4] : NULL);
}

static PyObject *
clone_walk_canonical_graph(PyObject *module, PyObject *const *args, Py_ssize_t count)
{
    if ((count != 4 && count != 5) || !PyCallable_Check(args[1])
        || !PyCallable_Check(args[2])
        || !PyDict_CheckExact(args[3])
        || (count == 5 && args[4] != Py_None && !PyFunction_Check(args[4]))) {
        PyErr_SetString(PyExc_TypeError,
                        "clone_walk_canonical_graph requires source, walker, admission and memo");
        return NULL;
    }
    return clone_direct_graph(module, args[0], NULL, args[1], args[2], args[3], 1,
                              count == 5 && args[4] != Py_None ? args[4] : NULL);
}

static PyObject *
clone_checked(PyObject *module, PyObject *source)
{
    (void)module;
    return clone_builtin_impl(source, 1);
}

static PyObject *
clone_graph(PyObject *module, PyObject *const *args, Py_ssize_t count)
{
    if (count < 1 || count > 3
        || (count >= 2 && !PyDict_CheckExact(args[1]) && !(count == 3 && args[1] == Py_None))
        || (count == 3 && !PyFunction_Check(args[2]))) {
        PyErr_SetString(PyExc_TypeError,
                        "clone_graph requires source, optional exact memo dict and optional canonical walker");
        return NULL;
    }
    PyObject *source = args[0];
    PyObject *memo = count >= 2 && args[1] != Py_None ? args[1] : NULL;
    if (memo != NULL && PyDict_GET_SIZE(memo) != 0) {
        PyObject *source_id = PyLong_FromVoidPtr(source);
        if (source_id == NULL) {
            return NULL;
        }
        PyObject *existing = PyDict_GetItemWithError(memo, source_id);
        Py_DECREF(source_id);
        if (existing != NULL) {
            return Py_NewRef(existing);
        }
        if (PyErr_Occurred()) {
            return NULL;
        }
    }
    if (atom(source)) {
        return Py_NewRef(source);
    }
    unsigned int budget;
    int recursion_limit;
    int allowance = clone_budget(&budget, &recursion_limit);
    if (allowance < 0) {
        return NULL;
    }
    if (allowance == 0) {
        Py_RETURN_NOTIMPLEMENTED;
    }
    if (count == 3 && memo == NULL) {
        PyObject *copied = NULL;
        int status = clone_temporal_sequence(source, args[2], PyModule_GetState(module),
                                             recursion_limit, &copied);
        if (status != 0) {
            return copied;
        }
    }
    /* A closed scalar dictionary contains no child identity to remember.
     * Avoid creating a table for this common root shape, and revalidate the
     * copied values because dictionary allocation may run mutating finalizers. */
    if (memo == NULL && PyDict_CheckExact(source) && shallow_atoms(source)) {
        PyObject *copied = PyDict_Copy(source);
        if (copied == NULL) {
            return NULL;
        }
        if (shallow_atoms(copied)
            && Py_GetRecursionLimit() == recursion_limit) {
            return copied;
        }
        Py_DECREF(copied);
        Py_RETURN_NOTIMPLEMENTED;
    }
    /* The copying pass validates types and budgets as it goes, detects active
     * ancestors, and discards its private memo on any refusal. An eligibility
     * tree walk here would repeatedly expand shared subgraphs. */
    CopyContext context = {0};
    context.remaining = MAX_CONTAINERS;
    context.memo = memo;
    context.entries = context.inline_entries;
    context.capacity = INLINE_MEMO_SIZE;
    /* The canonical public caller can continue in its shared-memo walk.
     * Legacy direct calls retain their explicit proved-cycle error. */
    context.refuse_cycles = count == 3;
    if (context.refuse_cycles) {
        context.cycle_walk = args[2];
        context.module_state = PyModule_GetState(module);
    }
    PyObject *result = NULL;
    int status = clone_closed_value(source, &result, budget, &context);
    if (status == 1 && Py_GetRecursionLimit() != recursion_limit) {
        Py_CLEAR(result);
        status = 0;
    }
    if (status == 1 && memo != NULL) {
        int exported = memo_export(&context);
        if (exported != 0) {
            Py_CLEAR(result);
            status = exported < 0 ? -1 : 0;
        }
    }
    memo_clear(&context);
    if (status < 0) {
        return NULL;
    }
    if (status == 0) {
        Py_RETURN_NOTIMPLEMENTED;
    }
    return result;
}

/* The source owns its type while these weak, bounded admission entries are
 * consulted. Class mutation invalidates tp_version_tag; zero tags never hit.
 * Cache installation happens after copying, avoiding allocations between the
 * final admission check and the selected twin's allocation. */
/* 0: empty/dead; 1: selected class; 2: another live class. */
static int
record_ref_status(PyObject *reference, PyTypeObject *type)
{
    if (reference == NULL) {
        return 0;
    }
#if PY_VERSION_HEX >= 0x030D0000
    PyObject *referent = NULL;
    if (PyWeakref_GetRef(reference, &referent) != 1) {
        return 0;
    }
    int status = referent == (PyObject *)type ? 1 : 2;
    Py_DECREF(referent);
    return status;
#else
    PyObject *referent = PyWeakref_GetObject(reference);
    return referent == Py_None ? 0 : referent == (PyObject *)type ? 1 : 2;
#endif
}

static int
record_cached(PyTypeObject *type, ModuleState *state)
{
    unsigned int version = record_version(type);
    if (version == 0) {
        return 0;
    }
    size_t index = memo_hash((PyObject *)type);
    for (size_t offset = 0; offset < 4; offset++) {
        RecordEntry *entry = &state->records[
            (index + offset) & (RECORD_CACHE_SIZE - 1)];
        if (entry->version == version
            && record_ref_status(entry->reference, type) == 1) {
            return 1;
        }
    }
    return 0;
}

static int
record_cache_store(PyTypeObject *type, unsigned int version, ModuleState *state)
{
    if (version == 0 || record_version(type) != version) {
        return 0;
    }
    size_t index = memo_hash((PyObject *)type);
    /* Population is optional. An unchanged version entry needs no rewrite,
     * including when earlier probe slots belong to other live classes.
     * Retrieval still validates both the live version and weakref identity. */
    for (size_t offset = 0; offset < 4; offset++) {
        RecordEntry *entry = &state->records[
            (index + offset) & (RECORD_CACHE_SIZE - 1)];
        if (entry->version == version) {
            return 0;
        }
    }
    RecordEntry *available = NULL;
    for (size_t offset = 0; offset < 4; offset++) {
        RecordEntry *entry = &state->records[
            (index + offset) & (RECORD_CACHE_SIZE - 1)];
        int status = record_ref_status(entry->reference, type);
        if (status == 1) {
            entry->version = version;
            return 0;
        }
        if (status == 0 && available == NULL) {
            available = entry;
        }
    }
    if (available == NULL) {
        available = &state->records[index & (RECORD_CACHE_SIZE - 1)];
    }
    /* Weakref allocation may collect and move both source and twin to another
     * class. Hold the admitted class until its final version check is done. */
    Py_INCREF(type);
    PyObject *reference = PyWeakref_NewRef((PyObject *)type, NULL);
    if (reference == NULL) {
        Py_DECREF(type);
        return -1;
    }
    if (record_version(type) == version) {
        Py_XSETREF(available->reference, reference);
        available->version = version;
    } else {
        Py_DECREF(reference);
    }
    Py_DECREF(type);
    return 0;
}

static int
memo_pin_source(PyObject *source, PyObject *memo)
{
    PyObject *identity = PyLong_FromVoidPtr(memo);
    if (identity == NULL) {
        return -1;
    }
    PyObject *alive = PyDict_GetItemWithError(memo, identity);
    int status = 0;
    if (alive == NULL) {
        if (PyErr_Occurred()) {
            Py_DECREF(identity);
            return -1;
        }
        alive = PyList_New(1);
        if (alive == NULL) {
            Py_DECREF(identity);
            return -1;
        }
        PyList_SET_ITEM(alive, 0, Py_NewRef(source));
        status = PyDict_SetItem(memo, identity, alive);
        Py_DECREF(alive);
    } else {
        Py_INCREF(alive);
        if (PyList_CheckExact(alive)) {
            Py_ssize_t size = PyList_GET_SIZE(alive);
            /* The normal Python walker just pinned this source already. */
            if (size == 0 || PyList_GET_ITEM(alive, size - 1) != source) {
                status = PyList_Append(alive, source);
            }
        } else {
            PyObject *returned = PyObject_CallMethod(alive, "append", "O", source);
            status = returned == NULL ? -1 : 0;
            Py_XDECREF(returned);
        }
        Py_DECREF(alive);
    }
    Py_DECREF(identity);
    return status;
}

/* Retain the wrapper and after-memo body's frames during state continuation. */
static PyObject *
clone_state_graph(PyObject *module, PyObject *source, PyObject *memo, PyObject *walk)
{
#if PY_VERSION_HEX >= 0x030C0000 && PY_VERSION_HEX < 0x030F0000
    PyThreadState *thread = PyThreadState_Get();
    if (thread->py_recursion_remaining < 2) {
        PyErr_SetString(PyExc_RecursionError,
                        "maximum recursion depth while cloning record state");
        return NULL;
    }
    thread->py_recursion_remaining -= 2;
#endif
    PyObject *copied = clone_direct_graph(module, source, NULL, walk, NULL, memo, 0, NULL);
    if (copied == Py_NotImplemented) {
        Py_DECREF(copied);
        PyObject *values[] = {source, memo};
        copied = PyObject_Vectorcall(walk, values, 2, NULL);
    }
#if PY_VERSION_HEX >= 0x030C0000 && PY_VERSION_HEX < 0x030F0000
    /* Add rather than restore a saved value: hooks may change the limit. */
    thread->py_recursion_remaining += 2;
#endif
    return copied;
}

/* The Python admission layer has already selected the default dataclass
 * protocol. Recheck the narrower allocation contract before creating a twin;
 * after allocation, finish that same twin even if collection changes its class.
 * The empty instance stays tracked and is memoized before any state callbacks. */
static PyObject *
clone_dataclass(PyObject *module, PyObject *const *args, Py_ssize_t count)
{
    if ((count != 3 && count != 4) || !PyDict_CheckExact(args[1])
        || !PyCallable_Check(args[2])
        || (count == 4 && args[3] != Py_True && args[3] != Py_False)) {
        PyErr_SetString(PyExc_TypeError,
                        "clone_dataclass requires source, exact memo, walker and optional reuse flag");
        return NULL;
    }
    PyObject *source = args[0], *memo = args[1], *walk = args[2];
    PyObject *identity = PyLong_FromVoidPtr(source);
    if (identity == NULL) {
        return NULL;
    }
    /* A standalone helper honors a preseeded twin. Once Python has selected
     * this reconstruction handler, admission/GC writes must not skip it. */
    PyObject *existing = count == 3 || args[3] == Py_True
        ? PyDict_GetItemWithError(memo, identity) : NULL;
    if (existing != NULL) {
        Py_DECREF(identity);
        return Py_NewRef(existing);
    }
    if (PyErr_Occurred()) {
        Py_DECREF(identity);
        return NULL;
    }
    PyTypeObject *type = Py_TYPE(source);
    ModuleState *state = PyModule_GetState(module);
    int cached = record_cached(type, state);
    if (!cached && !record_admissible(type)) {
        Py_DECREF(identity);
        Py_RETURN_NOTIMPLEMENTED;
    }
    unsigned int version = record_version(type);
    /* Keep the selected class through every callback and cache check, even
     * when both source and twin move to another class during reconstruction. */
    Py_INCREF(type);
    PyObject *twin = PyType_GenericAlloc(type, 0);
    if (twin == NULL) {
        Py_DECREF(type);
        Py_DECREF(identity);
        return NULL;
    }
    if (PyDict_SetItem(memo, identity, twin) < 0) {
        Py_DECREF(identity);
        Py_DECREF(twin);
        Py_DECREF(type);
        return NULL;
    }
    Py_DECREF(identity);
    /* A dispatched Python handler already pinned its source, including an
     * observable custom append. Standalone helper calls own that pin here. */
    if ((count == 3 || args[3] == Py_True)
        && memo_pin_source(source, memo) < 0) {
        Py_DECREF(twin);
        Py_DECREF(type);
        return NULL;
    }
    PyObject *source_state = PyObject_GetAttr(source, state->dict_name);
    if (source_state == NULL) {
        Py_DECREF(twin);
        Py_DECREF(type);
        return NULL;
    }
    int truth = PyObject_IsTrue(source_state);
    if (truth < 0) {
        Py_DECREF(source_state);
        Py_DECREF(twin);
        Py_DECREF(type);
        return NULL;
    }
    if (truth != 0) {
        PyObject *destination = PyObject_GetAttr(twin, state->dict_name);
        if (destination == NULL) {
            Py_DECREF(source_state);
            Py_DECREF(twin);
            Py_DECREF(type);
            return NULL;
        }
        int builtin_update = PyDict_CheckExact(source_state)
            && PyDict_CheckExact(destination);
        PyObject *update = builtin_update ? NULL
            : PyObject_GetAttr(destination, state->update_name);
        if (!builtin_update && update == NULL) {
            Py_DECREF(destination);
            Py_DECREF(source_state);
            Py_DECREF(twin);
            Py_DECREF(type);
            return NULL;
        }
        PyObject *copied = clone_state_graph(module, source_state, memo, walk);
        if (copied == NULL) {
            Py_XDECREF(update);
            Py_DECREF(destination);
            Py_DECREF(source_state);
            Py_DECREF(twin);
            Py_DECREF(type);
            return NULL;
        }
        int applied;
        if (builtin_update) {
            applied = PyDict_Update(destination, copied);
        } else {
            PyObject *returned = PyObject_CallOneArg(update, copied);
            applied = returned == NULL ? -1 : 0;
            Py_XDECREF(returned);
        }
        Py_XDECREF(update);
        Py_DECREF(copied);
        Py_DECREF(destination);
        if (applied < 0) {
            Py_DECREF(source_state);
            Py_DECREF(twin);
            Py_DECREF(type);
            return NULL;
        }
    }
    Py_DECREF(source_state);
    /* Populate only the cache entry for the class this call actually used. */
    if (!cached && type == Py_TYPE(source) && type == Py_TYPE(twin)
        && record_cache_store(type, version, state) < 0) {
        Py_DECREF(twin);
        Py_DECREF(type);
        return NULL;
    }
    Py_DECREF(type);
    return twin;
}

static int
module_exec(PyObject *module)
{
    ModuleState *state = PyModule_GetState(module);
    state->dict_name = PyUnicode_InternFromString("__dict__");
    if (state->dict_name == NULL) {
        return -1;
    }
    state->update_name = PyUnicode_InternFromString("update");
    state->datetime_module = PyImport_ImportModule("datetime");
    if (state->update_name == NULL || state->datetime_module == NULL) {
        return -1;
    }
    state->datetime_capsule = PyObject_GetAttrString(state->datetime_module, "datetime_CAPI");
    if (state->datetime_capsule == NULL) {
        return -1;
    }
    state->datetime_api = PyCapsule_GetPointer(state->datetime_capsule, PyDateTime_CAPSULE_NAME);
    state->copy_module = PyImport_ImportModule("copy");
    if (state->datetime_api == NULL || state->copy_module == NULL) {
        return -1;
    }
    state->deepcopy = PyObject_GetAttrString(state->copy_module, "deepcopy");
    state->tz_keywords = Py_BuildValue("(s)", "tzinfo");
    state->closed_names = Py_BuildValue("(ssss)", "_ATOMS", "date", "timedelta", "_GRAPH_ATOMS");
    if (state->closed_names == NULL || PyModule_AddIntConstant(module, "CLOSED_TEMPORALS", 1) < 0) {
        return -1;
    }
    state->temporal_names = Py_BuildValue("(ssssssssssss)", "_TEMPORAL_WALK", "_TEMPORAL_WALK_CODE",
        "_stdlib", "_dt", "datetime", "time", "deepcopy", "dispatch_table", "_deepcopy_dispatch", "_walk", "timezone", "_TIMEZONES");
    state->model_names = Py_BuildValue("(ssssssssssssssssssss)", "__new__", "__pydantic_extra__",
        "__pydantic_fields_set__", "__pydantic_private__", "_walk_data", "_record_admit",
        "_walk", "_native_clone", "_model_private", "_MODEL_WALK", "_MODEL_WALK_CODE",
        "_PYDANTIC_UNDEFINED", "_MODEL_DATA_WALK", "_MODEL_DATA_WALK_CODE",
        "_walk_data_after_memo", "_MODEL_DATA_AFTER_MEMO", "_MODEL_DATA_AFTER_MEMO_CODE",
        "_native_walk_graph", "_model", "_NATIVE_MODEL_CONTINUATION");
    if (state->model_names == NULL || PyModule_AddIntConstant(module, "MODEL_CONTINUATION", 1) < 0) {
        return -1;
    }
    return state->deepcopy == NULL || state->tz_keywords == NULL
        || state->temporal_names == NULL ? -1 : 0;
}

static int
module_traverse(PyObject *module, visitproc visit, void *arg)
{
    ModuleState *state = PyModule_GetState(module);
    Py_VISIT(state->dict_name);
    Py_VISIT(state->update_name);
    Py_VISIT(state->datetime_module);
    Py_VISIT(state->datetime_capsule);
    Py_VISIT(state->copy_module);
    Py_VISIT(state->deepcopy);
    Py_VISIT(state->tz_keywords);
    Py_VISIT(state->temporal_names);
    Py_VISIT(state->model_names);
    Py_VISIT(state->closed_names);
    for (unsigned int i = 0; i < RECORD_CACHE_SIZE; i++) {
        Py_VISIT(state->records[i].reference);
    }
    return 0;
}

static int
module_clear(PyObject *module)
{
    ModuleState *state = PyModule_GetState(module);
    Py_CLEAR(state->dict_name);
    Py_CLEAR(state->update_name);
    state->datetime_api = NULL;
    Py_CLEAR(state->datetime_module);
    Py_CLEAR(state->datetime_capsule);
    Py_CLEAR(state->copy_module);
    Py_CLEAR(state->deepcopy);
    Py_CLEAR(state->tz_keywords);
    Py_CLEAR(state->temporal_names);
    Py_CLEAR(state->model_names);
    Py_CLEAR(state->closed_names);
    for (unsigned int i = 0; i < RECORD_CACHE_SIZE; i++) {
        Py_CLEAR(state->records[i].reference);
        state->records[i].version = 0;
    }
    return 0;
}

static void
module_free(void *module)
{
    /* Direct module deallocation does not run the GC clear slot first. */
    module_clear((PyObject *)module);
}

static PyMethodDef methods[] = {
    {"clone", clone_builtin, METH_O,
     "Clone exact builtin data or return NotImplemented without copy hooks."},
    {"clone_checked", clone_checked, METH_O,
     "Clone builtin data, raising RecursionError for a proved builtin cycle."},
    {"clone_graph", (PyCFunction)(void(*)(void))clone_graph, METH_FASTCALL,
     "Clone builtin data while preserving identities in a shared copy memo."},
    {"clone_walk_canonical_graph", (PyCFunction)(void(*)(void))clone_walk_canonical_graph, METH_FASTCALL,
     "Clone canonical structural graphs with guarded immutable temporal leaves."},
    {"clone_walk_graph", (PyCFunction)(void(*)(void))clone_walk_graph, METH_FASTCALL,
     "Clone builtin and admitted record graphs with a shared Python memo."},
    {"clone_record_graph", (PyCFunction)(void(*)(void))clone_record_graph, METH_FASTCALL,
     "Clone an admitted record graph with a shared Python memo."},
    {"clone_dataclass", (PyCFunction)(void(*)(void))clone_dataclass, METH_FASTCALL,
     "Clone an admitted dataclass with its shared Python memo."},
    {"clone_records", (PyCFunction)(void(*)(void))clone_records, METH_FASTCALL,
     "Clone an admitted record tree, delegating foreign nodes to its walker."},
    {NULL, NULL, 0, NULL}
};

static PyModuleDef_Slot slots[] = {
    {Py_mod_exec, module_exec},
    {Py_mod_multiple_interpreters, Py_MOD_PER_INTERPRETER_GIL_SUPPORTED},
#if PY_VERSION_HEX >= 0x030D0000
    {Py_mod_gil, Py_MOD_GIL_USED},
#endif
    {0, NULL}
};

static struct PyModuleDef definition = {
    PyModuleDef_HEAD_INIT,
    "_clone_native",
     "Optional structural cloning with Python-owned protocol delegation.",
    sizeof(ModuleState),
    methods,
    slots,
    module_traverse,
    module_clear,
    module_free
};

PyMODINIT_FUNC
PyInit__clone_native(void)
{
#ifdef Py_GIL_DISABLED
    PyErr_SetString(PyExc_ImportError, "native cloning requires a GIL build");
    return NULL;
#else
    return PyModuleDef_Init(&definition);
#endif
}
