/* Synthetic BDIF block write/flush/readback tests, no Apple code. */
#include "runtime.h"
#ifndef TEST_CASE
#define TEST_CASE 0
#endif
#define DATA 0x72000000ULL
#define COPY 0x72010000ULL
#define REQ 0x72800000ULL
#define STATUS 0x72800100ULL
struct part {uint64_t addr;uint32_t len,flags;};
struct sector {uint32_t pad0,pad1,number,pad3;};
_Static_assert(sizeof(struct part)==16,"BDIF part");
#if TEST_CASE >= 9
#ifndef ROOT_BACKEND_SECTORS
#define ROOT_BACKEND_SECTORS 0
#endif
#ifndef AUX_BACKEND_SECTORS
#define AUX_BACKEND_SECTORS 0
#endif
static unsigned block_io(uint64_t base,uint32_t sector,unsigned length,unsigned write) {
    volatile struct part *p=(void *)(uintptr_t)REQ;
    volatile struct sector *s=(void *)(uintptr_t)(REQ+64);
    volatile unsigned char *ret=(void *)(uintptr_t)STATUS;
    *s=(struct sector){0,0,sector,0};
    p[0]=(struct part){REQ+64,16,0};
    p[1]=(struct part){write?DATA:COPY,length,write?0x00010001:0x00030001};
    p[2]=(struct part){STATUS,1,0};
    *ret=0xa5;dsb_sy();
    *(volatile uint64_t *)(uintptr_t)(base+0x408)=REQ;dsb_sy();
    return *ret;
}
static void tail_io(uint64_t base,uint32_t sectors,unsigned salt) {
    if(!sectors)return;
    volatile unsigned char *src=(void *)(uintptr_t)DATA;
    volatile unsigned char *dst=(void *)(uintptr_t)COPY;
    for(unsigned i=0;i<1024;i++){src[i]=(unsigned char)(i*17+salt);dst[i]=0xa6;}
    expect(block_io(base,sectors-1,512,1)==0,"TAIL_WRITE_FAILED");
    expect(block_io(base,sectors-1,512,0)==0,"TAIL_READ_FAILED");
    for(unsigned i=0;i<512;i++)expect(dst[i]==(unsigned char)(i*17+salt),"TAIL_DATA_MISMATCH");
    expect(block_io(base,sectors-1,1024,1)==1,"CROSS_END_WRITE_ACCEPTED");
    expect(block_io(base,sectors,512,1)==1,"BEYOND_END_WRITE_ACCEPTED");
    for(unsigned i=0;i<1024;i++)dst[i]=0xa6;
    expect(block_io(base,sectors-1,1024,0)==1,"CROSS_END_READ_ACCEPTED");
    expect(block_io(base,sectors,512,0)==1,"BEYOND_END_READ_ACCEPTED");
    for(unsigned i=0;i<1024;i++)expect(dst[i]==0xa6,"FAILED_READ_MODIFIED_DMA");
}
static void capacity_test(void) {
    uint64_t root32=mmio32(0x30000420ULL),aux32=mmio32(0x30010420ULL);
    uint64_t root64=*(volatile uint64_t *)(uintptr_t)0x30000420ULL;
    uint64_t aux64=*(volatile uint64_t *)(uintptr_t)0x30010420ULL;
    field_uart("ROOT_CAPACITY32",root32);field_uart("AUX_CAPACITY32",aux32);
    field_uart("ROOT_CAPACITY64",root64);field_uart("AUX_CAPACITY64",aux64);
    expect(root32==ROOT_BACKEND_SECTORS && root64==ROOT_BACKEND_SECTORS,"ROOT_CAPACITY_MISMATCH");
    expect(aux32==AUX_BACKEND_SECTORS && aux64==AUX_BACKEND_SECTORS,"AUX_CAPACITY_MISMATCH");
    if(TEST_CASE==10){tail_io(0x30000000ULL,ROOT_BACKEND_SECTORS,83);tail_io(0x30010000ULL,AUX_BACKEND_SECTORS,57);}
    puts_uart("BDIF CAPACITY CONTRACT PASS\n");platform_exit(0);
}
#endif
void main(void){
#if TEST_CASE >= 9
    capacity_test();
#endif
    volatile struct part *p=(void *)(uintptr_t)REQ;
    volatile struct sector *s=(void *)(uintptr_t)(REQ+64);
    volatile unsigned char *src=(void *)(uintptr_t)DATA;
    volatile unsigned char *dst=(void *)(uintptr_t)COPY;
    volatile unsigned char *ret=(void *)(uintptr_t)STATUS;
    unsigned root=(TEST_CASE==1),length=root?1024:512;
    uint32_t sec=root?1:32768;
    if(TEST_CASE==3||TEST_CASE==4)sec=65535;
    if(TEST_CASE==4)length=1024;
    for(unsigned i=0;i<length;i++){src[i]=(unsigned char)(i*17+83);dst[i]=0xa6;}
    s->pad0=s->pad1=s->pad3=0;s->number=sec;
    p[0]=(struct part){REQ+64,16,0};
    p[1]=(struct part){DATA,length,0x00010001};
    p[2]=(struct part){STATUS,1,0};
    if(TEST_CASE==5)p[1].addr=0xa0000000;
    if(TEST_CASE==6)p[1].flags=0x4444;
    if(TEST_CASE==7)p[2].addr=0xa0000000;
    *ret=0xa5;dsb_sy();
    uint64_t base=root?0x30000000ULL:0x30010000ULL;
    *(volatile uint64_t *)(uintptr_t)(base+0x408)=TEST_CASE==8?0xa0000000ULL:REQ;
    dsb_sy();field_uart("WRITE_STATUS",*ret);
    if(TEST_CASE==7||TEST_CASE==8){
       expect(*ret==0xa5,"INVALID_DESCRIPTOR_MUST_NOT_WRITE_STATUS");
    }else if(TEST_CASE==2||TEST_CASE>=4){
       expect(*ret==1,"INVALID_OR_READONLY_REQUEST_MUST_FAIL");
    }else{
       expect(*ret==0,"BLOCK_WRITE_FAILED");
       p[1]=(struct part){COPY,length,0x00030001};*ret=0xa5;dsb_sy();
       *(volatile uint64_t *)(uintptr_t)(base+0x408)=REQ;dsb_sy();
       expect(*ret==0,"BLOCK_READBACK_FAILED");
       for(unsigned i=0;i<length;i++)expect(dst[i]==(unsigned char)(i*17+83),"BLOCK_DATA_MISMATCH");
    }
    puts_uart("BDIF BLOCK CONTRACT PASS\n");platform_exit(0);
}
