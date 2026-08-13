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

"""Client-local signal helpers compatible with :mod:`uhd.dsp.signals`."""
from __future__ import annotations

import math

import numpy as np


def get_continuous_tone(
    rate,
    freq,
    ampl,
    desired_size=None,
    max_size=None,
    waveform="sine",
):
    """Generate a repeatable complex waveform using UHD's public semantics."""
    desired_size = desired_size or float(rate)
    max_size = max_size or 100e6
    assert rate > freq

    common = math.gcd(int(rate), int(freq))
    reduced_rate = int(rate) / common
    reduced_freq = int(freq) / common
    length = int(max(reduced_freq * reduced_rate, 1))
    normalized_freq = freq / rate

    if waveform in {"sine", "square"}:
        phase = 2j * np.pi * normalized_freq * np.arange(length)
        tone = np.exp(phase).astype(np.complex64, copy=False)
        tone = np.sign(tone) * ampl if waveform == "square" else tone * ampl
    elif waveform == "ramp":
        indexes = np.arange(length)
        tone = np.asarray(
            2 * (indexes * normalized_freq - np.floor(0.5 + indexes * normalized_freq)),
            dtype=np.complex64,
        )
    elif waveform == "const":
        tone = np.ones(length, dtype=np.complex64) * ampl
    else:
        raise KeyError(f"Invalid waveform type: `{waveform}'")

    if length < desired_size:
        tone = np.tile(tone, int(desired_size // length))
    if length > max_size:
        raise ValueError("Cannot create a TX buffer! Rate/Freq ratio is too odd.")
    return tone


def get_power_dbfs(signal):
    """Return variance power in dB relative to digital full scale."""
    return 10 * np.log10(np.var(signal))


def make_get_usrp_power(uhd_module):
    """Bind the receive-power helper to a generated UHD compatibility module."""

    def get_usrp_power(streamer, num_samps=1e6, chan=0):
        sample_count = int(num_samps)
        receive_buffer = np.zeros(
            (streamer.get_num_channels(), sample_count),
            dtype=np.complex64,
        )
        metadata = uhd_module.types.RXMetadata()
        command = uhd_module.types.StreamCMD(uhd_module.types.StreamMode.num_done)
        command.num_samps = sample_count
        command.stream_now = True
        streamer.issue_stream_cmd(command)
        received = streamer.recv(receive_buffer, metadata, 5.0)
        if received != sample_count:
            raise RuntimeError(
                "ERROR! get_usrp_power(): Did not receive the correct number of samples!"
            )
        return get_power_dbfs(receive_buffer[chan])

    return get_usrp_power
