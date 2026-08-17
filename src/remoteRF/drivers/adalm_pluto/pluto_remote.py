# Copyright (C) 2026 RemoteRF
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

from ...common.utils import *
from .._virtual import (
    is_virtual_token,
    make_virtual_token,
    retag_virtual_token,
    virtual_call,
    virtual_get,
    virtual_set,
)


def _rpc_client(*, function_name, args):
    # Keep transport/configuration initialization out of the virtual path.
    from ...core.grpc_client import rpc_client

    return rpc_client(function_name=function_name, args=args)

def try_get(function_name, token):
    if is_virtual_token(token):
        return virtual_get(token, function_name)
    try:
        return unmap_arg(_rpc_client(function_name=f"Pluto:{function_name}:GET", args={'a':map_arg(token)}).results[function_name])
    except Exception as e:
        input(f"Error: {e}\nHit enter to continue...")
    return None

def try_set(function_name, value, token):
    if is_virtual_token(token):
        virtual_set(token, function_name, value)
        return None
    try:
        _rpc_client(function_name=f"Pluto:{function_name}:SET", args={function_name: map_arg(value), 'a':map_arg(token)})
    except Exception as e:
        input(f"Error: {e}\nHit enter to continue...")
        
def try_call_0_arg(function_name, token):   # 0 argument call
    if is_virtual_token(token):
        return virtual_call(token, function_name)
    try:
        response = _rpc_client(
            function_name=f"Pluto:{function_name}:CALL0", 
            args={
                'a': map_arg(token)
            }
        )
        return unmap_arg(response.results[function_name])
    except Exception as e:
        input(f"RPC_0_call Error: {e}\nHit enter to continue...")
    return None
        
def try_call_1_arg(function_name, arg, token):  # 1 argument call
    if is_virtual_token(token):
        return virtual_call(token, function_name, arg, has_arg=True)
    try:
        response = _rpc_client(
            function_name=f"Pluto:{function_name}:CALL1", 
            args={
                'a': map_arg(token),
                'arg1': map_arg(arg)
            }
        )
        # The server should return something like {function_name: <something>}
        return unmap_arg(response.results[function_name])
    except Exception as e:
        input(f"RPC_1_call Error: {e}\nHit enter to continue...")
    return None

class rx_def:
    pass

class tx_def:
    pass

class rx_tx_def(rx_def, tx_def):
    pass

class ad9364(rx_tx_def):
    pass

class Pluto: # client
    
    def __init__(self, token: str | None = None, debug=False, *, virtual=False):
        self.virtual = bool(virtual)
        if self.virtual:
            self.token = make_virtual_token(token, device_type="adalm_pluto")
            return
        if token is None:
            raise ValueError("A reservation token is required unless virtual=True")
        self.token = token
        try_call_0_arg(function_name="ip", token=token)
        
        
    def api_token(self, token:str) -> None:
        if self.virtual:
            self.token = retag_virtual_token(self.token, token)
            return
        self.token = token
        try_call_0_arg(function_name="ip", token=token)
    
    # PlutoSDR
    
    _device_name = "PlutoSDR"
    _uri_auto = "ip:pluto.local"

    def __repr__(self): # ! UNTESTED !
        return try_get("__repr__", self.token)
    
    #region ad9364
    """AD9364 Transceiver"""

    @property
    def filter(self):
        return try_get("filter", self.token)

    @filter.setter
    def filter(self, value):
        try_set("filter", value, self.token)

    @property
    def loopback(self):
        """loopback: Set loopback mode. Options are:
        0 (Disable), 1 (Digital), 2 (RF)"""
        return try_get("loopback", self.token)

    @loopback.setter
    def loopback(self, value):
        try_set("loopback", value, self.token)

    @property
    def gain_control_mode_chan0(self):
        """gain_control_mode_chan0: Mode of receive path AGC. Options are:
        slow_attack, fast_attack, manual"""
        return try_get("gain_control_mode_chan0", self.token)

    @gain_control_mode_chan0.setter
    def gain_control_mode_chan0(self, value):
        try_set("gain_control_mode_chan0", value, self.token)

    @property
    def rx_hardwaregain_chan0(self):
        """rx_hardwaregain_chan0: Gain applied to RX path. Only applicable when
        gain_control_mode is set to 'manual'"""
        return try_get("rx_hardwaregain_chan0", self.token)

    @rx_hardwaregain_chan0.setter
    def rx_hardwaregain_chan0(self, value):
        try_set("rx_hardwaregain_chan0", value, self.token)

    @property
    def tx_hardwaregain_chan0(self):
        """tx_hardwaregain_chan0: Attenuation applied to TX path"""
        return try_get("tx_hardwaregain_chan0", self.token)

    @tx_hardwaregain_chan0.setter
    def tx_hardwaregain_chan0(self, value):
        try_set("tx_hardwaregain_chan0", value, self.token)

    @property
    def rx_rf_bandwidth(self):
        """rx_rf_bandwidth: Bandwidth of front-end analog filter of RX path"""
        return try_get("rx_rf_bandwidth", self.token)

    @rx_rf_bandwidth.setter
    def rx_rf_bandwidth(self, value):
        try_set("rx_rf_bandwidth", value, self.token)

    @property
    def tx_rf_bandwidth(self):
        """tx_rf_bandwidth: Bandwidth of front-end analog filter of TX path"""
        return try_get("tx_rf_bandwidth", self.token)

    @tx_rf_bandwidth.setter
    def tx_rf_bandwidth(self, value):
        try_set("tx_rf_bandwidth", value, self.token)

    @property
    def sample_rate(self):                # ! UNTESTED !
        """sample_rate: Sample rate RX and TX paths in samples per second"""
        return try_get("sample_rate", self.token)

    @sample_rate.setter
    def sample_rate(self, rate):          # ! UNTESTED !
        try_set("sample_rate", rate, self.token)

    @property
    def rx_lo(self):
        """rx_lo: Carrier frequency of RX path"""
        return try_get("rx_lo", self.token)

    @rx_lo.setter
    def rx_lo(self, value):
        try_set("rx_lo", value, self.token)

    @property
    def tx_lo(self):
        """tx_lo: Carrier frequency of TX path"""
        return try_get("tx_lo", self.token)

    @tx_lo.setter
    def tx_lo(self, value):
        try_set("tx_lo", value, self.token)
        
    @property
    def tx_cyclic_buffer(self):
        """tx_cyclic_buffer: Size of cyclic buffer"""
        return try_get("tx_cyclic_buffer", self.token)
    
    @tx_cyclic_buffer.setter
    def tx_cyclic_buffer(self, value):
        try_set("tx_cyclic_buffer", value, self.token)
        
    def tx_destroy_buffer(self):
        try_call_0_arg("tx_destroy_buffer", self.token)

    def rx_destroy_buffer(self):
        try_call_0_arg("rx_destroy_buffer", self.token)

    #endregion
    #region rx_def
    
    def rx(self):
        return try_get("rx", self.token)
    
    @property
    def rx_buffer_size(self):
        return try_get("rx_buffer_size", self.token)
    
    @rx_buffer_size.setter
    def rx_buffer_size(self, value):
        try_set("rx_buffer_size", value, self.token)
    
    #endregion
    #region tx_def
    
    def tx(self, value):
        return try_call_1_arg("tx", value, self.token)
    
    # @tx.setter
    # def tx(self, value):
    #     try_set("tx", value, self.token)
    
    #endregion
    
    #region tx
    
    #endregion
    
    #region _dec_int_fpga_filter
    
    """Decimator and interpolator fpga filter controls"""

    def _get_rates(self, dev, output):           # ! UNTESTED !
        """Get the decimation and interpolation rates"""
        return try_get("rates", self.token)

    @property
    def rx_dec8_filter_en(self) -> bool:         # ! UNTESTED !
        """rx_dec8_filter_en: Enable decimate by 8 filter in FPGA"""
        return try_get("rx_dec8_filter_en", self.token)

    @rx_dec8_filter_en.setter
    def rx_dec8_filter_en(self, value: bool):    # ! UNTESTED !
        """rx_dec8_filter_en: Enable decimate by 8 filter in FPGA"""
        return try_set("rx_dec8_filter_en", value, self.token)

    @property
    def tx_int8_filter_en(self) -> bool:         # ! UNTESTED !
        """tx_int8_filter_en: Enable interpolate by 8 filter in FPGA"""
        return try_get("tx_int8_filter_en", self.token)

    @tx_int8_filter_en.setter
    def tx_int8_filter_en(self, value: bool):    # ! UNTESTED !
        """tx_int8_filter_en: Enable interpolate by 8 filter in FPGA"""
        return try_set("tx_int8_filter_en", value, self.token)

    #endregion
