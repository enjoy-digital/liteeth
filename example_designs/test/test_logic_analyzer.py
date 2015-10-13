import time
from litescope.software.driver.logic_analyzer import LiteScopeLogicAnalyzerDriver


def main(wb):
    logic_analyzer = LiteScopeLogicAnalyzerDriver(wb.regs, "logic_analyzer", debug=True)

    wb.open()
    regs = wb.regs
    # # #
    conditions = {}
    logic_analyzer.configure_term(port=0, cond=conditions)
    logic_analyzer.configure_sum("term")
    # Run Logic Analyzer
    logic_analyzer.run(offset=2048, length=4000)

    while not logic_analyzer.done():
        pass

    logic_analyzer.upload()
    logic_analyzer.save("dump.vcd")
    # # #
    wb.close()
