// This file is Copyright (c) 2015 Florent Kermarrec <florent@enjoy-digital.fr>
// License: BSD

#include "net/ip/uip.h"
#include "net/ip/uipopt.h"
#include "liteethmac-drv.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <system.h>
#include <hw/flags.h>
#include <console.h>
#include <generated/csr.h>
#include <generated/mem.h>

#define ETHMAC_RX0_BASE ETHMAC_BASE
#define ETHMAC_RX1_BASE (ETHMAC_BASE+0x0800)
#define ETHMAC_TX0_BASE (ETHMAC_BASE+0x1000)
#define ETHMAC_TX1_BASE (ETHMAC_BASE+0x1800)

#define MIN(a,b) (((a)<(b))?(a):(b))
#define MAX(a,b) (((a)>(b))?(a):(b))

typedef union {
  unsigned char raw[1514];
} ethernet_buffer;

static unsigned int rxslot;
static unsigned int rxlen;
static ethernet_buffer *rxbuffer;
static ethernet_buffer *rxbuffer0;
static ethernet_buffer *rxbuffer1;
static unsigned int txslot;
static unsigned int txlen;
static ethernet_buffer *txbuffer;
static ethernet_buffer *txbuffer0;
static ethernet_buffer *txbuffer1;

void liteethmac_init(void)
{
  ethmac_sram_reader_ev_pending_write(ETHMAC_EV_SRAM_READER);
  ethmac_sram_writer_ev_pending_write(ETHMAC_EV_SRAM_WRITER);

  rxbuffer0 = (ethernet_buffer *)ETHMAC_RX0_BASE;
  rxbuffer1 = (ethernet_buffer *)ETHMAC_RX1_BASE;
  txbuffer0 = (ethernet_buffer *)ETHMAC_TX0_BASE;
  txbuffer1 = (ethernet_buffer *)ETHMAC_TX1_BASE;

  rxslot = 0;
  txslot = 0;

  rxbuffer = rxbuffer0;
  txbuffer = txbuffer0;
}

uint16_t liteethmac_poll(void)
{
  if(ethmac_sram_writer_ev_pending_read() & ETHMAC_EV_SRAM_WRITER) {
    rxslot = ethmac_sram_writer_slot_read();
    rxlen = ethmac_sram_writer_length_read();
    if (rxslot)
      rxbuffer = rxbuffer1;
    else
      rxbuffer = rxbuffer0;
    memcpy(uip_buf, rxbuffer, rxlen);
    uip_len = rxlen;
    ethmac_sram_writer_ev_pending_write(ETHMAC_EV_SRAM_WRITER);
    return rxlen;
  }
  return 0;
}

void liteethmac_send(void)
{
  txlen = uip_len;
  memset(txbuffer, 0, 60);
  txlen = MIN(txlen, 1514);
  memcpy(txbuffer, uip_buf, txlen);
  txlen = MAX(txlen, 60);
  ethmac_sram_reader_slot_write(txslot);
  ethmac_sram_reader_length_write(txlen);
  while(!(ethmac_sram_reader_ready_read()));
  ethmac_sram_reader_start_write(1);

  txslot = (txslot+1)%2;
  if (txslot)
    txbuffer = txbuffer1;
  else
    txbuffer = txbuffer0;
}

void liteethmac_exit(void)
{
}
