/* Portable ndarray specializations through the buffer protocol, no NumPy ABI. */
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <math.h>
#include <fenv.h>

#if defined(__clang__)
#pragma STDC FENV_ACCESS ON
#endif
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

/* This module owns no process-global Python objects. It retains the GIL while
   reading inputs: small-call latency is the purpose of these kernels. */
typedef struct {
    PyObject *ndarray;
    PyObject *empty;
    PyObject *f32;
    PyObject *f64;
    PyObject *intp;
    PyObject *negative;
    PyObject *invert;
    PyObject *argsort;
    PyObject *stable;
    PyObject *minus_one;
    PyObject *setflags;
    PyObject *readonly_type;
} State;

typedef struct { Py_buffer view; int code; } Array;

typedef struct { PyObject_HEAD Py_buffer source; } Readonly;

static int
readonly_buffer(PyObject *object, Py_buffer *view, int flags)
{
    Readonly *owner=(Readonly *)object;
    return PyBuffer_FillInfo(view,object,owner->source.buf,owner->source.len,1,flags);
}

static void
readonly_free(PyObject *object)
{
    Readonly *owner=(Readonly *)object;
    PyTypeObject *type=Py_TYPE(object);
    if(owner->source.obj!=NULL)PyBuffer_Release(&owner->source);
    type->tp_free(object);
    Py_DECREF(type);
}

static PyType_Slot readonly_slots[]={
    {Py_bf_getbuffer,readonly_buffer},
    {Py_tp_dealloc,readonly_free},
    {0,NULL}
};

static PyType_Spec readonly_spec={
    "unstd.math._math_native._Readonly",sizeof(Readonly),0,
    Py_TPFLAGS_DEFAULT|Py_TPFLAGS_DISALLOW_INSTANTIATION,readonly_slots
};

typedef struct { fexcept_t previous; int saved; } Floating;

static void
fp_begin(Floating *state)
{
    state->saved=fegetexceptflag(&state->previous,FE_ALL_EXCEPT)==0;
    feclearexcept(FE_ALL_EXCEPT);
}

static int
fp_end(Floating *state)
{
    int occurred=fetestexcept(FE_INVALID|FE_DIVBYZERO|FE_OVERFLOW|FE_UNDERFLOW);
    if(state->saved)fesetexceptflag(&state->previous,FE_ALL_EXCEPT);
    else feclearexcept(FE_ALL_EXCEPT);
    return occurred;
}

static PyObject *
fp_finish(Floating *state,PyObject *result)
{
    int occurred=fp_end(state);
    if(result==NULL)return NULL;
    if(occurred){Py_DECREF(result);Py_RETURN_NOTIMPLEMENTED;}
    return result;
}

static int
invalid(const char *message)
{
    PyErr_SetString(PyExc_ValueError, message);
    return -1;
}

static int
ready(State *state)
{
    if (state->empty != NULL) return 0;
    PyObject *numpy = PyImport_ImportModule("numpy");
    if (numpy == NULL) return -1;
    PyObject *ndarray = PyObject_GetAttrString(numpy, "ndarray");
    PyObject *empty = PyObject_GetAttrString(numpy, "empty");
    PyObject *f32 = PyObject_GetAttrString(numpy, "float32");
    PyObject *f64 = PyObject_GetAttrString(numpy, "float64");
    PyObject *intp = PyObject_GetAttrString(numpy, "intp");
    PyObject *negative = PyObject_GetAttrString(numpy, "negative");
    PyObject *invert = PyObject_GetAttrString(numpy, "bitwise_not");
    PyObject *argsort = PyObject_GetAttrString(numpy, "argsort");
    PyObject *stable = PyUnicode_FromString("stable");
    PyObject *minus_one = PyLong_FromLong(-1);
    PyObject *setflags = ndarray == NULL ? NULL : PyObject_GetAttrString(ndarray, "setflags");
    Py_DECREF(numpy);
    if (ndarray == NULL || empty == NULL || f32 == NULL || f64 == NULL || intp == NULL ||
        negative == NULL || invert == NULL || argsort == NULL || stable == NULL ||
        minus_one == NULL || setflags == NULL) {
        Py_XDECREF(ndarray); Py_XDECREF(empty); Py_XDECREF(f32);
        Py_XDECREF(f64); Py_XDECREF(intp); Py_XDECREF(negative);
        Py_XDECREF(invert); Py_XDECREF(argsort); Py_XDECREF(stable);
        Py_XDECREF(minus_one); Py_XDECREF(setflags);
        return -1;
    }
    if (!PyType_Check(ndarray) || !PyCallable_Check(empty)) {
        Py_DECREF(ndarray); Py_DECREF(empty); Py_DECREF(f32);
        Py_DECREF(f64); Py_DECREF(intp); Py_DECREF(negative); Py_DECREF(invert);
        Py_DECREF(argsort); Py_DECREF(stable); Py_DECREF(minus_one); Py_DECREF(setflags);
        return invalid("NumPy ndarray/empty contract unavailable");
    }
    state->ndarray = ndarray; state->f32 = f32;
    state->f64 = f64; state->intp = intp; state->negative = negative;
    state->invert = invert; state->argsort = argsort; state->stable = stable;
    state->minus_one = minus_one; state->setflags = setflags; state->empty = empty;
    return 0;
}

static int
native_format(const Py_buffer *view)
{
    const char *format = view->format;
    if (format == NULL || *format == '\0') return 0;
    if (*format == '@' || *format == '=') format++;
    else if (*format == '<' || *format == '>' || *format == '!') {
        const uint16_t one = 1;
        int little = *(const unsigned char *)&one;
        if ((*format == '<') != little) return 0;
        format++;
    }
    if (format[0] == '\0' || format[1] != '\0') return 0;
    char c = *format;
    if (c == 'f' && view->itemsize == 4) return c;
    if (c == 'd' && view->itemsize == 8) return c;
    if (strchr("bBhHiIlLqQnN", c) == NULL) return 0;
    if (view->itemsize != 1 && view->itemsize != 2 &&
        view->itemsize != 4 && view->itemsize != 8) return 0;
    return c;
}

static int
admit(State *state, PyObject *object, int ndim, Array *array, int floats_only)
{
    memset(array, 0, sizeof(*array));
    if ((PyObject *)Py_TYPE(object) != state->ndarray) return 0;
    if (PyObject_GetBuffer(object, &array->view, PyBUF_STRIDES | PyBUF_FORMAT) < 0)
        return -1;
    if (ndim >= 0 && array->view.ndim != ndim) {
        PyBuffer_Release(&array->view);
        return invalid(ndim == 1 ? "expected 1-dimensional values" :
                                  "expected 2-dimensional values");
    }
    array->code = native_format(&array->view);
    if (array->code == 0 || array->view.suboffsets != NULL ||
        (ndim == 2 && array->view.shape[1] > 65536) ||
        (floats_only && array->code != 'f' && array->code != 'd')) {
        PyBuffer_Release(&array->view);
        return 0;
    }
    return 1;
}

static int
small_matrix(const Array *array)
{
    Py_ssize_t rows = array->view.shape[0], columns = array->view.shape[1];
    return rows <= 2048 && columns <= 2048 &&
           (columns == 0 || rows <= 2048 / columns);
}

static int
precision(PyObject *object)
{
    if (PyUnicode_Check(object)) {
        if (PyUnicode_CompareWithASCIIString(object, "float64") == 0) return 8;
        if (PyUnicode_CompareWithASCIIString(object, "float32") == 0) return 4;
    }
    return invalid("dtype must be 'float32' or 'float64'");
}

static int
count(PyObject *object, const char *name, Py_ssize_t *result)
{
    /* Only exact Python ints enter this specialization. NumPy scalars and
       arbitrary __index__ implementations remain with public validation. */
    if (!PyLong_CheckExact(object)) return 0;
    *result = PyLong_AsSsize_t(object);
    if (*result == -1 && PyErr_Occurred()) {
        if (PyErr_ExceptionMatches(PyExc_OverflowError)) {
            PyErr_Clear(); return 0;
        }
        return -1;
    }
    if (*result < 0) return invalid(name);
    return 1;
}

static double
number(const char *pointer, int code, Py_ssize_t width)
{
    if (code == 'f') { float value; memcpy(&value, pointer, 4); return value; }
    if (code == 'd') { double value; memcpy(&value, pointer, 8); return value; }
    if (strchr("bhilqn", code) != NULL) {
        if (width == 1) { int8_t value; memcpy(&value, pointer, 1); return value; }
        if (width == 2) { int16_t value; memcpy(&value, pointer, 2); return value; }
        if (width == 4) { int32_t value; memcpy(&value, pointer, 4); return value; }
        int64_t value; memcpy(&value, pointer, 8); return (double)value;
    }
    if (width == 1) { uint8_t value; memcpy(&value, pointer, 1); return value; }
    if (width == 2) { uint16_t value; memcpy(&value, pointer, 2); return value; }
    if (width == 4) { uint32_t value; memcpy(&value, pointer, 4); return value; }
    uint64_t value; memcpy(&value, pointer, 8); return (double)value;
}

static double
at(const Array *array, Py_ssize_t row, Py_ssize_t column)
{
    const char *pointer = (const char *)array->view.buf + row * array->view.strides[0];
    if (array->view.ndim == 2) pointer += column * array->view.strides[1];
    return number(pointer, array->code, array->view.itemsize);
}

static PyObject *
allocate(State *state, Py_ssize_t rows, Py_ssize_t columns, int width, Py_buffer *out)
{
    PyObject *shape = columns < 0 ? PyLong_FromSsize_t(rows) :
                                  Py_BuildValue("(nn)", rows, columns);
    if (shape == NULL) return NULL;
    PyObject *arguments[2] = {shape, width == 0 ? state->intp :
                                    width == 4 ? state->f32 : state->f64};
    PyObject *result = PyObject_Vectorcall(state->empty, arguments, 2, NULL);
    Py_DECREF(shape);
    if (result == NULL) return NULL;
    if ((PyObject *)Py_TYPE(result) != state->ndarray) {
        Py_DECREF(result);
        invalid("NumPy empty returned an unexpected object");
        return NULL;
    }
    if (PyObject_GetBuffer(result, out, PyBUF_WRITABLE | PyBUF_STRIDES | PyBUF_FORMAT) < 0) {
        Py_DECREF(result);
        return NULL;
    }
    Py_ssize_t expected = width == 0 ? (Py_ssize_t)sizeof(Py_ssize_t) : width;
    if (out->itemsize != expected || out->ndim != (columns < 0 ? 1 : 2) ||
        out->shape[0] != rows || (columns >= 0 && out->shape[1] != columns) ||
        !PyBuffer_IsContiguous(out, 'C') ||
        (width == 4 && native_format(out) != 'f') ||
        (width == 8 && native_format(out) != 'd') ||
        (width == 0 && strchr("ilqn", native_format(out)) == NULL) ||
        ((uintptr_t)out->buf % (uintptr_t)expected) != 0) {
        PyBuffer_Release(out); Py_DECREF(result);
        invalid("NumPy empty returned an incompatible buffer");
        return NULL;
    }
    return result;
}

static void
store(void *output, Py_ssize_t index, int width, double value)
{
    if (width == 4) ((float *)output)[index] = (float)value;
    else ((double *)output)[index] = value;
}

static double
square(const Array *array, Py_ssize_t row, Py_ssize_t columns, int width)
{
    /* Independent lanes expose portable instruction-level parallelism without
       compiler fast-math, which would invalidate NaN and zero contracts. */
    if (width == 4) {
        float a = 0, b = 0, c = 0, d = 0;
        Py_ssize_t column = 0;
        for (; column + 3 < columns; column += 4) {
            float x = (float)at(array, row, column);
            float y = (float)at(array, row, column + 1);
            float z = (float)at(array, row, column + 2);
            float w = (float)at(array, row, column + 3);
            a += x*x; b += y*y; c += z*z; d += w*w;
        }
        float total = (a + b) + (c + d);
        for (; column < columns; column++) {
            float x = (float)at(array, row, column); total += x*x;
        }
        return total;
    }
    double a = 0, b = 0, c = 0, d = 0;
    Py_ssize_t column = 0;
    for (; column + 3 < columns; column += 4) {
        double x = at(array, row, column), y = at(array, row, column + 1);
        double z = at(array, row, column + 2), w = at(array, row, column + 3);
        a += x*x; b += y*y; c += z*z; d += w*w;
    }
    double total = (a + b) + (c + d);
    for (; column < columns; column++) { double x = at(array, row, column); total += x*x; }
    return total;
}

static PyObject *
mean_square(PyObject *module, PyObject *args)
{
    PyObject *values, *dtype;
    if (!PyArg_ParseTuple(args, "OO:mean_square", &values, &dtype)) return NULL;
    State *state = PyModule_GetState(module);
    if (ready(state) < 0) return NULL;
    Array rows; int ok = admit(state, values, 2, &rows, 1);
    if (ok < 0) return NULL;
    if (!ok) Py_RETURN_NOTIMPLEMENTED;
    if (!small_matrix(&rows) ||
        (rows.view.shape[1] != 0 && rows.view.shape[0] > 1024 / rows.view.shape[1])) {
        PyBuffer_Release(&rows.view); Py_RETURN_NOTIMPLEMENTED;
    }
    int width = precision(dtype);
    if (width < 0) { PyBuffer_Release(&rows.view); return NULL; }
    Py_ssize_t n = rows.view.shape[0], columns = rows.view.shape[1];
    Floating floating;fp_begin(&floating);
    Py_buffer output;
    PyObject *result = allocate(state, n, -1, width, &output);
    if (result != NULL) {
        for (Py_ssize_t row = 0; row < n; row++) {
            if ((row & 63) == 0 && PyErr_CheckSignals() < 0) {
                Py_CLEAR(result); break;
            }
            /* UCRT's arithmetic NAN macro can itself raise FP exceptions under
               /fp:strict. Construct the intentional quiet NaN without them. */
            double value = columns ? square(&rows, row, columns, width) / columns : nan("");
            store(output.buf, row, width, value);
        }
        PyBuffer_Release(&output);
    }
    PyBuffer_Release(&rows.view);
    return fp_finish(&floating,result);
}

static PyObject *
normalize_rows(PyObject *module, PyObject *args)
{
    PyObject *values, *dtype; double eps;
    if (!PyArg_ParseTuple(args, "OdO:normalize_rows", &values, &eps, &dtype)) return NULL;
    State *state = PyModule_GetState(module);
    if (ready(state) < 0) return NULL;
    Array rows; int ok = admit(state, values, 2, &rows, 1);
    if (ok < 0) return NULL;
    if (!ok) Py_RETURN_NOTIMPLEMENTED;
    if (!small_matrix(&rows)) { PyBuffer_Release(&rows.view); Py_RETURN_NOTIMPLEMENTED; }
    int width = precision(dtype);
    if (width < 0 || !isfinite(eps) || eps < 0) {
        PyBuffer_Release(&rows.view);
        if (width >= 0) invalid("eps must be finite and nonnegative");
        return NULL;
    }
    Py_ssize_t n = rows.view.shape[0], columns = rows.view.shape[1];
    Floating floating;fp_begin(&floating);
    Py_buffer output;
    PyObject *result = allocate(state, n, columns, width, &output);
    if (result != NULL) {
        for (Py_ssize_t row = 0; row < n; row++) {
            if ((row & 63) == 0 && PyErr_CheckSignals() < 0) {
                Py_CLEAR(result); break;
            }
            double denominator = width == 4 ?
                (double)((float)sqrtf((float)square(&rows, row, columns, width)) + (float)eps) :
                sqrt(square(&rows, row, columns, width)) + eps;
            for (Py_ssize_t column = 0; column < columns; column++) {
                double value = 0;
                if (denominator != 0) value = width == 4 ?
                    (double)((float)at(&rows, row, column) / (float)denominator) :
                    at(&rows, row, column) / denominator;
                store(output.buf, row*columns + column, width, value);
            }
        }
        PyBuffer_Release(&output);
    }
    PyBuffer_Release(&rows.view);
    return fp_finish(&floating,result);
}

static PyObject *
cosine_rows(PyObject *module, PyObject *args)
{
    PyObject *values, *query, *dtype; double eps;
    if (!PyArg_ParseTuple(args, "OOdO:cosine_rows", &values, &query, &eps, &dtype)) return NULL;
    State *state = PyModule_GetState(module);
    if (ready(state) < 0) return NULL;
    Array rows, vector; int ok = admit(state, values, 2, &rows, 1);
    if (ok < 0) return NULL;
    if (!ok) Py_RETURN_NOTIMPLEMENTED;
    if (!small_matrix(&rows)) { PyBuffer_Release(&rows.view); Py_RETURN_NOTIMPLEMENTED; }
    ok = admit(state, query, 1, &vector, 1);
    if (ok <= 0) { PyBuffer_Release(&rows.view); if (ok < 0) return NULL; Py_RETURN_NOTIMPLEMENTED; }
    int width = precision(dtype);
    if (width < 0 || !isfinite(eps) || eps < 0 || rows.view.shape[1] != vector.view.shape[0]) {
        if (width >= 0) invalid(!isfinite(eps) || eps < 0 ?
            "eps must be finite and nonnegative" : "query length must match the matrix width");
        PyBuffer_Release(&rows.view); PyBuffer_Release(&vector.view); return NULL;
    }
    Py_ssize_t n = rows.view.shape[0], columns = rows.view.shape[1];
    Floating floating;fp_begin(&floating);
    /* square accepts the first dimension as row for matrices; vector buffers
       use a zero row and column stride through this temporary matrix view. */
    Py_ssize_t shape[2] = {1, columns};
    Py_ssize_t strides[2] = {0, vector.view.strides[0]};
    Array matrix = vector;
    matrix.view.ndim = 2; matrix.view.shape = shape; matrix.view.strides = strides;
    double qnorm = width == 4 ? sqrtf((float)square(&matrix, 0, columns, width)) :
                               sqrt(square(&matrix, 0, columns, width));
    Py_buffer output;
    PyObject *result = allocate(state, n, -1, width, &output);
    if (result != NULL) {
        for (Py_ssize_t row = 0; row < n; row++) {
            if ((row & 63) == 0 && PyErr_CheckSignals() < 0) {
                Py_CLEAR(result); break;
            }
            double denominator, total;
            if (width == 4) {
                float a=0, b=0, c=0, d=0; Py_ssize_t column=0;
                for (; column+3 < columns; column+=4) {
                    a += (float)at(&rows,row,column)*(float)at(&matrix,0,column);
                    b += (float)at(&rows,row,column+1)*(float)at(&matrix,0,column+1);
                    c += (float)at(&rows,row,column+2)*(float)at(&matrix,0,column+2);
                    d += (float)at(&rows,row,column+3)*(float)at(&matrix,0,column+3);
                }
                float sum=(a+b)+(c+d);
                for (; column<columns; column++) sum += (float)at(&rows,row,column)*(float)at(&matrix,0,column);
                total=sum;
                float rn=sqrtf((float)square(&rows,row,columns,width))+(float)eps;
                /* Keep the selected float32 arithmetic before widening storage.
                   Its overflow must reach fp_finish and NumPy's error policy. */
                float product=rn*((float)qnorm+(float)eps);
                denominator=product;
            } else {
                double sum=0;
                for (Py_ssize_t column=0; column<columns; column++) sum += at(&rows,row,column)*at(&matrix,0,column);
                total=sum;
                denominator=(sqrt(square(&rows,row,columns,width))+eps)*(qnorm+eps);
            }
            store(output.buf,row,width,denominator==0 ? 0 : total/denominator);
        }
        PyBuffer_Release(&output);
    }
    PyBuffer_Release(&rows.view); PyBuffer_Release(&vector.view);
    return fp_finish(&floating,result);
}

/* An integer order key preserves values above 2^53 and signed minima. Floating
   keys retain normal numeric order and position breaks all ties, including ±0. */
typedef struct { double floating; uint64_t integer; Py_ssize_t position; } Score;

static int
better(const Score *a, const Score *b, int integers)
{
    if (integers) {
        if (a->integer != b->integer) return a->integer > b->integer;
    } else {
        if (a->floating != b->floating) return a->floating > b->floating;
    }
    return a->position < b->position;
}

static uint64_t
integer_word(const char *pointer, Py_ssize_t width)
{
    uint64_t word=0;
    if (width==1) { uint8_t value; memcpy(&value,pointer,1); word=value; }
    else if (width==2) { uint16_t value; memcpy(&value,pointer,2); word=value; }
    else if (width==4) { uint32_t value; memcpy(&value,pointer,4); word=value; }
    else memcpy(&word,pointer,8);
    return word;
}

static uint64_t
integer_key(const Array *array, Py_ssize_t position)
{
    const char *pointer=(const char *)array->view.buf+position*array->view.strides[0];
    uint64_t word=integer_word(pointer,array->view.itemsize);
    if (strchr("bhilqn",array->code) != NULL)
        word ^= UINT64_C(1) << (array->view.itemsize*8-1);
    return word;
}

static int
within_tolerance(State *state, PyObject *object, uint64_t *value)
{
    if (PyLong_CheckExact(object)) {
        *value=PyLong_AsUnsignedLongLong(object);
        if (!PyErr_Occurred()) return 1;
        if (!PyErr_ExceptionMatches(PyExc_OverflowError)) return -1;
        PyErr_Clear(); return 0;
    }
    Array scalar;int ok=admit(state,object,-1,&scalar,0);
    if(ok<=0)return ok;
    if(scalar.view.ndim!=0 || scalar.code=='f' || scalar.code=='d') {
        PyBuffer_Release(&scalar.view);return 0;
    }
    uint64_t word=integer_word(scalar.view.buf,scalar.view.itemsize);
    int negative=strchr("bhilqn",scalar.code)!=NULL &&
                 (word & (UINT64_C(1) << (scalar.view.itemsize*8-1)))!=0;
    PyBuffer_Release(&scalar.view);
    if(negative)return 0;
    *value=word;return 1;
}

static PyObject *
within(PyObject *module, PyObject *args)
{
    PyObject *actual,*expected,*atol;
    if(!PyArg_ParseTuple(args,"OOO:within",&actual,&expected,&atol))return NULL;
    State *state=PyModule_GetState(module);
    if(ready(state)<0)return NULL;
    uint64_t tolerance;int ok=within_tolerance(state,atol,&tolerance);
    if(ok<0)return NULL;
    if(!ok)Py_RETURN_NOTIMPLEMENTED;
    Array left,right;ok=admit(state,actual,-1,&left,0);
    if(ok<0)return NULL;
    if(!ok)Py_RETURN_NOTIMPLEMENTED;
    ok=admit(state,expected,-1,&right,0);
    if(ok<=0){PyBuffer_Release(&left.view);if(ok<0)return NULL;Py_RETURN_NOTIMPLEMENTED;}
    PyObject *result=NULL;
    int left_signed=strchr("bhilqn",left.code)!=NULL;
    int right_signed=strchr("bhilqn",right.code)!=NULL;
    if(left.view.itemsize!=8 || right.view.itemsize!=8 ||
       left.code=='f' || left.code=='d' || right.code=='f' || right.code=='d' ||
       left_signed!=right_signed || left.view.ndim!=right.view.ndim ||
       !PyBuffer_IsContiguous(&left.view,'C') ||
       !PyBuffer_IsContiguous(&right.view,'C'))goto declined;
    for(int axis=0;axis<left.view.ndim;axis++)
        if(left.view.shape[axis]!=right.view.shape[axis])goto declined;
    if(left.view.len!=right.view.len || left.view.len%8!=0)goto declined;
    Py_ssize_t n=left.view.len/8;
    const char *a=left.view.buf,*b=right.view.buf;
    uint64_t sign=left_signed ? UINT64_C(1)<<63 : 0;
    for(Py_ssize_t start=0;start<n;) {
        if(PyErr_CheckSignals()<0)goto finished;
        Py_ssize_t count=n-start;
        if(count>65536)count=65536;
        unsigned bad=0;
        if(tolerance==0)bad=memcmp(a+start*8,b+start*8,(size_t)count*8)!=0;
        else for(Py_ssize_t i=0;i<count;i++) {
            uint64_t x,y;
            memcpy(&x,a+(start+i)*8,8);memcpy(&y,b+(start+i)*8,8);
            x^=sign;y^=sign;
            uint64_t distance=x>y ? x-y : y-x;
            bad|=distance>tolerance;
        }
        if(bad){result=Py_NewRef(Py_False);goto finished;}
        start+=count;
    }
    result=Py_NewRef(Py_True);goto finished;
declined:
    result=Py_NewRef(Py_NotImplemented);
finished:
    PyBuffer_Release(&right.view);PyBuffer_Release(&left.view);return result;
}

static void
sift(Score *heap, Py_ssize_t size, Py_ssize_t root, int integers)
{
    /* Worst entry at the root, so only better scores enter the selected heap. */
    for (;;) {
        Py_ssize_t child=root*2+1;
        if (child>=size) break;
        if (child+1<size && better(&heap[child],&heap[child+1],integers)) child++;
        if (!better(&heap[root],&heap[child],integers)) break;
        Score temporary=heap[root]; heap[root]=heap[child]; heap[child]=temporary;
        root=child;
    }
}

static PyObject *
topk(PyObject *module, PyObject *args)
{
    PyObject *values,*count_object;
    if (!PyArg_ParseTuple(args,"OO:topk",&values,&count_object)) return NULL;
    State *state=PyModule_GetState(module);
    if (ready(state)<0) return NULL;
    Py_ssize_t k; int checked=count(count_object,"k must be nonnegative",&k);
    if (checked<0) return NULL;
    if (!checked) Py_RETURN_NOTIMPLEMENTED;
    Array array; int ok=admit(state,values,1,&array,0);
    if (ok<0) return NULL;
    if (!ok) Py_RETURN_NOTIMPLEMENTED;
    Py_ssize_t n=array.view.shape[0];
    if (k>n) { PyBuffer_Release(&array.view); invalid("k exceeds the number of values"); return NULL; }
    if (n >= 32 && k > n/4) {
        if (array.code == 'f' || array.code == 'd') for (Py_ssize_t i=0; i<n; i++) {
            if ((i & 65535) == 0 && PyErr_CheckSignals() < 0) {
                PyBuffer_Release(&array.view); return NULL;
            }
            if (isnan(at(&array,i,0))) {
                PyBuffer_Release(&array.view);
                invalid("topk does not accept NaN scores"); return NULL;
            }
        }
        PyObject *keys=PyObject_CallOneArg(array.code=='f'||array.code=='d' ?
                                          state->negative : state->invert, values);
        PyBuffer_Release(&array.view);
        if (keys==NULL) return NULL;
        PyObject *arguments[3]={keys,state->minus_one,state->stable};
        PyObject *sorted=PyObject_Vectorcall(state->argsort,arguments,3,NULL);
        Py_DECREF(keys);
        if (sorted==NULL || k==n) return sorted;
        PyObject *prefix=PySequence_GetSlice(sorted,0,k);Py_DECREF(sorted);return prefix;
    }
    if ((size_t)k > SIZE_MAX/sizeof(Score)) { PyBuffer_Release(&array.view); return PyErr_NoMemory(); }
    Score *heap=k ? PyMem_Malloc((size_t)k*sizeof(Score)) : NULL;
    if (k && heap==NULL) { PyBuffer_Release(&array.view); return PyErr_NoMemory(); }
    int integers=array.code!='f' && array.code!='d';
    for (Py_ssize_t i=0;i<n;i++) {
        if ((i & 65535) == 0 && PyErr_CheckSignals() < 0) {
            PyMem_Free(heap); PyBuffer_Release(&array.view); return NULL;
        }
        Score entry; entry.position=i;
        if (integers) entry.integer=integer_key(&array,i);
        else {
            entry.floating=at(&array,i,0);
            if (isnan(entry.floating)) {
                PyMem_Free(heap); PyBuffer_Release(&array.view);
                invalid("topk does not accept NaN scores"); return NULL;
            }
        }
        if (!k) continue;
        if (i<k) {
            heap[i]=entry;
            if (i==k-1) for (Py_ssize_t parent=k/2;parent>0;parent--) sift(heap,k,parent-1,integers);
        } else if (better(&entry,&heap[0],integers)) {
            heap[0]=entry; sift(heap,k,0,integers);
        }
    }
    Py_buffer output; PyObject *result=allocate(state,k,-1,0,&output);
    if (result!=NULL) {
        /* Heap extraction visits worst to best. Fill backwards for best-first. */
        for (Py_ssize_t size=k;size>0;size--) {
            if ((size & 65535) == 0 && PyErr_CheckSignals() < 0) {
                Py_CLEAR(result); break;
            }
            ((Py_ssize_t *)output.buf)[size-1]=heap[0].position;
            heap[0]=heap[size-1]; sift(heap,size-1,0,integers);
        }
        PyBuffer_Release(&output);
    }
    PyMem_Free(heap); PyBuffer_Release(&array.view);
    return result;
}

static PyObject *
weighted_mean(PyObject *module, PyObject *args)
{
    PyObject *values,*weights,*dtype;
    if (!PyArg_ParseTuple(args,"OOO:weighted_mean",&values,&weights,&dtype)) return NULL;
    State *state=PyModule_GetState(module);
    if (ready(state)<0) return NULL;
    Array rows,weight; int ok=admit(state,values,2,&rows,1);
    if (ok<0) return NULL;
    if (!ok) Py_RETURN_NOTIMPLEMENTED;
    if (!small_matrix(&rows)) { PyBuffer_Release(&rows.view); Py_RETURN_NOTIMPLEMENTED; }
    ok=admit(state,weights,1,&weight,1);
    if (ok<=0) { PyBuffer_Release(&rows.view); if(ok<0)return NULL; Py_RETURN_NOTIMPLEMENTED; }
    int width=precision(dtype);
    if (width<0 || rows.view.shape[0]!=weight.view.shape[0]) {
        if(width>=0)invalid("one weight is required per matrix row");
        PyBuffer_Release(&rows.view);PyBuffer_Release(&weight.view);return NULL;
    }
    Py_ssize_t n=rows.view.shape[0],columns=rows.view.shape[1];
    Floating floating;fp_begin(&floating);
    double total=0; float total32=0;
    for(Py_ssize_t row=0;row<n;row++) {
        if ((row & 65535) == 0 && PyErr_CheckSignals() < 0) {
            PyBuffer_Release(&rows.view); PyBuffer_Release(&weight.view);
            return fp_finish(&floating,NULL);
        }
        double value=at(&weight,row,0);
        if(!isfinite(value)||value<0) {total=nan("");break;}
        if(width==4)total32+=(float)value; else total+=value;
    }
    if(width==4 && !isnan(total))total=total32;
    if(!isfinite(total)||total<=0) {
        int occurred=fp_end(&floating);
        PyBuffer_Release(&rows.view);PyBuffer_Release(&weight.view);
        if(occurred)Py_RETURN_NOTIMPLEMENTED;
        invalid("weights must be finite and nonnegative with a finite positive total");return NULL;
    }
    Py_buffer output;PyObject *result=allocate(state,columns,-1,width,&output);
    if(result!=NULL) {
        memset(output.buf,0,(size_t)output.len);
        for(Py_ssize_t row=0;row<n;row++) {
            if ((row & 63) == 0 && PyErr_CheckSignals() < 0) {
                Py_CLEAR(result); break;
            }
            double w=at(&weight,row,0);
            for(Py_ssize_t column=0;column<columns;column++) {
                if(width==4)((float *)output.buf)[column]+=(float)at(&rows,row,column)*(float)w;
                else ((double *)output.buf)[column]+=at(&rows,row,column)*w;
            }
        }
        if (result != NULL) for(Py_ssize_t column=0;column<columns;column++)
            store(output.buf,column,width,number((const char *)output.buf+column*width,width==4?'f':'d',width)/total);
        PyBuffer_Release(&output);
    }
    PyBuffer_Release(&rows.view);PyBuffer_Release(&weight.view);
    return fp_finish(&floating,result);
}

static int
label_at(const Array *labels,Py_ssize_t row,Py_ssize_t groups,Py_ssize_t *result)
{
    uint64_t key=integer_key(labels,row);
    if(strchr("bhilqn",labels->code)!=NULL) {
        uint64_t sign=UINT64_C(1)<<(labels->view.itemsize*8-1);
        if(key<sign)return invalid("labels must be in [0, groups)");
        key^=sign;
    }
    if(key>=(uint64_t)groups)return invalid("labels must be in [0, groups)");
    *result=(Py_ssize_t)key;return 0;
}

static PyObject *
grouped_mean(PyObject *module,PyObject *args)
{
    PyObject *values,*labels_object,*groups_object;
    if(!PyArg_ParseTuple(args,"OOO:grouped_mean",&values,&labels_object,&groups_object))return NULL;
    State *state=PyModule_GetState(module);
    if(ready(state)<0)return NULL;
    Py_ssize_t groups;int checked=count(groups_object,"groups must be nonnegative",&groups);
    if(checked<0)return NULL;
    if(!checked)Py_RETURN_NOTIMPLEMENTED;
    Array rows,labels;int ok=admit(state,values,2,&rows,1);
    if(ok<0)return NULL;
    if(!ok)Py_RETURN_NOTIMPLEMENTED;
    ok=admit(state,labels_object,1,&labels,0);
    if(ok<=0){PyBuffer_Release(&rows.view);if(ok<0)return NULL;Py_RETURN_NOTIMPLEMENTED;}
    if ((labels.code=='f'||labels.code=='d') && labels.view.shape[0] != 0) {
        PyBuffer_Release(&rows.view);PyBuffer_Release(&labels.view);
        PyErr_SetString(PyExc_TypeError,"labels must have an integer dtype");return NULL;
    }
    Py_ssize_t n=rows.view.shape[0],columns=rows.view.shape[1];
    if(n!=labels.view.shape[0]) {
        PyBuffer_Release(&rows.view);PyBuffer_Release(&labels.view);
        invalid("one label is required per matrix row");return NULL;
    }
    if((size_t)groups>SIZE_MAX/sizeof(Py_ssize_t)) {
        PyBuffer_Release(&rows.view);PyBuffer_Release(&labels.view);return PyErr_NoMemory();
    }
    Py_ssize_t *counts=PyMem_Calloc((size_t)(groups?groups:1),sizeof(Py_ssize_t));
    if(counts==NULL){PyBuffer_Release(&rows.view);PyBuffer_Release(&labels.view);return PyErr_NoMemory();}
    Floating floating;fp_begin(&floating);
    Py_buffer output;PyObject *result=allocate(state,groups,columns,8,&output);
    if(result!=NULL) {
        memset(output.buf,0,(size_t)output.len);
        for(Py_ssize_t row=0;row<n;row++) {
            if ((row & 63) == 0 && PyErr_CheckSignals() < 0) {
                PyBuffer_Release(&output); Py_CLEAR(result); break;
            }
            Py_ssize_t label;
            if(label_at(&labels,row,groups,&label)<0){PyBuffer_Release(&output);Py_CLEAR(result);break;}
            counts[label]++;
            for(Py_ssize_t column=0;column<columns;column++)
                ((double *)output.buf)[label*columns+column]+=at(&rows,row,column);
        }
        if(result!=NULL) {
            for(Py_ssize_t group=0;group<groups;group++)if(counts[group])
                for(Py_ssize_t column=0;column<columns;column++)
                    ((double *)output.buf)[group*columns+column]/=counts[group];
            PyBuffer_Release(&output);
        }
    }
    PyMem_Free(counts);PyBuffer_Release(&rows.view);PyBuffer_Release(&labels.view);
    return fp_finish(&floating,result);
}

static PyObject *
frame(PyObject *module,PyObject *args)
{
    PyObject *values,*length_object,*hop_object;
    if(!PyArg_ParseTuple(args,"OOO:frame",&values,&length_object,&hop_object))return NULL;
    State *state=PyModule_GetState(module);
    if(ready(state)<0)return NULL;
    Py_ssize_t length,hop;int checked=count(length_object,"length must be positive",&length);
    if(checked<0)return NULL;
    if(!checked)Py_RETURN_NOTIMPLEMENTED;
    checked=count(hop_object,"hop must be positive",&hop);
    if(checked<0)return NULL;
    if(!checked)Py_RETURN_NOTIMPLEMENTED;
    if(length==0||hop==0){invalid("length and hop must be positive");return NULL;}
    Array samples;int ok=admit(state,values,1,&samples,0);
    if(ok<0)return NULL;
    if(!ok)Py_RETURN_NOTIMPLEMENTED;
    if(!PyBuffer_IsContiguous(&samples.view,'C') || hop>PY_SSIZE_T_MAX/samples.view.itemsize) {
        PyBuffer_Release(&samples.view);Py_RETURN_NOTIMPLEMENTED;
    }
    PyObject *dtype=PyObject_GetAttrString(values,"dtype");
    if(dtype==NULL){PyBuffer_Release(&samples.view);return NULL;}
    Py_ssize_t n=samples.view.shape[0];
    Py_ssize_t rows=n<length?0:1+(n-length)/hop;
    PyObject *shape=Py_BuildValue("(nn)",rows,length);
    if(shape==NULL){Py_DECREF(dtype);PyBuffer_Release(&samples.view);return NULL;}
    PyObject *result;
    if(n<length) {
        PyObject *arguments[2]={shape,dtype};
        result=PyObject_Vectorcall(state->empty,arguments,2,NULL);
    } else {
        PyObject *strides=Py_BuildValue("(nn)",hop*samples.view.itemsize,samples.view.itemsize);
        PyObject *zero=PyLong_FromLong(0);
        if(strides==NULL||zero==NULL){
            Py_XDECREF(strides);Py_XDECREF(zero);Py_DECREF(shape);Py_DECREF(dtype);
            PyBuffer_Release(&samples.view);return NULL;
        }
        Readonly *owner=(Readonly *)((PyTypeObject *)state->readonly_type)->tp_alloc(
            (PyTypeObject *)state->readonly_type,0);
        if(owner==NULL){
            Py_DECREF(strides);Py_DECREF(zero);Py_DECREF(shape);Py_DECREF(dtype);
            PyBuffer_Release(&samples.view);return NULL;
        }
        owner->source=samples.view;
        memset(&samples.view,0,sizeof(samples.view));
        PyObject *arguments[5]={shape,dtype,(PyObject *)owner,zero,strides};
        result=PyObject_Vectorcall(state->ndarray,arguments,5,NULL);
        Py_DECREF(owner);Py_DECREF(strides);Py_DECREF(zero);
    }
    Py_DECREF(shape);Py_DECREF(dtype);PyBuffer_Release(&samples.view);
    if(result==NULL)return NULL;
    PyObject *arguments[2]={result,Py_False};
    PyObject *flags=PyObject_Vectorcall(state->setflags,arguments,2,NULL);
    if(flags==NULL){Py_DECREF(result);return NULL;}
    Py_DECREF(flags);return result;
}

static int
traverse(PyObject *module,visitproc visit,void *arg)
{
    State *state=PyModule_GetState(module);
    Py_VISIT(state->ndarray);Py_VISIT(state->empty);Py_VISIT(state->f32);
    Py_VISIT(state->f64);Py_VISIT(state->intp);Py_VISIT(state->negative);
    Py_VISIT(state->invert);Py_VISIT(state->argsort);Py_VISIT(state->stable);
    Py_VISIT(state->minus_one);Py_VISIT(state->setflags);Py_VISIT(state->readonly_type);return 0;
}

static int
clear(PyObject *module)
{
    State *state=PyModule_GetState(module);
    Py_CLEAR(state->ndarray);Py_CLEAR(state->empty);Py_CLEAR(state->f32);
    Py_CLEAR(state->f64);Py_CLEAR(state->intp);Py_CLEAR(state->negative);
    Py_CLEAR(state->invert);Py_CLEAR(state->argsort);Py_CLEAR(state->stable);
    Py_CLEAR(state->minus_one);Py_CLEAR(state->setflags);Py_CLEAR(state->readonly_type);return 0;
}

static int
execute(PyObject *module)
{
    State *state=PyModule_GetState(module);
    state->readonly_type=PyType_FromSpec(&readonly_spec);
    if(state->readonly_type==NULL)return -1;
    return PyModule_AddIntConstant(module,"KERNEL_VERSION",2);
}

static PyMethodDef methods[]={
    {"within",within,METH_VARARGS,"Exact contiguous 64-bit integer distances, or NotImplemented."},
    {"topk",topk,METH_VARARGS,"Stable descending top-k indices, or NotImplemented."},
    {"frame",frame,METH_VARARGS,"Read-only complete frame views, or NotImplemented."},
    {"normalize_rows",normalize_rows,METH_VARARGS,"Row L2 normalization, or NotImplemented."},
    {"cosine_rows",cosine_rows,METH_VARARGS,"Cosines against matrix rows, or NotImplemented."},
    {"mean_square",mean_square,METH_VARARGS,"Row mean-square, or NotImplemented."},
    {"weighted_mean",weighted_mean,METH_VARARGS,"Weighted row mean, or NotImplemented."},
    {"grouped_mean",grouped_mean,METH_VARARGS,"Dense grouped means, or NotImplemented."},
    {NULL,NULL,0,NULL}
};

static PyModuleDef_Slot slots[]={
    {Py_mod_exec,execute},
#if PY_VERSION_HEX >= 0x030C0000
    {Py_mod_multiple_interpreters,Py_MOD_PER_INTERPRETER_GIL_SUPPORTED},
#endif
#if PY_VERSION_HEX >= 0x030D0000
    {Py_mod_gil,Py_MOD_GIL_USED},
#endif
    {0,NULL}
};

static struct PyModuleDef definition={
    PyModuleDef_HEAD_INIT,"_math_native","Portable ndarray specializations.",
    sizeof(State),methods,slots,traverse,clear,NULL
};

PyMODINIT_FUNC PyInit__math_native(void){return PyModuleDef_Init(&definition);}
