# Generate the IP expected by USP_CMAC_100G in an open Vivado project.
# Set cmac_name, cmac_site and cmac_gt_group in the target before sourcing this file.
# The target owns physical placement; no board pin assignments are supplied here.
create_ip -name cmac_usplus -vendor xilinx.com -library ip -version 3.1 -module_name $cmac_name
set_property -dict [list \
    CONFIG.CMAC_CAUI4_MODE {1} \
    CONFIG.NUM_LANES {4x25} \
    CONFIG.USER_INTERFACE {AXIS} \
    CONFIG.GT_REF_CLK_FREQ {161.1328125} \
    CONFIG.GT_DRP_CLK {100} \
    CONFIG.GT_LOCATION {1} \
    CONFIG.INCLUDE_SHARED_LOGIC {2} \
    CONFIG.INCLUDE_RS_FEC {0} \
    CONFIG.ENABLE_AXI_INTERFACE {0} \
    CONFIG.TX_FLOW_CONTROL {0} \
    CONFIG.RX_FLOW_CONTROL {0} \
    CONFIG.TX_FRAME_CRC_CHECKING {Enable FCS Insertion} \
    CONFIG.RX_FRAME_CRC_CHECKING {Enable FCS Stripping} \
    CONFIG.RX_MIN_PACKET_LEN {64} \
    CONFIG.RX_MAX_PACKET_LEN {9022} \
    CONFIG.CMAC_CORE_SELECT $cmac_site \
    CONFIG.GT_GROUP_SELECT $cmac_gt_group \
] [get_ips $cmac_name]
generate_target all [get_ips $cmac_name]
