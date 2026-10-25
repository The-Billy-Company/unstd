#include <stddef.h>
#include <stdint.h>
#include <arm_sve.h>
#include <arm_sme.h>
__attribute__((target("sme")))
__arm_locally_streaming static int scan_sme(const uint32_t *data, size_t count) {
    svuint32_t m0=svdup_u32(0),m1=m0,m2=m0,m3=m0;
    svbool_t all=svptrue_b32();
    size_t i=0, lanes=svcntw(), block=4*lanes;
    for(; count-i>=block;i+=block){
        svuint32_t a=svand_n_u32_x(all,svld1_vnum_u32(all,data+i,0),0x7fffffffU);
        svuint32_t b=svand_n_u32_x(all,svld1_vnum_u32(all,data+i,1),0x7fffffffU);
        svuint32_t c=svand_n_u32_x(all,svld1_vnum_u32(all,data+i,2),0x7fffffffU);
        svuint32_t d=svand_n_u32_x(all,svld1_vnum_u32(all,data+i,3),0x7fffffffU);
        m0=svmax_u32_m(all,m0,a);m1=svmax_u32_m(all,m1,b);
        m2=svmax_u32_m(all,m2,c);m3=svmax_u32_m(all,m3,d);
    }
    m0=svmax_u32_m(all,svmax_u32_m(all,m0,m1),svmax_u32_m(all,m2,m3));
    for(;i<count;i+=lanes){
        svbool_t active=svwhilelt_b32((uint64_t)i,(uint64_t)count);
        svuint32_t bits=svand_n_u32_x(active,svld1_u32(active,data+i),0x7fffffffU);
        m0=svmax_u32_m(active,m0,bits);
    }
    return svmaxv_u32(all,m0)<0x7f800000U;
}
int invoke_sme(const uint32_t *data,size_t count){return scan_sme(data,count);}
__attribute__((target("sme")))
__arm_locally_streaming static size_t sme_width(void){return svcntw();}
size_t sme_lanes(void){return sme_width();}
