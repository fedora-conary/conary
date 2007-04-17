/*
 *
 * Copyright (c) 2004 rPath, Inc.
 *
 * This program is distributed under the terms of the Common Public License,
 * version 1.0. A copy of this license should have been distributed with this
 * source file in a file called LICENSE. If it is not present, the license
 * is always available at http://www.opensource.org/licenses/cpl.php.
 *
 * This program is distributed in the hope that it will be useful, but
 * without any warranty; without even the implied warranty of merchantability
 * or fitness for a particular purpose. See the Common Public License for
 * full details.
 *
 */

#include <Python.h>

#include <ctype.h>
#include <errno.h>
#include <malloc.h>
#include <netinet/in.h>
#include <sys/stat.h>

/* debugging aid */
#if defined(__i386__) || defined(__x86_64__)
# define breakpoint do {__asm__ __volatile__ ("int $03");} while (0)
#endif

static PyObject * depSetSplit(PyObject *self, PyObject *args);
static PyObject * depSplit(PyObject *self, PyObject *args);
static PyObject * exists(PyObject *self, PyObject *args);
static PyObject * malloced(PyObject *self, PyObject *args);
static PyObject * removeIfExists(PyObject *self, PyObject *args);
static PyObject * unpack(PyObject *self, PyObject *args);
static PyObject * py_pread(PyObject *self, PyObject *args);

static PyMethodDef MiscMethods[] = {
    { "depSetSplit", depSetSplit, METH_VARARGS },
    { "depSplit", depSplit, METH_VARARGS },
    { "exists", exists, METH_VARARGS,
        "returns a boolean reflecting whether a file (even a broken symlink) "
        "exists in the filesystem" },
    { "malloced", malloced, METH_VARARGS, 
	"amount of memory currently allocated through malloc()" },
    { "removeIfExists", removeIfExists, METH_VARARGS, 
	"unlinks a file if it exists; silently fails if it does not exist. "
	"returns a boolean indicating whether or not a file was removed" },
    { "unpack", unpack, METH_VARARGS },
    { "pread", py_pread, METH_VARARGS },
    {NULL}  /* Sentinel */
};

static PyObject * malloced(PyObject *self, PyObject *args) {
    struct mallinfo ma;

    ma = mallinfo();

    /* worked */
    return Py_BuildValue("i", ma.uordblks);
}

static PyObject * depSetSplit(PyObject *self, PyObject *args) {
    char * data, * dataPtr, * endPtr;
    int offset, tag;
    PyObject * retVal;
    PyObject * offsetArg, * dataArg;

    /* This avoids PyArg_ParseTuple because it's sloooow */
    if (PyTuple_GET_SIZE(args) != 2) {
        PyErr_SetString(PyExc_TypeError, "exactly two arguments expected");
        return NULL;
    }

    offsetArg = PyTuple_GET_ITEM(args, 0);
    dataArg = PyTuple_GET_ITEM(args, 1);

    if (!PyInt_CheckExact(offsetArg)) {
        PyErr_SetString(PyExc_TypeError, "first argument must be an int");
        return NULL;
    } else if (!PyString_CheckExact(dataArg)) {
        PyErr_SetString(PyExc_TypeError, "second argument must be a string");
        return NULL;
    }

    offset = PyInt_AS_LONG(offsetArg);
    data = PyString_AS_STRING(dataArg);

    dataPtr = data + offset;
    /* this while is a cheap goto for the error case */
    while (*dataPtr) {
        endPtr = dataPtr;

        tag = 0;
        /* Grab the tag first. Go ahead an convert it to an int while we're
           grabbing it. */
        while (*endPtr && *endPtr != '#') {
            tag *= 10;
            tag += *endPtr - '0';
            endPtr++;
        }
        dataPtr = endPtr + 1;

        /* Now look for the frozen dependency */
        /* Grab the tag first */
        while (*endPtr && *endPtr != '|')
            endPtr++;

        retVal = Py_BuildValue("iis#", endPtr - data + 1, tag, dataPtr,
                                endPtr - dataPtr);
        return retVal;
    }

    PyErr_SetString(PyExc_ValueError, "invalid frozen dependency");
    return NULL;
}

static PyObject * depSplit(PyObject *self, PyObject *args) {
    char * origData, * data, * chptr, * endPtr;
    PyObject * flags, * flag, * name, * ret, * dataArg;

    /* This avoids PyArg_ParseTuple because it's sloooow */
    if (PyTuple_GET_SIZE(args) != 1) {
        PyErr_SetString(PyExc_TypeError, "exactly one argument expected");
        return NULL;
    }

    dataArg = PyTuple_GET_ITEM(args, 0);

    if (!PyString_CheckExact(dataArg)) {
        PyErr_SetString(PyExc_TypeError, "first argument must be a string");
        return NULL;
    }

    origData = PyString_AS_STRING(dataArg);

    /* Copy the original string over, replace single : with a '\0' and
       double :: with a single : */
    endPtr = data = malloc(strlen(origData) + 1);
    chptr = origData;
    while (*chptr) {
        if (*chptr == ':') {
            chptr++;
            if (*chptr == ':') {
                *endPtr++ = ':';
                chptr++;
            } else {
                *endPtr++ = '\0';
            }
        } else { 
            *endPtr++ = *chptr++;
        }
    }

    *endPtr++ = '\0';

    /* We're left with a '\0' separated list of name, flag1, ..., flagN. Get
       the name first. */
    name = PyString_FromString(data);
    chptr = data + strlen(data) + 1;

    flags = PyList_New(0);

    while (chptr < endPtr) {
        flag = PyString_FromString(chptr);
        PyList_Append(flags, flag);
        Py_DECREF(flag);
        chptr += strlen(chptr) + 1;
    }

    ret = PyTuple_Pack(2, name, flags);
    Py_DECREF(name);
    Py_DECREF(flags);
    free(data);
    return ret;
}

static PyObject * exists(PyObject *self, PyObject *args) {
    char * fn;
    struct stat sb;

    if (!PyArg_ParseTuple(args, "s", &fn))
        return NULL;

    if (lstat(fn, &sb)) {
        if (errno == ENOENT || errno == ENOTDIR || errno == ENAMETOOLONG) {
            Py_INCREF(Py_False);
            return Py_False;
        }

        PyErr_SetFromErrnoWithFilename(PyExc_OSError, fn);
        return NULL;
    }

    Py_INCREF(Py_True);
    return Py_True;
}

static PyObject * removeIfExists(PyObject *self, PyObject *args) {
    char * fn;

    if (!PyArg_ParseTuple(args, "s", &fn))
        return NULL;

    if (unlink(fn)) {
        if (errno == ENOENT || errno == ENAMETOOLONG) {
            Py_INCREF(Py_False);
            return Py_False;
        }

        PyErr_SetFromErrnoWithFilename(PyExc_OSError, fn);
        return NULL;
    }

    Py_INCREF(Py_True);
    return Py_True;
}

static PyObject * unpack(PyObject *self, PyObject *args) {
    char * data, * format;
    int dataLen;
    char * dataPtr, * formatPtr;
    int offset;
    PyObject * retList, * dataObj;
    int intVal;
    PyObject * formatArg, * offsetArg, * dataArg, * retVal;

    /* This avoids PyArg_ParseTuple because it's sloooow */
    if (PyTuple_GET_SIZE(args) != 3) {
        PyErr_SetString(PyExc_TypeError, "exactly three arguments expected");
        return NULL;
    }

    formatArg = PyTuple_GET_ITEM(args, 0);
    offsetArg = PyTuple_GET_ITEM(args, 1);
    dataArg = PyTuple_GET_ITEM(args, 2);

    if (!PyString_CheckExact(formatArg)) {
        PyErr_SetString(PyExc_TypeError, "first argument must be a string");
        return NULL;
    } else if (!PyInt_CheckExact(offsetArg)) {
        PyErr_SetString(PyExc_TypeError, "second argument must be an int");
        return NULL;
    } else if (!PyString_CheckExact(dataArg)) {
        PyErr_SetString(PyExc_TypeError, "third argument must be a string");
        return NULL;
    }

    format = PyString_AS_STRING(formatArg);
    offset = PyInt_AS_LONG(offsetArg);
    data = PyString_AS_STRING(dataArg);
    dataLen = PyString_GET_SIZE(dataArg);

    formatPtr = format;

    if (*formatPtr != '!') {
        PyErr_SetString(PyExc_ValueError, "format must begin with !");
        return NULL;
    }
    formatPtr++;

    retList = PyList_New(0);
    dataPtr = data + offset;

    while (*formatPtr) {
        switch (*formatPtr) {
          case 'B':
            intVal = (int) *dataPtr++;
            dataObj = PyInt_FromLong(intVal);
            PyList_Append(retList, dataObj);
            Py_DECREF(dataObj);
            formatPtr++;
            break;

          case 'H':
            intVal = ntohs(*((short *) dataPtr));
            dataObj = PyInt_FromLong(intVal);
            PyList_Append(retList, dataObj);
            Py_DECREF(dataObj);
            dataPtr += 2;
            formatPtr++;
            break;

          case 'S':
            /* extension -- extract a string based on the length which
               preceeds it */
            formatPtr++;

            if (*formatPtr == 'H') {
                intVal = ntohs(*((short *) dataPtr));
                dataPtr += 2;
                formatPtr++;
            } else if (*formatPtr == 'I') {
                intVal = ntohl(*((int *) dataPtr));
                dataPtr += 4;
                formatPtr++;
            } else if (isdigit(*formatPtr)) {
                char lenStr[10];
                char * lenPtr = lenStr;

                /* '\0' isn't a digit, so this check stops at the end */
                while (isdigit(*formatPtr) &&
                       (lenPtr - lenStr) < sizeof(lenStr))
                    *lenPtr++ = *formatPtr++;

                if ((lenPtr - lenStr) == sizeof(lenStr)) {
                    Py_DECREF(retList);
                    PyErr_SetString(PyExc_ValueError, 
                                    "length too long for S format");
                    return NULL;
                }

                *lenPtr = '\0';

                intVal = atoi(lenStr);
            } else {
                Py_DECREF(retList);
                PyErr_SetString(PyExc_ValueError, 
                                "# must be followed by H or I in format");
                return NULL;
            }

            dataObj = PyString_FromStringAndSize(dataPtr, intVal);
            PyList_Append(retList, dataObj);
            Py_DECREF(dataObj);
            dataPtr += intVal;
            break;

          default:
            Py_DECREF(retList);
            PyErr_SetString(PyExc_ValueError, "unknown character in format");
            return NULL;
        }
    }

    retVal = Py_BuildValue("iO", dataPtr - data, retList);
    Py_DECREF(retList);

    return retVal;
}

static PyObject * py_pread(PyObject *self, PyObject *args) {
    void * data;
    int fd;
    size_t size;
    off_t offset, rc;
    PyObject *pysize, *pyfd, *pyoffset, *buf;

    if (PyTuple_GET_SIZE(args) != 3) {
        PyErr_SetString(PyExc_TypeError, "exactly three arguments expected");
        return NULL;
    }

    pyfd = PyTuple_GET_ITEM(args, 0);
    pysize = PyTuple_GET_ITEM(args, 1);
    pyoffset = PyTuple_GET_ITEM(args, 2);

    if (!PyInt_CheckExact(pyfd)) {
        PyErr_SetString(PyExc_TypeError, "first argument must be an int");
        return NULL;
    } else if (!PyInt_CheckExact(pysize)) {
        PyErr_SetString(PyExc_TypeError, "second argument must be an int");
        return NULL;
    } else if (!PyInt_CheckExact(pyoffset)) {
        PyErr_SetString(PyExc_TypeError, "third argument must be an int");
        return NULL;
    }

    fd = PyInt_AS_LONG(pyfd);
    offset = PyInt_AS_LONG(pyoffset);
    size = PyInt_AS_LONG(pysize);

    data = malloc(size);

    if (NULL == data) {
	PyErr_NoMemory();
	return NULL;
    }

    rc = pread(fd, data, size, offset);
    if (-1 == rc) {
	free(data);
        PyErr_SetFromErrno(PyExc_OSError);
	return NULL;
    }

    buf = PyString_FromStringAndSize(data, rc);
    free(data);
    return buf;
}


PyMODINIT_FUNC
initmisc(void)
{
    Py_InitModule3("misc", MiscMethods, 
		   "miscelaneous low-level C functions for conary");
}
