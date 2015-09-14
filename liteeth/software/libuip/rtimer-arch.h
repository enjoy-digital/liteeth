#ifndef RTIMER_ARCH_H_
#define RTIMER_ARCH_H_

#include "contiki-conf.h"

#include <stdint.h>

void rtimer_callback(void);
void rtimer_arch_init(void);
rtimer_clock_t rtimer_arch_now(void);
void rtimer_arch_schedule(rtimer_clock_t t);
#define RTIMER_ARCH_SECOND 312500

#endif

/** @} */
