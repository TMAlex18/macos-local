/* Synthetic USB device in ARM guest RAM. No Apple firmware or identifiers. */
#include "runtime.h"
#define B 0x30100000ULL
struct desc {uint64_t addr;uint32_t len;uint16_t next,flags;};
static volatile unsigned char *rx=(void *)(uintptr_t)0x72000000;
static volatile unsigned char *tx=(void *)(uintptr_t)0x72400000;
static volatile struct desc *descriptor=(void *)(uintptr_t)0x72800000;
static unsigned char stored[512];static unsigned stored_len;
static uint64_t hz;
static const unsigned char device[18]={18,1,0,2,0xff,0,0,64,0x0d,0xf0,0x0b,0xb0,0,1,0,0,0,1};
static unsigned short get16(unsigned off){return rx[off]|(rx[off+1]<<8);}
static unsigned get32(unsigned off){return get16(off)|((unsigned)get16(off+2)<<16);}
static void post(unsigned q,uint64_t ptr,unsigned n){
 descriptor->addr=ptr;descriptor->len=n;descriptor->next=0;descriptor->flags=0;
 dsb_sy();write32(B+0x410,q);*(volatile uint64_t *)(uintptr_t)(B+0x408)=(uintptr_t)descriptor;dsb_sy();
}
static int ready(unsigned ms){uint64_t start=READ_SYSREG(cntvct_el0);
 while(READ_SYSREG(cntvct_el0)-start<hz*ms/1000)if(mmio32(B+0x10)&1)return 1;return 0;}
static void receive(void){post(0,(uintptr_t)rx,1024);expect(ready(4000),"CONTROL_RX_TIMEOUT");dsb_sy();}
static void reply(unsigned type,unsigned status,const unsigned char *data,unsigned n){
 tx[0]=type;tx[1]=status;for(unsigned i=0;i<n;i++)tx[i+2]=data[i];
 post(1,(uintptr_t)tx,n+2);expect(ready(1000),"CONTROL_TX_TIMEOUT");
}
void main(void){
 hz=READ_SYSREG(cntfrq_el0);puts_uart("CONTROL_GUEST_START\n");
 for(;;){
  receive();expect(get32(0)==8 && rx[4]==0 && rx[5]==1,"BAD_CONTROL_SETUP_FRAME");
  unsigned bm=rx[6],request=rx[7],value=get16(8),length=get16(12);
  if(bm==0x80 && request==6 && value==0x100){reply(1,0,device,length<18?length:18);}
  else if(bm==0x40 && request==0x5a){
   unsigned n=length;expect(n<=sizeof(stored),"OUT_TOO_LARGE");receive();
   expect(get32(0)==n && rx[4]==0 && rx[5]==3,"BAD_CONTROL_OUT_FRAME");
   for(unsigned i=0;i<n;i++)stored[i]=rx[i+6];stored_len=n;reply(5,0,0,0);
   puts_uart("CONTROL_GUEST_OUT_STORED\n");
  } else if(bm==0xc0 && request==0x5b){reply(1,0,stored,length<stored_len?length:stored_len);}
  else if(bm==0xa1 && request==3){
   if(value==2){puts_uart("CONTROL_GUEST_SILENT_STATUS\n");continue;}
   if(value==1){reply(1,0,0,0);puts_uart("CONTROL_GUEST_EMPTY_STATUS\n");}
   else{const unsigned char status[6]={0,0,0,0,2,0};reply(1,0,status,6);}
  } else if(bm==0xc0 && request==0x97){
   tx[0]=1;post(1,(uintptr_t)tx,1);expect(ready(1000),"MALFORMED_TX_TIMEOUT");
  } else if(bm==0x40 && request==0xfe){reply(1,0,0,0);puts_uart("CONTROL_GUEST_PASS\n");platform_exit(0);}
  else reply(1,1,0,0);
 }
}
