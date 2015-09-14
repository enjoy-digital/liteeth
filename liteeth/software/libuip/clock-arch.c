// This file is Copyright (c) 2015 Florent Kermarrec <florent@enjoy-digital.fr>
// License: BSD

#include "contiki-conf.h"
#include "clock-arch.h"
#include <generated/csr.h>

/*-----------------------------------------------------------------------------------*/
void clock_init(void)
{
	timer0_en_write(0);
	timer0_load_write(0xffffffff);
	timer0_reload_write(0xffffffff);
	timer0_en_write(1);
}

/*---------------------------------------------------------------------------*/
clock_time_t clock_time(void)
{
	unsigned int freq;
	unsigned int prescaler;
	clock_time_t ticks;

	freq = identifier_frequency_read();
	prescaler = freq/CLOCK_CONF_SECOND;
	timer0_update_value_write(1);
	ticks = (0xffffffff - timer0_value_read())/prescaler;
	return ticks;
}
