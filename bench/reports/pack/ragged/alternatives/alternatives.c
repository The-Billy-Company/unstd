#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <Accelerate/Accelerate.h>
#include <arm_neon.h>
#include <dispatch/dispatch.h>
#include <fenv.h>
#include <math.h>
#include <stdint.h>
#include <sys/sysctl.h>
#include <string.h>
#include "../../native/pack/src/adapter.c"
#include "sme-check.c"

static int max_scan(const unsigned char *data,Py_ssize_t count,int little){
    uint32x4_t mask=vdupq_n_u32(0x7fffffffU),a0=vdupq_n_u32(0),a1=a0,a2=a0,a3=a0;
    if(little){
        for(;count>=16;count-=16,data+=64){
            a0=vmaxq_u32(a0,vandq_u32(vreinterpretq_u32_u8(vld1q_u8(data)),mask));
            a1=vmaxq_u32(a1,vandq_u32(vreinterpretq_u32_u8(vld1q_u8(data+16)),mask));
            a2=vmaxq_u32(a2,vandq_u32(vreinterpretq_u32_u8(vld1q_u8(data+32)),mask));
            a3=vmaxq_u32(a3,vandq_u32(vreinterpretq_u32_u8(vld1q_u8(data+48)),mask));
        }
    }else{
        for(;count>=16;count-=16,data+=64){
            a0=vmaxq_u32(a0,vandq_u32(vreinterpretq_u32_u8(vrev32q_u8(vld1q_u8(data))),mask));
            a1=vmaxq_u32(a1,vandq_u32(vreinterpretq_u32_u8(vrev32q_u8(vld1q_u8(data+16))),mask));
            a2=vmaxq_u32(a2,vandq_u32(vreinterpretq_u32_u8(vrev32q_u8(vld1q_u8(data+32))),mask));
            a3=vmaxq_u32(a3,vandq_u32(vreinterpretq_u32_u8(vrev32q_u8(vld1q_u8(data+48))),mask));
        }
    }
    a0=vmaxq_u32(vmaxq_u32(a0,a1),vmaxq_u32(a2,a3));
    for(;count>=4;count-=4,data+=16){
        uint8x16_t raw=vld1q_u8(data);if(!little)raw=vrev32q_u8(raw);
        a0=vmaxq_u32(a0,vandq_u32(vreinterpretq_u32_u8(raw),mask));
    }
    uint32_t maximum=vmaxvq_u32(a0);
    for(;count;count--,data+=4){uint32_t value=(uint32_t)word(data,4,little)&0x7fffffffU;if(value>maximum)maximum=value;}
    return maximum<0x7f800000U;
}
struct result_slot{int valid;unsigned char padding[124];};
struct job{const unsigned char *data;Py_ssize_t count;int little;int sme;size_t workers;struct result_slot results[64];};
static void work(void *context,size_t i){
    struct job *job=context;
    Py_ssize_t start=(job->count/(Py_ssize_t)job->workers)*(Py_ssize_t)i;
    Py_ssize_t end=i+1==job->workers?job->count:(job->count/(Py_ssize_t)job->workers)*(Py_ssize_t)(i+1);
    const unsigned char *data=job->data+start*4;
    if(job->sme && job->little && ((uintptr_t)data%4)==0)job->results[i].valid=invoke_sme((const uint32_t *)data,(size_t)(end-start));
    else job->results[i].valid=max_scan(data,end-start,job->little);
}
static int parallel_scan(const unsigned char *data,Py_ssize_t count,int little,size_t workers,int sme){
    struct job job={.data=data,.count=count,.little=little,.sme=sme,.workers=workers};
    dispatch_apply_f(workers,dispatch_get_global_queue(QOS_CLASS_USER_INITIATED,0),&job,work);
    for(size_t i=0;i<workers;i++)if(!job.results[i].valid)return 0;
    return 1;
}
static int sme_available;
static int has_sme(void){return sme_available;}
static int accelerated(const unsigned char *data,Py_ssize_t count,int little,int scaled){
    if(!little || ((uintptr_t)data%4)!=0)return max_scan(data,count,little);
    if(count==0)return 1;
    float result,scale=0x1p-126f;
    fenv_t environment;feholdexcept(&environment);fesetround(FE_TONEAREST);
    if(scaled)vDSP_dotpr((const float *)data,1,&scale,0,&result,(vDSP_Length)count);
    else vDSP_maxmgv((const float *)data,1,&result,(vDSP_Length)count);
    fesetenv(&environment);
    uint32_t bits;memcpy(&bits,&result,4);return (bits&0x7f800000U)!=0x7f800000U;
}
static PyObject *alternative_scan(PyObject *module,PyObject *args){
    (void)module;PyObject *data;Py_ssize_t offset;int little,workers=1;const char *method;
    if(!PyArg_ParseTuple(args,"Onps|i:scan",&data,&offset,&little,&method,&workers))return NULL;
    if(!PyBytes_CheckExact(data)){PyErr_SetString(PyExc_TypeError,"exact bytes required");return NULL;}
    Py_ssize_t length=PyBytes_GET_SIZE(data);
    if(offset<0||offset>length||(length-offset)%4||workers<1||workers>64){PyErr_SetString(PyExc_ValueError,"invalid scan window/workers");return NULL;}
    int kind=strcmp(method,"max")==0?0:strcmp(method,"parallel")==0?1:strcmp(method,"sme")==0?2:strcmp(method,"parallel_sme")==0?3:strcmp(method,"scaled_dot")==0?4:strcmp(method,"max_control")==0?5:strcmp(method,"reference")==0?6:-1;
    if(kind<0){PyErr_SetString(PyExc_ValueError,"unknown scan method");return NULL;}
    if((kind==2||kind==3)&&!has_sme()){PyErr_SetString(PyExc_NotImplementedError,"SME unavailable");return NULL;}
    const unsigned char *bytes=(const unsigned char *)PyBytes_AS_STRING(data)+offset;
    Py_ssize_t count=(length-offset)/4;int valid;
    Py_BEGIN_ALLOW_THREADS
    if(kind==0)valid=max_scan(bytes,count,little);
    else if(kind==1||kind==3)valid=parallel_scan(bytes,count,little,(size_t)workers,kind==3);
    else if(kind==2)valid=little&&((uintptr_t)bytes%4)==0?invoke_sme((const uint32_t *)bytes,(size_t)count):max_scan(bytes,count,little);
    else if(kind==4||kind==5)valid=accelerated(bytes,count,little,kind==4);
    else valid=finite_f32(bytes,count,little);
    Py_END_ALLOW_THREADS
    return PyBool_FromLong(valid);
}
static PyObject *alternative_metadata(PyObject *module,PyObject *unused){
    (void)module;(void)unused;
    return Py_BuildValue("{s:i,s:k}","sme",has_sme(),"streaming_f32_lanes",(unsigned long)(has_sme()?sme_lanes():0));
}
static PyMethodDef alternative_methods[]={{"scan",alternative_scan,METH_VARARGS,"Scratch exact scan mechanisms; max_control deliberately invalid for NaNs."},{"metadata",alternative_metadata,METH_NOARGS,"Hardware streaming metadata."},{NULL,NULL,0,NULL}};
static struct PyModuleDef alternative_definition={PyModuleDef_HEAD_INIT,"_scan_alternatives","Scratch finite scan alternatives.",0,alternative_methods,NULL,NULL,NULL,NULL};
PyMODINIT_FUNC PyInit__scan_alternatives(void){int value=0;size_t size=sizeof(value);sme_available=sysctlbyname("hw.optional.arm.FEAT_SME",&value,&size,NULL,0)==0&&value!=0;return PyModule_Create(&alternative_definition);}
