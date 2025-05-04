#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Fluigent Pressure Pump Controller

This module provides a convenient interface to read and set pressure
on a Fluigent pressure controller, supporting multiple units:
mmHg, Pa, mbar, kPa, and mmH₂O. It also supports a timed pressure
setting: maintain a target pressure for a specified duration,
with real-time monitoring.

Usage:
    # As a library
    from fluigent_pressure_controller import FluigentPump

    pump = FluigentPump(channel=0)
    current_pa = pump.read_pressure(unit='Pa')
    pump.set_pressure(120, unit='mmHg')
    pump.set_pressure_for(100, unit='mbar', duration=30)
    pump.close()

    # As a CLI
    python fluigent_pressure_controller.py --read --unit kPa
    python fluigent_pressure_controller.py --set 750 --unit mmHg
    python fluigent_pressure_controller.py --set 500 --unit mbar --duration 30
"""

import argparse
import sys
import time

from Fluigent.SDK import (
    fgt_detect,
    fgt_init,
    fgt_close,
    fgt_get_pressure,
    fgt_set_pressure,
)

# Conversion constants
PA_PER_MBAR = 100.0
PA_PER_MMHG = 133.322
PA_PER_MMH2O = 9.80665

# Fluigent SDK error code descriptions
ERROR_CODE_DESCRIPTIONS = {
    0: "OK: No error",
    1: "USB_error: USB communication error",
    2: "Wrong_command: Wrong command was sent",
    3: "No_module_at_index: There is no module initialized at selected index",
    4: "Wrong_module: Wrong module was selected, unavailable feature",
    5: "Module_is_sleep: Module is in sleep mode, orders are not taken into account",
    6: "Master_error: Controller error",
    7: "Failed_init_all_instr: Some instruments failed to initialize",
    8: "Wrong_parameter: Function parameter is not correct or out of the bounds",
    9: "Overpressure: Pressure module is in overpressure protection",
    10: "Underpressure: Pressure module is in underpressure protection",
    11: "No_instr_found: No Fluigent instrument was found",
    12: "No_modules_found: No Fluigent pressure controller was found",
    13: "No_pressure_controller_found: No Fluigent pressure controller was found",
    14: "Calibrating: Pressure or sensor module is calibrating, read value may be incorrect",
    15: "Dll_dependency_error: Some dependencies are not found",
    16: "Processing: M-Switch is still rotating",
}


class FluigentError(Exception):
    """Custom exception for Fluigent SDK errors."""
    pass


class FluigentPump:
    def __init__(self, channel: int = 0):
        """
        Initialize the Fluigent session and select a pressure channel.
        """
        sns, types = fgt_detect()
        if not sns:
            raise FluigentError("No Fluigent controllers detected.")
        err = fgt_init()
        self._handle_error(err)
        self.channel = channel

    def read_pressure(self, unit: str = 'mbar') -> float:
        """
        Read the current pressure from the pump in the desired unit.
        Supported units: 'mbar', 'Pa', 'kPa', 'mmHg', 'mmH2O'.
        """
        res = fgt_get_pressure(self.channel, get_error=True)
        if isinstance(res, tuple):
            err, mbar = res
        else:
            err, mbar = 0, res
        self._handle_error(err)
        return self._convert_from_mbar(mbar, unit)

    def set_pressure(self, value: float, unit: str = 'mbar'):
        """
        Set the pump pressure to the specified value and unit.
        Supported units: 'mbar', 'Pa', 'kPa', 'mmHg', 'mmH2O'.
        """
        mbar = self._convert_to_mbar(value, unit)
        res = fgt_set_pressure(self.channel, mbar, get_error=True)
        if isinstance(res, tuple):
            err = res[0]
        else:
            err = res or 0
        self._handle_error(err)

    def set_pressure_for(self, value: float, unit: str = 'mbar', duration: int = 30):
        """
        Maintain the pump at the specified pressure for a given duration.
        每秒打印一次当前压力和目标压力。

        :param value: target pressure value
        :param unit: unit of pressure ('mbar','Pa','kPa','mmHg','mmH2O')
        :param duration: duration in seconds
        """
        # Convert and apply pressure
        target_mbar = self._convert_to_mbar(value, unit)
        res = fgt_set_pressure(self.channel, target_mbar)
        if isinstance(res, tuple):
            err = res[0]
        else:
            err = res or 0
        self._handle_error(err)

        # Loop for duration, printing status every second
        print(f"Maintaining {value:.4f} {unit} for {duration}s...")
        for elapsed in range(1, duration + 1):
            current = self.read_pressure(unit)
            print(f"{elapsed:>3}s | Current: {current:.4f} {unit} | Target: {value:.4f} {unit}")
            time.sleep(1)
        print("Timed pressure maintenance complete.")

    def close(self):
        """
        Reset pressure to zero and close the Fluigent session.
        """
        try:
            fgt_set_pressure(self.channel, 0)
        finally:
            fgt_close()

    @classmethod
    def _handle_error(cls, code: int):
        if code != 0:
            desc = ERROR_CODE_DESCRIPTIONS.get(code, "Unknown error")
            raise FluigentError(f"Fluigent error {code}: {desc}")

    @staticmethod
    def _convert_to_mbar(value: float, unit: str) -> float:
        u = unit.lower()
        if u == 'mbar':
            return value
        elif u == 'pa':
            return value / PA_PER_MBAR
        elif u == 'kpa':
            return value * 10.0  # 1 kPa = 10 mbar
        elif u == 'mmhg':
            return value * PA_PER_MMHG / PA_PER_MBAR
        elif u == 'mmh2o':
            return value * PA_PER_MMH2O / PA_PER_MBAR
        else:
            raise ValueError(f"Unsupported unit: {unit}")

    @staticmethod
    def _convert_from_mbar(mbar: float, unit: str) -> float:
        u = unit.lower()
        if u == 'mbar':
            return mbar
        elif u == 'pa':
            return mbar * PA_PER_MBAR
        elif u == 'kpa':
            return mbar * 0.1
        elif u == 'mmhg':
            return mbar * PA_PER_MBAR / PA_PER_MMHG
        elif u == 'mmh2o':
            return mbar * PA_PER_MBAR / PA_PER_MMH2O
        else:
            raise ValueError(f"Unsupported unit: {unit}")


def main():
    parser = argparse.ArgumentParser(description="Control Fluigent pressure pump.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--read', action='store_true', help='Read current pressure')
    group.add_argument('--set', type=float, metavar='VALUE', help='Set pressure to VALUE')
    parser.add_argument('--unit', type=str, default='mbar',
                        choices=['mbar', 'Pa', 'kPa', 'mmHg', 'mmH2O'],
                        help='Pressure unit')
    parser.add_argument('--channel', type=int, default=0, help='Pressure channel index')
    parser.add_argument('--duration', type=int,
                        help='Duration in seconds to maintain and monitor pressure')

    args = parser.parse_args()

    pump = None
    try:
        pump = FluigentPump(channel=args.channel)
        if args.read:
            val = pump.read_pressure(unit=args.unit)
            print(f"Current pressure on channel {args.channel}: {val:.4f} {args.unit}")
        elif args.set is not None and args.duration:
            pump.set_pressure_for(args.set, unit=args.unit, duration=args.duration)
        else:
            pump.set_pressure(args.set, unit=args.unit)
            print(f"Set pressure on channel {args.channel} to {args.set:.4f} {args.unit}")
    except FluigentError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Unexpected error: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        if pump:
            pump.close()

if __name__ == '__main__':
    main()