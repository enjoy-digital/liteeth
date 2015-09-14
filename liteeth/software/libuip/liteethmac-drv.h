// This file is Copyright (c) 2015 Florent Kermarrec <florent@enjoy-digital.fr>
// License: BSD
#ifndef __LITEETHMAC_H__
#define __LITEETHMAC_H__

void liteethmac_init(void);
uint16_t liteethmac_poll(void);
void liteethmac_send(void);
void liteethmac_exit(void);

#endif /* __LITEETHMAC_H__ */
