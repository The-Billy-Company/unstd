/* Captured POSIX output exchange. Popen retains descriptor ownership and reaping.
 *
 * Current Billy proc callers inherit stdin. Their two captured output streams
 * can be drained together without Python selector, allocation or append calls
 * per pipe chunk. Nonempty stdin remains on stdlib's complete communicate path:
 * closing its Python stream at the right moment is part of that contract.
 */
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include "pipes.h"

#ifndef _WIN32
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <math.h>
#include <poll.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#ifdef __APPLE__
#include <mach/mach_time.h>
#endif

#define READ_CHUNK 65536

typedef struct {
    char *data;
    Py_ssize_t length;
    Py_ssize_t capacity;
    int observed;
    int closed;
} Output;

typedef struct {
#ifdef __APPLE__
    mach_timebase_info_data_t scale;
#else
    int unused;
#endif
} Clock;

static int
monotonic_now(double *result, const Clock *clock)
{
#ifdef __APPLE__
    /* CPython uses mach_absolute_time for time.monotonic on macOS. Its epoch
     * differs from CLOCK_MONOTONIC after suspend, so deadlines must use this
     * same clock rather than an otherwise monotonic POSIX alternative. */
    *result = (double)mach_absolute_time() * clock->scale.numer
        / clock->scale.denom / 1000000000.0;
#else
    (void)clock;
    struct timespec reading;
    if (clock_gettime(CLOCK_MONOTONIC, &reading) < 0) {
        return -1;
    }
    *result = (double)reading.tv_sec + (double)reading.tv_nsec / 1000000000.0;
#endif
    return 0;
}

static int
append(Output *output, const char *data, Py_ssize_t length)
{
    if (length > PY_SSIZE_T_MAX - output->length) {
        return -1;
    }
    Py_ssize_t wanted = output->length + length;
    if (wanted > output->capacity) {
        Py_ssize_t capacity = output->capacity ? output->capacity : READ_CHUNK;
        while (capacity < wanted) {
            capacity = capacity > PY_SSIZE_T_MAX / 2
                ? PY_SSIZE_T_MAX : capacity * 2;
        }
        char *grown = PyMem_RawRealloc(output->data, (size_t)capacity);
        if (grown == NULL) {
            return -1;
        }
        output->data = grown;
        output->capacity = capacity;
    }
    memcpy(output->data + output->length, data, (size_t)length);
    output->length = wanted;
    return 0;
}

static int
set_flags(int descriptor, int flags)
{
    int result;
    do {
        result = fcntl(descriptor, F_SETFL, flags);
    } while (result < 0 && errno == EINTR);
    return result;
}

static PyObject *
exchange(int outfd, int errfd, double deadline)
{
    int descriptors[2] = {outfd, errfd};
    int flags[2] = {-1, -1};
    Output outputs[2] = {{0}, {0}};
    int saved_error = 0;
    int memory_error = 0;
    int timed_out = 0;
    PyObject *result = NULL;
    char scratch[READ_CHUNK];
    Clock clock = {0};
#ifdef __APPLE__
    if (mach_timebase_info(&clock.scale) != KERN_SUCCESS || clock.scale.denom == 0) {
        errno = EINVAL;
        return PyErr_SetFromErrno(PyExc_OSError);
    }
#endif

    for (int index = 0; index < 2; index++) {
        do {
            flags[index] = fcntl(descriptors[index], F_GETFL);
        } while (flags[index] < 0 && errno == EINTR);
        if (flags[index] < 0
            || set_flags(descriptors[index], flags[index] | O_NONBLOCK) < 0) {
            saved_error = errno;
            goto done;
        }
    }

    while (!outputs[0].closed || !outputs[1].closed) {
        double now;
        if (monotonic_now(&now, &clock) < 0) {
            saved_error = errno;
            goto done;
        }
        if (now >= deadline) {
            timed_out = 1;
            break;
        }
        double milliseconds = (deadline - now) * 1000.0;
        int timeout = milliseconds >= INT_MAX ? INT_MAX : (int)milliseconds;
        if (timeout < milliseconds && timeout < INT_MAX) {
            timeout++;
        }
        struct pollfd pending[2];
        for (int index = 0; index < 2; index++) {
            pending[index].fd = outputs[index].closed ? -1 : descriptors[index];
            pending[index].events = POLLIN;
            pending[index].revents = 0;
        }
        int ready;
        int poll_error;
        Py_BEGIN_ALLOW_THREADS
        ready = poll(pending, 2, timeout);
        poll_error = errno;
        if (ready >= 0) {
            /* Match communicate's deadline check immediately after its selector.
             * A readable pipe does not waive an already expired timeout. */
            if (monotonic_now(&now, &clock) < 0) {
                saved_error = errno;
            }
            else if (now > deadline) {
                timed_out = 1;
            }
            for (int index = 0;
                 index < 2 && !saved_error && !memory_error && !timed_out;
                 index++) {
                if (!pending[index].revents) {
                    continue;
                }
                if (pending[index].revents & POLLNVAL) {
                    saved_error = EBADF;
                    break;
                }
                ssize_t received = read(descriptors[index], scratch, sizeof scratch);
                if (received < 0) {
                    if (errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR) {
                        saved_error = errno;
                    }
                    continue;
                }
                outputs[index].observed = 1;
                if (received == 0) {
                    outputs[index].closed = 1;
                }
                else if (append(&outputs[index], scratch, (Py_ssize_t)received) < 0) {
                    memory_error = 1;
                }
            }
        }
        Py_END_ALLOW_THREADS
        /* One readiness round handles at most one chunk from each stream,
         * bounding signal latency and preventing a busy stream starving its peer.
         * RAW buffer allocation is thread-safe without the GIL. */
        if (PyErr_CheckSignals() < 0 || saved_error || memory_error) {
            goto done;
        }
        if (ready < 0 && poll_error != EINTR) {
            saved_error = poll_error;
            goto done;
        }
        if (timed_out) {
            break;
        }
    }

    {
        PyObject *out = PyBytes_FromStringAndSize(outputs[0].data, outputs[0].length);
        if (out == NULL) {
            goto done;
        }
        PyObject *err = PyBytes_FromStringAndSize(outputs[1].data, outputs[1].length);
        if (err == NULL) {
            Py_DECREF(out);
            goto done;
        }
        result = PyTuple_New(7);
        if (result == NULL) {
            Py_DECREF(out);
            Py_DECREF(err);
            goto done;
        }
        PyTuple_SET_ITEM(result, 0, out);
        PyTuple_SET_ITEM(result, 1, err);
        PyTuple_SET_ITEM(result, 2, PyBool_FromLong(timed_out));
        PyTuple_SET_ITEM(result, 3, PyBool_FromLong(outputs[0].observed));
        PyTuple_SET_ITEM(result, 4, PyBool_FromLong(outputs[1].observed));
        PyTuple_SET_ITEM(result, 5, PyBool_FromLong(outputs[0].closed));
        PyTuple_SET_ITEM(result, 6, PyBool_FromLong(outputs[1].closed));
    }

done:
    for (int index = 0; index < 2; index++) {
        if (flags[index] >= 0 && set_flags(descriptors[index], flags[index]) < 0
            && saved_error == 0 && !PyErr_Occurred()) {
            saved_error = errno;
        }
        PyMem_RawFree(outputs[index].data);
    }
    if (PyErr_Occurred()) {
        Py_XDECREF(result);
        return NULL;
    }
    if (memory_error) {
        Py_XDECREF(result);
        return PyErr_NoMemory();
    }
    if (saved_error) {
        Py_XDECREF(result);
        errno = saved_error;
        return PyErr_SetFromErrno(PyExc_OSError);
    }
    return result;
}
#endif

PyObject *
proc_communicate(PyObject *module, PyObject *args)
{
    (void)module;
#ifdef _WIN32
    (void)args;
    Py_RETURN_NOTIMPLEMENTED;
#else
    int outfd, errfd;
    double deadline;
    if (!PyArg_ParseTuple(args, "iid:communicate", &outfd, &errfd, &deadline)) {
        return NULL;
    }
    if (outfd < 0 || errfd < 0 || outfd == errfd) {
        Py_RETURN_NOTIMPLEMENTED;
    }
    if (!isfinite(deadline)) {
        PyErr_SetString(PyExc_ValueError, "deadline must be finite");
        return NULL;
    }
    return exchange(outfd, errfd, deadline);
#endif
}
