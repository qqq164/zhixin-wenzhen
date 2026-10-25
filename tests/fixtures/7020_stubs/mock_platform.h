#ifndef FDW_PWM_TEST_PLATFORM_H
#define FDW_PWM_TEST_PLATFORM_H
#include <stdint.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
typedef uint64_t XTime;
typedef uint16_t u16_t;
typedef int err_t;
#define COUNTS_PER_SECOND 1000000ULL
#define xil_printf(...) ((void)0)
#define ERR_OK 0
#define ERR_MEM (-1)
#define ERR_ABRT (-13)
#define TCP_WRITE_FLAG_COPY 1
#define IPADDR_TYPE_ANY 0
#define IP_ANY_TYPE NULL
struct pbuf { struct pbuf *next; void *payload; u16_t len; u16_t tot_len; };
struct tcp_pcb { void *arg; u16_t capacity; int aborted; int closed; unsigned int tx_length; char tx[20000]; };
typedef err_t (*tcp_accept_fn)(void *, struct tcp_pcb *, err_t);
typedef err_t (*tcp_recv_fn)(void *, struct tcp_pcb *, struct pbuf *, err_t);
typedef err_t (*tcp_sent_fn)(void *, struct tcp_pcb *, u16_t);
typedef err_t (*tcp_poll_fn)(void *, struct tcp_pcb *);
typedef void (*tcp_err_fn)(void *, err_t);
void XTime_GetTime(XTime *time);
void *mem_malloc(size_t size);
void mem_free(void *memory);
void tcp_arg(struct tcp_pcb *pcb, void *arg);
void tcp_recv(struct tcp_pcb *pcb, tcp_recv_fn callback);
void tcp_sent(struct tcp_pcb *pcb, tcp_sent_fn callback);
void tcp_poll(struct tcp_pcb *pcb, tcp_poll_fn callback, unsigned int interval);
void tcp_err(struct tcp_pcb *pcb, tcp_err_fn callback);
void tcp_accept(struct tcp_pcb *pcb, tcp_accept_fn callback);
void tcp_abort(struct tcp_pcb *pcb);
err_t tcp_close(struct tcp_pcb *pcb);
void tcp_recved(struct tcp_pcb *pcb, u16_t length);
u16_t tcp_sndbuf(struct tcp_pcb *pcb);
err_t tcp_write(struct tcp_pcb *pcb, const void *data, u16_t length, unsigned int flags);
err_t tcp_output(struct tcp_pcb *pcb);
unsigned int pbuf_free(struct pbuf *buffer);
struct tcp_pcb *tcp_new_ip_type(unsigned int type);
err_t tcp_bind(struct tcp_pcb *pcb, const void *address, u16_t port);
struct tcp_pcb *tcp_listen(struct tcp_pcb *pcb);
#endif
