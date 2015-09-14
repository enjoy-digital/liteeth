#ifndef CONTIKI_CONF_H__
#define CONTIKI_CONF_H__

#include <stdio.h>
#include <stdint.h>

#define CCIF
#define CLIF

#define NETSTACK_CONF_WITH_IPV4 1
#define WITH_ASCII 1

#define CLOCK_CONF_SECOND 128

typedef unsigned char u8_t;
typedef unsigned short u16_t;
typedef unsigned int u32_t;
typedef char s8_t;
typedef short s16_t;
typedef int s32_t;

typedef unsigned int clock_time_t;
typedef unsigned int uip_stats_t;

#ifndef BV
#define BV(x) (1<<(x))
#endif

/* uIP configuration */
#define UIP_CONF_BYTE_ORDER	UIP_BIG_ENDIAN
#define UIP_CONF_LLH_LEN	14
#define UIP_CONF_BROADCAST	1
#define UIP_CONF_LOGGING	1
#define UIP_CONF_BUFFER_SIZE	116

#define UIP_CONF_TCP_FORWARD	1

#endif /* CONTIKI_CONF_H__ */
