## Purpose
Example of UDP loopback with LiteETH IP/UDP hardware stack. The FPGA will echo back any UDP packet it
receives on the specified port (default=8000) to the sender.

You can also found a detailed tutorial [here](https://yehowshuaimmanuel.com/fpga/migen/ethernet_ecp5/).

## Usage
    #!bash
    ./versa_ecp5.py
    ./versa_ecp5.py load

The IP address assigned to the FPGA in this example is ``192.168.1.50`` and the Host is expected to
be configured with ``192.168.1.100``. Since ``192.168.1.XXX`` is a common address in home networks, a
collisiton is quite possible and in this case, you will need to re-configure the FPGA and the python
scripts accordingly.

Once you are able to ping the FPGA board from your computer, you can run the sender and listener scripts
and should see the date/time UDP packets emitted by the sender looped back to the the listener:

    #!bash
    $python3 listener.py &
    $python3 sender.py

    2019-11-20 08:31:00
    2019-11-20 08:31:01
    2019-11-20 08:31:01
    2019-11-20 08:31:02
    2019-11-20 08:31:02
    2019-11-20 08:31:03
    2019-11-20 08:31:03
    2019-11-20 08:31:04
    2019-11-20 08:31:04
    [...]
