# XXX remove this
MSCDIR=../../../../misoc
include $(MSCDIR)/software/common.mak

UIPDIR=../uip
LIBUIPDIR=../libuip

CFLAGS += $(CPPFLAGS) -I. \
	-I$(UIPDIR) \
	-I$(UIPDIR)/net \
	-I$(UIPDIR)/net\ip \
	-I$(UIPDIR)/net\ipv4 \
	-Wno-char-subscripts \
	-fno-strict-aliasing -fpack-struct

#See here for-fno-strict-aliasing -fpack-struct:
#http://sourceforge.net/p/contiki/mailman/message/28002063/

CCDEP=$(CC)

UIPCOREOBJS=$(UIPDIR)/net/ip/dhcpc.o \
	$(UIPDIR)/net/ip/psock.o \
	$(UIPDIR)/net/ip/resolv.o \
	$(UIPDIR)/net/ip/simple-udp.o \
	$(UIPDIR)/net/ip/slipdev.o \
	$(UIPDIR)/net/ip/tcpip.o \
	$(UIPDIR)/net/ip/tcp-socket.o \
	$(UIPDIR)/net/ip/udp-socket.o \
	$(UIPDIR)/net/ip/uip-debug.o \
	$(UIPDIR)/net/ip/uiplib.o \
	$(UIPDIR)/net/ip/uip-packetqueue.o \
	$(UIPDIR)/net/ip/uip-split.o \
	$(UIPDIR)/net/ip/uip-udp-packet.o \
	$(UIPDIR)/net/ipv4/uaodv.o \
	$(UIPDIR)/net/ipv4/uaodv-rt.o \
	$(UIPDIR)/net/ipv4/uip.o \
	$(UIPDIR)/net/ipv4/uip_arp.o \
	$(UIPDIR)/net/ipv4/uip-fw.o \
	$(UIPDIR)/net/ipv4/uip-fw-drv.o \
	$(UIPDIR)/net/ipv4/uip-neighbor.o \
	$(UIPDIR)/net/ipv4/uip-over-mesh.o \
	$(UIPDIR)/net/linkaddr.o \
	$(UIPDIR)/net/nbr-table.o \
	$(UIPDIR)/net/netstack.o \
	$(UIPDIR)/net/packetbuf.o \
	$(UIPDIR)/net/queuebuf.o \
	$(UIPDIR)/sys/process.o \
	$(UIPDIR)/sys/etimer.o \
	$(UIPDIR)/sys/timer.o \
	$(UIPDIR)/lib/list.o

UIPARCHOBJS=clock-arch.o \
	rtimer-arch.o \
	liteethmac-drv.o

UIPOBJS=$(UIPCOREOBJS) $(UIPARCHOBJS)
OBJS_LIB+=$(UIPOBJS)

UIPLIB=libuip.a

all: $(UIPLIB)

.PHONY: all compile clean

%.o: %.c
	$(compile-dep)

%.o: %.S
	$(assemble)

clean:
	rm -f $(UIPOBJS) $(UIPOBJS:.o=.d) libuip.a

libuip.a: $(UIPOBJS)
	$(AR) clr libuip.a $(UIPOBJS)
	$(RANLIB) libuip.a
