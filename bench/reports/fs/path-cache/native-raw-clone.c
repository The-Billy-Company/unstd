/* Opportunity proof only: before-import admission and GC reentrancy unqualified. */
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <structmember.h>

static PyObject *factory;
static PyTypeObject *concrete;
static Py_ssize_t raw_offset;
static unsigned int version;
static PyObject *new_func, *init_func, *new_code, *init_code;
static PyObject *globals, *builtins, *os_dict;
static PyObject *global_keys[5], *global_values[5];
static PyObject *builtin_keys[2], *builtin_values[2];
static PyObject *os_name_key, *os_name;

static int
bindings_current(PyObject *current_factory)
{
    if (current_factory != factory || concrete->tp_version_tag != version || version == 0
        || PyFunction_GET_CODE(new_func) != new_code
        || PyFunction_GET_CODE(init_func) != init_code)
        return 0;
    for (size_t i = 0; i < 5; ++i)
        if (PyDict_GetItemWithError(globals, global_keys[i]) != global_values[i]) return 0;
    for (size_t i = 0; i < 2; ++i)
        if (PyDict_GetItemWithError(globals, builtin_keys[i]) != NULL
            || PyDict_GetItemWithError(builtins, builtin_keys[i]) != builtin_values[i]) return 0;
    return PyDict_GetItemWithError(os_dict, os_name_key) == os_name;
}
static PyObject *
normalize(PyObject *module, PyObject *const *args, Py_ssize_t count)
{
    (void)module;
    if (count != 2) { PyErr_SetString(PyExc_TypeError,"normalize requires source and current factory"); return NULL; }
    PyObject *source = args[0], *current_factory = args[1];
    if (Py_TYPE(source) != concrete || !bindings_current(current_factory))
        return PyObject_CallOneArg(current_factory, source);
    PyObject *raw = *(PyObject **)((char *)source + raw_offset);
    if (raw == NULL || !PyList_CheckExact(raw))
        return PyObject_CallOneArg(current_factory, source);
    for (Py_ssize_t i = 0; i < PyList_GET_SIZE(raw); ++i)
        if (!PyUnicode_CheckExact(PyList_GET_ITEM(raw, i)))
            return PyObject_CallOneArg(current_factory, source);
    Py_INCREF(raw);
    PyObject *output = PyType_GenericAlloc(concrete, 0);
    if (output == NULL) { Py_DECREF(raw); return NULL; }
    if (Py_TYPE(source) != concrete || !bindings_current(current_factory)
        || *(PyObject **)((char *)source + raw_offset) != raw) {
        Py_DECREF(output); Py_DECREF(raw);
        return PyObject_CallOneArg(current_factory, source);
    }
    PyObject *fresh_raw = PyList_GetSlice(raw, 0, PyList_GET_SIZE(raw));
    Py_DECREF(raw);
    if (fresh_raw == NULL) { Py_DECREF(output); return NULL; }
    *(PyObject **)((char *)output + raw_offset) = fresh_raw;
    return output;
}
static PyMethodDef methods[] = {
    {"normalize", (PyCFunction)(void(*)(void))normalize, METH_FASTCALL, "Unqualified guarded fresh raw-list opportunity."},
    {NULL, NULL, 0, NULL}
};
static struct PyModuleDef definition = {PyModuleDef_HEAD_INIT, "_path_probe", NULL, -1, methods};
PyMODINIT_FUNC PyInit__path_probe(void)
{
    PyObject *pathlib = PyImport_ImportModule("pathlib");
    if (pathlib == NULL) return NULL;
    factory = PyObject_GetAttrString(pathlib, "Path");
    Py_DECREF(pathlib);
    if (factory == NULL) return NULL;
    PyObject *instance = PyObject_CallNoArgs(factory);
    if (instance == NULL) return NULL;
    concrete = Py_TYPE(instance);
    Py_INCREF(concrete);
    PyObject *descriptor = PyObject_GetAttrString((PyObject *)concrete, "_raw_paths");
    if (descriptor == NULL) { Py_DECREF(instance); return NULL; }
    if (!Py_IS_TYPE(descriptor, &PyMemberDescr_Type)) {
        PyErr_SetString(PyExc_TypeError,"Noncanonical raw slot");
        Py_DECREF(descriptor); Py_DECREF(instance); return NULL;
    }
    PyMemberDef *member = ((PyMemberDescrObject *)descriptor)->d_member;
    if (member->type != T_OBJECT_EX || member->offset < (Py_ssize_t)sizeof(PyObject) || member->offset + (Py_ssize_t)sizeof(PyObject *) > concrete->tp_basicsize) {
        PyErr_SetString(PyExc_TypeError,"Invalid raw slot");
        Py_DECREF(descriptor); Py_DECREF(instance); return NULL;
    }
    raw_offset = member->offset;
    Py_DECREF(descriptor); Py_DECREF(instance);
    new_func = PyObject_GetAttrString((PyObject *)concrete, "__new__");
    init_func = PyObject_GetAttrString((PyObject *)concrete, "__init__");
    if (new_func == NULL || init_func == NULL) return NULL;
    if (!PyFunction_Check(new_func) || !PyFunction_Check(init_func)) {
        PyErr_SetString(PyExc_TypeError,"Noncanonical constructor functions"); return NULL;
    }
    new_code = Py_NewRef(PyFunction_GET_CODE(new_func));
    init_code = Py_NewRef(PyFunction_GET_CODE(init_func));
    globals = Py_NewRef(PyFunction_GET_GLOBALS(new_func));
    if (globals != PyFunction_GET_GLOBALS(init_func)) {
        PyErr_SetString(PyExc_TypeError,"Different constructor namespaces"); return NULL;
    }
    builtins = PyObject_GetAttrString(new_func,"__builtins__");
    if (builtins == NULL || !PyDict_CheckExact(builtins)) return NULL;
    const char *names[] = {"Path","PurePath","PosixPath","WindowsPath","os"};
    for (size_t i = 0; i < 5; ++i) {
        global_keys[i] = PyUnicode_InternFromString(names[i]);
        global_values[i] = Py_XNewRef(PyDict_GetItemWithError(globals,global_keys[i]));
        if (global_values[i] == NULL) return NULL;
    }
    const char *bnames[] = {"object","isinstance"};
    for (size_t i = 0; i < 2; ++i) {
        builtin_keys[i] = PyUnicode_InternFromString(bnames[i]);
        builtin_values[i] = Py_XNewRef(PyDict_GetItemWithError(builtins,builtin_keys[i]));
        if (builtin_values[i] == NULL || PyDict_GetItemWithError(globals,builtin_keys[i]) != NULL) return NULL;
    }
    os_dict = Py_NewRef(PyModule_GetDict(global_values[4]));
    os_name_key = PyUnicode_InternFromString("name");
    os_name = Py_XNewRef(PyDict_GetItemWithError(os_dict,os_name_key));
    if (os_name == NULL) return NULL;
    version = concrete->tp_version_tag;
    return PyModule_Create(&definition);
}
