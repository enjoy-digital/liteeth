import time
from litex.soc.tools.remote import RemoteClient


wb = RemoteClient()
wb.open()

analyzer = LiteScopeLogicAnalyzerDriver(wb.regs, "logic_analyzer", debug=True)

# # #
conditions = {}
analyzer.configure_trigger(cond={})
# run logic analyzer
analyzer.run(offset=2048, length=4000)
analyzer.wait_done()
analyzer.upload()
analyzer.save("dump.vcd")

# # #

wb.close()
