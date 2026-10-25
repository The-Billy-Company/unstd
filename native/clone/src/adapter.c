/* Optional builtin tree and admitted record cloning.
 *
 * clone/clone_checked refuse foreign nodes without invoking copy/hash/equality
 * hooks. clone_records receives the Python walker for foreign nodes, so it can
 * finish already allocated twins without retrying observable reconstruction.
 *
 * Container allocation can run cyclic GC and arbitrary Python finalizers even
 * while the GIL is held. Every item is owned across recursive allocations;
 * dictionary cursors retain Python's mutation checks. Presized sequence
 * outputs stay untracked until their NULL slots have all been filled.
 */
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <stdint.h>

#define MAX_DEPTH 128
#define MAX_CONTAINERS 65536
#define INLINE_MEMO_SIZE 16
#define RECORD_CACHE_SIZE 32

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
} ModuleState;

typedef struct {
    Py_ssize_t remaining;
    unsigned int count;
    PyObject *ancestors[MAX_DEPTH];
} Proof;

typedef struct {
    Py_ssize_t remaining;
    PyTypeObject *record_type;
    unsigned int record_version;
    PyObject *walk;
    PyObject *dict_name;
    PyObject *update_name;
    PyObject *memo;
    MemoEntry *entries;
    Py_ssize_t capacity;
    Py_ssize_t used;
    MemoEntry inline_entries[INLINE_MEMO_SIZE];
    unsigned int count;
    PyObject *ancestors[MAX_DEPTH];
} CopyContext;

static int clone_value(PyObject *, PyObject **, unsigned int, CopyContext *);
static int atom(PyObject *);
static int eligible(PyObject *, unsigned int, Proof *);
static int record_current(CopyContext *);
static int clone_record(PyObject *, PyObject **, unsigned int, CopyContext *);
static PyObject *clone_graph(PyObject *, PyObject *const *, Py_ssize_t);

/* Only completed containers enter this private pointer-key table. Owning both
 * references keeps source addresses stable and never exposes NULL sequence
 * slots to Python, even if allocation-triggered GC mutates the source graph.
 * The inline table handles small graphs without allocating any Python memo
 * objects. Optional caller memos are exported only after the whole walk succeeds. */
static size_t
memo_hash(PyObject *source)
{
    uintptr_t address = (uintptr_t)source >> 4;
    return (size_t)(address ^ (address >> 16));
}

static MemoEntry *
memo_entry(MemoEntry *entries, Py_ssize_t capacity, PyObject *source)
{
    size_t mask = (size_t)capacity - 1;
    size_t position = memo_hash(source) & mask;
    while (entries[position].source != NULL
           && entries[position].source != source) {
        position = (position + 1) & mask;
    }
    return &entries[position];
}

static int
memo_lookup(PyObject *source, PyObject **result, CopyContext *context)
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
memo_store(PyObject *source, PyObject *copied, CopyContext *context)
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
        Py_DECREF(identity);
        if (status < 0) {
            break;
        }
    }
    if (status == 0) {
        PyObject_GC_Track(sources);
        /* Match copy._keep_alive's reserved ID(memo) entry so Python deepcopy
         * can continue using these identities without source address reuse. */
        PyObject *identity = PyLong_FromVoidPtr(context->memo);
        PyObject *alive = identity == NULL ? NULL
            : PyDict_GetItemWithError(context->memo, identity);
        if (identity == NULL || (alive == NULL && PyErr_Occurred())) {
            status = -1;
        } else if (alive == NULL) {
            status = PyDict_SetItem(context->memo, identity, sources);
        } else if (!PyList_CheckExact(alive)) {
            PyErr_SetString(PyExc_TypeError,
                            "clone memo keep-alive entry must be a list");
            status = -1;
        } else {
            Py_INCREF(alive);
            Py_ssize_t size = PyList_GET_SIZE(alive);
            status = PyList_SetSlice(alive, size, size, sources);
            Py_DECREF(alive);
        }
        Py_XDECREF(identity);
        if (status == 0) {
            status = PyDict_Update(context->memo, pending);
        }
    }
    Py_DECREF(sources);
    Py_DECREF(pending);
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

static int
record_current(CopyContext *context)
{
    PyTypeObject *type = context->record_type;
    if (context->record_version != 0
        && context->record_version == type->tp_version_tag) {
        return 1;
    }
    if (!record_admissible(type)) {
        return 0;
    }
    context->record_version = type->tp_version_tag;
    return 1;
}

static int
python_value(PyObject *source, PyObject **result, CopyContext *context)
{
    *result = PyObject_CallOneArg(context->walk, source);
    return *result == NULL ? -1 : 1;
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
atom(PyObject *value)
{
    PyTypeObject *kind = Py_TYPE(value);
    return kind == &PyLong_Type || kind == &PyUnicode_Type
        || kind == &PyFloat_Type || kind == &PyBool_Type || value == Py_None
        || kind == &PyBytes_Type || kind == &PyComplex_Type
        || value == Py_Ellipsis || value == Py_NotImplemented;
}

/* 1: owned result; 0: unsupported (no exception); -1: real failure. */
static int
clone_sequence(PyObject *source, PyObject **result, unsigned int depth,
               CopyContext *context)
{
    int tuple = PyTuple_CheckExact(source);
    Py_ssize_t size = Py_SIZE(source);
    PyObject *output = tuple ? PyTuple_New(size) : PyList_New(size);
    if (output == NULL) {
        return -1;
    }
    /* No partially initialized list/tuple may appear in gc.get_objects() or
     * gc.get_referrers() during allocations further down the tree. A completed
     * result is tracked again before it can be published to its parent. */
    if (PyObject_GC_IsTracked(output)) {
        PyObject_GC_UnTrack(output);
    }
    Py_ssize_t written = 0;
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
            status = clone_value(item, &copied, depth - 1, context);
            if (context->walk == NULL) {
                Py_DECREF(item);
            }
        }
        if (status != 1) {
            break;
        }
        if (tuple) {
            PyTuple_SET_ITEM(output, written, copied);
        } else if (written < size) {
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
    if (!tuple && written < size) {
        /* A source-shrinking finalizer can leave unused NULL slots. Slice only
         * the initialized prefix; never expose those slots to Python or GC. */
        *result = PyList_GetSlice(output, 0, written);
        Py_DECREF(output);
        return *result == NULL ? -1 : 1;
    }
    if (Py_SIZE(output) != 0 || !tuple) {
        PyObject_GC_Track(output);
    }
    *result = output;
    return 1;
}

static int
clone_dict(PyObject *source, PyObject **result, unsigned int depth,
           CopyContext *context)
{
    PyObject *output = _PyDict_NewPresized(PyDict_Size(source));
    if (output == NULL) {
        return -1;
    }
    if (PyObject_GC_IsTracked(output)) {
        PyObject_GC_UnTrack(output);
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
            status = python_value(key, &copied_key, context);
            if (status != 1) {
                break;
            }
        }
        PyObject *copied = NULL;
        if (atom(value)) {
            copied = Py_NewRef(value);
        } else {
            status = clone_value(value, &copied, depth - 1, context);
        }
        if (status != 1) {
            Py_DECREF(copied_key);
            break;
        }
        int inserted = PyDict_SetItem(output, copied_key, copied);
        /* This private result cannot be reached through GC introspection while
         * another child is allocated. The presized table needs no growth for
         * the expected number of items. All stored keys are exact strings. */
        if (PyObject_GC_IsTracked(output)) {
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
    PyObject_GC_Track(output);
    *result = output;
    return 1;
}

static int
clone_value(PyObject *source, PyObject **result, unsigned int depth,
            CopyContext *context)
{
    if (atom(source)) {
        *result = Py_NewRef(source);
        return 1;
    }
    if (context->entries != NULL) {
        int found = memo_lookup(source, result, context);
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
    if (context->walk != NULL || context->entries != NULL) {
        if (record && !record_current(context)) {
            return python_value(source, result, context);
        }
        for (unsigned int i = 0; i < context->count; i++) {
            if (context->ancestors[i] == source) {
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
    if (context->walk != NULL || context->entries != NULL) {
        context->ancestors[context->count++] = source;
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
            ? clone_dict(source, result, depth, context)
            : clone_sequence(source, result, depth, context);
    }
    if (context->walk != NULL || context->entries != NULL) {
        --context->count;
    }
    Py_LeaveRecursiveCall();
    if (status == 1 && context->entries != NULL
        && memo_store(source, *result, context) < 0) {
        Py_CLEAR(*result);
        return -1;
    }
    return status;
}

static int
clone_record(PyObject *source, PyObject **result, unsigned int depth,
             CopyContext *context)
{
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
        int status = clone_value(state, &copied, depth - 1, context);
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
    int status = clone_value(source, &result, budget, &context);
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
    context.record_version = type->tp_version_tag;
    context.walk = args[2];
    context.dict_name = dict_name;
    context.update_name = update_name;
    PyObject *result = NULL;
    int status = clone_value(source, &result, budget, &context);
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
clone_checked(PyObject *module, PyObject *source)
{
    (void)module;
    return clone_builtin_impl(source, 1);
}

static PyObject *
clone_graph(PyObject *module, PyObject *const *args, Py_ssize_t count)
{
    (void)module;
    if (count < 1 || count > 2
        || (count == 2 && !PyDict_CheckExact(args[1]))) {
        PyErr_SetString(PyExc_TypeError,
                        "clone_graph requires source and optional exact memo dict");
        return NULL;
    }
    PyObject *source = args[0];
    PyObject *memo = count == 2 ? args[1] : NULL;
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
    PyObject *result = NULL;
    int status = clone_value(source, &result, budget, &context);
    if (status == 1 && Py_GetRecursionLimit() != recursion_limit) {
        Py_CLEAR(result);
        status = 0;
    }
    if (status == 1 && memo != NULL && memo_export(&context) < 0) {
        Py_CLEAR(result);
        status = -1;
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
    unsigned int version = type->tp_version_tag;
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
    if (version == 0 || type->tp_version_tag != version) {
        return 0;
    }
    size_t index = memo_hash((PyObject *)type);
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
    if (type->tp_version_tag == version) {
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
    } else if (!PyList_CheckExact(alive)) {
        PyErr_SetString(PyExc_TypeError,
                        "clone memo keep-alive entry must be a list");
        status = -1;
    } else {
        Py_ssize_t size = PyList_GET_SIZE(alive);
        /* The normal Python walker just pinned this source already. */
        if (size == 0 || PyList_GET_ITEM(alive, size - 1) != source) {
            status = PyList_Append(alive, source);
        }
    }
    Py_DECREF(identity);
    return status;
}

/* The Python admission layer has already selected the default dataclass
 * protocol. Recheck the narrower allocation contract before creating a twin;
 * after allocation, finish that same twin even if collection changes its class.
 * The empty instance stays tracked and is memoized before any state callbacks. */
static PyObject *
clone_dataclass(PyObject *module, PyObject *const *args, Py_ssize_t count)
{
    if (count != 3 || !PyDict_CheckExact(args[1]) || !PyCallable_Check(args[2])) {
        PyErr_SetString(PyExc_TypeError,
                        "clone_dataclass requires source, exact memo dict and walker");
        return NULL;
    }
    PyObject *source = args[0], *memo = args[1], *walk = args[2];
    PyObject *identity = PyLong_FromVoidPtr(source);
    if (identity == NULL) {
        return NULL;
    }
    PyObject *existing = PyDict_GetItemWithError(memo, identity);
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
    unsigned int version = type->tp_version_tag;
    /* Allocation can collect and change source.__class__ before the new
     * instance itself owns the selected type. Keep that allocator argument. */
    Py_INCREF(type);
    PyObject *twin = PyType_GenericAlloc(type, 0);
    Py_DECREF(type);
    if (twin == NULL) {
        Py_DECREF(identity);
        return NULL;
    }
    if (PyDict_SetItem(memo, identity, twin) < 0) {
        Py_DECREF(identity);
        Py_DECREF(twin);
        return NULL;
    }
    Py_DECREF(identity);
    if (memo_pin_source(source, memo) < 0) {
        Py_DECREF(twin);
        return NULL;
    }
    PyObject *source_state = PyObject_GetAttr(source, state->dict_name);
    if (source_state == NULL) {
        Py_DECREF(twin);
        return NULL;
    }
    int truth = PyObject_IsTrue(source_state);
    if (truth < 0) {
        Py_DECREF(source_state);
        Py_DECREF(twin);
        return NULL;
    }
    if (truth != 0) {
        PyObject *destination = PyObject_GetAttr(twin, state->dict_name);
        if (destination == NULL) {
            Py_DECREF(source_state);
            Py_DECREF(twin);
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
            return NULL;
        }
        PyObject *values[] = {source_state, memo};
        PyObject *copied = clone_graph(module, values, 2);
        if (copied == Py_NotImplemented) {
            Py_DECREF(copied);
            copied = PyObject_Vectorcall(walk, values, 2, NULL);
        }
        if (copied == NULL) {
            Py_XDECREF(update);
            Py_DECREF(destination);
            Py_DECREF(source_state);
            Py_DECREF(twin);
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
            return NULL;
        }
    }
    Py_DECREF(source_state);
    /* A user state callback can move both source and twin to another class.
     * In that case the original borrowed type may no longer be alive. */
    if (!cached && type == Py_TYPE(source) && type == Py_TYPE(twin)
        && record_cache_store(type, version, state) < 0) {
        Py_DECREF(twin);
        return NULL;
    }
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
    return state->update_name == NULL ? -1 : 0;
}

static int
module_traverse(PyObject *module, visitproc visit, void *arg)
{
    ModuleState *state = PyModule_GetState(module);
    Py_VISIT(state->dict_name);
    Py_VISIT(state->update_name);
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
