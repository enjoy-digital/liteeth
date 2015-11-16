from litex.soc.tools.remote import RemoteClient

wb = RemoteClient()
wb.open()

# # #

identifier = ""
for i in range(30):
    identifier += chr(wb.read(wb.bases.identifier_mem + 4*(i+1))) # TODO: why + 1?
print(identifier)
print("frequency : {}MHz".format(wb.constants.system_clock_frequency/1000000))

SRAM_BASE = 0x02000000
wb.write(SRAM_BASE, [i for i in range(64)])
print(wb.read(SRAM_BASE, 64))

# # #

wb.close()
