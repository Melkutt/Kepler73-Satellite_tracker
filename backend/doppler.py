# -*- coding: utf-8 -*-
"""
Kepler73 - Doppler frequency helpers.

``sgp4_engine.radial_velocity_ms`` returns range-rate in m/s, positive when the
satellite is receding. A signal you RECEIVE from a receding satellite arrives
low, so tune:

    f_rx = f_down * (1 - v_radial / C)

The satellite's own receiver hears your uplink shifted the same way, so to land
on its nominal input frequency you must transmit high when it is receding:

    f_tx = f_up * (1 + v_radial / C)

First-order approximation - ample for LEO amateur work (v_radial peaks near
+/-8 km/s, i.e. ~3e-5 of the carrier).
"""

C = 299_792_458.0


def rx_freq(f_down_hz: float, v_radial_ms: float) -> float:
    """Dial frequency to receive a downlink of ``f_down_hz``."""
    return f_down_hz * (1.0 - v_radial_ms / C)


def tx_freq(f_up_hz: float, v_radial_ms: float) -> float:
    """Dial frequency to hit an uplink of ``f_up_hz`` at the satellite."""
    return f_up_hz * (1.0 + v_radial_ms / C)


def shift_hz(f_hz: float, v_radial_ms: float) -> float:
    """Signed Doppler offset on a received carrier (negative while receding)."""
    return -f_hz * v_radial_ms / C
