/* Independent synthetic AArch64 guest for VMApple BDIF USB descriptors.
 * The protocol is a research prototype, not an Apple DFU boot test. */
#include "runtime.h"
#ifndef TEST_CASE
#define TEST_CASE 0
#endif
#ifndef TEST_LENGTH
#define TEST_LENGTH 33
#endif
#define USB_BASE 0x30100000ULL
#define RX_BASE 0x72000000ULL
#define TX_BASE 0x72400000ULL
#define DESC_BASE 0x72800000ULL
struct desc { uint64_t addr; uint32_t len; uint16_t next, flags; };
_Static_assert(sizeof(struct desc)==16,"wire descriptor");
static uint64_t hz;
static void wait_ms(unsigned ms) {
    uint64_t begin=READ_SYSREG(cntvct_el0);
    while (READ_SYSREG(cntvct_el0)-begin < hz*ms/1000) {}
}
static void post(unsigned queue,uint64_t address,uint32_t length,int boundary) {
    volatile struct desc *d=(void *)(uintptr_t)(boundary?0x8ffffff0ULL:DESC_BASE);
    d->addr=address;d->len=length;d->next=0;d->flags=0;
    dsb_sy();write32(USB_BASE+0x410,queue);
    *(volatile uint64_t *)(uintptr_t)(USB_BASE+0x408)=(uintptr_t)d;
    dsb_sy();
}
static int ready_for(unsigned ms) {
    uint64_t begin=READ_SYSREG(cntvct_el0);
    while (READ_SYSREG(cntvct_el0)-begin < hz*ms/1000) {
        if (mmio32(USB_BASE+0x10)&1) { dsb_sy();return 1; }
    }
    return 0;
}
static unsigned char value(unsigned i,unsigned salt) { return (unsigned char)(i*17+salt); }
static void fill(uint64_t addr,unsigned n,unsigned char val) {
    volatile unsigned char *p=(void *)(uintptr_t)addr;
    for(unsigned i=0;i<n;i++)p[i]=val;
}
static void check_rx(unsigned n,unsigned salt) {
    volatile unsigned char *p=(void *)(uintptr_t)RX_BASE;
    for(unsigned i=0;i<n;i++)if(p[i]!=value(i,salt)){
       field_uart("MISMATCH_OFFSET",i);fail("RX_DATA_MISMATCH");
    }
    for(unsigned i=n;i<n+32;i++)expect(p[i]==0x5a,"RX_OVERRAN_SENTINEL");
}
static void answer(unsigned n,unsigned salt) {
    volatile unsigned char *p=(void *)(uintptr_t)TX_BASE;
    for(unsigned i=0;i<n;i++)p[i]=(unsigned char)(value(i,salt)^0xa5);
    post(1,TX_BASE,n,TEST_CASE==2);
    expect(ready_for(700),"TX_NOT_READY");
}
void main(void) {
    hz=READ_SYSREG(cntfrq_el0);
    expect(hz>1000,"BAD_FREQ");
    puts_uart("USB_GUEST_START\n");
    /* Real AVPBooter polls TX readiness BEFORE posting its first request.
     * Keep this precondition in every transport scenario, not only in
     * tests that post a descriptor before observing completion. */
    write32(USB_BASE+0x410,1);
    expect(ready_for(300),"TX_NOT_READY_BEFORE_FIRST_DESCRIPTOR");

    if(TEST_CASE==13) {
       /* Real ROM teardown: disable and observe zero before new-stage init.
        * A pending RX must not survive this transition. A disabled TX
        * submission must not generate a packet. */
       expect(mmio32(USB_BASE+0x404)==1,"QUEUE_CONTROL_INITIAL");
       write32(USB_BASE+0x404,3);
       expect(mmio32(USB_BASE+0x404)==3,"QUEUE_CONTROL_ENABLE_READBACK");
       fill(RX_BASE+4096,TEST_LENGTH+32,0x77);
       post(0,RX_BASE+4096,TEST_LENGTH,0);
       write32(USB_BASE+0x404,0);
       expect(mmio32(USB_BASE+0x404)==0,"QUEUE_CONTROL_DISABLE_READBACK");
       write32(USB_BASE+0x410,1);
       expect(!ready_for(20),"DISABLED_QUEUE_FALSE_READY");
       post(1,TX_BASE,16,0);
       expect(!ready_for(20),"DISABLED_QUEUE_ACCEPTED_TX");
       write32(USB_BASE+0x404,1);
       expect(mmio32(USB_BASE+0x404)==1,"QUEUE_REENABLE_READBACK");
       write32(USB_BASE+0x410,1);
       expect(ready_for(100),"QUEUE_REENABLE_TX_NOT_READY");
       puts_uart("USB_QUEUE_SHUTDOWN_REINIT_PASS\n");
    }

    if(TEST_CASE==12) {
       struct block_req {uint64_t addr;uint32_t len,flags;};
       volatile struct block_req *req=(void *)(uintptr_t)DESC_BASE;
       volatile uint32_t *sector=(void *)(uintptr_t)(DESC_BASE+128);
       for(unsigned which=0;which<2;which++) {
          sector[0]=sector[1]=sector[3]=0;sector[2]=which?2:1;
          req[0].addr=(uintptr_t)sector;req[0].len=16;req[0].flags=0;
          req[1].addr=RX_BASE;req[1].len=64;req[1].flags=0x00030001;
          req[2].addr=TX_BASE;req[2].len=1;req[2].flags=0;
          *(volatile unsigned char *)(uintptr_t)TX_BASE=0xaa;
          fill(RX_BASE,96,0x5a);dsb_sy();
          *(volatile uint64_t *)(uintptr_t)(0x30000408ULL+(which?0x10000:0))=DESC_BASE;
          dsb_sy();expect(*(volatile unsigned char *)(uintptr_t)TX_BASE==0,"VBLK_READ_STATUS");
          check_rx(64,which?99:61);
       }
       puts_uart("USB_BRANCH_ROOT_AUX_READS_PASS\n");
       answer(32,79);puts_uart("USB_GUEST_PASS\n");platform_exit(0);
    }
    if(TEST_CASE==11) {
       for(unsigned i=0;i<256;i++)answer(4096,79+(i%100));
       puts_uart("USB_GUEST_PASS\n");platform_exit(0);
    }

    if (TEST_CASE==6) {
        /* Descriptor/data invalid requests must not produce a wire frame. */
        post(1,0xa0000000,32,0); wait_ms(30);
        answer(32,79);puts_uart("USB_GUEST_PASS\n");platform_exit(0);
    }
    if (TEST_CASE==7) {
        post(1,TX_BASE,4097,0);wait_ms(30);
        answer(32,79);puts_uart("USB_GUEST_PASS\n");platform_exit(0);
    }
    if (TEST_CASE==8) {
        answer(4096,79);puts_uart("USB_GUEST_PASS\n");platform_exit(0);
    }
    if (TEST_CASE==10) {
        /* Full descriptor unavailable. QEMU must not manufacture a transfer. */
        write32(USB_BASE+0x410,1);
        *(volatile uint64_t *)(uintptr_t)(USB_BASE+0x408)=0xa0000000;
        wait_ms(30);answer(32,79);puts_uart("USB_GUEST_PASS\n");platform_exit(0);
    }
    fill(RX_BASE,TEST_LENGTH+32,0x5a);
    if(TEST_CASE==4) {
       puts_uart("USB_PREPOST_WINDOW\n");wait_ms(150);
       volatile unsigned char *p=(void *)(uintptr_t)RX_BASE;
       for(unsigned i=0;i<TEST_LENGTH+32;i++)expect(p[i]==0x5a,"DMA_BEFORE_POST");
    }
    if(TEST_CASE==9) {puts_uart("USB_RECONNECT_WINDOW\n");wait_ms(300);}
    post(0,RX_BASE,TEST_CASE==14 ? 0x100006 : TEST_LENGTH,TEST_CASE==2);
    puts_uart("USB_RX_READY\n");
    int ok=ready_for(1200);
    if(TEST_CASE==5) {
       expect(!ok,"RX_FALSE_COMPLETION_WITHOUT_PEER_DATA");
       puts_uart("USB_NO_DATA_EXPECTED_TIMEOUT\n");platform_exit(1);
    }
    expect(ok,"RX_TIMEOUT");
    check_rx(TEST_LENGTH,79);
    expect((mmio32(USB_BASE+0x10)&1)==0,"RX_COMPLETION_NOT_CONSUMED");
    if(TEST_CASE==1) {
       fill(RX_BASE,TEST_LENGTH+32,0x5a);
       post(0,RX_BASE,TEST_LENGTH,0);
       expect(ready_for(1200),"SECOND_RX_TIMEOUT");
       check_rx(TEST_LENGTH,113);
       answer(TEST_LENGTH<4096?TEST_LENGTH:4096,113);
    } else answer(TEST_LENGTH<4096?TEST_LENGTH:4096,79);
    puts_uart("USB_GUEST_PASS\n");platform_exit(0);
}
