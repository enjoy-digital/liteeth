import unittest

from migen import *
from litex.gen import *

from liteeth.phy.titanium_lvds_1000basex import *

class SimQuadDeser(LiteXModule):
    def __init__(self, input_bits=20, clock_stretch=1.0):
        """
        - input_bits: Number of input bits (configurable)
        - clock_stretch: Floating-point stretch factor
        """
        self.input_bits = input_bits      # Configurable input bit size
        self.clock_stretch = clock_stretch  # Active stretch factor

        # Inputs
        self.enable      = Signal()       # Enable (~reset)
        self.input_data  = Signal(input_bits)  # Variable-length input data
        self.offset      = Signal(5)      # 5-bit offset (0-31 to support longer input sizes)

        # Outputs
        self.serdes_out  = [Signal(10) for _ in range(4)]  # Four 10-bit outputs
        self.last_chunk  = Signal()       # Signals last chunk of transmission

        # Internal simulation storage
        self._bitstream  = None # Processed bitstream
        self._chunk_idx  = 0    # Current index in bitstream
        self._transmitting = False  # Transmission state

        self.transmitting = Signal()

        self.data_out = Signal(10)
        self.data_out_valid = Signal()

        self.end = Signal()

        self.rx = rx = EfinixSerdesDiffRxClockRecovery(
            Signal(4),
            Signal(4),
            self.data_out,
            self.data_out_valid,
            False,
            None,
            None,
            dummy=True,
        )

        self.comb += [
            rx.serdesrx0.data.eq(self.serdes_out[0]),
            rx.serdesrx1.data.eq(self.serdes_out[1]),
            rx.serdesrx2.data.eq(self.serdes_out[2]),
            rx.serdesrx3.data.eq(self.serdes_out[3]),
        ]

    def set_clock_stretch(self, new_stretch):
        """Set a new clock stretch factor. It will be applied the next time transmission starts."""
        self.clock_stretch = new_stretch

    def _to_bitlist(self, value, width):
        """Convert integer `value` into a list of bits [MSB, ..., LSB] of length `width`."""
        return [(value >> i) & 1 for i in range(width)]

    def _from_bitlist(self, bits):
        """Convert a list of bits [MSB, ..., LSB] into an integer."""
        return sum(b << i for i, b in enumerate(bits))

    def _arrs_from_bitlist(self, bits):
        """
        Takes a list of 40 bits [b0, b1, ..., b39] and returns
        4 integers [r0, r1, r2, r3], each being 10 bits of interleaved data.
        r0 corresponds to bits [b0, b4, b8, ... b36], r1 -> [b1, b5, b9, ...], etc.
        """
        # Slicing out every 4th bit gives each channel's 10 bits in LSB->MSB order
        r0_bits = bits[0::4]  # b0, b4, b8, b12, ...
        r1_bits = bits[1::4]  # b1, b5, b9, b13, ...
        r2_bits = bits[2::4]  # b2, b6, b10, b14, ...
        r3_bits = bits[3::4]  # b3, b7, b11, b15, ...

        # Convert each 10-bit list into an integer
        r0 = self._from_bitlist(r0_bits)
        r1 = self._from_bitlist(r1_bits)
        r2 = self._from_bitlist(r2_bits)
        r3 = self._from_bitlist(r3_bits)

        return [r0, r1, r2, r3]

    def do_simulation(self, dut):
        """Handles simulation logic dynamically."""
        end_counter = 0
        while True:
            yield self.transmitting.eq(self._transmitting)
            if (yield self.enable) == 0:
                # Reset all logic if enable goes low
                self._transmitting = False
                self._bitstream = []
                self._chunk_idx = 0
                yield self.last_chunk.eq(0)
                for i in range(4):
                    yield self.serdes_out[i].eq(0)

            elif not self._transmitting:
                # Capture input data
                input_val = (yield self.input_data)
                offset = (yield self.offset)

                # Apply offset (right shift)
                input_val = (input_val >> offset) & ((1 << self.input_bits) - 1)
                input_bits = self._to_bitlist(input_val, self.input_bits)

                print("id", (yield self.input_data), "iv", input_val, "ib", input_bits, "sib", self.input_bits)

                # Compute final output bit length: offset + input_bits * 4 * stretch
                total_output_bits = int((self.input_bits * 4 * self.clock_stretch) + offset)

                # Upsample by 4 (each bit appears 4 times)
                upsampled = []
                for bit in input_bits:
                    upsampled.extend([bit] * 4)

                # Apply clock stretch
                stretched = []
                for j in range(total_output_bits):
                    i = max(0, min(int(round(j / self.clock_stretch)), len(upsampled) - 1))  # Ensure valid index range
                    stretched.append(upsampled[i])

                # Store processed bitstream
                self._bitstream = stretched
                self._chunk_idx = 0
                self._transmitting = True
                yield self.last_chunk.eq(0)
                for i in range(4):
                    yield self.serdes_out[i].eq(0)
            elif self._bitstream is not None:
                # Output next 40-bit chunk to interleaved SERDES channels
                print(self._chunk_idx)
                start = self._chunk_idx * 40
                end = start + 40
                bits = self._bitstream[start:end]

                # Zero-pad if fewer than 10 bits remain
                if len(bits) < 40:
                    bits += [0] * (40 - len(bits))

                chunk_values = self._arrs_from_bitlist(bits)

                for i in range(4):
                    yield self.serdes_out[i].eq(chunk_values[i])

                # Move to next chunk
                self._chunk_idx += 1
                done = self._chunk_idx * 40 >= len(self._bitstream)
                self._transmitting = not done
                print(len(self._bitstream), self._chunk_idx * 40, self._transmitting)
                yield self.last_chunk.eq(done)
            
            if (yield self.end & ~self.enable & (self.data_out ==0)):
                end_counter += 1
                if end_counter > 10:
                    break
            else:
                end_counter = 0

            yield  # Proceed to the next simulation step

class TestCoreDeser(unittest.TestCase):
    def setUp(self):
        self.cd = ClockDomain("sys")
        self.cd.rst = Signal(reset=1)

    def test_sim_default(self):
        def testbench(dut):
            # Configure initial inputs
            yield dut.input_data.eq(0b11101000110000001111111010001100000011111110100011000000111111101000110000001111111010001100000011111110100011000000111111101000110000001111111010001100000011111110100011000000111111101000110000001111)
            yield dut.offset.eq(0)
            yield dut.enable.eq(1)  # Enable module

            print("Starting simulation...")

            # Run simulation for enough cycles to complete transmission
            for cycle in range(100):
                yield
                serdes_vals = []
                for i in range(4):
                    serdes_vals.append((yield dut.serdes_out[i]))
                last_chunk_flag = yield dut.last_chunk
                print(f"Cycle {cycle}: serdes_out={serdes_vals}, last_chunk={last_chunk_flag}")

                if last_chunk_flag:
                    print("Final chunk transmitted!")
                    break

            # Change stretch and restart transmission
            yield dut.enable.eq(0)  # Reset module
            for _ in range(5):
                yield  # Let reset settle

            print("\nChanging clock stretch to 2.0 and restarting transmission...\n")
            dut.set_clock_stretch(2.0)
            yield dut.enable.eq(1)  # Start again with new stretch factor

            for cycle in range(100):
                yield
                serdes_vals = []
                for i in range(4):
                    serdes_vals.append((yield dut.serdes_out[i]))
                last_chunk_flag = yield dut.last_chunk
                print(f"Cycle {cycle}: serdes_out={serdes_vals}, last_chunk={last_chunk_flag}")

                if last_chunk_flag:
                    print("Final chunk transmitted with new stretch factor!")
                    yield dut.enable.eq(0)
                    break
            
            yield dut.end.eq(1)

        # Instantiate and run
        dut = SimQuadDeser(input_bits=200, clock_stretch=1.00)
        run_simulation(dut, [testbench(dut), dut.do_simulation(dut)], vcd_name="serdes_sim.vcd")

if __name__ == "__main__":
    unittest.main()
