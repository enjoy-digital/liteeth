#include "contiki.h"

#include <sys/rtimer.h>
#include <sys/clock.h>

#include <stdio.h>

#define RTIMER_DEBUG

void rtimer_callback(void)
{
#ifdef RTIMER_DEBUG
	printf("XXX rtimer_callback");
#endif
	rtimer_run_next();
}

void rtimer_arch_init(void)
{
#ifdef RTIMER_DEBUG
	printf("XXX rtimer_arch_init");
#endif
}

rtimer_clock_t rtimer_arch_now(void)
{
#ifdef RTIMER_DEBUG
	printf("XXX rtimer_arch_now");
#endif
  return 0;
}

void rtimer_arch_schedule(rtimer_clock_t t)
{
#ifdef RTIMER_DEBUG
	printf("XXX rtimer_arch_schedule");
#endif
}
