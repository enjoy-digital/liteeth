import time
from litex.soc.tools.remote import RemoteClient


wb = RemoteClient()
wb.open()

logic_analyzer = LiteScopeLogicAnalyzerDriver(wb.regs, "logic_analyzer", debug=True)

# # #
conditions = {}
logic_analyzer.configure_term(port=0, cond=conditions)
logic_analyzer.configure_sum("term")
# run logic analyzer
logic_analyzer.run(offset=2048, length=4000)

while not logic_analyzer.done():
    pass

logic_analyzer.upload()
logic_analyzer.save("dump.vcd")

# # #

wb.close()
