#ifndef E11_GUEST_INPUT_H
#define E11_GUEST_INPUT_H
void input_usb_init(u32 expected_devices);
void input_usb_wait_key(u32 released);
void input_usb_shutdown(void);
void input_usb_irq_start(void);
void input_usb_wait_irq_key(u32 released);
void input_usb_irq_shutdown(void);
u32 input_usb_irq_handle(u32 id);
#endif
